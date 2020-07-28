#
# Analyse files with Musly, and provide an API to retrieve similar tracks
#
# Copyright (c) 2020 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#

import json
import logging
import os
import sqlite3
from . import cue, tags

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


    def remove_old_tracks(self, source_path):
        non_existant_files = []
        _LOGGER.debug('Looking for old tracks to remove')
        try:
            self.cursor.execute('SELECT file FROM tracks')
            rows = self.cursor.fetchall()
            for row in rows:
                if not os.path.exists(os.path.join(source_path, cue.convert_to_source(row[0]))):
                    _LOGGER.debug("'%s' no longer exists" % row[0])
                    non_existant_files.append(row[0])

            _LOGGER.debug('Num old tracks: %d' % len(non_existant_files))
            if len(non_existant_files)>0:
                # Remove entries...
                for path in non_existant_files:
                    self.cursor.execute('DELETE from tracks where file=?', (path, ))

                # Calculate new ID values...
                self.cursor.execute('SELECT id, file FROM tracks')
                rows = self.cursor.fetchall()
                row_id = 1
                for row in rows:
                    if row[0]!=row_id:
                        self.cursor.execute('UPDATE tracks SET id=? WHERE file=?', (row_id, row[1]))
                    row_id +=1
                return True
        except Exception as e:
            _LOGGER.error('Failed to remove old tracks - %s' % str(e))
            pass
        return False


    def file_already_analysed(self, path):
        self.cursor.execute('SELECT vals FROM tracks WHERE file=?', (path,))
        return self.cursor.fetchone() is not None


    def get_cursor(self):
        return self.cursor
