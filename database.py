from sqlalchemy import create_engine, Column, ForeignKey, Integer, String, Unicode, Text, PickleType, BigInteger, \
    DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, backref
from datetime import datetime

__author__ = 'TheArchitect'

Base = declarative_base()


class TrackerRecord(Base):
    __tablename__ = 'trackerscrape'
    __table_args__ = {'mysql_engine': 'InnoDB', 'useexisting': True}
    id = Column(Integer, primary_key=True, autoincrement=True)
    infohash = Column(Unicode(2048), nullable=False)
    complete = Column(Integer, nullable=False)
    incomplete = Column(Integer, nullable=False)
    downloads = Column(Integer, nullable=False)
    scrapetime = Column(DateTime, nullable=False)
    tracker = Column(Unicode(2048), nullable=False)
    scrape = Column(PickleType(), nullable=False)  # json dump of scraped links

    def __repr__(self):
        if self.id != None:
            return "<TrackerRecord('%d','%s','%d')>" % (self.id, self.infohash, self.downloads)
        else:
            return "<TrackerRecord(None,'%s', '%d')>" % (self.infohash, self.downloads)

    def __init__(self, infohash, complete, incomplete, downloads, tracker, scrape):
        self.infohash = infohash
        self.complete = complete
        self.incomplete = incomplete
        self.downloads = downloads
        self.tracker = tracker
        self.scrapetime = datetime.now()
        self.scrape = scrape


class Torrent(Base):
    __tablename__ = 'torrentfile'
    __table_args__ = {'mysql_engine': 'InnoDB', 'useexisting': True}
    id = Column(Integer, primary_key=True, autoincrement=True)
    infohash = Column(Unicode(2048), nullable=False)
    name = Column(Unicode(2048), nullable=False)
    active = Column(Boolean(), nullable=False)
    dateuploaded = Column(DateTime)
    data = Column(PickleType(), nullable=False)

    def __repr__(self):
        if self.id != None:
            return "<Torrent('%d','%s','%s')>" % (self.id, self.infohash, self.name)
        else:
            return "<Torrent(None,'%s', '%s')>" % (self.infohash, self.name)

    def __init__(self, infohash, name, data, active=True):
        self.infohash = infohash
        self.name = name
        self.active = active
        self.data = data
        self.dateuploaded = datetime.now()


engine = create_engine("sqlite:///C:/work/projects/torrentspy/database.db")  # , echo=True)
Session = sessionmaker(bind=engine)
# session = Session()
# Base.metadata.create_all(engine)
