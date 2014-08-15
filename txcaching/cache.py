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
    global config
    config = ConfigSchema(**kwargs)


def close_connection(result, proto):
    """Callback for closing connection with memcached server."""

    proto.transport.loseConnection()


def remove_args(url, args):
    regexps = [re.compile(r'%s=[^&?]*[&]?' % arg) for arg in args]
    for regexp in regexps:
        url = re.sub(regexp, "", url)
    return url


def register_key(success, proto, key, func, args, kwargs, exclude_self, class_name):
    if success:
        if exclude_self:
            args = args[1:]
        keyregistry.register(key, func, args, kwargs, class_name)
    close_connection(None, proto)


def set_metadata(wrapper, func):
    wrapper.__name__ = getattr(func, "__name__", "noname")
    wrapper.__module__ = getattr(func, "__module__", "noname")
    wrapper.init_func = func


def create_key(request, redundant_args=()):
    """Forms key for caching from request arguments"""

    uri = remove_args(request.uri, redundant_args)
    return str(uri)


def connect():
    """Connect to memcached server"""

    return protocol.ClientCreator(reactor, MemCacheProtocol).connectTCP(config.ip, config.port)


class RequestCachingWrapper(object):
    """Request wrapper for asynchronous functions render_GET (returning server.NOT_DONE_YET)"""

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
            addCallback(register_key, self.cache_proto, self.cache_key, self.func, (self.resource,),\
                        self.request.args, self.exclude_self, self.class_name)

    def __str__(self):
        return self.stream.getvalue()

    def __getattr__(self, item):
        if not hasattr(self, item):
            return getattr(self.request, item)


def cache_sync_render_GET(expireTime=0, redundant_args=(), exclude_self=False, class_name=""):
    """Кэшировать вывод функции render_GET, возвращающей строку"""

    def decorator(func):
        if config.disable:
            return func

        def wrapper(self, request):
            d = connect()

            def final(cache, proto):
                flags, value = cache
                if value is not None:
                    close_connection(None, proto)
                    request.write(value)
                    request.finish()
                else:
                    cache_key = create_key(request, redundant_args=redundant_args)
                    value = read_without_cache(None)
                    proto.add(cache_key, value, expireTime=expireTime).\
                        addCallback(register_key, proto, cache_key, func, (self,), request.args, exclude_self, class_name=class_name).\
                        addErrback(close_connection, proto)

            def read_without_cache(_):
                result = str(func(self, request))
                request.write(result)
                request.finish()
                return result

            def check_in_cache(proto):
                return proto.get(create_key(request)).addCallback(final, proto).addErrback(read_without_cache)

            d.addCallbacks(check_in_cache, read_without_cache)
            return server.NOT_DONE_YET

        set_metadata(wrapper, func)
        return wrapper

    return decorator


def cache_async_render_GET(expireTime=0, exclude_self=False, class_name=""):
    """Кэшировать вывод функции render_GET, возвращающей server.NOT_DONE_YET"""

    def decorator(func):
        if config.disable:
            return func

        def wrapper(self, request):
            d = connect()

            def final(cache, proto):
                flags, value = cache
                if value is not None:
                    close_connection(None, proto)
                    request.write(value)
                    request.finish()
                else:
                    read_without_cache(None, proto)

            def read_without_cache(arg, proto=None):
                cache_key = create_key(request)
                if proto is None:
                    caching_request = request
                else:
                    caching_request = RequestCachingWrapper(request, cache_key, proto, func, self, expireTime=expireTime,\
                                                            exclude_self=exclude_self, class_name=class_name)

                return func(self, caching_request)

            def check_in_cache(proto):
                return proto.get(create_key(request)).addCallback(final, proto).addErrback(read_without_cache, None)

            d.addCallbacks(check_in_cache, read_without_cache)
            return server.NOT_DONE_YET

        set_metadata(wrapper, func)
        return wrapper

    return decorator


def default_lazy_key(func, args, kwargs, exclude_self=False, class_name=""):
    """Default function which generates cache key using function and its arguments.
    All the arguments must be picklable. It the function is a method, the object (self)
    is not pickled. Instead, its name is used. If you want the object state to affect the key,
    implement your own "lazy key" function.
    """

    if exclude_self:
        args = args[1:]

    args = tuple(args) if args else tuple()
    func_id = keyregistry.func_id(func, class_name=class_name)
    cache_key = "_".join([pickle.dumps(arg) for arg in (func_id,) + tuple(args) + tuple(kwargs.iteritems())])
    cache_key = "".join(cache_key.split())
    return cache_key


def cache(cache_key=None, lazy_key=default_lazy_key, class_name="", expireTime=0, exclude_self=False):
    """Кэшировать вывод функции. Независимо от того, является ли функция блокирующей,
    обертка будет возвращать Deferred.
    """
    def decorator(func):
        if config.disable:
            return func

        def wrapper(*args, **kwargs):
            key = cache_key or lazy_key(func, args, kwargs, exclude_self=exclude_self, class_name=class_name)

            d = connect()

            def write_to_cache(value, proto):
                proto.add(key, pickle.dumps(value), expireTime=expireTime).\
                    addBoth(register_key, proto, key, func, args, kwargs, exclude_self, class_name)

                close_connection(None, proto)
                return value

            def final(cache, proto):
                flags, value = cache
                if value is not None:
                    close_connection(None, proto)
                    return defer.succeed(pickle.loads(value))
                else:
                    return maybeDeferred(func, *args, **kwargs).addCallback(write_to_cache, proto)

            def read_without_cache(arg):
                return func(*args, **kwargs)

            def check_in_cache(proto):
                return proto.get(key).addCallback(final, proto).addErrback(read_without_cache)

            return d.addCallbacks(check_in_cache, read_without_cache)

        set_metadata(wrapper, func)
        return wrapper

    return decorator


def replace(key, val, flags=0, expireTime=0):
    connect().addCallback(lambda proto: proto.replace(key, pickle.dumps(val), flags, expireTime))


def add(key, val, flags=0, expireTime=0):
    connect().addCallback(lambda proto: proto.add(key, pickle.dumps(val), flags, expireTime))


def set(key, val, flags=0, expireTime=0):
    connect().addCallback(lambda proto: proto.set(key, pickle.dumps(val), flags, expireTime))


def get(key, withIdentifier=False):
    connect().addCallback(lambda proto: proto.get(key, withIdentifier)).addCallback(lambda data: data[:-1] + (pickle.loads(data[-1]),))


def getMultiple(keys, withIdentifier=False):
    connect().addCallback(lambda proto: proto.getMultiple(keys, withIdentifier)).addCallback(lambda data: {
        key: info[:-1] + (pickle.loads(info[-1]),)
        for key, info in data.iteritems()
    })


def delete(key):
    connect().addCallback(lambda proto: proto.delete(key))


def flushAll():
    connect().addCallback(lambda proto: proto.flushAll())


def version():
    connect().addCallback(lambda proto: proto.version())


def stats():
    connect().addCallback(lambda proto: proto.stats())


def append(key, val):
    connect().addCallback(lambda proto: proto.append(key, pickle.dumps(val)))


def prepend(key, val):
    connect().addCallback(lambda proto: proto.prepend(key, pickle.dumps(val)))


def increment(key, val=1):
    connect().addCallback(lambda proto: proto.increment(key, pickle.dumps(val)))


def decrement(key, val=1):
    connect().addCallback(lambda proto: proto.decrement(key, pickle.dumps(val)))