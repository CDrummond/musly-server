#
# Analyse files with Musly, and provide an API to retrieve similar tracks
#
# Copyright (c) 2020-2021 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#

import json
import logging
import os
import random
import sqlite3
from . import cue, tags

DB_FILE = 'musly.db'
GENRE_SEPARATOR = ';'
_LOGGER = logging.getLogger(__name__)

album_rem = ['anniversary edition', 'deluxe edition', 'expanded edition', 'extended edition', 'special edition', 'deluxe', 'deluxe version', 'extended deluxe', 'super deluxe', 're-issue', 'remastered', 'mixed', 'remixed and remastered']
artist_rem = ['feat', 'ft', 'featuring']
title_rem = ['demo', 'demo version', 'radio edit', 'remastered', 'session version', 'live', 'live acoustic', 'acoustic', 'industrial remix', 'alternative version', 'alternate version', 'original mix', 'bonus track', 're-recording', 'alternate']


def normalize_str(s):
    if not s:
        return s
    s=s.replace('.', '').replace('(', '').replace(')', '').replace('[', '').replace(']', '').replace(' & ', ' and ')
    while '  ' in s:
        s=s.replace('  ', ' ')
    return s


def normalize_album(album):
    if not album:
        return album
    s = album.lower()
    global album_rem
    for r in album_rem:
        s=s.replace(' (%s)' % r, '').replace(' [%s]' % r, '')
    return normalize_str(s)


def normalize_artist(artist):
    if not artist:
        return artist
    ar = normalize_str(artist.lower())
    global artist_rem
    for r in artist_rem:
        pos = ar.find(' %s ' % r)
        if pos>2:
            return ar[:pos]
    return ar
        

def normalize_title(title):
    if not title:
        return title
    s = title.lower()
    global title_rem
    for r in title_rem:
        s=s.replace(' (%s)' % r, '').replace(' [%s]' % r, '')
    return normalize_str(s)


def set_normalize_options(opts):
    if 'album' in opts and isinstance(opts['album'], list):
        global album_rem
        album_rem = [e.lower() for e in opts['album']]
    if 'artist' in opts and isinstance(opts['artist'], list):
        global artist_rem
        artist_rem = [e.lower() for e in opts['album']]
    if 'title' in opts and isinstance(opts['title'], list):
        global title_rem
        title_rem = [e.lower() for e in opts['title']]


class MetadataDb(object):
    def __init__(self, config):
        path = os.path.join(config['paths']['db'], DB_FILE)
        self.conn = sqlite3.connect(path)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS tracks (
                    file varchar UNIQUE NOT NULL,
                    title varchar,
                    artist varchar,
                    album varchar,
                    albumartist varchar,
                    genre varchar,
                    duration integer,
                    ignore integer,
                    vals blob NOT NULL)''')
        self.cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS tracks_idx ON tracks(file)')
        # Add 'title' column - will fail if already exists (which it should, but older instances might not have it)
        try:
            self.cursor.execute('ALTER TABLE tracks ADD COLUMN title varchar default null')
        except:
            pass

        # Create temp table for rowid re-calc
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS tracks_tmp (
                    file varchar UNIQUE NOT NULL,
                    title varchar,
                    artist varchar,
                    album varchar,
                    albumartist varchar,
                    genre varchar,
                    duration integer,
                    ignore integer,
                    vals blob)''')


    def commit(self):
        self.conn.commit()


    def close(self):
        self.cursor.close()
        self.conn.close()


    def get_metadata(self, i):
        try:
            self.cursor.execute('SELECT title, artist, album, albumartist, genre, duration, ignore FROM tracks WHERE rowid=?', (i,))
            row = self.cursor.fetchone()
            meta = {'title':normalize_title(row[0]), 'artist':normalize_artist(row[1]), 'album':normalize_album(row[2]), 'albumartist':normalize_artist(row[3]), 'duration':row[5]}
            if row[4] and len(row[4])>0:
                meta['genres']=row[4].split(GENRE_SEPARATOR)
            meta['ignore']=row[6] is not None and row[6]==1
            return meta
        except Exception as e:
            _LOGGER.error('Failed to read metadata for %d - %s' % (i, str(e)))
            pass
        return None


    def set_metadata(self, track):
        meta = tags.read_tags(track['abs'], GENRE_SEPARATOR)
        if meta is not None:
            if 'track' in track and 'title' in track['track']: # Tracks from CUE files
                meta['title'] = track['track']['title']
            if not 'albumartist' in meta or meta['albumartist'] is None:
                if not 'genres' in meta or meta['genres'] is None:
                    self.cursor.execute('UPDATE tracks SET title=?, artist=?, album=?, duration=? WHERE file=?', (meta['title'], meta['artist'], meta['album'], meta['duration'], track['db']))
                else:
                    self.cursor.execute('UPDATE tracks SET title=?, artist=?, album=?, genre=?, duration=? WHERE file=?', (meta['title'], meta['artist'], meta['album'], GENRE_SEPARATOR.join(meta['genres']), meta['duration'], track['db']))
            else:
                if not 'genres' in meta or meta['genres'] is None:
                    self.cursor.execute('UPDATE tracks SET title=?, artist=?, album=?, albumartist=?, duration=? WHERE file=?', (meta['title'], meta['artist'], meta['album'], meta['albumartist'], meta['duration'], track['db']))
                else:
                    self.cursor.execute('UPDATE tracks SET title=?, artist=?, album=?, albumartist=?, genre=?, duration=? WHERE file=?', (meta['title'], meta['artist'], meta['album'], meta['albumartist'], GENRE_SEPARATOR.join(meta['genres']), meta['duration'], track['db']))


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
                self.force_rowid_update()
                return True
        except Exception as e:
            _LOGGER.error('Failed to remove old tracks - %s' % str(e))
            pass
        return False


    def force_rowid_update(self):
        ''' Copy tracks into tmp and back - to force rowid to be updated '''
        self.commit()
        self.cursor.execute('DELETE from tracks_tmp')
        self.cursor.execute('INSERT INTO tracks_tmp SELECT * from tracks')
        self.cursor.execute('DELETE from tracks')
        self.cursor.execute('INSERT INTO tracks SELECT * from tracks_tmp')
        self.cursor.execute('DELETE from tracks_tmp')
        self.commit()
        self.cursor.execute('VACUUM')
        self.commit()


    def file_already_analysed(self, path):
        self.cursor.execute('SELECT vals FROM tracks WHERE file=?', (path,))
        return self.cursor.fetchone() is not None


    def get_albums(self):
        albums = []
        self.cursor.execute('SELECT distinct albumartist, album FROM tracks')
        rows = self.cursor.fetchall()
        for row in rows:
            albums.append({'artist':row[0], 'title':row[1]})
        return albums


    def get_sample_track(self, album):
        ''' Get a random track betwen 60 and 5mins '''
        self.cursor.execute('SELECT rowid, duration from tracks where albumartist=? and album=? order by duration asc', (album['artist'], album['title']))
        rows = self.cursor.fetchall()
        candidates = []
        over5min = []
        over7min = []
        tooshort = []
        for row in rows:
            if row[1]<60:
                tooshort.append(row[0])
            elif row[1]>420:
                over7min.append(row[0])
            elif row[1]>300:
                over5min.append(row[0])
            else:
                candidates.append(row[0])

        while (len(candidates)<3) and (len(over5min)>0 or len(over7min)>0):
            if len(over5min)>0:
                candidates.append(over5min.pop())
            elif len(over7min)>0:
                candidates.append(over7min.pop())
        if len(candidates)>0:
            return random.choice(candidates)
        return None


    def get_cursor(self):
        return self.cursor
