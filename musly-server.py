#!/usr/bin/env python3

#
# Analyse files with Musly, and provide an API to retrieve simialr tracks
#
# Copyright (c) 2020 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#


import argparse
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
from flask import Flask, abort, request
from urllib.parse import urlunparse, quote
from concurrent.futures import ThreadPoolExecutor

from lib import musly

DB_FILE = 'musly.db'
JUKEBOX_FILE = 'musly.jukebox'
AUDIO_EXTENSIONS = ['m4a', 'mp3', 'ogg', 'flac', 'opus']
CUE_TRACK = '.CUE_TRACK.'
VARIOUS_ARTISTS = ['Various', 'Various Artists']
GENRE_SEPARATOR = ';'
_LOGGER = logging.getLogger(__name__)
app = Flask(__name__)
mus = None
mta = None
lms_db = None


def init_db(path):
    sconn = sqlite3.connect(path)
    scursor = sconn.cursor()
    scursor.execute('''CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file varchar UNIQUE NOT NULL,
                artist varchar,
                album varchar,
                albumartist varchar,
                genre varchar,
                duration integer,
                vals blob NOT NULL)''')
    scursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS tracks_idx ON tracks(file)')
    return sconn, scursor


def close_db(sconn, scursor):
    scursor.close()
    sconn.close()


def get_metadata(scursor, i):
    try:
        scursor.execute('SELECT artist, album, albumartist, genre, duration FROM tracks WHERE id=?', (i,))
        row = scursor.fetchone()
        meta = {'artist':row[0], 'album':row[1], 'albumartist':row[2], 'duration':row[4]}
        if row[3] and len(row[3])>0:
            meta['genres']=row[3].split(GENRE_SEPARATOR)
        return meta
    except Exception as e:
        _LOGGER.error('Failed to read metadata - %s' % str(e))
        pass
    return None


def get_ogg_or_flac(path):
    from mutagen.oggflac import OggFLAC
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    from mutagen.flac import FLAC

    try:
        return OggVorbis(path)
    except:
        pass
    try:
        return FLAC(path)
    except:
        pass
    try:
        return OggFLAC(path)
    except:
        pass
    try:
        return OggOpus(path)
    except:
        pass
    return None


def read_metadata(path):
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4

    try:
        audio = MP4(path)
        meta = {'artist':str(audio['\xa9ART'][0]), 'album':str(audio['\xa9alb'][0]), 'duration':int(audio.info.length)}
        if 'aART' in audio:
            meta['albumartist']=str(audio['aART'][0])
        if '\xa9gen' in audio:
            meta['genres']=[]
            for g in audio['\xa9gen']:
                meta['genres'].append(str(g))
        _LOGGER.debug('MP4 File:%s Meta:%s' % (path, json.dumps(meta)))
        return meta
    except:
        pass

    try:
        audio = MP3(path)
        meta = {'artist':str(audio['TPE1']), 'album':str(audio['TALB']), 'duration':int(audio.info.length)}
        if 'TPE2' in audio:
            meta['albumartist']=str(audio['TPE2'])
        if 'TCON' in audio:
            meta['genres']=str(audio['TCON']).split(GENRE_SEPARATOR)
        _LOGGER.debug('MP3 File:%s Meta:%s' % (path, json.dumps(meta)))
        return meta
    except Exception as e:
        print("EX:%s" % str(e))
        pass

    try:
        audio = ID3(path)
        meta = {'artist':str(audio['TPE1']), 'album':str(audio['TALB']), 'duration':0}
        if 'TPE2' in audio:
            meta['albumartist']=str(audio['TPE2'])
        if 'TCON' in audio:
            meta['genres']=str(audio['TCON']).split(GENRE_SEPARATOR)
        _LOGGER.debug('ID3 File:%s Meta:%s' % (path, json.dumps(meta)))
        return meta
    except:
        pass

    audio = get_ogg_or_flac(path)
    if audio:
        meta = {'artist':str(audio['ARTIST'][0]), 'album':str(audio['ALBUM'][0]), 'duration':int(audio.info.length)}
        if 'ALBUMARTIST' in audio:
            meta['albumartist']=str(audio['ALBUMARTIST'][0])
        if 'GENRE' in audio:
            meta['genres']=[]
            for g in audio['GENRE']:
                meta['genres'].append(str(g))
        _LOGGER.debug('OGG File:%s Meta:%s' % (path, json.dumps(meta)))
        return meta

    _LOGGER.debug('File:%s Meta:NONE' % path)
    return None


def set_metadata(scursor, track):
    meta = read_metadata(track['abs'])
    if meta is not None:
        if not 'albumartist' in meta or meta['albumartist'] is None:
            if not 'genres' in meta or meta['genres'] is None:
                scursor.execute('UPDATE tracks SET artist=?, album=?, duration=? WHERE file=?', (meta['artist'], meta['album'], meta['duration'], track['db']))
            else:
                scursor.execute('UPDATE tracks SET artist=?, album=?, genre=?, duration=? WHERE file=?', (meta['artist'], meta['album'], GENRE_SEPARATOR.join(meta['genres']), meta['duration'], track['db']))
        else:
            if not 'genres' in meta or meta['genres'] is None:
                scursor.execute('UPDATE tracks SET artist=?, album=?, albumartist=?, duration=? WHERE file=?', (meta['artist'], meta['album'], meta['albumartist'], meta['duration'], track['db']))
            else:
                scursor.execute('UPDATE tracks SET artist=?, album=?, albumartist=?, genre=?, duration=? WHERE file=?', (meta['artist'], meta['album'], meta['albumartist'], GENRE_SEPARATOR.join(meta['genres']), meta['duration'], track['db']))


def file_already_analysed(scursor, path):
    scursor.execute('SELECT vals FROM tracks WHERE file=?', (path,))
    return scursor.fetchone() is not None


def get_cue_tracks(path, musly_root_len, tmp_path):
    global config
    tracks=[]
    if lms_db is not None:
        # Convert musly path into LMS path...
        lms_path = '%s%s' % (config['paths']['lms'], path[musly_root_len:])
        # Get list of cue track from LMS db...
        cursor = lms_db.execute("select url from tracks where url like '%%%s#%%'" % quote(lms_path))
        for row in cursor:
            parts=row[0].split('#')
            if 2==len(parts):
                times=parts[1].split('-')
                if 2==len(times):
                    track_path='%s%s%s%s-%s.mp3' % (tmp_path, path[musly_root_len:], CUE_TRACK, times[0], times[1])
                    tracks.append({'file':track_path, 'start':times[0], 'end':times[1]})
    return tracks


def split_cue_track(path, track):
    _LOGGER.debug("Create %s" % track['file'])
    dirname=os.path.dirname(track['file'])
    end = float(track['end'])-float(track['start'])
    command=['ffmpeg', '-hide_banner', '-loglevel', 'panic', '-i', path, '-b:a', '128k', '-ss', track['start'], '-t', "%f" % end, track['file']]
    subprocess.Popen(command).wait()
    return True


def split_cue_tracks(files):    
    # Create temporary folders
    for file in files:
        if 'track' in file:
            dirname=os.path.dirname(file['track']['file'])
            if not os.path.exists(dirname):
                os.makedirs(dirname)

    # Split into tracks
    futures_list = []
    with ThreadPoolExecutor(max_workers=config['threads']) as executor:
        for file in files:
            if 'track' in file:
                futures = executor.submit(split_cue_track, file['src'], file['track'])
                futures_list.append(futures)
        for future in futures_list:
            try:
                future.result()
            except Exception as e:
                _LOGGER.debug("Thread exception? - %s" % str(e))
                pass

                          
def convert_to_cue_url(path):
    cue = path.find(CUE_TRACK)
    if cue>0:
        path='file://'+path.replace(CUE_TRACK, '#')
        return path[:-4]
    return path


def get_files_to_analyse(scursor, path, files, musly_root_len, tmp_path, tmp_path_len, meta_only):
    if not os.path.exists(path):
        _LOGGER.error("'%s' does not exist" % path)
        return
    if os.path.isdir(path):
        for e in sorted(os.listdir(path)):
            get_files_to_analyse(scursor, os.path.join(path, e), files, musly_root_len, tmp_path, tmp_path_len, meta_only)
    elif path.rsplit('.', 1)[1].lower() in AUDIO_EXTENSIONS:
        if os.path.exists(path.rsplit('.', 1)[0]+'.cue'):
            for track in get_cue_tracks(path, musly_root_len, tmp_path):
                if meta_only or not not file_already_analysed(scursor, track['file'][tmp_path_len:]):
                    files.append({'abs':track['file'], 'db':track['file'][tmp_path_len:], 'track':track, 'src':path})
        elif meta_only or not file_already_analysed(scursor, path[musly_root_len:]):
            files.append({'abs':path, 'db':path[musly_root_len:]})


def analyse_files(path, meta_only):
    global config
    global lms_db

    sconn, scursor = init_db(os.path.join(config['paths']['db'], DB_FILE))
    if lms_db is None and 'lmsdb' in config:
        lms_db = sqlite3.connect(config['lmsdb'])
        
    files = []
    musly_root_len = len(config['paths']['musly'])

    temp_dir = config['paths']['tmp'] if 'tmp' in config['paths'] else None
    with tempfile.TemporaryDirectory(dir=temp_dir) as tmp_path:
        _LOGGER.debug('Temp folder: %s' % tmp_path)
        get_files_to_analyse(scursor, path, files, musly_root_len, tmp_path+'/', len(tmp_path)+1, meta_only)
        _LOGGER.debug('Num files: %d' % len(files))
        split_cue_tracks(files)
        if (len(files)>0):
            roots = [config['paths']['musly'], tmp_path+'/']
            if not meta_only:
                tracks = mus.analyze_files(scursor, files, roots, num_threads=config['threads'])
                sconn.commit()
                mus.add_tracks(tracks)
            _LOGGER.debug('Save metadata')
            for file in files:
                set_metadata(scursor, file)
            sconn.commit()
            if not meta_only:
                mus.write_jukebox(os.path.join(config['paths']['db'], JUKEBOX_FILE))
    _LOGGER.debug('Finished analysis')


def same_artist_or_album(seeds, track):
    for seed in seeds:
        if seed['artist']==track['artist']:
            return True
        if seed['album']==track['album'] and seed['albumartist']==track['albumartist'] and track['albumartist'] not in VARIOUS_ARTISTS:
            return True
    return False


def genre_matches(seed_genres, track):
    if 'genres' not in track or len(track['genres'])<1:
        return True # Track has no genre? Then can't filtre out...

    if len(seed_genres)<1:
        # No filtering for seed track genres
        global config
        if 'all_genres' in config:
            for tg in track['genres']:
                if tg in config['all_genres']:
                    # Track's genre is in config list, but not in seeds, so filter out track
                    return False
        # No seed genres, and track's genre not in filters, so accept track
        return True

    for sg in seed_genres:
        if sg in track['genres']:
            return True

    return False


def check_duration(min_duration, max_duration, meta):
    if 'duration' not in meta or meta['duration'] is None or meta['duration']<=0:
        return True # No duration to check!

    if min_duration>0 and meta['duration']<min_duration:
        return False

    if max_duration>0 and meta['duration']>max_duration:
        return False

    return True


@app.route('/api/similar')
def similar_api():
    global mta
    global config
    params = request.args.to_dict(flat=False)

    if not 'track' in params:
        abort(400)
    tracks = params['track']

    count = int(params['count'][0]) if 'count' in params else 5
    if count<5:
        count = 5
    elif count>50:
        count = 50

    match_genre = 'filtergenre' in params and params['filtergenre'][0]=='1'
    min_duration = int(params['min'][0]) if 'min' in params else 0
    max_duration = int(params['max'][0]) if 'max' in params else 0

    # Strip LMS root path from track path
    root = config['paths']['lms']
    genres = config['genre'][0]==1 if 'genre' in config else None
    
    # Similar tracks
    similar_tracks=[]
    # Track IDs of similar tracks - used to avoid duplicates
    similar_track_ids=[]
    # Similar tracks ignored because of artist/album
    filtered_by_seeds_tracks=[]
    filtered_by_current_tracks=[]
    
    # Artist/album of seed tracks
    seed_metadata=[]
    seed_genres=[]
    
    # Artist/album of chosen tracks
    current_metadata_keys={}
    current_metadata=[]

    if min_duration>0 or max_duration>0:
        _LOGGER.debug('Duration:%d .. %d' % (min_duration, max_duration))

    (sconn, scursor) = init_db(os.path.join(config['paths']['db'], DB_FILE))

    # Musly IDs of seed traks
    track_ids = []
    for track in tracks:
        if track.startswith(root):
            track=track[len(root):]

        # Check that musly knows about this track
        track_id = -1
        try:
            track_id = mta.paths.index( track )
            _LOGGER.debug('Get %d similar track(s) to %s, index: %d' % (count, track, track_id))
        except:
            pass
        if track_id is not None and track_id>=0:
            track_ids.append(track_id)
            meta = get_metadata(scursor, track_id+1) # IDs in SQLite are 1.. musly is 0..
            _LOGGER.debug('Seed %d metadata:%s' % (track_id, json.dumps(meta)))
            if meta is not None:
                seed_metadata.append(meta)
                # Get genres for this seed track - this takes its genres and gets any matching genres from config
                if 'genres' in meta and 'genres' in config:
                    for genre in meta['genres']:
                        for group in config['genres']:
                            if genre in group:
                                for cg in group:
                                    if not cg in seed_genres:
                                        seed_genres.append(cg)
    if match_genre:
        _LOGGER.debug('Seed genres: %s' % seed_genres)

    for track_id in track_ids:
        # Query musly for similar tracks
        ( resp_ids, resp_similarity ) = mus.get_similars( mta.mtracks, mta.mtrackids, track_id, (count*20)+1 )
        accepted_tracks = 0
        for i in range(1, len(resp_ids)):
            if not resp_ids[i] in similar_track_ids and resp_similarity[i]>0.0:
                similar_track_ids.append(resp_ids[i])

                meta = get_metadata(scursor, resp_ids[i]+1) # IDs in SQLite are 1.. musly is 0..
                if (min_duration>0 or max_duration>0) and not check_duration(min_duration, max_duration, meta):
                    _LOGGER.debug('IGNORE(duration) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                elif match_genre and not genre_matches(seed_genres, meta):
                    _LOGGER.debug('IGNORE(genre) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                else:
                    if same_artist_or_album(seed_metadata, meta):
                        _LOGGER.debug('FILTERED(seeds) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                        filtered_by_seeds_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
                    elif same_artist_or_album(current_metadata, meta):
                        _LOGGER.debug('FILTERED(current) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                        filtered_by_current_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
                    else:
                        key = '%s::%s::%s' % (meta['artist'], meta['album'], meta['albumartist'] if 'albumartist' in meta and meta['albumartist'] is not None else '')
                        if not key in current_metadata_keys:
                            current_metadata_keys[key]=1
                            current_metadata.append(meta)
                        _LOGGER.debug('USABLE ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                        similar_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
                        accepted_tracks += 1
                        if accepted_tracks>=count:
                            break

    # Too few tracks? Add some from the filtered list
    if len(similar_tracks)<count and len(filtered_by_current_tracks)>0:
        filtered_by_seeds_tracks = sorted(filtered_by_current_tracks, key=lambda k: k['similarity'])
        similar_tracks = similar_tracks + filtered_by_current_tracks[:count-len(similar_tracks)]
    if len(similar_tracks)<count and len(filtered_by_seeds_tracks)>0:
        filtered_by_seeds_tracks = sorted(filtered_by_seeds_tracks, key=lambda k: k['similarity'])
        similar_tracks = similar_tracks + filtered_by_seeds_tracks[:count-len(similar_tracks)]

    # Sort by similarity
    similar_tracks = sorted(similar_tracks, key=lambda k: k['similarity'])
    
    # Take top 'count' tracks
    similar_tracks = similar_tracks[:count]
    track_list = []
    for track in similar_tracks:
        path = '%s%s' % (root, track['path'])
        track_list.append(convert_to_cue_url(path))
        _LOGGER.debug('Path:%s' % path)

    close_db(sconn, scursor)
    if 'format' in params and 'text'==params['format'][0]:
        return '\n'.join(track_list)
    else:
        return json.dumps(track_list)


def read_config(path, analyse):
    config={}

    if not os.path.exists(path):
        _LOGGER.error('%s does not exist' % path)
        exit(-1)
    try:
        with open(path, 'r') as configFile:
            config = json.load(configFile)
    except ValueError:
        _LOGGER.error('Failed to parse config file')
        exit(-1)
    except IOError:
        _LOGGER.error('Failed to read config file')
        exit(-1)
    for key in ['libmusly', 'paths']:
        if not key in config:
            _LOGGER.error("'%s' not in config file" % key)
            exit(-1)
    for key in ['musly', 'lms', 'db']:
        if not key in config['paths']:
            _LOGGER.error("'paths.%s' not in config file" % key)
            exit(-1)
        if (key=='db' and not os.path.exists(config['paths'][key])) or (analyse and key=='musly' and not os.path.exists(config['paths'][key])):
            _LOGGER.error("'%s' does not exist" % config['paths'][key])
            exit(-1)

    if 'tmp' in config['paths'] and not os.path.exists(config['paths']['tmp']):
        _LOGGER.error("'%s' does not exist" % config['paths']['tmp'])
        exit(-1)

    if not 'port' in config:
        config['port']=11000
    if not 'host' in config:
        config['host']='0.0.0.0'
    if not 'threads' in config:
        config['threads']=8

    if 'genres' in config:
        config['all_genres']=[]
        for genres in config['genres']:
            for g in genres:
                if not g in config['all_genres']:
                    config['all_genres'].append(g)
    return config

                
if __name__=='__main__':
    parser = argparse.ArgumentParser(description='Musly API Server')
    parser.add_argument('-c', '--config', type=str, help='Config file (default: config.json)', default='config.json')
    parser.add_argument('-l', '--log-level', action='store', choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'], default='INFO', help='Set log level (default: %(default)s)')
    parser.add_argument('-a', '--analyse', metavar='PATH', type=str, help="Analyse file/folder (use 'm' for configured musly folder)", default='')
    parser.add_argument('-m', '--meta-only', action='store_true', default=False, help='Update metadata database only (used in conjuction with --analyse)')
    args = parser.parse_args()
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s',
                        level=args.log_level,
                        datefmt='%Y-%m-%d %H:%M:%S')
    config = read_config(args.config, args.analyse)
    _LOGGER.debug('Init DB')
    lib = config['libmusly']
    if not lib.startswith('/'):
        lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), lib)
    _LOGGER.debug('Init Musly')
    mus = musly.Musly(lib)
    if args.analyse:
        path = config['paths']['musly'] if args.analyse =='m' else args.analyse
        _LOGGER.debug('Analyse %s' % path)
        analyse_files(path, args.meta_only)
    else:
        _LOGGER.debug('Start server')
        flask_logging = logging.getLogger('werkzeug')
        flask_logging.setLevel(args.log_level)
        flask_logging.disabled = 'DEBUG'!=args.log_level
        (sconn, scursor) = init_db(os.path.join(config['paths']['db'], DB_FILE))
        (paths, tracks) = mus.get_alltracks_db(scursor)
        close_db(sconn, scursor)
        ids = None

        # If we can, load musly from jukebox...
        jukebox_path = os.path.join(config['paths']['db'], JUKEBOX_FILE)
        if os.path.exists(jukebox_path):
            ids = mus.get_jukebox_from_file(jukebox_path)

        if ids==None or len(ids)!=len(tracks):
            _LOGGER.debug('Adding tracks from DB to musly')
            ids = mus.add_tracks(tracks)
            mus.write_jukebox(jukebox_path)

        mta = musly.MuslyTracksAdded(paths, tracks, ids)

        _LOGGER.debug('Ready to process requests')
        app.run(host=config['host'], port=config['port'])

