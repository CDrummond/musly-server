#!/usr/bin/env python3

#
# Analyse files with Musly, and provide an API to retrieve similar tracks
#
# Copyright (c) 2020-2021 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#

import argparse
import logging
import os
from lib import analysis, app, config, metadata_db, musly, version

JUKEBOX_FILE = 'musly.jukebox'
_LOGGER = logging.getLogger(__name__)
        
if __name__=='__main__':
    parser = argparse.ArgumentParser(description='Musly API Server (v%s)' % version.MUSLY_SERVER_VERSION)
    parser.add_argument('-c', '--config', type=str, help='Config file (default: config.json)', default='config.json')
    parser.add_argument('-l', '--log-level', action='store', choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'], default='INFO', help='Set log level (default: %(default)s)')
    parser.add_argument('-a', '--analyse', metavar='PATH', type=str, help="Analyse file/folder (use 'm' for configured musly folder)", default='')
    parser.add_argument('-m', '--meta-only', action='store_true', default=False, help='Update metadata database only (used in conjuction with --analyse)')
    parser.add_argument('-k', '--keep-old', action='store_true', default=False, help='Do not remove non-existant tracks from DB (used in conjuction with --analyse)')
    args = parser.parse_args()
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=args.log_level, datefmt='%Y-%m-%d %H:%M:%S')
    cfg = config.read_config(args.config, args.analyse)
    _LOGGER.debug('Init DB')
    lib = cfg['libmusly']
    if not lib.startswith('/'):
        lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), lib)
    _LOGGER.debug('Init Musly')
    mus = musly.Musly(lib)
    jukebox_file = os.path.join(cfg['paths']['db'], JUKEBOX_FILE)
    if args.analyse:
        path = cfg['paths']['musly'] if args.analyse =='m' else args.analyse
        analysis.analyse_files(mus, cfg, path, not args.keep_old, args.meta_only, jukebox_file)
    else:
        app.start_app(args, mus, cfg, jukebox_file)

