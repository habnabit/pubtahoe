import bsddb3
from twisted.application import internet, service
from twisted.internet import reactor
from twisted.web.client import Agent, HTTPConnectionPool
from twisted.web.server import Site

from pubtahoe import PubTahoeResource, TahoeConverterResource


pool = HTTPConnectionPool(reactor)
agent = Agent(reactor, pool=pool)
shortdb = bsddb3.hashopen('short.db')

root = PubTahoeResource(shortdb, agent, 'http://localhost:3456/uri/')
root.putChild('', TahoeConverterResource(shortdb))
site = Site(root)

application = service.Application('pubtahoe')
internet.TCPServer(8456, site).setServiceParent(application)
