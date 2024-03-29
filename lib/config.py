#
# Analyse files with Musly, and provide an API to retrieve similar tracks
#
# Copyright (c) 2020-2021 Craig Drummond <craig.p.drummond@gmail.com>
# GPLv3 license.
#

import json
import logging
import os
from . import metadata_db

_LOGGER = logging.getLogger(__name__)

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

    for key in config['paths']:
        if not config['paths'][key].endswith('/'):
            config['paths'][key]=config['paths'][key]+'/'

    if 'tmp' in config['paths'] and not os.path.exists(config['paths']['tmp']):
        _LOGGER.error("'%s' does not exist" % config['paths']['tmp'])
        exit(-1)

    if not 'port' in config:
        config['port']=11000

    if not 'host' in config:
        config['host']='0.0.0.0'

    if not 'threads' in config:
        config['threads']=os.cpu_count()

    if not 'extractlen' in config:
        config['extractlen']=30

    if not 'extractstart' in config:
        config['extractstart']=-48

    if not 'styletracks' in config:
        config['styletracks']=1000

    if not 'styletracksmethod' in config:
        config['styletracksmethod']='genres'

    if 'genres' in config:
        config['all_genres']=[]
        for genres in config['genres']:
            for g in genres:
                if not g in config['all_genres']:
                    config['all_genres'].append(g)

    if 'ignoregenre' in config:
        if isinstance(config['ignoregenre'], list):
            ignore=[]
            for item in config['ignoregenre']:
                ignore.append(metadata_db.normalize_artist(item))
            config['ignoregenre']=ignore
        else:
            config['ignoregenre']=[config['ignoregenre']]

    if 'normalize' in config:
        metadata_db.set_normalize_options(config['normalize'])

    return config
