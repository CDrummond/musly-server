#
# Analyse files with Musly, and provide an API to retrieve simialr tracks
#
# Copyright (c) 2020 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#

import json
import logging
import os
import sqlite3
from . import tags

DB_FILE = 'musly.db'
GENRE_SEPARATOR = ';'
_LOGGER = logging.getLogger(__name__)

class MetadataDb(object):
    def __init__(self, config):
        path = os.path.join(config['paths']['db'], DB_FILE)
        self.conn = sqlite3.connect(path)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file varchar UNIQUE NOT NULL,
                    artist varchar,
                    album varchar,
                    albumartist varchar,
                    genre varchar,
                    duration integer,
                    ignore integer,
                    vals blob NOT NULL)''')
        self.cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS tracks_idx ON tracks(file)')


    def commit(self):
        self.conn.commit()


    def close(self):
        self.cursor.close()
        self.conn.close()


    def get_metadata(self, i):
        try:
            self.cursor.execute('SELECT artist, album, albumartist, genre, duration, ignore FROM tracks WHERE id=?', (i,))
            row = self.cursor.fetchone()
            meta = {'artist':row[0], 'album':row[1], 'albumartist':row[2], 'duration':row[4]}
            if row[3] and len(row[3])>0:
                meta['genres']=row[3].split(GENRE_SEPARATOR)
            if row[3] is not None and row[3]==1:
                meta['ignore']=True
            return meta
        except Exception as e:
            _LOGGER.error('Failed to read metadata - %s' % str(e))
            pass
        return None


    def set_metadata(self, track):
        meta = tags.read_tags(track['abs'], GENRE_SEPARATOR)
        if meta is not None:
            if not 'albumartist' in meta or meta['albumartist'] is None:
                if not 'genres' in meta or meta['genres'] is None:
                    self.cursor.execute('UPDATE tracks SET artist=?, album=?, duration=? WHERE file=?', (meta['artist'], meta['album'], meta['duration'], track['db']))
                else:
                    self.cursor.execute('UPDATE tracks SET artist=?, album=?, genre=?, duration=? WHERE file=?', (meta['artist'], meta['album'], GENRE_SEPARATOR.join(meta['genres']), meta['duration'], track['db']))
            else:
                if not 'genres' in meta or meta['genres'] is None:
                    self.cursor.execute('UPDATE tracks SET artist=?, album=?, albumartist=?, duration=? WHERE file=?', (meta['artist'], meta['album'], meta['albumartist'], meta['duration'], track['db']))
                else:
                    self.cursor.execute('UPDATE tracks SET artist=?, album=?, albumartist=?, genre=?, duration=? WHERE file=?', (meta['artist'], meta['album'], meta['albumartist'], GENRE_SEPARATOR.join(meta['genres']), meta['duration'], track['db']))


    def file_already_analysed(self, path):
        self.cursor.execute('SELECT vals FROM tracks WHERE file=?', (path,))
        return self.cursor.fetchone() is not None


    def get_cursor(self):
        return self.cursor
