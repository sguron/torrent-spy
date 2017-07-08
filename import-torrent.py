import socket
import struct
import hashlib
from bencode import bdecode, bencode

__author__ = 'TheArchitect'

key = 'a' * 20
protoname = 'TorrentLoader'

def send( s, payload):
	msglen = len(payload) # len(msgtype) + len(payload)
	msg = struct.pack(">L", msglen)
	msg = msg + payload
	s.send(msg)

hs_pack = struct.pack('>B13s20s', len(protoname), protoname, key )

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 7877) )
send(s, hs_pack)

path = raw_input("Path to torrent: ")
torrent = open(path,'rb').read()
dtorrent = bdecode(torrent)
infodict = dtorrent["info"]
infohash = hashlib.sha1(bencode(infodict)).digest()


#Add
#send(s, chr(1) + torrent)

#remove
send(s, chr(2) + infohash)
