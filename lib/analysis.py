#
# Analyse files with Musly, and provide an API to retrieve simialr tracks
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


def analyse_files(mus, config, path, meta_only, jukebox):
    _LOGGER.debug('Analyse %s' % path)
    meta_db = metadata_db.MetadataDb(config)
    lms_db = sqlite3.connect(config['lmsdb']) if 'lmsdb' in config else None
        
    files = []
    musly_root_len = len(config['paths']['musly'])
    lms_path = config['paths']['lms']

    temp_dir = config['paths']['tmp'] if 'tmp' in config['paths'] else None
    with tempfile.TemporaryDirectory(dir=temp_dir) as tmp_path:
        _LOGGER.debug('Temp folder: %s' % tmp_path)
        get_files_to_analyse(meta_db, lms_db, lms_path, path, files, musly_root_len, tmp_path+'/', len(tmp_path)+1, meta_only)
        _LOGGER.debug('Num files: %d' % len(files))
        cue.split_cue_tracks(files, config['threads'])
        if (len(files)>0):
            roots = [config['paths']['musly'], tmp_path+'/']
            if not meta_only:
                tracks = mus.analyze_files(meta_db.get_cursor(), files, roots, num_threads=config['threads'])
                mus.add_tracks(tracks, config['styletracks'])
            _LOGGER.debug('Save metadata')
            for file in files:
                meta_db.set_metadata(file)
            meta_db.commit()
            meta_db.close()
            if not meta_only:
                mus.write_jukebox(jukebox)
    _LOGGER.debug('Finished analysis')
