from twisted.internet.protocol import DatagramProtocol
from twisted.internet import protocol, reactor, defer
import hashlib
from bencode import bencode, bdecode
from IPtools import num2ip
import struct
from random import randint
from urlparse import urlparse
import urllib
from binascii import *
from twisted.web.client import getPage

__author__ = 'TheArchitect'

class UDPScrapeProtocol(DatagramProtocol):
    '''
        Scraping trackers via the UDB protocol

        Implements UDP Tracker Protocol for BitTorrent
        http://www.bittorrent.org/beps/bep_0015.html

        Implements compact peers
        http://www.bittorrent.org/beps/bep_0023.html

    '''
    connection_id = 0x41727101980     #  default initial connection id


    def __init__(self, peerid, infohash, tracker, port,  btport, d, peer_count):
        self.tracker = tracker
        self.action = 0
        self.tracker_port = port
        self.PEER_ID = peerid
        self.INFOHASH = infohash
        self.BT_PORT = btport
        self.NUM_PEERS = peer_count
        self.transaction_id = randint(10000, 12345)
        self.d = d
        self._ips = []

    def datagramReceived(self, data, (host, port)):
        if self.action == 0:
            self.action = 1
            data = struct.unpack(">LLQ", data)
            self.connection_id = data[2]
            assert data[0] == 0
            assert data[1] == self.transaction_id
            self.sendAnnounce()
        elif self.action == 1:
            self.action = 2
            header = struct.unpack(">LLLLL", data[0:20])
            self.interval = header[2]
            assert header[0] == 1
            assert header[1] == self.transaction_id
            ipbytes = len(data) - 20

            for x in range(0, ipbytes / 6):
                ip_port = struct.unpack(">LH", data[20 + (x * 6): 26 + (x * 6)])
                self._ips.append(( num2ip(ip_port[0]), ip_port[1], ))
            self.sendScrape()
        elif self.action == 2:
            self.action = 3
            scrapedata = struct.unpack(">LLLLL", data)
            assert scrapedata[0] == 2
            assert scrapedata[1] == self.transaction_id
            self.complete = scrapedata[2]
            self.downloaded = scrapedata[3]
            self.incomplete = scrapedata[4]
            self.running = False
            self.returnResult()
            self.transport.loseConnection()
            #self.transport.stopListening()


    def returnResult(self):
        ret =  { 'peers' : self._ips,
                 'complete' : self.complete,
                 'downloaded': self.downloaded,
                 'incomplete' : self.incomplete,
                 'tracker' : self.tracker
                }
        self.d.callback(ret)

    def startProtocol(self):
        self.running = True
        reactor.resolve(self.tracker).addCallback(self.sendHandShake).addErrback(self.shutdown)

    def shutdown(self,e):
        #print 'shutdown ', self.tracker
        if self.running:
            self.running = False
            self.d.errback(e)
            self.transport.loseConnection()

    def connectionfail(self):
        if self.running:
            e = Exception('Nobody Listening at %s' % self.tracker)
            self.shutdown( e )

    def sendHandShake(self, ip):
        self.transport.connect(ip, self.tracker_port)
        msg = self.makeHandShake()
        self.transport.write(msg)
        reactor.callLater(30, self.connectionfail)

    def sendScrape(self):
        self.transaction_id = randint(10000, 12345)
        msg = struct.pack(">QLL20s", self.connection_id, self.action, self.transaction_id, str(self.INFOHASH))
        self.transport.write(msg)

    def sendAnnounce(self):
        self.transaction_id = randint(10000, 12345)
        msg = struct.pack(">QLL20s20sQQQLLLLH", self.connection_id, self.action, self.transaction_id, str(self.INFOHASH)
                          , str(self.PEER_ID), 0, 1000, 0, 0, 0, 1288, self.NUM_PEERS, self.BT_PORT)
        self.transport.write(msg)

    def makeHandShake(self):
        handshake_pack = struct.pack(">QLL", self.connection_id, self.action, self.transaction_id)
        return handshake_pack

    def clientConnectionFailed(self, connector, reason):
        pass
        #print "tracker connection failed:", reason
        #reactor.stop()


class TrackerScrape(object):
    def __init__(self, torrent, btport, peer_id):
        self.torrent = torrent
        self.infodict = torrent["info"]
        self.infohash = hashlib.sha1(bencode(self.infodict)).digest()
        self._trackers = []
        self.peer_id = peer_id
        self.btport = btport
        self._readtrackers()
        self._result = []


    def _readtrackers(self):
        if self.torrent.has_key("announce"):
            self._trackers.append( self.torrent["announce"] )
        if self.torrent.has_key("announce-list"):
            for tracker in self.torrent["announce-list"]:
                if tracker[0] not in self._trackers:
                    self._trackers.append( tracker[0] )

    def process_results(self):
        peers = []
        for result in self._result:
            if result['complete'] > self.complete:
                self.complete = result['complete']
            if result['incomplete'] > self.incomplete:
                self.incomplete = result['incomplete']
            if result['downloaded'] > self.downloaded:
                self.downloaded = result['downloaded']
            peers.extend(result['peers'])
        ret = { 'peers' : peers,
                 'complete' : self.complete,
                 'downloaded': self.downloaded,
                 'incomplete' : self.incomplete,
                 'infohash': self.infohash
                }
        self.d.callback(ret)

    def process_deferred_scrape_result(self,result):
        self._deferred_pending -= 1
        self._result.append(result)
        #print "got good scrape"
        #print self._deferred_pending
        if not self._deferred_pending:
            self.process_results()

    def process_deferred_scrape_error(self,f):
        self._deferred_pending -= 1 #reduce pending count by 1
        #print f
        #print self._deferred_pending
        if not self._deferred_pending:
            self.process_results()

    def process_http_scrape(self, result):
        data = bdecode(result)
        _ips = []
        if data.has_key('peers'):
            peer_data = data['peers']
            for x in xrange(0, len(peer_data), 6):
                ip_port = struct.unpack(">LH", data[20 + (x * 6): 26 + (x * 6)])
                self._ips.append(( num2ip(ip_port[0]), ip_port[1], ))
        ret =  { 'peers' : self._ips,
                 'complete' : data['complete'],
                 'downloaded': data['downloaded'],
                 'incomplete' : data['incomplete'],
                 'infohash' : self.infohash
                 #'tracker' : self.tracker
                }
        self.process_deferred_scrape_result(ret)


    def scrape_http_tracker(self, tracker_url, peer_count):
        payload = {
			"info_hash" : self.infohash,
            "peer_id" : self.peer_id,
            "port" : self.btport,
            "uploaded" : 0,
            "downloaded" : 0,
            "left" : 1000,
			"corrupt" : 0,
			"numwant" : peer_count,
            "compact" : 1,
			"no_peer_id" : 1}
        payload = urllib.urlencode(payload)
        d = getPage(tracker_url + "?" + payload)
        #agent = Agent(reactor)
        #d = agent.request(
        #                    'GET',
        #                    tracker_url + "?" + payload,
        #                    Headers({'User-Agent': ['uTorrent/3000(26473)']})
        #                    )
        d.addCallback(self.process_http_scrape)
        def temp(e):
            e = Exception('Failed in %s' % tracker_url)
            return self.process_deferred_scrape_error(e)
        d.addErrback(temp)

    def scrapeAllTrackers(self, peer_count = 200):
        #First prepairing variables to hold waiting counter and tracker results
        self._deferred_pending = 0
        self.complete = 0
        self.incomplete = 0
        self.downloaded = 0
        for tracker in self._trackers:
            u = urlparse(tracker)
            if u.scheme.upper() == 'UDP':
                d = defer.Deferred()
                d.addCallback(self.process_deferred_scrape_result)
                d.addErrback(self.process_deferred_scrape_error)
                #Its done this way because 'reactor.listenUDP' cant return a deferred.
                #so we just pass one to the constructor
                p = UDPScrapeProtocol(self.peer_id, self.infohash, u.hostname, u.port , self.btport, d, peer_count )
                t = reactor.listenUDP(0, p)
                self._deferred_pending += 1
            elif u.scheme.upper() == 'HTTP':
                self.scrape_http_tracker( tracker, peer_count )
                self._deferred_pending += 1
        self.d = defer.Deferred()
        return self.d

if __name__ == '__main__':
    torrentfile = "wc2.torrent"

    torrent = open(torrentfile, 'rb').read()
    torrent = bdecode(torrent)
    ts = TrackerScrape(torrent, 7827, '-UT3000-ig\x83\xe1\x94\x8c\x93v6\xbaw\xaf')
    d = ts.scrapeAllTrackers()
    def print_callback(data):
        print 'in print callback'
        print data
        reactor.stop()
    d.addCallback(print_callback)
    print 'scrape in progress'
    reactor.run()
