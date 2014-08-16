# -*- coding: utf-8 -*-
import time

from twisted.internet import defer, reactor
from twisted.web import server
from twisted.web.resource import Resource

from txcaching import cache, keyregistry

cache.load_config(**{"disable": False, "ip": "127.0.0.1", "port": 11212})

header = """
<html>
<head>
    <style>
        body {
            font-family: "Calisto MT", "Bookman Old Style", Bookman, "Goudy Old Style", Garamond, "Hoefler Text", "Bitstream Charter", Georgia, serif;
            font-size: 1.3em;
            border: #000000 2px solid;
            border-radius: 20px;
            padding: 15px;
        }
    </style>
</head>
<body>
BODY
</body>
</html>
"""


main_html = header.replace("BODY", """
<h1>Users</h1>
<ol>
%s
</ol>
<h6><a href="/set">Set email for a user</a>
""")

get_email_by_name = header.replace("BODY", """
<form action="/get" method="get">
    <label>
        Username:
        <input name="username" type="text"/>
    </label>
	<input class='btn' type="submit" value="Get email" />
</form>
<a href="/">Home</a>
""")

email_response = header.replace("BODY", """
<h4>EMAIL=%s</h4>
<a href="/">Home</a>
""")

email_not_found = header.replace("BODY", """
<h4>Email is not set for the user %s</h4>
<a href="/">Home</a>
""")

email_set_confirmation = header.replace("BODY", """
<h4>Email %s for user %s has been set.</h4>
<a href="/">Home</a>
""")

set_email = header.replace("BODY", """
<form action="/set" method="post">
    <label>
        Username:
        <input name="username" type="text"/>
    </label>
    <label>
        Email:
        <input name="email" type="text"/>
    </label>
	<input class='btn' type="submit" value="Set email" />
</form>
<a href="/">Home</a>
""")


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

db = DB()


class Getter(Resource):
    def getChild(self, path, request):
        return EmailGetter(path)


class EmailGetter(Resource):
    def __init__(self, username):
        self.children = {
            "": self
        }
        self.username = username

    def render_GET(self, request):

        d = db.get(self.username)
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
        db.set(username, email)
        return email_set_confirmation % (username, email)


class MainResource(Resource):

    def getChild(self, path, request):
        if not path:
            return self
        if path == "set":
            return EmailSetter()
        if path == "get":
            return Getter()

    def render_GET(self, request):
        return main_html % "\n".join(
            '<li><a href="/get/%s">%s</a>' % (username, username)
            for username in db.data.keys()
        )

cache.flushAll()
reactor.listenTCP(8888, server.Site(MainResource()))
reactor.run()