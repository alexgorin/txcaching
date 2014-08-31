
txcaching
=====

txcaching is a library that makes operations with memcached in Twisted applications much easier.
The operations in Twisted are rather specific due to its asynchronous nature; but using this library,
for many cases just a few lines of code will allow you to cache your methods calls or services.


Version:
-------
0.3

Requirements:
-------------
* Python 2.7
* [Twisted 13](https://twistedmatrix.com/trac/)
* Working [memcached](http://memcached.org/) server


Installation
----
```
pip install txcaching
```
To use this library, you will need a memcached server. To run the examples, you may use the configuration in
[this file](https://github.com/alexgorin/txcaching/blob/master/examples/memcached_examples.conf). Just copy it
to your /etc directory and run
```
service memcached start examples
```
And just run examples with your python. 

Documentation
-------------
[API reference](https://pythonhosted.org/txcaching/)



Short tutorial
----
Everything starts with the settings. To load settings of your memcached client, use `cache.load_config()` and set IP-address and port of your memcached server. For example:
```python
cache.load_config(**{"disable": False, "ip": "127.0.0.1", "port": 11212})
```
Also with this function you can disable caching in you application. Be careful: caching is disabled by default.

Module txcaching.cache provides 3 decorators to cache the calls of various types of functions:
* `cache.cache` - caches the output of a function. The function may be either blocking or asynchronous - after decoration it will be asynchronous anyway. It may seem strange, because decorators don't usually affect the behaviour of functions that way. But if you need to cache your function calls, it usually means that it is an important part of your architecture and a potential bottleneck, so it is not cool to add there a blocking call to an external server. This decorator may be used with methods as well as with functions, but if you use it with a method, you must provide the name of the class in `class_name` argument.
* `cache.cache_sync_render_GET` -caches the output of render_GET method of Resource subclass. The method must return a string - not server.NOT_DONE_YET constant.
* `cache.cache_async_render_GET` -caches the output of render_GET method of Resource subclass. The method must return a server.NOT_DONE_YET constant.

All the functions above use the arguments of cached functions to generate keys on memcached server, so you don't have to keep track of your keys on the server. 

Of course, the caching itself is just a part of the task - we also need to change or remove the cached data when our data storage is changed. But with txcaching it will be very easy as well. The library provides module `keyregistry` to help you work with cached data.

Let us see how it happens looking at the [examples](https://github.com/alexgorin/txcaching/tree/master/examples).
For these simple examples the implementation of caching may be not optimal, but it may be suitable for more complicated cases.

Both examples implement almost the same functionality - a simple server that allows to add users and their emails to data storage. The operation of getting email by username represents the case of a long heavy operation (the 2 seconds delay was added manually), so we want to cache the results of requests. In the first [example](https://github.com/alexgorin/txcaching/blob/master/examples/cache_data_store_example.py) we use decorator `cache` to cache the storage itself:

```python
class DB:
    def __init__(self):
        self.data = {}


    @cache.cache(class_name="DB", exclude_self=True)
    def get(self, username):
        """Very heavy request"""

        print "Reading from DB"
        time.sleep(2)
        email = self.data.get(username, None)
        if email:
            return defer.succeed(email)
        else:
            return defer.fail(Exception("User not found"))

    def set(self, username, email):
        self.data[username] = email
        cache_key = keyregistry.key(DB.get, args=(username,))
        if cache_key:
            cache.replace(cache_key, email)
```

Method `DB.get` is decorated by `cache` function, so its results will be cached. Pay attention to the function `DB.set`: it checks if the value corresponding to the username has been cached using `keyregistry.key` and updates the cache.
Note that we didn't have to work with cache keys directly. 

In the second [example](https://github.com/alexgorin/txcaching/blob/master/examples/cache_render_get_example.py) we apply another approach - we use `cache.cache_async_render_GET` to cache the service. (Use of `cache.cache_sync_render_GET` would be almost the same.)

```python
class EmailGetter(Resource):
    def __init__(self, username):
        self.username = username

    @cache.cache_async_render_GET(class_name="EmailGetter")
    def render_GET(self, request):

        d = DB.get(self.username)
        d.addCallback(lambda email: request.write(email_response % email))
        d.addErrback(lambda failure: request.write(email_not_found % self.username))
        d.addBoth(lambda _: request.finish())

        return server.NOT_DONE_YET


class EmailSetter(Resource):
    def render_GET(self, request):
        return set_email

    def render_POST(self, request):
        username = request.args.get("username", [""])[0]
        email = request.args.get("email", [""])[0]

        cache_key = keyregistry.key(EmailGetter.render_GET, args=(EmailGetter(username),))
        if cache_key:
            cache.delete(cache_key)

        DB.set(username, email)
        return email_set_confirmation % (username, email)
```

`EmailGetter.render_GET` is decorated by `cache_async_render_GET`, so its results will be cached. Note that in this case the result will depend on the state of Resource object (`self.username` field), so we don't set ```exclude_self=True``` in `cache_async_render_GET`.
`EmailSetter.render_POST` checks if the value corresponding to the username has been cached using `keyregistry.key` and drops the cache corresponding to the particular username.

The third [example](https://github.com/alexgorin/txcaching/blob/master/examples/cache_render_get_with_args_example.py) shows the same approach, but with reading the request
arguments instead of dynamic URL processing. In addition, in this example we ignore an argument ('_dc') that we don't want to process.

```python

class EmailGetter(Resource):

    @cache.cache_async_render_GET(class_name="EmailGetter", redundant_args=("_dc",), exclude_self=True)
    def render_GET(self, request):

        username = request.args.get("username", [""])[0]
        d = DB.get(username)
        d.addCallback(lambda email: request.write(email_response % email))
        d.addErrback(lambda failure: request.write(email_not_found % username))
        d.addBoth(lambda _: request.finish())

        return server.NOT_DONE_YET


class EmailSetter(Resource):
    def render_GET(self, request):
        return set_email

    def render_POST(self, request):
        username = request.args.get("username", [""])[0]
        email = request.args.get("email", [""])[0]

        cache_key = keyregistry.key(EmailGetter.render_GET, kwargs={"username": [username]})
        if cache_key:
            cache.delete(cache_key)

        DB.set(username, email)
        return email_set_confirmation % (username, email)
```


To work with cached data you may also use other functions provided by module `cache`: get(), set(), append(), flushAll() etc.

