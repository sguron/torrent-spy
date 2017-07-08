import os, sys
import workerpool
import threading
from bencode import bencode, bdecode
import hashlib
import IPtools
import time
import socket
import struct
import MySQLdb


# you would never see as many threads running in another application then this one!
__author__ = 'TheArchitect'

dir = sys.path[0]
torrentdir = dir + "/input/"
torrentstore = dir + "/output/"
watchersleeptime = 300  # 5 mins
databasesettings = ('localhost', 'username', 'password', 'database',)


# InfohashTrackerPool = workerpool.WorkerPool(size=5)
# InfohashPeerPool = workerpool.WorkerPool(size=5)


class DirWatcher(threading.Thread):
    '''
     Dirwatcher sits in the darkness doing its one job. it monitors a directory and any
     new file that is added gets pickedup, processed, added as a new job and wiped off from the directory
    '''
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        while 1:
            files = os.listdir()
            if len(files) > 0:
                # check if any of em are torrents
                for f in files:
                    splits = f.split(".")
                    if splits[-1:][0] == "torrent":
                        f2 = open(torrentdir + f, 'rb')
                        torrentdata = f2.read()
                        f2.close()
                        del f2
                        torrent = bdecode(torrentdata)
                        info = bencode(torrent['info'])
                        infohash = hashlib.sha1(info).hexdigest()
                        nonhinfohash = hashlib.sha1(info).digest()
                        # we just store the non-hex infohash for tracker purposes
                        torrent['infohash'] = noninfohash
                        del info
                        #  Do "Castration" of the torrent file to make it useless for downloading purposes and save some
                        #  space too :)
                        del torrent['info']['pieces']

                        f3 = open(torrentstore + infohash, 'wb')
                        f3.write(bencode(torrent))
                        f3.close()
                        # Delete the original torrent from the input dir
                        try:
                            os.remove(torrentdir + f)

                            # Start the tracking job !!!!!!
                            job = TorrentSpyJob(infohash)
                            InfohashTrackerPool.put(job)
                        except:
                            raise  ## TODO Logging
                    else:
                        try:
                            os.remove(torrentdir + f)
                        except:
                            raise  ## TODO Logging
            # no new files sleep for some time
            time.sleep(watchersleeptime)


class TorrentSpyJob(workerpool.Job):
    def __init__(self, infohash):
        self.infohash = infohash
        self.nonhexinfohash = ''  # we need infohash in non-hexdigest format which we read from the torrent file
        self.connection = urllib2.build_opener()
        self.connection.addheaders = [('User-Agent', 'uTorrent/1820')]
        self.peerid = u'-UT1820-\x78c\xde\x23\x12\x34\x56\x78\x12\x34\x56\x78'
        self.numpeers = 500
        self.port = 6881

    def queryTracker(self, url):
        if url[0:4].upper() == "HTTP":
            get = '?info_hash=' + urllib.quote(self.nonhexinfohash) + '&peer_id=' + urllib.quote(
                self.peerid) + '&port=' + urllib.quote(
                self.port) + '&uploaded=0&downloaded=0&left=0&corrupt=0&numwant=' + urllib.quote(
                self.numpeers) + '&compact=1&no_peer_id=1'
            reply = self.connection.open(url + get)
            data = reply.read()
            return data
        elif urlurl[0:3].upper() == "UDP":
            connection_id = 0x41727101980

    def getTrackers(self, infohash):
        f = open(torrentstore + infohash, 'rb').read()
        torrent = bdecode(f)
        self.nonhexinfohash = torrent['infohash']
        trackers = []
        if torrent.has_key('announce'):
            trackers.append(torrent['announce'])
        if torrent.has_key('announce-list'):
            for tracker in torrent['announce-list']:
                trackers.append(tracker[0])
        ## TODO if len of tracker list = 0  then log this
        return trackers

    def run(self):
        """
        Ask the tracker for some peers :)
        """
        trackers = self.getTrackers(self.infohash)
        peerlist = []
        for tracker in trackers:
            try:
                announcedata = self.queryTracker(tracker)
            except:
                raise  ## TODO Add this to the log
            ## TODO Add the torrent to the
            try:
                peers = self.process_tracker(announcedata)
            except:
                raise  ## TODO add this to logging
            for peer in peers:
                peerlist.append(peer)
        spytime = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + (30 * 60)))
        uniquepeers = self.removeDuplicate(peerlist)  # the output is in form of [(ip,port),(),(),(),]
        job = InfohashPeerJob(infohash, spytime, uniquepeers)
        InfohashPeerPool.put(job)

    def removeDuplicate(self, peerlist):
        temp = []
        out = []
        for pair in peerlist:
            if pair[0] not in temp:
                temp.append(pair[0])
                out.append(pair)
        del temp
        return out

    def process_tracker(data):
        assert type(data) is str, "'%s' should be a string" % data
        re_announce_timeout = 30
        _peers = {}
        ret = []
        try:
            responce = decode(data)
        except ValueError:
            raise
        else:
            if "failure reason" in responce:
                print "Failure"
                raise Exception
            if "peers" in responce:
                previous_peer_count = len(_peers)
                peer_data = responce.get("peers")
                if type(peer_data) is str:
                    # using low-bandwidth binary format
                    for x in xrange(0, len(peer_data), 6):
                        key = peer_data[x:x + 6]
                        if not _peers.has_key(key):
                            ip = ".".join([str(ord(i)) for i in peer_data[x:x + 4]])
                            port = (ord(peer_data[x + 4]) << 8) | ord(peer_data[x + 5])
                            _peers[key] = (ip, port)
                elif type(peer_data) is list:
                    # using bencoded format
                    for dic in peer_data:
                        if type(dic) is dict:
                            peer_id = dic.get("peer id")
                            ip = dic.get("ip")
                            port = dic.get("port")
                            if type(peer_id) is str and type(ip) is str and type(port) in (int, long):
                                if (ip, port) not in _peers:
                                    _peers[(ip, port)] = (ip, port)
                if previous_peer_count == 0 and len(_peers) > 0:
                    # Placeholder.connection_handler.create_connections()
                    pass
            else:
                print "Announce failure: The tracker did not provide us with a list of peers"
            if "interval" in responce and responce["interval"] in (int, long):
                # re_announce_timeout = max(re_announce_timeout, responce["interval"])
                pass
            if "min interval" in responce and responce["min interval"] in (int, long):
                re_announce_timeout = max(re_announce_timeout, responce["min interval"])
            for key, value in _peers:
                numericip = IPtools.ip2num(value[0])  ## TODO add this library and function
                ret.append((numericip, value[1],))  # value[1] has the port of the peer. We log this as well :)
            return ret


class InfohashPeerJob(workerpool.Job):
    """
    It connects to a data base does a transaction and then waits for more data.
    since myISAM doesnt support TX we would use LOCK TABLES tablename WRITE; statement

    To further increase performance we modify our INSERT's to include ON DUPLICATE KEY UPDATE lastseen=VALUES(lastseen)
    """

    def __init__(self, infohash, spytime, peers):
        self.infohash = infohash
        self.spytime = spytime
        self.peers = peers
        self.connection = MySQLdb.connect(host=databasesettings[0],
                                          user=databasesettings[1],
                                          passwd=databasesettings[2],
                                          db=databasesettings[3])
        self.cur = self.connection.cursor()

    def run(self):
        query = "LOCK TABLES peers WRITE;"
        insertquery = 'INSERT INTO peers(ip,port,infohash,firstseen) VALUES(%d,%d,"%s","%s") ON duplicate KEY UPDATE lastseen="%s";'
        for peer in peers:
            query += insertquery % (peer[0], peer[1], self.infohash, self.spytime)
        query += "UNLOCK TABLES;"
        self.cur.execute(query)
        print query
        ## TODO Log this event as well
        query2 = "INSERT INTO timeout(infohash,) values(" + self.infohash + "); "
        self.cur.execute(query2)
        self.cur.close()
        self.connection.commit()
        self.connection.close()

    ## TODO next iteration should remove any dead torrents from the database


##
class PersistentWatcher(threading.Thread):
    def __init__(self, database, sleeptime, timeout):
        threading.Thread.__init__(self)
        self.connection = MySQLdb.connect(host=database[0],
                                          user=database[1],
                                          passwd=database[2],
                                          db=database[3])
        self.cur = self.connection.cursor()
        self.sleeptime = sleeptime
        self.timeout = timeout

    def run(self):
        query = 'SELECT * FROM timeout WHERE refreshon <= "%s"' % time.strftime("%Y-%m-%d %H:%M:%S",
                                                                                time.gmtime(time.time()))
        while 1:
            reply = self.cur.execute(query, self.timeout)
            infohashlist = process(reply)

            #
            #  Read all tracked torrents from the database and start a TorrentTSpyJob of each in a new thread.
            #
            for infohash in infohashlist:
                job = TorrentSpyJob(infohash)
                InfohashTrackerPool.put(job)

            # Sleep and query db again
            time.sleep(self.sleeptime)

    def __del__(self):
        self.cur.close()


def spyCore():
    # This is the core of torrentspy
    print "Running"
    dw = DirWatcher()
    dw.start()
    tw = PersistentWatcher(databasesettings, 8, 8)
    tw.start()


if __name__ == '__main__':
    spyCore()