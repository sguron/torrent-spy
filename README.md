Torrent Spy
===========

This is the **data collection** portion of the bigger torrent analytics project that I worked on for a client.

The idea is to target certain torrents and gather information such as ip's and geographical location of downloader's, most used torrent client, peerid's of downloader's, etc. The analytics part of the project also tries to determine what other torrents were downloaded by the client in the recent past and tries to guess the users demographics.


Requirements
============
* Python 2.7
* Twisted
* SQLAlchemy


Parts
=====
1. spyCore.py runs and monitors the spying aspect of the project. Also responsible for storing collected data in the database
2. bt.py implements the bittorrent protocol in class MicroTorrentProtocol
3. scrape.py implements scraping trackers using the more efficient UPD scrape protocol


License
=======
Copyright(c) 2017 Sukhneer Guron
Source released under GPL v3 - see LICENSE file for more info
