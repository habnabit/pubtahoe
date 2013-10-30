# Copyright (c) Aaron Gallagher <_@habnab.it>
# See COPYING for details.

from twisted.internet.error import ConnectionDone, ConnectionLost
from twisted.internet import protocol, defer
from twisted.web.client import ResponseDone, ResponseFailed
from twisted.web.http import PotentialDataLoss
from twisted.web.resource import Resource, NoResource
from twisted.web.server import NOT_DONE_YET
from twisted.web.template import tags, renderElement

import crockford
import magic

import mimetypes
import base64
import urllib
import json
import re
import os


tahoeRegex = re.compile(
    '(URI:(?:(?:CHK|DIR2(?:-MDMF)?(?:-(?:CHK|LIT|RO))?|LIT|(?:SSK|MDMF)(?:-RO)'
    '?):)[A-Za-z0-9:]+)'
)


class StringReceiver(protocol.Protocol):
    def __init__(self, byteLimit=None):
        self.bytesRemaining = byteLimit
        self.deferred = defer.Deferred()
        self._buffer = []

    def dataReceived(self, data):
        data = data[:self.bytesRemaining]
        self._buffer.append(data)
        if self.bytesRemaining is not None:
            self.bytesRemaining -= len(data)
            if not self.bytesRemaining:
                self.transport.stopProducing()

    def connectionLost(self, reason):
        if ((reason.check(ResponseFailed) and any(exn.check(ConnectionDone, ConnectionLost)
                                                  for exn in reason.value.reasons))
                or reason.check(ResponseDone, PotentialDataLoss)):
            self.deferred.callback(''.join(self._buffer))
        else:
            self.deferred.errback(reason)


def receive(response, receiver):
    response.deliverBody(receiver)
    return receiver.deferred


class TahoeReceiver(protocol.Protocol):
    def __init__(self, request):
        self.request = request
        self.proto = None
        self.buffer = []
        self.received = 0
        self.initialDeferred = defer.Deferred()
        self.requestFinished = False
        self.done = False

    def dataReceived(self, data):
        if self.buffer is None:
            self.request.write(data)
            return
        self.buffer.append(data)
        self.received += len(data)
        if self.received > 16384:
            self.initialDeferred.callback(''.join(self.buffer))
            self.buffer = None

    def finish(self, result):
        self.requestFinished = True
        self.transport.stopProducing()

    def connectionLost(self, reason):
        if self.buffer is not None:
            self.initialDeferred.callback(''.join(self.buffer))
        if not self.requestFinished:
            self.request.finish()
        self.done = True


class TahoeResource(Resource):
    isLeaf = True

    def __init__(self, agent, capURL, extension=None):
        Resource.__init__(self)
        self.agent = agent
        self.capURL = capURL
        self.extension = extension

    def _showDirectory(self, request, dirinfo):
        children = dirinfo[1]['children']
        body = tags.ul(*[
            tags.li(tags.a(name, href='/' + info[1]['ro_uri']))
            for name, info in children.iteritems()
        ])
        renderElement(request, body)

    @defer.inlineCallbacks
    def _fetchFromTahoe(self, request):
        d = self.agent.request('GET', self.capURL + '?t=json')
        d.addCallback(receive, StringReceiver())
        d.addCallback(json.loads)
        info = yield d
        if info[0] == 'dirnode':
            self._showDirectory(request, info)
            return
        elif info[0] != 'filenode':
            raise ValueError("that's not a valid CAP!")

        resp = yield self.agent.request('GET', self.capURL)
        receiver = TahoeReceiver(request)
        resp.deliverBody(receiver)
        initial = yield receiver.initialDeferred
        if self.extension:
            mime, _ = mimetypes.guess_type('x.%s' % (self.extension,))
        else:
            mime = magic.from_buffer(initial, mime=True)
        mime = mime or 'application/octet-stream'
        request.setHeader('content-type', mime)
        request.write(initial)
        request.notifyFinish().addBoth(receiver.finish)

    def render_GET(self, request):
        d = self._fetchFromTahoe(request)
        d.addErrback(request.processingFailed)
        return NOT_DONE_YET


class PubTahoeResource(Resource):
    def __init__(self, shortdb, agent, tahoeURL):
        Resource.__init__(self)
        self.shortdb = shortdb
        self.agent = agent
        self.tahoeURL = tahoeURL

    def getChild(self, child, request):
        cap, _, ext = child.partition('.')
        if len(cap) == 15 and cap in self.shortdb:
            cap, _, storedExt = self.shortdb[cap].partition('.')
            ext = ext or storedExt
        if not tahoeRegex.match(cap):
            try:
                cap = base64.urlsafe_b64decode(cap)
            except TypeError:  # TypeError??? really???
                return NoResource('Invalid base64')
            if not tahoeRegex.match(cap):
                return NoResource('Not a valid tahoe CAP URI')
        capURL = self.tahoeURL + urllib.quote(cap)
        return TahoeResource(self.agent, capURL, ext)


class TahoeConverterResource(Resource):
    def __init__(self, shortdb):
        Resource.__init__(self)
        self.shortdb = shortdb

    def render_GET(self, request):
        body = tags.form(
            tags.label('Tahoe URI', for_='uri'), ' ',
            tags.input(id='uri', type='text', name='uri'),
            tags.br(),
            tags.label('Extension', for_='ext'), ' ',
            tags.input(id='ext', type='text', name='ext'),
            tags.br(),
            tags.input(type='submit', value='Convert to HTTP URL'),
            action='', method='POST')
        return renderElement(request, body)

    def render_POST(self, request):
        uri = request.args.get('uri', [])
        if not uri:
            return self.render_GET(request)
        ext = request.args.get('ext', [])

        b64uri = base64.urlsafe_b64encode(uri[0])
        extension = ''
        if ext and ext[0]:
            extension = '.' + ext[0].lstrip('.')
        if uri[0] not in self.shortdb:
            while True:
                short = crockford.b32encode(os.urandom(9)).lower()
                if short not in self.shortdb:
                    break
            self.shortdb[short] = uri[0]
            self.shortdb[uri[0]] = short
            self.shortdb.sync()
        else:
            short = self.shortdb[uri[0]]

        body = tags.p(
            tags.a('long url', href=b64uri + extension), '; ',
            tags.a('medium url', href='/' + uri[0] + extension), '; ',
            tags.a('short url', href=short + extension))
        return renderElement(request, body)
