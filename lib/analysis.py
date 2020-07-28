#
# Analyse files with Musly, and provide an API to retrieve similar tracks
#
# Copyright (c) 2020 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#

import logging
import os
import sqlite3
import tempfile
from . import cue, metadata_db, musly

_LOGGER = logging.getLogger(__name__)
AUDIO_EXTENSIONS = ['m4a', 'mp3', 'ogg', 'flac', 'opus']

def get_files_to_analyse(meta_db, lms_db, lms_path, path, files, musly_root_len, tmp_path, tmp_path_len, meta_only):
    if not os.path.exists(path):
        _LOGGER.error("'%s' does not exist" % path)
        return
    if os.path.isdir(path):
        for e in sorted(os.listdir(path)):
            get_files_to_analyse(meta_db, lms_db, lms_path, os.path.join(path, e), files, musly_root_len, tmp_path, tmp_path_len, meta_only)
    elif path.rsplit('.', 1)[1].lower() in AUDIO_EXTENSIONS:
        if os.path.exists(path.rsplit('.', 1)[0]+'.cue'):
            for track in cue.get_cue_tracks(lms_db, lms_path, path, musly_root_len, tmp_path):
                if meta_only or not meta_db.file_already_analysed(track['file'][tmp_path_len:]):
                    files.append({'abs':track['file'], 'db':track['file'][tmp_path_len:], 'track':track, 'src':path})
        elif meta_only or not meta_db.file_already_analysed(path[musly_root_len:]):
            files.append({'abs':path, 'db':path[musly_root_len:]})


def analyse_files(mus, config, path, remove_tracks, meta_only, jukebox):
    _LOGGER.debug('Analyse %s' % path)
    meta_db = metadata_db.MetadataDb(config)
    lms_db = sqlite3.connect(config['lmsdb']) if 'lmsdb' in config else None
        
    files = []
    musly_root_len = len(config['paths']['musly'])
    lms_path = config['paths']['lms']
    temp_dir = config['paths']['tmp'] if 'tmp' in config['paths'] else None
    removed_tracks = meta_db.remove_old_tracks(config['paths']['musly']) if remove_tracks and not meta_only else False

    with tempfile.TemporaryDirectory(dir=temp_dir) as tmp_path:
        _LOGGER.debug('Temp folder: %s' % tmp_path)
        get_files_to_analyse(meta_db, lms_db, lms_path, path, files, musly_root_len, tmp_path+'/', len(tmp_path)+1, meta_only)
        _LOGGER.debug('Num tracks to update: %d' % len(files))
        cue.split_cue_tracks(files, config['threads'])
        added_tracks = len(files)>0
        if added_tracks or removed_tracks:
            roots = [config['paths']['musly'], tmp_path+'/']
            if added_tracks and not meta_only:
                mus.analyze_files(meta_db.get_cursor(), files, roots, num_threads=config['threads'])
            if removed_tracks or (added_tracks and not meta_only):
                (paths, db_tracks) = mus.get_alltracks_db(meta_db.get_cursor())
                mus.add_tracks(db_tracks, config['styletracks'])
            if added_tracks:
                _LOGGER.debug('Save metadata')
                for file in files:
                    meta_db.set_metadata(file)
            meta_db.commit()
            meta_db.close()
            if removed_tracks or not meta_only:
                mus.write_jukebox(jukebox)
    _LOGGER.debug('Finished analysis')
