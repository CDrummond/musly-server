#
# Analyse files with Musly, and provide an API to retrieve similar tracks
#
# Copyright (c) 2020-2021 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#

import argparse
from datetime import datetime
import json
import logging
import math
import os
import random
import sqlite3
import urllib
from flask import Flask, abort, request
from . import cue, filters, metadata_db, musly

_LOGGER = logging.getLogger(__name__)

DEFAULT_TRACKS_TO_RETURN              = 5  # Number of tracks to return, if none specified
MIN_TRACKS_TO_RETURN                  = 5  # Min value for 'count' parameter
MAX_TRACKS_TO_RETURN                  = 50 # Max value for 'count' parameter
DEFAULT_NUM_PREV_TRACKS_FILTER_ARTIST = 15 # Try to ensure artist is not in previous N tracks
DEFAULT_NUM_PREV_TRACKS_FILTER_ALBUM  = 25 # Try to ensure album is not in previous N tracks
NUM_SIMILAR_TRACKS_FACTOR             = 25 # Request count*NUM_SIMILAR_TRACKS_FACTOR from musly
SHUFFLE_FACTOR                        = 3  # How many (shuffle_factor*count) tracks to shuffle?


class MuslyApp(Flask):
    def init(self, args, mus, app_config, jukebox_path):
        _LOGGER.debug('Start server')
        self.app_config = app_config
        self.mus = mus
        
        flask_logging = logging.getLogger('werkzeug')
        flask_logging.setLevel(args.log_level)
        flask_logging.disabled = 'DEBUG'!=args.log_level
        meta_db = metadata_db.MetadataDb(app_config)
        (paths, tracks) = self.mus.get_alltracks_db(meta_db.get_cursor())
        random.seed()
        ids = None

        # If we can, load musly from jukebox...
        if os.path.exists(jukebox_path):
            ids = self.mus.get_jukebox_from_file(jukebox_path)

        if ids==None or len(ids)!=len(tracks):
            _LOGGER.debug('Adding tracks from DB to musly')
            ids = mus.add_tracks(tracks, app_config['styletracks'], app_config['styletracksmethod'], meta_db)
            self.mus.write_jukebox(jukebox_path)

        meta_db.close()
        self.mta=musly.MuslyTracksAdded(paths, tracks, ids)

    def get_config(self):
        return self.app_config

    def get_musly(self):
        return self.mus

    def get_mta(self):
        return self.mta
    
musly_app = MuslyApp(__name__)


def get_value(params, key, defVal, isPost):
    if isPost:
        return params[key] if key in params else defVal
    return params[key][0] if key in params else defVal


def decode(url, root):
    u = urllib.parse.unquote(url)
    if u.startswith('file://'):
        u=u[7:]
    elif u.startswith('tmp://'):
        u=u[6:]
    if u.startswith(root):
        u=u[len(root):]
    return cue.convert_from_cue_path(u)


def genre_adjust(seed, entry, seed_genres, all_genres, match_all_genres):
    if match_all_genres:
        return 0.0
    if 'genres' not in seed:
        return 0.1
    if 'genres' not in entry:
        return 0.1
    if seed['genres'][0]==entry['genres'][0]:
        # Exact genre match
        return 0.0
    if (seed_genres is not None and entry['genres'][0] not in seed_genres) or \
       (seed_genres is None and all_genres is not None and entry['genres'][0] in all_genres):
        return 0.05
    # Genre in group
    return 0.025


@musly_app.route('/api/dump', methods=['GET', 'POST'])
def dump_api():
    isPost = False
    if request.method=='GET':
        params = request.args.to_dict(flat=False)
    else:
        isPost = True
        params = request.get_json()
        _LOGGER.debug('Request: %s' % json.dumps(params))

    if not params:
        abort(400)

    if not 'track' in params:
        abort(400)

    if len(params['track'])!=1:
        abort(400)

    mta = musly_app.get_mta()
    mus = musly_app.get_musly()
    cfg = musly_app.get_config()
    meta_db = metadata_db.MetadataDb(cfg)

    # Strip LMS root path from track path
    root = cfg['paths']['lms']

    track = decode(params['track'][0], root)
    _LOGGER.debug('S TRACK %s -> %s' % (params['track'][0], track))

    # Check that musly knows about this track
    track_id = -1
    try:
        track_id = mta.paths.index( track )
        if track_id<0:
            abort(404)
        fmt = get_value(params, 'format', '', isPost)
        txt = fmt=='text'
        txt_url = fmt=='text-url'
        match_artist = int(get_value(params, 'filterartist', '0', isPost))==1
        meta = meta_db.get_metadata(track_id+1) # IDs (rowid) in SQLite are 1.. musly is 0..

        all_genres = cfg['all_genres'] if 'all_genres' in cfg else None
        seed_genres=[]
        if 'genres' in meta and 'genres' in cfg:
            for genre in meta['genres']:
                for group in cfg['genres']:
                    if genre in group:
                        for cg in group:
                            if not cg in seed_genres:
                                seed_genres.append(cg)

        simtracks = mus.get_similars( mta.mtracks, mta.mtrackids, track_id )

        resp=[]
        prev_id=-1
        count = int(get_value(params, 'count', 1000, isPost))

        tracks=[]
        for simtrack in simtracks:
            if simtrack['id']==prev_id:
                break
            prev_id=simtrack['id']
            if math.isnan(simtrack['sim']):
                continue

            track = meta_db.get_metadata(simtrack['id']+1)
            if match_artist and track['artist'] != meta['artist']:
                continue
            if not match_artist and track['ignore']:
                continue
            match_all_genres = ('ignoregenre' in cfg) and (('*'==cfg['ignoregenre'][0]) or (meta is not None and meta['artist'] in cfg['ignoregenre']))
            sim = simtrack['sim'] + genre_adjust(meta, track, seed_genres, all_genres, match_all_genres)
            tracks.append({'path':mta.paths[simtrack['id']], 'sim':sim})

        tracks = sorted(tracks, key=lambda k: k['sim'])
        for track in tracks:
            _LOGGER.debug("%s %s" % (track['path'], track['sim']))
            if txt:
                resp.append("%s\t%f" % (track['path'], track['sim']))
            elif txt_url:
                resp.append(cue.convert_to_cue_url('%s%s' % (root, track['path'])))
            else:
                resp.append({'file':track['path'], 'sim':track['sim']})
            if len(resp)>=count:
                break
        if txt or txt_url:
            return '\n'.join(resp)
        else:
            return json.dumps(resp)
    except Exception as e:
        _LOGGER.error("EX:%s" % str(e))
        abort(404)


@musly_app.route('/api/similar', methods=['GET', 'POST'])
def similar_api():
    isPost = False
    if request.method=='GET':
        params = request.args.to_dict(flat=False)
    else:
        isPost = True
        params = request.get_json()
        _LOGGER.debug('Request: %s' % json.dumps(params))

    if not params:
        abort(400)

    if not 'track' in params:
        abort(400)

    count = int(get_value(params, 'count', DEFAULT_TRACKS_TO_RETURN, isPost))
    if count < MIN_TRACKS_TO_RETURN:
        count = MIN_TRACKS_TO_RETURN
    elif count > MAX_TRACKS_TO_RETURN:
        count = MAX_TRACKS_TO_RETURN

    match_genre = int(get_value(params, 'filtergenre', '0', isPost))==1
    shuffle = int(get_value(params, 'shuffle', '1', isPost))==1
    max_similarity = int(get_value(params, 'maxsim', 75, isPost))/100.0
    min_duration = int(get_value(params, 'min', 0, isPost))
    max_duration = int(get_value(params, 'max', 0, isPost))
    no_repeat_artist = int(get_value(params, 'norepart', 0, isPost))
    no_repeat_album = int(get_value(params, 'norepalb', 0, isPost))
    exclude_christmas = int(get_value(params, 'filterxmas', '0', isPost))==1 and datetime.now().month!=12

    if no_repeat_artist<0 or no_repeat_artist>200:
        no_repeat_artist = DEFAULT_NUM_PREV_TRACKS_FILTER_ARTIST
    if no_repeat_album<0 or no_repeat_album>200:
        no_repeat_album = DEFAULT_NUM_PREV_TRACKS_FILTER_ALBUM

    no_repeat_artist_or_album = no_repeat_album if no_repeat_album>no_repeat_artist else no_repeat_artist

    mta = musly_app.get_mta()
    mus = musly_app.get_musly()
    cfg = musly_app.get_config()
    meta_db = metadata_db.MetadataDb(cfg)

    # Strip LMS root path from track path
    root = cfg['paths']['lms']
    
    # Similar tracks
    similar_tracks=[]
    # Track IDs of similar tracks - used to avoid duplicates
    similar_track_ids=set()
    # Similar tracks ignored because of artist/album
    filtered_by_seeds_tracks=[]
    filtered_by_current_tracks=[]
    filtered_by_previous_tracks=[]
    current_titles=[]

    # Artist/album of seed tracks
    seed_metadata=[]
    track_id_seed_metadata={} # Map from seed track's ID to its metadata
    seed_genres=[]
    all_genres = cfg['all_genres'] if 'all_genres' in cfg else None
    
    # Artist/album of chosen tracks
    current_metadata_keys={}
    current_metadata=[]

    if min_duration>0 or max_duration>0:
        _LOGGER.debug('Duration:%d .. %d' % (min_duration, max_duration))

    # Musly IDs of seed tracks
    track_ids = []
    for trk in params['track']:
        track = decode(trk, root)
        _LOGGER.debug('S TRACK %s -> %s' % (trk, track))

        # Check that musly knows about this track
        track_id = -1
        try:
            track_id = mta.paths.index( track )
            _LOGGER.debug('Get %d similar track(s) to %s, index: %d' % (count, track, track_id))
        except:
            pass
        if track_id is not None and track_id>=0:
            track_ids.append(track_id)
            meta = meta_db.get_metadata(track_id+1) # IDs (rowid) in SQLite are 1.. musly is 0..
            _LOGGER.debug('Seed %d metadata:%s' % (track_id, json.dumps(meta)))
            if meta is not None:
                seed_metadata.append(meta)
                track_id_seed_metadata[track_id]=meta
                # Get genres for this seed track - this takes its genres and gets any matching genres from config
                if 'genres' in meta and 'genres' in cfg:
                    for genre in meta['genres']:
                        for group in cfg['genres']:
                            if genre in group:
                                for cg in group:
                                    if not cg in seed_genres:
                                        seed_genres.append(cg)
                if 'title' in meta:
                    current_titles.append(meta['title'])
        else:
            _LOGGER.debug('Could not locate %s in DB' % track)

    previous_track_ids = set()
    previous_metadata = [] # Ignore tracks with same meta-data, i.e. artist
    if 'previous' in params:
        for trk in params['previous']:
            track = decode(trk, root)
            _LOGGER.debug('I TRACK %s -> %s' % (trk, track))

            # Check that musly knows about this track
            track_id = -1
            try:
                track_id = mta.paths.index(track)
            except:
                pass
            if track_id is not None and track_id>=0:
                previous_track_ids.add(track_id)
                if len(previous_metadata)<no_repeat_artist_or_album:
                    meta = meta_db.get_metadata(track_id+1) # IDs (rowid) in SQLite are 1.. musly is 0..
                    if meta:
                        previous_metadata.append(meta)
                        if 'title' in meta:
                            current_titles.append(meta['title'])
            else:
                _LOGGER.debug('Could not locate %s in DB' % track)
        _LOGGER.debug('Have %d previous tracks' % len(previous_track_ids))

    if match_genre:
        _LOGGER.debug('Seed genres: %s' % seed_genres)

    similarity_count = int(count * SHUFFLE_FACTOR) if shuffle else count

    matched_artists={}
    for track_id in track_ids:
        match_all_genres = ('ignoregenre' in cfg) and (('*'==cfg['ignoregenre'][0]) or ((track_id in track_id_seed_metadata) and (track_id_seed_metadata[track_id]['artist'] in cfg['ignoregenre'])))

        # Query musly for similar tracks
        _LOGGER.debug('Query musly for similar tracks to index: %d' % track_id)
        simtracks = mus.get_similars( mta.mtracks, mta.mtrackids, track_id )

        accepted_tracks = 0
        for simtrack in simtracks:
            if math.isnan(simtrack['sim']):
                continue
            if (not simtrack['id'] in track_ids) and (not simtrack['id'] in previous_track_ids) and (not simtrack['id'] in similar_track_ids) and (simtrack['sim']>0.0) and (simtrack['sim']<=max_similarity):
                similar_track_ids.add(simtrack['id'])

                meta = meta_db.get_metadata(simtrack['id']+1) # IDs (rowid) in SQLite are 1.. musly is 0..
                if not meta:
                    _LOGGER.debug('DISCARD(not found) ID:%d Path:%s Similarity:%f' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim']))
                elif meta['ignore']:
                    _LOGGER.debug('DISCARD(ignore) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                elif (min_duration>0 or max_duration>0) and not filters.check_duration(min_duration, max_duration, meta):
                    _LOGGER.debug('DISCARD(duration) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                elif match_genre and not match_all_genres and not filters.genre_matches(cfg, seed_genres, meta):
                    _LOGGER.debug('DISCARD(genre) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                elif exclude_christmas and filters.is_christmas(meta):
                    _LOGGER.debug('DISCARD(xmas) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                else:
                    if filters.same_artist_or_album(seed_metadata, meta):
                        _LOGGER.debug('FILTERED(seeds) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                        filtered_by_seeds_tracks.append({'path':mta.paths[simtrack['id']], 'similarity':simtrack['sim']})
                    elif filters.same_artist_or_album(current_metadata, meta):
                        _LOGGER.debug('FILTERED(current) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                        filtered_by_current_tracks.append({'path':mta.paths[simtrack['id']], 'similarity':simtrack['sim']})
                        if meta['artist'] in matched_artists and simtrack['sim'] - matched_artists[meta['artist']]['similarity'] <= 0.2:
                            matched_artists[meta['artist']]['tracks'].append({'path':mta.paths[simtrack['id']], 'similarity':simtrack['sim']})
                    elif no_repeat_artist>0 and filters.same_artist_or_album(previous_metadata, meta, False, no_repeat_artist):
                        _LOGGER.debug('FILTERED(previous(artist)) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                        filtered_by_previous_tracks.append({'path':mta.paths[simtrack['id']], 'similarity':simtrack['sim']})
                    elif no_repeat_album>0 and filters.same_artist_or_album(previous_metadata, meta, True, no_repeat_album):
                        _LOGGER.debug('FILTERED(previous(album)) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                    elif filters.match_title(current_titles, meta):
                        _LOGGER.debug('FILTERED(title) ID:%d Path:%s Similarity:%f Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], json.dumps(meta)))
                        filtered_by_previous_tracks.append({'path':mta.paths[simtrack['id']], 'similarity':simtrack['sim']})
                    else:
                        key = '%s::%s::%s' % (meta['artist'], meta['album'], meta['albumartist'] if 'albumartist' in meta and meta['albumartist'] is not None else '')
                        if not key in current_metadata_keys:
                            current_metadata_keys[key]=1
                            current_metadata.append(meta)
                        sim = simtrack['sim'] + genre_adjust(seed_metadata, meta, seed_genres, all_genres, match_all_genres)

                        _LOGGER.debug('USABLE ID:%d Path:%s Similarity:%f AdjSim:%s Meta:%s' % (simtrack['id'], mta.paths[simtrack['id']], simtrack['sim'], sim, json.dumps(meta)))
                        similar_tracks.append({'path':mta.paths[simtrack['id']], 'similarity':sim})
                        # Keep list of all tracks of an artist, so that we can randomly select one => we don't always use the same one
                        matched_artists[meta['artist']]={'similarity':simtrack['sim'], 'tracks':[{'path':mta.paths[simtrack['id']], 'similarity':sim}], 'pos':len(similar_tracks)-1}
                        if 'title' in meta:
                            current_titles.append(meta['title'])
                        accepted_tracks += 1
                        if accepted_tracks>=similarity_count:
                            break

    # For each matched_artists randonly select a track...
    for matched in matched_artists:
        if len(matched_artists[matched]['tracks'])>1:
            _LOGGER.debug('Choosing random track for %s (%d tracks)' % (matched, len(matched_artists[matched]['tracks'])))
            sim = similar_tracks[matched_artists[matched]['pos']]['similarity']
            similar_tracks[matched_artists[matched]['pos']] = random.choice(matched_artists[matched]['tracks'])
            similar_tracks[matched_artists[matched]['pos']]['similarity'] = sim

    # Too few tracks? Add some from the filtered lists
    min_count = 2
    if len(similar_tracks)<min_count and len(filtered_by_previous_tracks)>0:
        _LOGGER.debug('Add some tracks from filtered_by_previous_tracks, %d/%d' % (len(similar_tracks), len(filtered_by_previous_tracks)))
        filtered_by_previous_tracks = sorted(filtered_by_previous_tracks, key=lambda k: k['similarity'])
        similar_tracks = similar_tracks + filtered_by_previous_tracks[:min_count-len(similar_tracks)]
    if len(similar_tracks)<min_count and len(filtered_by_current_tracks)>0:
        _LOGGER.debug('Add some tracks from filtered_by_current_tracks, %d/%d' % (len(similar_tracks), len(filtered_by_current_tracks)))
        filtered_by_current_tracks = sorted(filtered_by_current_tracks, key=lambda k: k['similarity'])
        similar_tracks = similar_tracks + filtered_by_current_tracks[:min_count-len(similar_tracks)]
    if len(similar_tracks)<min_count and len(filtered_by_seeds_tracks)>0:
        _LOGGER.debug('Add some tracks from filtered_by_seeds_tracks, %d/%d' % (len(similar_tracks), len(filtered_by_seeds_tracks)))
        filtered_by_seeds_tracks = sorted(filtered_by_seeds_tracks, key=lambda k: k['similarity'])
        similar_tracks = similar_tracks + filtered_by_seeds_tracks[:min_count-len(similar_tracks)]

    # Sort by similarity
    similar_tracks = sorted(similar_tracks, key=lambda k: k['similarity'])
    
    # Take top 'similarity_count' tracks
    similar_tracks = similar_tracks[:similarity_count]

    if shuffle:
        random.shuffle(similar_tracks)
        similar_tracks = similar_tracks[:count]

    track_list = []
    for track in similar_tracks:
        path = '%s%s' % (root, track['path'])
        track_list.append(cue.convert_to_cue_url(path))
        _LOGGER.debug('Path:%s %f' % (path, track['similarity']))

    meta_db.close()
    if get_value(params, 'format', '', isPost)=='text':
        return '\n'.join(track_list)
    else:
        return json.dumps(track_list)


def start_app(args, mus, config, jukebox_path):
    musly_app.init(args, mus, config, jukebox_path)
    _LOGGER.debug('Ready to process requests')
    musly_app.run(host=config['host'], port=config['port'])
