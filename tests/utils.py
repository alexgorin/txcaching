# -*- coding: utf-8 -*-

from StringIO import StringIO

from twisted.internet.defer import Deferred


class MockSession(object):
    def __init__(self, utoken):
        self.utoken = utoken


class MockRequest(object):
    def __init__(self, utoken, uri=None):
        self._utoken = utoken
        self.uri = uri
        self._finishedDeferreds = []
        self.stream = StringIO()
        self.args = {}

    def getSession(self):
        return MockSession(self._utoken)

    def finish(self):
        pass

    def write(self, data):
        self.stream.write(data)

    def clear(self):
        self.stream.close()
        self.stream = StringIO()

    def notifyFinish(self):
        finished = Deferred()
        self._finishedDeferreds.append(finished)
        return finished

    def setResponseCode(self, code, error):
        self.code = code
        self.error = error

    def __str__(self):
        return "\nrequest:args = %s\ndata = %s\n" % (self.args, self.stream.getvalue())

