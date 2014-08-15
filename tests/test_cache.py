# -*- coding: utf-8 -*-
import inspect

from twisted.internet import defer
from twisted.web.server import NOT_DONE_YET
from twisted.trial import unittest

from txcaching import cache, keyregistry
from .mock import Mock
from .utils import MockRequest

_config = cache.config
cache.config = cache.ConfigSchema(False, None, None)
cache.load_config(**{"disable": False, "ip": None, "port": None})


def mocked(func):
    mock = Mock(wraps=func)
    mock.__name__ = func.__name__
    if hasattr(func, "im_class"):
        mock.im_class = func.im_class
    mock.func = func
    return mock


def redecorate(func):
    return func.init_func.func


class MockTransport:
    def __init__(self, cache_server):
        self.cache_server = cache_server

    def loseConnection(self):
        self.cache_server.connected = False


class MockCacheServer:
    def __init__(self):
        self.data = {}
        self.connected = True
        self.transport = MockTransport(self)

    def get(self, key, expireTime=0):
        return defer.succeed((0, self.data.get(key, None)))

    def add(self, key, value, expireTime=0):
        self.data[key] = value
        return defer.succeed(True)

    def flushAll(self):
        self.data = {}

@cache.cache(cache_key="blocking_func_without_args_key" )
@mocked
def blocking_func_without_args():
    return "val1", 2

@cache.cache(lazy_key=cache.default_lazy_key)
@mocked
def blocking_func_with_args(arg1, arg2="abc"):
    return str(arg1) + str(arg2), 1

@cache.cache(lazy_key=cache.default_lazy_key)
@mocked
def non_blocking_func_with_args(arg1, arg2="abc"):
    return defer.succeed((str(arg1) + str(arg2), 1))


class SomeClass:
    def __init__(self):
        self.call_count = 0

    @cache.cache(lazy_key=cache.default_lazy_key, class_name="SomeClass", exclude_self=True)
    def inc(self, x):
        self.call_count += 1
        return x + 1


class SyncService:

    def __init__(self):
        self.call_count = 0

    @cache.cache_sync_render_GET(class_name="SyncService", exclude_self=True)
    def render_GET(self, request):
        self.call_count += 1
        return "sync_render_get_result"


class AsyncService:

    def __init__(self):
        self.call_count = 0

    @cache.cache_async_render_GET(class_name="AsyncService", exclude_self=True)
    def render_GET(self, request):

        def write_response(response):
            request.write(response)
            request.finish()

        defer.succeed("async_render_get_result").addCallback(write_response)
        self.call_count += 1
        return NOT_DONE_YET


class AsyncFailingService:

    def __init__(self):
        self.call_count = 0

    @cache.cache_async_render_GET(class_name="AsyncFailService")
    def render_GET(self, request):

        def write_error(failure):
            request.setResponseCode(404, failure.value.message)
            request.finish()

        defer.fail(Exception("object not found")).addErrback(write_error)
        self.call_count += 1
        return NOT_DONE_YET

cache.config = _config

class TestCache(unittest.TestCase):
    def setUp(self):
        self.cache_server = MockCacheServer()
        self._config = cache.config
        self._connect = cache.connect
        cache.config = cache.ConfigSchema(False, None, None)
        cache.connect = lambda: defer.succeed(self.cache_server)

    def tearDown(self):
        cache.config = self._config
        cache.connect = self._connect

    @defer.inlineCallbacks
    def test_cache_blocking_func_without_args(self):
        value = "val1", 2
        func = blocking_func_without_args

        self.assertEqual(self.cache_server.connected, True)
        result = yield func()       #First call of initial function
        self.assertEqual(result, value)
        self.assertEqual(self.cache_server.connected, False) #Check that connection was closed after the call

        result = yield func()       #Get data from cache. Initial function is not called.
        self.assertEqual(result, value)
        result = yield func()       #Get data from cache. Initial function is not called.
        self.assertEqual(result, value)

        self.cache_server.flushAll()

        result = yield func()   #Second call of initial function, after we have cleared cache.
        self.assertEqual(result, value)
        result = yield func()
        self.assertEqual(result, value)

        #Check that the initial function was called only twice
        self.assertEqual(func.init_func.call_count, 2)

    @defer.inlineCallbacks
    def test_cache_blocking_func_with_args(self):
        func = blocking_func_with_args
        result = yield func(1, arg2=2)  #First call of initial function
        self.assertEqual(result, ("12", 1))

        result = yield func(1, arg2=2)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, ("12", 1))

        result = yield func(1, arg2=3)  #Second call of initial function, with another set of args
        self.assertEqual(result, ("13", 1))

        result = yield func(1, arg2=3)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, ("13", 1))

        result = yield func(1, arg2=2)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, ("12", 1))

        #Check that the initial function was called only twice
        self.assertEqual(func.init_func.call_count, 2)

    @defer.inlineCallbacks
    def test_cache_non_blocking_func_with_args(self):
        func = non_blocking_func_with_args

        result = yield func(1, arg2=2)  #First call of initial function
        self.assertEqual(result, ("12", 1))

        result = yield func(1, arg2=2)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, ("12", 1))

        result = yield func(1, arg2=3)  #Second call of initial function, with another set of args
        self.assertEqual(result, ("13", 1))

        result = yield func(1, arg2=3)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, ("13", 1))

        result = yield func(1, arg2=2)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, ("12", 1))

        #Check that the initial function was called only twice
        self.assertEqual(func.init_func.call_count, 2)

    @defer.inlineCallbacks
    def test_class_method(self):
        obj = SomeClass()

        result = yield obj.inc(1)  #First call of initial function
        self.assertEqual(result, 2)

        result = yield obj.inc(1)   #Get data from cache. Initial function is not called.
        self.assertEqual(result, 2)

        result = yield obj.inc(3)   #Second call of initial function, with another set of args
        self.assertEqual(result, 4)

        result = yield obj.inc(3)   #Get data from cache. Initial function is not called.
        self.assertEqual(result, 4)

        result = yield obj.inc(1)   #Get data from cache. Initial function is not called.
        self.assertEqual(result, 2)

        #Check that the initial function was called only twice
        self.assertEqual(obj.call_count, 2)


    @defer.inlineCallbacks
    def test_sync_render_get(self):
        service = SyncService()
        func = service.render_GET
        value = "sync_render_get_result"
        request = MockRequest("", "/ibd3/test_uri/?arg=1")
        request2 = MockRequest("", "/ibd3/test_uri/?arg=2")

        result = yield func(request)  #First call of initial function
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request.stream.getvalue(), value)
        request.clear()

        result = yield func(request)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request.stream.getvalue(), value)
        request.clear()

        result = yield func(request2)  #Second call of initial function, with another set of args
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request2.stream.getvalue(), value)
        request2.clear()

        result = yield func(request2)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request2.stream.getvalue(), value)
        request2.clear()

        result = yield func(request)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request.stream.getvalue(), value)
        request.clear()

        #Check that the initial function was called only twice
        self.assertEqual(service.call_count, 2)

    @defer.inlineCallbacks
    def test_async_render_get(self):
        service = AsyncService()
        func = service.render_GET
        value = "async_render_get_result"
        request = MockRequest("", "/ibd3/test_uri/?arg=1")
        request2 = MockRequest("", "/ibd3/test_uri/?arg=2")

        result = yield func(request)  #First call of initial function
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request.stream.getvalue(), value)
        request.clear()

        result = yield func(request)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request.stream.getvalue(), value)
        request.clear()

        result = yield func(request2)  #Second call of initial function, with another set of args
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request2.stream.getvalue(), value)
        request2.clear()

        result = yield func(request2)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request2.stream.getvalue(), value)
        request2.clear()

        result = yield func(request)  #Get data from cache. Initial function is not called.
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request.stream.getvalue(), value)
        request.clear()

        #Check that the initial function was called only twice
        self.assertEqual(service.call_count, 2)

    @defer.inlineCallbacks
    def test_async_render_get_fail(self):
        service = AsyncFailingService()
        func = service.render_GET
        error = "object not found"
        request = MockRequest("", "/ibd3/test_uri/?arg=1")

        result = yield func(request)  #First call of initial function
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request.code, 404)
        self.assertEqual(request.error, error)
        request.clear()

        result = yield func(request)  #Second call of initial function - errors must not be cached.
        self.assertEqual(result, NOT_DONE_YET)
        self.assertEqual(request.code, 404)
        self.assertEqual(request.error, error)

        #Check that the initial function was called twice
        self.assertEqual(service.call_count, 2)


    @defer.inlineCallbacks
    def test_key_registry(self):
        keyregistry.clear()

        func = cache.cache(cache_key="blocking_func_without_args_key" )(redecorate(blocking_func_without_args))
        yield func()
        self.assertEqual(keyregistry.key(func), "blocking_func_without_args_key")

        func = cache.cache(lazy_key=cache.default_lazy_key)(redecorate(blocking_func_with_args))
        args = (1,)
        kwargs = {"arg2": 2}
        yield func(*args, **kwargs)
        self.assertEqual(keyregistry.key(func, args, kwargs), cache.default_lazy_key(func, args, kwargs))
        self.assertEqual(keyregistry.keys(func), [cache.default_lazy_key(func, args, kwargs)])

        yield func(*args, **kwargs)
        self.assertEqual(keyregistry.keys(func), [cache.default_lazy_key(func, args, kwargs)])

        args2 = (3,)
        kwargs2 = {"arg2": 4}
        yield func(*args2, **kwargs2)
        self.assertEqual(keyregistry.keys(func), [cache.default_lazy_key(func, args, kwargs),
                                                  cache.default_lazy_key(func, args2, kwargs2)])

        keyregistry.remove(func)
        self.assertEqual(keyregistry.key(func, args, kwargs), None)
        self.assertEqual(keyregistry.keys(func), [])

        request = MockRequest("", "/ibd3/test_uri/?arg=1")
        request2 = MockRequest("", "/ibd3/test_uri2/?arg=2")

        func = SyncService().render_GET
        yield func(request)
        self.assertEqual(keyregistry.key(func, kwargs=request.args), "/ibd3/test_uri/?arg=1")

        func = AsyncService().render_GET
        yield func(request2)
        self.assertEqual(keyregistry.key(func, kwargs=request2.args), "/ibd3/test_uri2/?arg=2")


skipped = [
    #"test_cache_blocking_func_without_args",
    #"test_cache_blocking_func_with_args",
    #"test_cache_non_blocking_func_with_args",
    #"test_class_method",
    #"test_sync_render_get",
    #"test_async_render_get",
    #"test_async_render_get_fail",
    #"test_key_registry",
]

for test in skipped:
    getattr(TestCache, test).im_func.skip = "Temporarily skipped"