# -*- coding: utf-8 -*-

from collections import namedtuple
import cPickle as pickle
import re
from StringIO import StringIO

from twisted.internet import reactor, protocol
from twisted.internet import defer
from twisted.internet.defer import maybeDeferred
from twisted.protocols.memcache import MemCacheProtocol, DEFAULT_PORT
from twisted.web import server

from . import keyregistry

ConfigSchema = namedtuple("Config", ["disable", "ip", "port"])
default_config = ConfigSchema(**{"disable": True, "ip": "127.0.0.1", "port": DEFAULT_PORT})
config = default_config


def load_config(**kwargs):
    """Load configuration. Must be called before use of other method of this module.
    By default, caching is disabled.
    """
    global config
    config = ConfigSchema(**kwargs)


def _close_connection(result, proto):
    """Callback for closing connection with memcached server."""

    proto.transport.loseConnection()
    return defer.succeed(result)


def _remove_args(url, args):
    """Remove particular arguments from url.
    For example, _remove_args(/service/?a=1&b=2&c=3, (a,c)) == /service/?b=2
    """
    regexps = [re.compile(r'%s=[^&?]*[&]?' % arg) for arg in args]
    for regexp in regexps:
        url = re.sub(regexp, "", url)
    return url


def _register_key(success, proto, key, func, args, kwargs, exclude_self, class_name):
    """Put function call into registry."""

    if success:
        if exclude_self:
            args = args[1:]
        keyregistry.register(key, func, args, kwargs, class_name)

    _close_connection(None, proto)


def _set_metadata(wrapper, func):
    """Set metadata for function wrapper"""

    wrapper.__name__ = getattr(func, "__name__", "noname")
    wrapper.__module__ = getattr(func, "__module__", "noname")
    wrapper.init_func = func


def _create_key(request, redundant_args=()):
    """Forms key for caching from request arguments."""

    uri = _remove_args(request.uri, redundant_args)
    return str(uri)


def connect():
    """Connect to memcached server

    :returns: Deferred which fires with protocol instance
    """

    return protocol.ClientCreator(reactor, MemCacheProtocol).connectTCP(config.ip, config.port)


class RequestCachingWrapper(object):
    """Request wrapper for asynchronous functions render_GET (returning :const:`server.NOT_DONE_YET`).
    This class is not to be used directly.
    """

    def __init__(self, request, cache_key, cache_proto, func, resource, expireTime=0, exclude_self=False, class_name=""):
        self.request = request
        self.cache_key = cache_key
        self.cache_proto = cache_proto
        self.func = func
        self.resource = resource
        self.expireTime = expireTime
        self.exclude_self = exclude_self
        self.class_name = class_name

        self.stream = StringIO()
        self.error_occurred = False

    def getSession(self):
        return self.request.getSession()

    def finish(self):
        self.request.finish()
        if not self.error_occurred:
            self._write_to_cache()

    def write(self, data):
        self.stream.write(data)
        self.request.write(data)

    def notifyFinish(self):
        self.request.notifyFinish()

    def setResponseCode(self, code, error):
        self.request.setResponseCode(code, error)
        if code != 200:
            self.error_occurred = True
        else:
            self.error_occurred = False

    def _write_to_cache(self):
        self.cache_proto.add(self.cache_key, str(self), expireTime=self.expireTime).\
            addCallback(_register_key, self.cache_proto, self.cache_key, self.func, (self.resource,),\
                        self.request.args, self.exclude_self, self.class_name)

    def __str__(self):
        return self.stream.getvalue()

    def __getattr__(self, item):
        if not hasattr(self, item):
            return getattr(self.request, item)


def cache_sync_render_GET(expireTime=0, redundant_args=(), exclude_self=False, class_name=""):
    """Cache the output of function render_GET which returns a string.
    If it returns :const:`server.NOT_DONE_YET`, use :func:`cache_async_render_GET` instead. Shall be used as decorator.

    :param expireTime:
        The lifetime of the cache key. If set to 0, lifetime is not limited.
    :param redundant_args:
        Request arguments we want to ignore. For example, ExtJS generates the random argument _dc to prevent
        browser caching. We can get rid of it using redundant_args=("_dc",)
    :param exclude_self:
        If it is true, the state of resource object will not be used to create cache key. If it is set to false,
        changing the resource object will change the cache key, even if the request parameters are the same.
    :param class_name:
        Name of class of the resource. It is required because the decorator can only see an unbound method,
        unrelated to any class.
    """

    def decorator(func):
        if config.disable:
            return func

        def wrapper(self, request):
            d = connect()

            def final(cache, proto):
                flags, value = cache
                if value is not None:
                    _close_connection(None, proto)
                    request.write(value)
                    request.finish()
                else:
                    cache_key = _create_key(request, redundant_args=redundant_args)
                    value = read_without_cache(None)
                    proto.add(cache_key, value, expireTime=expireTime).\
                        addCallback(_register_key, proto, cache_key, func, (self,), request.args, exclude_self, class_name=class_name).\
                        addErrback(_close_connection, proto)

            def read_without_cache(_):
                result = str(func(self, request))
                request.write(result)
                request.finish()
                return result

            def check_in_cache(proto):
                return proto.get(_create_key(request)).addCallback(final, proto).addErrback(read_without_cache)

            d.addCallbacks(check_in_cache, read_without_cache)
            return server.NOT_DONE_YET

        _set_metadata(wrapper, func)
        return wrapper

    return decorator


def cache_async_render_GET(expireTime=0, redundant_args=(), exclude_self=False, class_name=""):
    """Cache the output of function render_GET which returns :const:`server.NOT_DONE_YET`.
    If it returns a string, use :func:`cache_sync_render_GET` instead. Shall be used as decorator.

    :param expireTime:
        The lifetime of the cache key. If set to 0, lifetime is not limited.
    :param redundant_args:
        Request arguments we want to ignore. For example, ExtJS generates the random argument _dc to prevent
        browser caching. We can get rid of it using redundant_args=("_dc",)
    :param exclude_self:
        If it is true, the state of resource object will not be used to create cache key. If it is set to false,
        changing the resource object will change the cache key, even if the request parameters are the same.
    :param class_name:
        Name of class of the resource. It is required because the decorator can only see an unbound method,
        unrelated to any class.
    """
    def decorator(func):
        if config.disable:
            return func

        def wrapper(self, request):
            d = connect()

            def final(cache, proto):
                flags, value = cache
                if value is not None:
                    _close_connection(None, proto)
                    request.write(value)
                    request.finish()
                else:
                    read_without_cache(None, proto)

            def read_without_cache(arg, proto=None):
                cache_key = _create_key(request, redundant_args)
                if proto is None:
                    caching_request = request
                else:
                    caching_request = RequestCachingWrapper(request, cache_key, proto, func, self, expireTime=expireTime,\
                                                            exclude_self=exclude_self, class_name=class_name)

                return func(self, caching_request)

            def check_in_cache(proto):
                return proto.get(_create_key(request)).addCallback(final, proto).addErrback(read_without_cache, None)

            d.addCallbacks(check_in_cache, read_without_cache)
            return server.NOT_DONE_YET

        _set_metadata(wrapper, func)
        return wrapper

    return decorator


def default_lazy_key(func, args, kwargs, exclude_self=False, class_name=""):
    """Default function which generates cache key using function and its arguments.
    All the arguments must be picklable.

    :param func: Function to cache
    :param args: Function arguments
    :param kwargs: Function keyword arguments
    :param exclude_self: If the function is a method and this parameter is set to true, the state of object will not be used to create cache key.
    :param class_name: If the function is a method, name of class of the object must be set up.

    :returns: key for caching server (str)
    """

    if exclude_self:
        args = args[1:]

    args = tuple(args) if args else tuple()
    func_id = keyregistry.func_id(func, class_name=class_name)
    cache_key = "_".join([pickle.dumps(arg) for arg in (func_id,) + tuple(args) + tuple(kwargs.iteritems())])
    cache_key = "".join(cache_key.split())
    return cache_key


def cache(cache_key=None, lazy_key=default_lazy_key, class_name="", expireTime=0, exclude_self=False):
    """ Cache the output of the function. Shall be used as decorator.

    :param cache_key: Set up the cache key directly. In this case, only one key will be used to store function output, \
    regardless from its arguments. Usually it is not recommended to use.
    :param lazy_key: Function that uses the function and its arguments to produce the cache keys. \
        For most cases, :func:`default_lazy_key` will be fine, but you may use your own function.
    :param class_name:
        Name of class of the resource. It is required because the decorator can only see an unbound method,\
        unrelated to any class.
    :param expireTime:
        The lifetime of the cache key. If set to 0, lifetime is not limited.
    :param exclude_self:
        If it is true, the state of resource object will not be used to create cache key. If it is set to false,
        changing the resource object will change the cache key, even if the other function arguments are the same.
    """
    def decorator(func):
        if config.disable:
            return func

        def wrapper(*args, **kwargs):
            key = cache_key or lazy_key(func, args, kwargs, exclude_self=exclude_self, class_name=class_name)

            d = connect()

            def write_to_cache(value, proto):
                proto.add(key, pickle.dumps(value), expireTime=expireTime).\
                    addBoth(_register_key, proto, key, func, args, kwargs, exclude_self, class_name)

                _close_connection(None, proto)
                return value

            def final(cache, proto):
                flags, value = cache
                if value is not None:
                    _close_connection(None, proto)
                    return defer.succeed(pickle.loads(value))
                else:
                    return maybeDeferred(func, *args, **kwargs).addCallback(write_to_cache, proto)

            def read_without_cache(arg):
                return func(*args, **kwargs)

            def check_in_cache(proto):
                return proto.get(key).addCallback(final, proto).addErrback(read_without_cache)

            return d.addCallbacks(check_in_cache, read_without_cache)

        _set_metadata(wrapper, func)
        return wrapper

    return decorator


def replace(key, val, flags=0, expireTime=0):
    """Wrapper for :meth:`twisted.protocol.memcached.MemCacheProtocol.replace`"""
    return connect().addCallback(lambda proto: proto.replace(key, pickle.dumps(val), flags, expireTime).\
                                                                        addBoth(_close_connection, proto))


def add(key, val, flags=0, expireTime=0):
    """Wrapper for :py:meth:`twisted.protocol.memcached.MemCacheProtocol.add`"""
    return connect().addCallback(lambda proto: proto.add(key, pickle.dumps(val), flags, expireTime).\
                                                                    addBoth(_close_connection, proto))


def set(key, val, flags=0, expireTime=0):
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.set`"""
    return connect().addCallback(lambda proto: proto.set(key, pickle.dumps(val), flags, expireTime).\
                                                                    addBoth(_close_connection, proto))


def get(key, withIdentifier=False):
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.get`"""
    return connect().addCallback(lambda proto: proto.get(key, withIdentifier).\
                                                        addBoth(_close_connection, proto)).\
                                                        addCallback(lambda data: data[:-1] + (pickle.loads(data[-1]),))


def getMultiple(keys, withIdentifier=False):
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.getMultiple`"""
    return connect().addCallback(lambda proto: proto.getMultiple(keys, withIdentifier).\
                                                addBoth(_close_connection, proto)).\
                                                addCallback(lambda data: {
                                                                key: info[:-1] + (pickle.loads(info[-1]),)
                                                                for key, info in data.iteritems()
                                                            })


def delete(key):
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.delete`"""
    return connect().addCallback(lambda proto: proto.delete(key).addBoth(_close_connection, proto))


def flushAll():
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.flushAll`"""
    return connect().addCallback(lambda proto: proto.flushAll().addBoth(_close_connection, proto))


def version():
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.version`"""
    return connect().addCallback(lambda proto: proto.version().addBoth(_close_connection, proto))


def stats():
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.stats`"""
    return connect().addCallback(lambda proto: proto.stats().addBoth(_close_connection, proto))


def append(key, val):
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.append`"""
    return connect().addCallback(lambda proto: proto.append(key, pickle.dumps(val)).addBoth(_close_connection, proto))


def prepend(key, val):
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.prepend`"""
    return connect().addCallback(lambda proto: proto.prepend(key, pickle.dumps(val)).addBoth(_close_connection, proto))


def increment(key, val=1):
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.increment`"""
    return connect().addCallback(lambda proto: proto.increment(key, pickle.dumps(val)).addBoth(_close_connection, proto))


def decrement(key, val=1):
    """Wrapper for :func:`twisted.protocol.memcached.MemCacheProtocol.decrement`"""
    return connect().addCallback(lambda proto: proto.decrement(key, pickle.dumps(val)).addBoth(_close_connection, proto))