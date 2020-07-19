#!/usr/bin/env python3

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
AUDIO_EXTENSIONS = ['m4a', 'mp3', 'ogg', 'flac']
CUE_TRACK = '.CUE_TRACK.'
VARIOUS_ARTISTS = ['Various', 'Various Artists']
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
                vals blob NOT NULL)''')
    scursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS tracks_idx ON tracks(file)')
    return sconn, scursor


def close_db(sconn, scursor):
    scursor.close()
    sconn.close()


def get_metadata(scursor, i):
    try:
        scursor.execute('SELECT artist, album, albumartist FROM tracks WHERE id=?', (i,))
        row = scursor.fetchone()
        return {'artist':row[0], 'album':row[1], 'albumartist':row[2]}
    except Exception as e:
        _LOGGER.error('Failed to read metadata - %s' % str(e))
        pass
    return None


def get_ogg_or_flac(path):
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


def read_tags(path):
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    from mutagen.oggflac import OggFLAC
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    from mutagen.flac import FLAC

    try:
        audio = ID3(path)
        meta = {'artist':audio['TPE1'], 'album':audio['TALB']}
        if 'TPE2' in audio:
            meta['albumartist']=audio['TPE2']
        return meta
    except:
        pass

    try:
        audio = MP4(path)
        meta = {'artist':audio['\xa9ART'][0], 'album':audio['\xa9alb'][0]}
        if 'aART' in audio:
            meta['albumartist']=audio['aART'][0]
        return meta
    except:
        pass

    audio = get_ogg_or_flac(path)
    if audio:
        meta = {'artist':audio['ARTIST'][0], 'album':audio['ALBUM']}[0]
        if 'ALBUMARTIST' in audio:
            meta['albumartist']=audio['ALBUMARTIST'][0]
        return meta

    return None, None, None


def set_metadata(scursor, track):
    tags = read_tags(track['abs'])
    if tags is not None:
        if tags['albumartist'] is None:
            scursor.execute('UPDATE tracks SET artist=?, album=? WHERE file=?', (str(tags['artist']), str(tags['album']), track['db']))
        else:
            scursor.execute('UPDATE tracks SET artist=?, album=?, albumartist=? WHERE file=?', (str(tags['artist']), str(tags['album']), str(tags['albumartist']), track['db']))


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


def get_files_to_analyse(scursor, path, files, musly_root_len, tmp_path, tmp_path_len):
    if not os.path.exists(path):
        _LOGGER.error("'%s' does not exist" % path)
        return
    if os.path.isdir(path):
        for e in sorted(os.listdir(path)):
            get_files_to_analyse(scursor, os.path.join(path, e), files, musly_root_len, tmp_path, tmp_path_len)
    elif path.rsplit('.', 1)[1].lower() in AUDIO_EXTENSIONS:
        if os.path.exists(path.rsplit('.', 1)[0]+'.cue'):
            for track in get_cue_tracks(path, musly_root_len, tmp_path):
                if not file_already_analysed(scursor, track['file'][tmp_path_len:]):
                    files.append({'abs':track['file'], 'db':track['file'][tmp_path_len:], 'track':track, 'src':path})
        elif not file_already_analysed(scursor, path[musly_root_len:]):
            files.append({'abs':path, 'db':path[musly_root_len:]})


def analyse_files(path):
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
        get_files_to_analyse(scursor, path, files, musly_root_len, tmp_path+'/', len(tmp_path)+1)
        split_cue_tracks(files)
        if (len(files)>0):
            roots = [config['paths']['musly'], tmp_path+'/']
            tracks = mus.analyze_files(scursor, files, roots, num_threads=config['threads'])
            sconn.commit()
            mus.add_tracks(tracks)
            _LOGGER.debug('Save metadata')
            for file in files:
                set_metadata(scursor, file)
            sconn.commit()
            mus.write_jukebox(os.path.join(config['paths']['db'], JUKEBOX_FILE))
    _LOGGER.debug('Finished analysis')


def same_artist_or_album(seeds, track):
    for seed in seeds:
        if seed['artist']==track['artist']:
            return True
        if seed['album']==track['album'] and seed['albumartist']==track['albumartist'] and track['albumartist'] not in VARIOUS_ARTISTS:
            return True
    return False


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

    # Strip LMS root path from track path
    root = config['paths']['lms']
    
    # Similar tracks
    similar_tracks=[]
    # Track IDs of similar tracks - used to avoid duplicates
    similar_track_ids=[]
    # Similar tracks ignored because of artist/album
    filtered_by_seeds_tracks=[]
    filtered_by_current_tracks=[]
    
    # Artist/album of seed tracks
    seed_metadata=[]
    
    # Artist/album of chosen tracks
    current_metadata_keys={}
    current_metadata=[]
    
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
            _LOGGER.debug('Seed %d metadata %s -- %s -- %s' % (track_id, meta['artist'], meta['album'], meta['albumartist']))
            if meta is not None:
                seed_metadata.append(meta)

    for track_id in track_ids:
        # Query musly for similar tracks
        ( resp_ids, resp_similarity ) = mus.get_similars( mta.mtracks, mta.mtrackids, track_id, (count*5)+1 )
        accepted_tracks = 0
        for i in range(1, len(resp_ids)):
            if not resp_ids[i] in similar_track_ids and resp_similarity[i]>0.0:
                similar_track_ids.append(resp_ids[i])

                meta = get_metadata(scursor, resp_ids[i]+1) # IDs in SQLite are 1.. musly is 0..
                if same_artist_or_album(seed_metadata, meta):
                    _LOGGER.debug('FILTERED(seeds) ID:%d Path:%s Similarity:%f Artist:%s Album:%s AlbumArtist:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], meta['artist'], meta['album'], meta['albumartist']))
                    filtered_by_seeds_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
                elif same_artist_or_album(current_metadata, meta):
                    _LOGGER.debug('FILTERED(current) ID:%d Path:%s Similarity:%f Artist:%s Album:%s AlbumArtist:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], meta['artist'], meta['album'], meta['albumartist']))
                    filtered_by_current_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
                else:
                    key = "%s::%s::%s" % (artist, album, albumartist)
                    if not key in current_metadata_keys:
                        current_metadata_keys[key]=1
                        current_metadata.append({'artist':artist, 'album':album, 'albumartist':albumartist})
                    _LOGGER.debug('USABLE ID:%d Path:%s Similarity:%f Artist:%s Album:%s AlbumArtist:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], meta['artist'], meta['album'], meta['albumartist']))
                    similar_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
                    accepted_tracks += 1
                    if accepted_tracks>=count:
                        break

    # Too few tracks? Add some from the filtered list
    if len(similar_tracks)<count and len(filtered_by_current_tracks)>0:
        filtered_by_seeds_tracks = sorted(filtered_by_current_tracks, key=lambda k: k['similarity'])
        similar_tracks = similar_tracks + filtered_by_current_tracks[count-len(similar_tracks)]
    if len(similar_tracks)<count and len(filtered_by_seeds_tracks)>0:
        filtered_by_seeds_tracks = sorted(filtered_by_seeds_tracks, key=lambda k: k['similarity'])
        similar_tracks = similar_tracks + filtered_by_seeds_tracks[count-len(similar_tracks)]

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
        return "\n".join(track_list)
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
    return config

                
if __name__=='__main__':
    parser = argparse.ArgumentParser(description='Musly API Server')
    parser.add_argument('-c', '--config', type=str, help='Config file (default: config.json)', default='config.json')
    parser.add_argument('-l', '--log-level', action='store', choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'], default='INFO', help='Set log level (default: %(default)s)')
    parser.add_argument('-a', '--analyse', metavar='PATH', type=str, help='Analyse file/folder (use "m" for configured musly folder)', default='')
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
        path = config['paths']['musly'] if args.analyse == "m" else args.analyse
        _LOGGER.debug('Analyse %s' % path)
        analyse_files(path)
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

