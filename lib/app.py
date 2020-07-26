#
# Analyse files with Musly, and provide an API to retrieve simialr tracks
#
# Copyright (c) 2020 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#

import argparse
from datetime import datetime
import json
import logging
import os
import sqlite3
from flask import Flask, abort, request
from . import cue, filters, metadata_db, musly

_LOGGER = logging.getLogger(__name__)

DEFAULT_TRACKS_TO_RETURN      = 5  # Number of tracks to return, if none specified
MIN_TRACKS_TO_RETURN          = 5  # Min value for 'count' parameter
MAX_TRACKS_TO_RETURN          = 50 # Max value for 'count' parameter
MAX_IGNORE_TRACKS_FILTER_META = 15 # How many of tracks in 'ignore' list should we also filter on metadata?
NUM_SIMILAR_TRACKS_FACTOR     = 25 # Request count*NUM_SIMILAR_TRACKS_FACTOR frmo musly

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
        meta_db.close()
        ids = None

        # If we can, load musly from jukebox...
        if os.path.exists(jukebox_path):
            ids = self.mus.get_jukebox_from_file(jukebox_path)

        if ids==None or len(ids)!=len(tracks):
            _LOGGER.debug('Adding tracks from DB to musly')
            ids = mus.add_tracks(tracks, app_config['styletracks'])
            self.mus.write_jukebox(jukebox_path)
        
        self.mta=musly.MuslyTracksAdded(paths, tracks, ids)

    def get_config(self):
        return self.app_config

    def get_musly(self):
        return self.mus

    def get_mta(self):
        return self.mta
    
musly_app = MuslyApp(__name__)

@musly_app.route('/api/similar')
def similar_api():
    params = request.args.to_dict(flat=False)

    if not 'track' in params:
        abort(400)
    tracks = params['track']

    count = int(params['count'][0]) if 'count' in params else DEFAULT_TRACKS_TO_RETURN
    if count < MIN_TRACKS_TO_RETURN:
        count = MIN_TRACKS_TO_RETURN
    elif count > MAX_TRACKS_TO_RETURN:
        count = MAX_TRACKS_TO_RETURN

    match_genre = 'filtergenre' in params and params['filtergenre'][0]=='1'
    min_duration = int(params['min'][0]) if 'min' in params else 0
    max_duration = int(params['max'][0]) if 'max' in params else 0
    exclude_christmas = 'filterxmas' in params and params['filterxmas'][0]=='1' and datetime.now().month!=12

    mta = musly_app.get_mta()
    mus = musly_app.get_musly()
    config = musly_app.get_config()
    meta_db = metadata_db.MetadataDb(config)

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
    filtered_by_ignore_tracks=[]
    
    # Artist/album of seed tracks
    seed_metadata=[]
    seed_genres=[]
    
    # Artist/album of chosen tracks
    current_metadata_keys={}
    current_metadata=[]

    if min_duration>0 or max_duration>0:
        _LOGGER.debug('Duration:%d .. %d' % (min_duration, max_duration))

    # Musly IDs of seed tracks
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
            meta = meta_db.get_metadata(track_id+1) # IDs in SQLite are 1.. musly is 0..
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

    ignore_track_ids = []
    ignore_metadata = []
    if 'ignore' in params:
        for track in params['ignore']:
            if track.startswith(root):
                track=track[len(root):]
            # Check that musly knows about this track
            track_id = -1
            try:
                track_id = mta.paths.index(track)
                if track_id is not None and track_id>=0:
                    ignore_track_ids.append(track_id)
                    meta = meta_db.get_metadata(track_id+1) # IDs in SQLite are 1.. musly is 0..
                    if meta:
                        ignore_metadata.append(meta)
            except:
                pass
        if len(ignore_metadata) > MAX_IGNORE_TRACKS_FILTER_META:
            ignore_metadata=ignore_metadata[-MAX_IGNORE_TRACKS_FILTER_META:]
        _LOGGER.debug('Have %d tracks to ignore' % len(ignore_track_ids))

    exclude_artists = []
    do_exclude_artists = False
    if 'exclude' in params:
        for artist in params['exclude']:
            exclude_artists.append(artist.strip())
        do_exclude_artists = len(exclude_artists)>0
        _LOGGER.debug('Have %d artists to ignore %s' % (len(exclude_artists), exclude_artists))

    if match_genre:
        _LOGGER.debug('Seed genres: %s' % seed_genres)

    for track_id in track_ids:
        # Query musly for similar tracks
        ( resp_ids, resp_similarity ) = mus.get_similars( mta.mtracks, mta.mtrackids, track_id, (count*NUM_SIMILAR_TRACKS_FACTOR)+1 )
        accepted_tracks = 0
        for i in range(1, len(resp_ids)):
            if not resp_ids[i] in track_ids and not resp_ids[i] in ignore_track_ids and not resp_ids[i] in similar_track_ids and resp_similarity[i]>0.0:
                similar_track_ids.append(resp_ids[i])

                meta = meta_db.get_metadata(resp_ids[i]+1) # IDs in SQLite are 1.. musly is 0..
                if 'ignore' in meta and meta['ignore']:
                    _LOGGER.debug('DISCARD(ignore) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                elif (min_duration>0 or max_duration>0) and not filters.check_duration(min_duration, max_duration, meta):
                    _LOGGER.debug('DISCARD(duration) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                elif match_genre and not filters.genre_matches(config, seed_genres, meta):
                    _LOGGER.debug('DISCARD(genre) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                elif exclude_christmas and filters.is_christmas(meta):
                    _LOGGER.debug('DISCARD(xmas) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                elif do_exclude_artists and filters.match_artist(exclude_artists, meta):
                    _LOGGER.debug('DISCARD(artist) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                else:
                    if filters.same_artist_or_album(seed_metadata, meta):
                        _LOGGER.debug('FILTERED(seeds) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                        filtered_by_seeds_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
                    elif filters.same_artist_or_album(current_metadata, meta):
                        _LOGGER.debug('FILTERED(current) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                        filtered_by_current_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
                    elif filters.same_artist_or_album(ignore_metadata, meta):
                        _LOGGER.debug('FILTERED(ignore) ID:%d Path:%s Similarity:%f Meta:%s' % (resp_ids[i], mta.paths[resp_ids[i]], resp_similarity[i], json.dumps(meta)))
                        filtered_by_ignore_tracks.append({'path':mta.paths[resp_ids[i]], 'similarity':resp_similarity[i]})
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

    # Too few tracks? Add some from the filtered lists
    if len(similar_tracks)<count and len(filtered_by_ignore_tracks)>0:
        filtered_by_ignore_tracks = sorted(filtered_by_ignore_tracks, key=lambda k: k['similarity'])
        similar_tracks = similar_tracks + filtered_by_ignore_tracks[:count-len(similar_tracks)]
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
        track_list.append(cue.convert_to_cue_url(path))
        _LOGGER.debug('Path:%s' % path)

    meta_db.close()
    if 'format' in params and 'text'==params['format'][0]:
        return '\n'.join(track_list)
    else:
        return json.dumps(track_list)


def start_app(args, mus, config, jukebox_path):
    musly_app.init(args, mus, config, jukebox_path)
    _LOGGER.debug('Ready to process requests')
    musly_app.run(host=config['host'], port=config['port'])
