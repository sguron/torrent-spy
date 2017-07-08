import base64
import json
import pickle
import random
import urllib
import urllib2
from zope.interface import Interface, implements
from twisted.internet import protocol, reactor, defer, task
from twisted.protocols.basic import IntNStringReceiver, _RecvdCompatHack, _PauseableMixin, LineReceiver
from twisted.python import components
from twisted.application import internet, service
from twisted.internet.threads import deferToThread
import struct
from IPtools import num2ip, ip2num, ip2bin, bin2ip
from bencode import bencode, bdecode
import hashlib
from copy import deepcopy
from binascii import hexlify
from database import *
from scrape import TrackerScrape
from collections import namedtuple
import ctypes
from twisted.web.client import getPage

__author__ = 'TheArchitect'

#
#
#  This file implements the bittorrent protocol using twisted. Objective here is to track the torrent
#  clients and gather ip's of downloaders and then send it off to a web analytics service.
#  Part of the bittorrent protocol (PIECE, REQUEST) that actually transfers (copyrighted) data
#  has not been implemented.
#


PEERLIST_UPLOAD_DELAY = 1 * 60 #30minutes
#TrackerStatus = namedtuple('TrackerStatus',['downloaded','complete','incomplete'])

class IMicroTorrentService(Interface):
    def getUser(user):
        """
          Return a deferred returning a string.
          """

    def getUsers(self):
        """
          Return a deferred returning a list of strings.
        """


class MicroTorrent(Interface):
    def getUser(user):
        """
          Return a deferred returning a string.
          """

    def getUsers(self):
        """
          Return a deferred returning a list of strings.
          """


def catchError(err):
    return "Internal error in server"


class HandshakedIntNStringReceiver(protocol.Protocol, _PauseableMixin):
    '''
     HandshakedIntNStringReceiver adds Handshake support to twisted's IntNStringReceiver class
     Also improves data handing over initial code to support massive amounts of socket requests
    '''
    handshake = False
    handshake_len = 68
    _hs_unprocessed = ''
    _unprocessed = ''
    maxlength = 9999
    max_msg_length = 8000
    msg_length = -1
    MAX_LENGTH = 9999
    _unprocessed = ""
    _compatibilityOffset = 0
    recvd = _RecvdCompatHack()


    def dataReceived(self, data):
        if not self.handshake:
            self.handshakeDataReceived(data)
        else:
            #IntNStringReceiver.dataReceived(self,data)
            self.stringDataReceived(data)

    def handshakeReceived(self, data):
        raise NotImplementedError

    def stringReceived(self, string):
        raise NotImplementedError

    def lengthLimitExceeded(self, length):
        print 'Length exceeded.'

    def stringDataReceived(self, data):
        """
        Convert int prefixed strings into calls to stringReceived.
        """
        # Try to minimize string copying (via slices) by keeping one buffer
        # containing all the data we have so far and a separate offset into that
        # buffer.
        alldata = self._unprocessed + data
        currentOffset = 0
        prefixLength = self.prefixLength
        fmt = self.structFormat
        self._unprocessed = alldata

        while len(alldata) >= (currentOffset + prefixLength) and not self.paused:
            messageStart = currentOffset + prefixLength
            length, = struct.unpack(fmt, alldata[currentOffset:messageStart])
            if length > self.MAX_LENGTH:
                self._unprocessed = alldata
                self._compatibilityOffset = currentOffset
                self.lengthLimitExceeded(length)
                return
            messageEnd = messageStart + length
            if len(alldata) < messageEnd:
                break

            # Here we have to slice the working buffer so we can send just the
            # netstring into the stringReceived callback.
            packet = alldata[messageStart:messageEnd]
            currentOffset = messageEnd
            self._compatibilityOffset = currentOffset
            self.stringReceived(packet)

            # Check to see if the backwards compat "recvd" attribute got written
            # to by application code.  If so, drop the current data buffer and
            # switch to the new buffer given by that attribute's value.
            if 'recvd' in self.__dict__:
                alldata = self.__dict__.pop('recvd')
                self._unprocessed = alldata
                self._compatibilityOffset = currentOffset = 0
                if alldata:
                    continue
                return

        # Slice off all the data that has been processed, avoiding holding onto
        # memory to store it, and update the compatibility attributes to reflect
        # that change.
        self._unprocessed = alldata[currentOffset:]
        self._compatibilityOffset = 0

    def handshakeDataReceived(self, data):
        data = self._hs_unprocessed + data
        handshakeLength = self.handshake_len
        self._hs_unprocessed = data
        if len(self._hs_unprocessed) == handshakeLength:
            packet = self._hs_unprocessed
            self._hs_unprocessed = ''
            self.handshakeReceived(packet)
        elif len(self._hs_unprocessed) > handshakeLength:
            packet = self._hs_unprocessed[:handshakeLength]
            self.handshakeReceived(packet)
            if self.handshake:
                self.stringDataReceived(self._hs_unprocessed[handshakeLength:])
            self._hs_unprocessed = ''
        else:
            return


class MicroTorrentProtocol(HandshakedIntNStringReceiver):
    '''
    This class implements the bittorrent specification according to bep 00003
    http://www.bittorrent.org/beps/bep_0003.html

    Utilizes HandshakedIntNStringReceiver

    Implements PEER-EXCHANGE ie ut-pex
    http://www.bittorrent.org/beps/bep_0011.html

    Implements compact peers
    http://www.bittorrent.org/beps/bep_0023.html

    Also implements utorrent metadataexchange
    http://www.bittorrent.org/beps/bep_0009.html

    Implements the following bittorrent messages: INTERESTED, NOTINTERESTED, EXTENDED ignores everything else.
    '''
    structFormat = ">L"
    prefixLength = struct.calcsize(structFormat)
    #handshake = False
    protocol_name = 'BitTorrent protocol'
    reserved_bits = 0x0000000000100005 #immitate uTorrent implementation
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    # index
    HAVE = 4
    # index, bitfield
    BITFIELD = 5
    # index, begin, length
    REQUEST = 6
    # index, begin, piece
    PIECE = 7
    # index, begin, piece
    CANCEL = 8
    PORT = 9
    EXTENDED = 20
    #adding support for supporting FaxtExtension in the future ;)
    SUGGEST = 0x0D
    HAVEALL = 0x0E
    HAVENONE = 0x0F
    REJECTREQUEST = 0x10
    ALLOWEDFAST = 0x11
    BIT_EXTENDED = 20

    def __init__(self):
        self._download_id = ''
        self.__disconnecting = False
        self.__bad_proto = False
        self.__extended_handshake = False
        self._ltep_header = {}
        self.client = False
        self._removing_torrent_underway = False

    def setClient(self):
        self.client = True

    def handshakeReceived(self, data):
        #print 'HR'
        buff = DataBuffer(data)
        self.process_classic_handshake(buff.extract(68))

    def stringReceived(self, data):
        #print 'SR'
        buff = DataBuffer(data)
        while(buff.dataleft()):
            self.process_message(buff)

    def old_dataReceived(self, data):
        buff = DataBuffer(data)
        while(buff.dataleft()):
            if not self.handshake:
                if buff.tell() < 68:
                    print '<<<BAD HS', buff.tell()
                    print buff.extract(68)
                    self.transport.loseConnection()
                else:
                    self.process_classic_handshake(buff.extract(68))
                    print "Buffer left ==", buff.tell()
            else:
                if buff.tell() < 4: #& buff.tell() != 0:
                    print "<<<INVALID MSG of size", buff.tell(), buff
                    buff.clear()
                else:
                    self.process_message(buff)
                #expecting a header with (length, type, payload format)

    def isReservedBitSet(self, pos):
        mask = 1 << pos
        ans = ( self.far_reserved_bits & mask ) >> pos
        return ans

    def process_classic_handshake(self, data):
        unpack = struct.unpack('>B19sQ20s20s', data[:68])
        self._download_id = unpack[3]
        self.binhash = unpack[3]
        self.farip = self.transport.getPeer().host
        self.farport = self.transport.getPeer().port
        self.far_reserved_bits = unpack[2] #bin(unpack[2])[::-1]
        #print unpack[1]
        ## The next 3 lines are a very crude hack to avoid rewriting this function
        ## Essentially these lines should follow after the protocol check
        if unpack[1] != self.protocol_name:
            #Its very likely that we got an encrypted connection
            #the other cases include misconfigured clients, port scan or hack attempts
            #In eighter case the following will disable further data and disconnect the socket
            #self.factory.service.ch[self._download_id].ipTried(self.farip)
            self.__bad_proto = True
            self.hangupConnection()
            print 'Invalid protocol'
        elif not self.factory.assertInfohash(str(self._download_id)):
            self.hangupConnection()
            print 'Invalid hash'
        elif self.factory.service.ch[self._download_id].isThisIncomingNew( self.farip, self.farport ):
            #print "<<<HANDSHAKE", self._download_id
            self.handshake = True
            self.factory.addClient(self._download_id, self)
            self.send_classic_handshake()
            if self.isReservedBitSet(self.BIT_EXTENDED):
                self.send_extended_handshake()
        else:
            self.hangupConnection()

    def hangupConnection(self):
        self.transport._writeDisconnected=True
        self.transport.abortConnection()

    def send_classic_handshake(self):
        pid = self.factory.getPeerID()
        msg = struct.pack('>B19sQ20s20s', len(self.protocol_name), self.protocol_name, self.reserved_bits,
                          self._download_id, str(pid))
        #print ">>>HANDSHAKE"
        self.transport.write(msg)

    def process_extended_handshake(self, data):
        #print "<<<EHANDSHAKE"
        edict = bdecode(data)
        #self._ltep_header = {}
        if edict.has_key('m'):
            for key in edict['m'].keys():
                self._ltep_header[edict['m'][key]] = key
        if self._ltep_header.has_key('ut_pex'):
            self.has_pex = True
            #TODO: ADD TIMED DISCONNECT CODE
        self.far_client = edict['v']


    def send_extended_handshake(self):
        feat_dict = self.factory.get_featDict(self._download_id)
        peer = self.transport.getPeer()
        host = self.transport.getHost()
        #feat_dict['ipv6'] = 2001:0000:5EF5:79FD:38B6:1101:AC05:3C4E
        feat_dict['ipv4'] = ip2bin('117.197.151.159') #todo: Eighter pull this from the air or enter a fixed value..
        #better idea. get yourip from extended msg and vote for self ip or get it from wimi
        feat_dict['yourip'] = ip2bin(peer.host)
        self.__extended_handshake = True

        msgtype = self.EXTENDED
        payload = '\x00' + bencode(feat_dict) # '\x00' is to indicate that is a an extended HS dict. ltep type header
        msg = self.makeMessage(msgtype, payload)
        #print ">>>EHANDSHAKE"
        self.transport.write(msg)

    def makeMessage(self, msgtype, payload):
        msglen = 1 + len(payload) # len(msgtype) + len(payload)
        msg = struct.pack(">LB", msglen, msgtype)
        msg = msg + payload
        return msg

    def extract_length_id(self, buff):
        header = buff.extract(4)
        header = struct.unpack(">L", header)
        length = header[0]
        if length == 0:
            #we just got an empty keep alive msg
            type = -1
        else:
            header = buff.extract(1)
            header = struct.unpack(">B", header)
            type = header[0]
        return length, type

    def disconnectPeerLater(self,time=300):
        time = random.randint(time,time+30)
        def callbackfunc(obj):
            obj.transport.loseConnection()
        self.factory.service.callLater(time, callbackfunc, self)

    def process_extended_message( self, msg_type , msg_id,  payload ):
        if msg_type == 'ut_pex':
            data = bdecode(payload)
            peer_data = ''
            if data.has_key('added'):
                peer_data += data['added']
            if data.has_key('dropped'):
                peer_data += data['dropped']
            _ips = []
            for x in xrange(0, len(peer_data), 6):
               key = peer_data[x:x+6]
               ip = bin2ip(key[:4])
               port = (ord(peer_data[x+4]) << 8) | ord(peer_data[x+5])
               _ips.append((ip, port,))
            print '<<<EXTENDED ut_pex GOT: %d peers' % len(_ips)
            self.factory.service.ch[self._download_id].addNewFromList( _ips,  'pex' )
            self.factory.service.ch[self._download_id].status()
            if not self.__disconnecting:
                self.__disconnecting = True
                self.disconnectPeerLater()
        elif msg_type == 'ut_metadata':
            #Tried implementing the specs but only managed to send corrupt data
            #for protocol stability sake, rejecting this request for now.
            far_header_id = msg_id
            reply = { 'msg_type': 2, 'piece': 0 }
            reply = chr( far_header_id) + bencode(reply )
            msg = self.makeMessage(self.EXTENDED, reply)
            print '>>>EXTENDED ut_metadata REJECT'
            self.transport.write(msg)
        else:
            print "<<<EXTENDED SKIPPED", msg_type

    def process_message(self, buff):
        header = buff.extract(1)
        length = buff.tell()
        m, = struct.unpack(">B", header)
        payload = buff.extract(length)
        #print 'len:',length ,'pay', len(payload)
        if m == -1:
            #print "<<<KEEPALIVE"
            pass
        elif m == self.CHOKE:
            #print "<<<CHOKE", length + 1
            pass
        elif m == self.UNCHOKE:
            #print "<<<UNCHOKE", length + 1
            pass
        elif m == self.INTERESTED:
            print "<<<INTERESTED", length + 1
        elif m == self.NOT_INTERESTED:
            print "<<<NOT_INTERESTED", length + 1
        elif m == self.HAVE:
            pass
            #print "<<<HAVE", length + 1 #causes too much output
        elif m == self.BITFIELD:
            #print "<<<BITFIELD", length + 1
            pass
        elif m == self.REQUEST:
            print "<<<REQUEST:", length + 1
        elif m == self.PIECE:
            #print "<<<PIECE", length + 1
            pass
        elif m == self.CANCEL:
            print "<<<CANCEL", length + 1
        elif m == self.PORT:
            #print "<<<PORT", length + 1
            pass
        elif m == self.SUGGEST:
            #print "<<<SUGGEST", length + 1
            pass
        elif m == self.HAVEALL:
            #print "<<<HAVEALL", length + 1
            pass
        elif m == self.HAVENONE:
            #print "<<<HAVENONE", length + 1
            pass
        elif m == self.REJECTREQUEST:
            #print "<<<REJECTREQUEST", length + 1
            pass
        elif m == self.ALLOWEDFAST:
            #print "<<<ALLOWEDFAST", length + 1
            pass
        elif m == self.EXTENDED:
            e_type = ord(payload[0])
            payload = payload[1:]
            #print "Pack", length, "PL" , len(payload)
            if e_type == 0:
                self.process_extended_handshake(payload)
            elif self.__extended_handshake and self._ltep_header.has_key(e_type):
                msg_type = self._ltep_header[e_type]
                self.process_extended_message( msg_type ,e_type,  payload )
            else:
                print self._ltep_header
                print "<<<EXTENDED UNKNOWN", e_type
        else:
            print "<<<UNKNOWN MSG", m, length + 1
            #f = open('dumps/%s-unknown.txt' % self.transport.getPeer().host, 'a+b').write(payload)

    def connectionLost(self, reason):
        self.factory.connections -= 1

        if self.handshake:
            self.factory.service.ch[self._download_id].ipTried(self.farip)
            self.factory.removeClient(self._download_id, self)
        elif self.__bad_proto:
            pass #maybe add this peer to a global ban list ? untill encryption is implemented ?
        else:
            #self.factory.service.ch[self._download_id].ipDead(self.farip)
            #the client hasnt sent a handshake yet so self._download_id is unknown
            #maybe log the number of disconnects per hour . if excessive number of
            #disconnects are detected then add the ip to a global ban list
            pass
        print 'Disconnected from', self.transport.getPeer()
        print 'Connected ', self.factory.connections, 'Since Start', self.factory.connection_since_start

    def connectionMade(self):
        if self.client: # Added for outbound connections
            self.send_classic_handshake()
        self.factory.connections += 1
        self.factory.connection_since_start += 1
        print "Connection from", self.transport.getPeer()
        print 'Connected ', self.factory.connections, 'Since Start', self.factory.connection_since_start


class ConnectionHolder(object):
    def __init__(self):
        self._waiting = {}   # all ips first fill in here (from tracker, pex, dht )
        self.current = {}    # all connections currently being tried for more available data
        self._tried = []     # connections sucked dry
        self._dead = []      # non pex clients never tried which we will never try again
        self._ban = []
        self.connections = []
        self.tracker_info = (0,0,0)

    def _isNewConnection(self,ip):
        if self._waiting.has_key(ip) or self.current.has_key(ip) or ip in self._tried:
            return False
        return True

    def fetchFromWaiting(self):
        ip = random.choice(self._waiting.keys())
        dict = self._waiting.pop(ip)
        self.current[ip] = dict
        return ip, self.current[ip]

    def addNewFromList(self,iplist,source):
        for ip, port in iplist:
            if self._isNewConnection(ip):
                self._waiting[ip] = {'port':port, 'source': source, 'extended':False}
        return source

    def ipTried(self,ip):
        if self.current.has_key(ip): del self.current[ip]
        self._tried.append(ip)

    def ipDead(self,ip):
        if self.current.has_key(ip): del self.current[ip]
        self._dead.append(ip)

    def addBan(self, ip):
        self._ban.append(ip)

    def trackerStatus(self, downloaded, complete, incomplete):
        self.tracker_info = (downloaded,complete,incomplete) #removed TrackerStatus class to tuple

    def isThisIncomingNew(self,ip, obj, port=False):
        '''
        Used to verify new incoming connections
        '''
        if ip in self._tried:
            return False
        elif ip in self._ban:
            return False
        elif self._waiting.has_key(ip):
            dict = self._waiting[ip]
            self.current[ip] = dict
            del self._waiting[ip]
            return True
        elif ip in self._dead:
            self._dead.remove(ip)
            self.current[ip] = {'source':'dead'}
            return True
        elif self.current.has_key(ip):
            return False
        else:
            self.current[ip] = {'source':'incoming', 'port' : port }
            return True

    def addConnectionObj(self, obj):
        self.connections.append(obj)

    def removeConnectionObj(self, obj):
        if obj in self.connections:
            self.connections.remove(obj)

    def dumpConnectionData(self):
        _peers = []
        waiting_ips = deepcopy(self._waiting.keys())
        current_ips = deepcopy(self.current.keys())
        tried_ips = deepcopy(self._tried)
        _peers.extend(waiting_ips)
        _peers.extend(current_ips)
        _peers.extend(tried_ips)
        ret = {'peers': _peers, 'trackerdata':deepcopy(self.tracker_info)}
        return ret
        #When done disconnect all peers, move them to tried_ips, refresh tried every 24 hrs

    def purgeIPs(self):
        self._tried = []
        self._ban = []
        self._dead = []
    
    def status(self):
        total = len(self._waiting) + len(self.current) + len(self._tried)
        print "Peers: ", total
        

class DataBuffer():
    def __init__(self, buff):
        self.buff = buff

    def extract(self, len):
        ret = self.buff[:len]
        self.buff = self.buff[len:]
        return ret

    def __repr__(self):
        return self.buff

    def dump(self):
        return self.buff

    def clear(self):
        self.buff = ''
        
    def add(self, string):
        self.buff = self.buff + string

    def read(self,index):
        return self.buff[:index]

    def dataleft(self):
        if len(self.buff):
            return True
        return False

    def tell(self):
        return len(self.buff)



class IMicroTorrentFactory(Interface):
    def getUser(user):
        """
          Return a deferred returning a string.
          """

    def assertInfohash(hash):
        '''
          Asserts the infohash to ones being served.
        '''

    def buildProtocol(addr):
        """
          Return a protocol returning a string.
          """

    def getPeerID(self):
        '''
          Returns the peerid of the torrentclient
        '''

    def get_featDict(self):
        '''
          Returns featdict
        '''


class MicroTorrentFactoryFromService(protocol.ServerFactory):
    implements(IMicroTorrentFactory)

    protocol = MicroTorrentProtocol

    def __init__(self, service):
        self.service = service
        #self.ch = {} # ConnectionHolder()
        self.connections = 0
        self.connection_since_start = 0
        self.clients = []

    def getUser(self, user):
        return self.service.getUser(user)

    def getPeerID(self):
        return self.service.PEER_ID

    def assertInfohash(self, hash):
        if self.service.torrents.has_key(hash):
            return True
        else: return False

    def addClient(self, infohash, object):
        self.service.addClient(infohash, object)

    def removeClient(self, infohash, object):
        self.service.removeClient(infohash, object)

    def get_featDict(self, download_id):
        feat = self.service.getFeat( download_id )
        feat_dict = {
            'e': 0,
            'complete_ago': 0,
            'm': {
                'upload_only': 3,
                'ut_holepunch': 4,
                'ut_metadata': 2,
                'ut_pex': 1,
                },
            'metadata_size': feat['metadata_size'],
            'p': feat['port'],
            'reqq': 255,
            'v': feat['version']

        }
        return feat_dict

components.registerAdapter(MicroTorrentFactoryFromService,
                           IMicroTorrentService,
                           IMicroTorrentFactory)

class MicroTorrentService(service.Service):
    implements(IMicroTorrentService)
    PEER_ID = '-UT3000-ig\x83\xe1\x94\x8c\x93v6\xbaw\xaf'
    version = '\xc2\xb5Torrent 3.0'
    hashlist = []
    torrents = {}
    def __init__(self, torrentids, port=7828):#pass rpc address here
        self.torrentids = torrentids
        self.port = port
        self.RPCaddress = '127.0.0.1:8000'
        self.ch = {} # ConnectionHolder()'s
        self.lc = task.LoopingCall(self.upload_peerlist)
        self.lc.start(PEERLIST_UPLOAD_DELAY, now=False)
        #self.s = Session()
        #todo: write upload_peerlist_threaded function. make db tables schema.
        #todo: Outbound connections. Add pex dead support

    def _read_torrent(self):
        tor = self.s.query(Torrent).get(self.torrentid)
        f = tor.data
        self.torrent = bdecode(f)
        self.metadata_size = len(bencode(self.torrent['info']))
        infodict = self.torrent["info"]
        infohash = hashlib.sha1(bencode(infodict)).digest()
        self.hashlist.append(infohash)
        self.refreshTracker(self.torrent)

    def loadTorrentFromDB(self, torrentid):
        tor = self.s.query(Torrent).get(torrentid)
        torrdata = tor.data
        torrent = bdecode(torrdata)
        self.addTorrent(torrent)

    def addTorrent(self, torrent):
        metadata_size = len(bencode(torrent['info']))
        infodict = torrent["info"]
        infohash = hashlib.sha1(bencode(infodict)).digest()
        self.ch[infohash] = ConnectionHolder()
        self.torrents[infohash] = {'torrent': torrent, 'metadata_size': metadata_size }
        self.refreshTracker(infohash)

    def removeTorrent(self,infohash):
        if self.hasTorrent(infohash):
            #disconnect all clients
            #remove torrent from list
            #set a flag and remove all torrent resources in the next upload_peerlist
            self.torrents[infohash]['refresh_handler'].cancel()
            del self.torrents[infohash]
            for client in self.ch[infohash].connections:
                client._removing_torrent_underway = True
                client.hangupConnection()
            #Torrent will actually in removeClient function
            #when all the clients for that have disconnected
            self.__removeTorrent(infohash) #Just in case no clients have connected yet
        else:
            raise Exception('No such torrent loaded')

    def __removeTorrent(self, infohash):
        if not len(self.ch[infohash].connections):
            del self.ch[infohash]
            print "TORRENT REMOVED"

    def hasTorrent(self, hash):
        if self.torrents.has_key(hash): return True
        else: return False

    def addClient(self, infohash, obj):
        self.ch[infohash].addConnectionObj(obj)

    def removeClient(self, infohash, obj):
        self.ch[infohash].removeConnectionObj(obj)
        if obj._removing_torrent_underway:
            self.__removeTorrent(infohash)

        
    def getHashList(self):
        return self.torrents.keys()

    def refreshTracker(self, infohash):
        print 'scraping tracker'
        torrent = self.torrents[infohash]['torrent']
        ts = TrackerScrape(torrent, self.port, self.PEER_ID, )
        d = ts.scrapeAllTrackers(peer_count=200)
        d.addCallback(self.process_tracker_scrape)
        refresh_handler = self.callLater(20 * 60, self.refreshTracker, infohash)
        self.torrents[infohash]['refresh_handler'] = refresh_handler

    def upload_peerlist(self):
        def upload_peerlist_threaded(combined_ch): #in dict format
            """
            This is deferred to a thread. Go nuts.
            """
            print 'Started remote data upload'
            postdata = pickle.dumps(combined_ch)
            #zip the data if possible.
            postdata = base64.b64encode(postdata)
            reply = urllib2.urlopen("http://%s/egg-rpc/dump-receiver/" % self.RPCaddress, urllib.urlencode({'datadump':postdata}))
            r_data = reply.read()
            #TODO: Log this
            print 'Upload data complete'
        combined_ch = {}
        for hash in self.getHashList():
            combined_ch[hash] = self.ch[hash].dumpConnectionData()
            self.ch[hash].purgeIPs()
        d = deferToThread(upload_peerlist_threaded, combined_ch)
        d.addCallback(self.upload_peerlist_callback)

    def upload_peerlist_callback(self, result):
        print 'upload peerlist callback.' #use this to modify, purge dead peers etc


    def process_tracker_scrape(self, result):
        print 'processing tracker scrape'
        if self.ch.has_key(result['infohash']): #To avoid an edge case during torrent remove
            self.ch[result['infohash']].addNewFromList(result['peers'], 'tracker')
            self.ch[result['infohash']].trackerStatus(result['downloaded'], result['complete'], result['incomplete'] )
            self.ch[result['infohash']].status()

    def callLater(self,time, func, *args, **kw):
        return reactor.callLater(time, func, *args, **kw)

    #self.call = reactor.callLater(30, self._read)

    def getUser(self, user):
        return defer.succeed(self.users.get(user, "No such user"))

    def getUsers(self):
        return defer.succeed(self.users.keys())

    def getFeat(self, infohash):
        return {'metadata_size': self.torrents[infohash]['metadata_size'], 'port': self.port, 'version': self.version}

    def startService(self):
        if type(self.torrentids) == type([]) or type(self.torrentids) == type( (), ) : #simulate a tuple type
            for id in self.torrentids:
                self.loadTorrentFromDB(id)
        elif type(self.torrentids) == type(2): #simulate an int type
            self.loadTorrentFromDB(self.torrentids)
        service.Service.startService(self)

    def stopService(self):
        service.Service.stopService(self)

    #self.call.cancel()

class ITorrentLoaderFactory(Interface):
    pass


class ITorrentLoaderService(Interface):
    pass

class TorrentLoaderProtocol(IntNStringReceiver):
    implements(ITorrentLoaderFactory)
    structFormat = '>L'
    prefixLength = struct.calcsize(structFormat)
    proto_name = 'TorrentLoader'

    HANDSHAKE       = chr(0xB)
    LOADTORRENT     = chr(1)
    UNLOADTORRENT   = chr(2)
    ISLOADED        = chr(3)
    
    SETRPCSERVER    = chr(4)
    TRUNCATEIPS     = chr(5)

    PULLNOTIFY      = chr(6)

    def __init__(self):
        self.__handshake = False

    def hangupConnection(self):
        self.transport._writeDisconnected=True
        self.transport.abortConnection()

    #self.lineReceived = self.stringReceived
    def sendString(self,msg):
        msg_length = len(msg) + 1
        packet = struct.pack('>L',msg_length) + msg
        self.transport.write(packet)
        
    def stringReceived(self, msg):
        print 'Got packet'
        if self.__handshake:
            buf = DataBuffer(msg)
            self.process_message(buf)
            #torrent = bdecode(msg)
            #hash = hashlib.sha1(bencode(torrent["info"])).digest()
            #if self.infohash != hash:
            #    self.sendString('409 Conflict')
            #   self.transport.looseConnection()
            #
            #else:
            #    id = self.factory.loadTorrent(torrent, make_active = True)
        else:
            #(len(protoname) 13, protoname, infohash
            protolen, protoname, tempkey = struct.unpack('>B13s20s', msg[:68])
            if protolen != 13 or protoname != self.proto_name:
                self.hangupConnection()
            #if protoname != self.proto_name:
            #    self.hangupConnection()
            #if self.factory.isTorrentLoaded(infohash):
            #    self.sendString('409 Conflict')
            #    self.transport.looseConnection()
            else:
                #self.infohash = infohash
                self.__handshake = True
                print 'Handshake done'


    def process_message(self, buf):
        msg_code = buf.extract(1)
        print 'processing message', ord(msg_code)
        if msg_code == self.LOADTORRENT:
            torrent = buf.dump()
            print 'torrent length', len(torrent)
            reply = self.loadTorrent(torrent)
            self.sendString(reply)

        elif msg_code == self.UNLOADTORRENT:
            infohash = buf.dump()
            reply = self.unloadTorrent(infohash)
            self.sendString(reply)

        elif msg_code == self.ISLOADED:
            infohash = buf.dump()
            try:
                reply = self.isTorrentLoaded(infohash)
                if reply:
                    self.sendString("302 Found")
                else:
                    self.sendString("404 Not Found")
            except:
                self.transport.loseConnection()
        elif msg_code == self.PULLNOTIFY:
            self.factory.notificationPuller()
            self.sendString('200 OK')

    def loadTorrent(self, torrent):
        try:
            torrent = bdecode(torrent)
            infohash = hashlib.sha1(bencode(torrent['info'])).digest()
            #explicitly taking infohash directly from the torrent
            reply = self.factory.loadTorrent(infohash,torrent)
            return reply
        except:
            raise
            self.transport.loseConnection()
            print "error while loading torrent"

    def unloadTorrent(self, infohash): #in non hex format
        try:
            reply = self.factory.removeTorrent(infohash)
            return self.sendString(reply)
        except:
            raise
            self.transport.loseConnection()
            print 'Error while unloading torrent'

    def isTorrentLoaded(self, infohash): #has to be non-hex infohash
        if self.factory.isTorrentLoaded(infohash):
            return True
        else: return False

    def connectionMade(self):
        print "control connection from", self.transport.getPeer()

    def connectionLost(self, reason):
        print 'Control diconnected from', self.transport.getPeer()


class TorrentLoaderFactoryFromService(protocol.ServerFactory):
    implements(ITorrentLoaderFactory)
    protocol = TorrentLoaderProtocol

    WEBSERVICE_URL = "http://127.0.0.1:8000/egg-rpc/sync/"

    def __init__(self, service):
        self.service = service

    def startFactory(self):
        print 'Factory Started'
        self.callLater(6, self.notificationPuller )

    def notificationPuller(self):
        def handle_page(data):
            self.process_sync_data( data)
        print 'Notification puller on'
        d = getPage(self.WEBSERVICE_URL + "?method=askingsyncdata")
        d.addCallback(handle_page)
        d.debug = True

    def process_sync_data( self, encoded_data, *args, **kwargs):
        #b64decode
        print "Process sync data"
        encrypted_data = base64.b64decode(encoded_data)
        #unencrypt
        key = 'makeittopin'
        #pickled_data = triple_des(key).encrypt(encrypted_data, padmode=2)
        #unpickle
        sync_data = pickle.loads(pickled_data)
        #process
        for infohash,active,torrent_data in sync_data:
            if self.isTorrentLoaded(infohash) and not active:
                reply = self.removeTorrent(infohash)
            elif not self.isTorrentLoaded(infohash) and active:
                print "in add torrent"#, self.loadTorrent(torrent_data)
                reply = self.loadTorrent(torrent_data)
            elif self.isTorrentLoaded(infohash) and active:
                pass #ok to ignore
            elif not self.isTorrentLoaded(infohash) and not active:
                pass #ok to ignore

    def get_torrent_list(self, infohash):
        hash_list = self.service.torrent_server.getHashList()
        if infohash in hash_list:
            return True
        else: return False

    #def hasTorrent(self, infohash):
    #    return self.service.torrent_server.hasTorrent(infohash)

    def isTorrentLoaded(self, infohash):
        return self.service.torrent_server.hasTorrent(infohash)

    def loadTorrent(self, torrdata):
        #if self.isTorrentLoaded(infohash):
        #    return False
        print "in LoadTorrent"
        torrent = bdecode(torrdata)
        self.service.torrent_server.addTorrent(torrent)
        #return True

    def removeTorrent(self,infohash):
        self.service.torrent_server.removeTorrent(infohash)
        return True

    def clientConnectionLost(self, client):
        print 'Disconnected from', client.transport.getPeer()

    def clientConnectionMade(self, client):
        print 'Connected to', client.transport.getPeer()

    def callLater(self,time, func, *args, **kw):
        return reactor.callLater(time, func, *args, **kw)

components.registerAdapter( TorrentLoaderFactoryFromService,
                           ITorrentLoaderService,
                           ITorrentLoaderFactory)


class TorrentLoaderService(service.Service):
    implements(ITorrentLoaderService)
    def __init__(self, torrent_server):
        self.torrent_server = torrent_server


PORT = 37814
application = service.Application('Mtorrent', uid=1, gid=1)

#Loading the main mtorrent service
f = MicroTorrentService([], PORT)
serviceCollection = service.IServiceCollection(application)
f.setServiceParent(serviceCollection)

#loading the mtorrent control service
f2 = TorrentLoaderService(f)
f2.setName(serviceCollection)



#Start listening
internet.TCPServer(PORT, IMicroTorrentFactory(f)).setServiceParent(serviceCollection)
internet.TCPServer(7877, ITorrentLoaderFactory(f2)).setServiceParent(serviceCollection)


