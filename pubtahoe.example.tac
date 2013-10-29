from twisted.application import internet, service
from twisted.internet import reactor
from twisted.web.client import Agent, HTTPConnectionPool
from twisted.web.server import Site

from pubtahoe import PubTahoeResource, TahoeConverterResource


pool = HTTPConnectionPool(reactor)
agent = Agent(reactor, pool=pool)

root = PubTahoeResource(agent, 'http://localhost:3456/uri/')
root.putChild('', TahoeConverterResource())
site = Site(root)

application = service.Application('pubtahoe')
internet.TCPServer(8456, site).setServiceParent(application)
