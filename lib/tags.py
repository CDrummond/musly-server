import json
import logging

_LOGGER = logging.getLogger(__name__)

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


def read_tags(path, genre_separator):
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4

    try:
        audio = MP4(path)
        tags = {'artist':str(audio['\xa9ART'][0]), 'album':str(audio['\xa9alb'][0]), 'duration':int(audio.info.length)}
        if 'aART' in audio:
            tags['albumartist']=str(audio['aART'][0])
        if '\xa9gen' in audio:
            tags['genres']=[]
            for g in audio['\xa9gen']:
                tags['genres'].append(str(g))
        _LOGGER.debug('MP4 File:%s Meta:%s' % (path, json.dumps(tags)))
        return tags
    except:
        pass

    try:
        audio = MP3(path)
        tags = {'artist':str(audio['TPE1']), 'album':str(audio['TALB']), 'duration':int(audio.info.length)}
        if 'TPE2' in audio:
            tags['albumartist']=str(audio['TPE2'])
        if 'TCON' in audio:
            tags['genres']=str(audio['TCON']).split(genre_separator)
        _LOGGER.debug('MP3 File:%s Meta:%s' % (path, json.dumps(tags)))
        return tags
    except Exception as e:
        print("EX:%s" % str(e))
        pass

    try:
        audio = ID3(path)
        tags = {'artist':str(audio['TPE1']), 'album':str(audio['TALB']), 'duration':0}
        if 'TPE2' in audio:
            tags['albumartist']=str(audio['TPE2'])
        if 'TCON' in audio:
            tags['genres']=str(audio['TCON']).split(genre_separator)
        _LOGGER.debug('ID3 File:%s Meta:%s' % (path, json.dumps(tags)))
        return tags
    except:
        pass

    audio = get_ogg_or_flac(path)
    if audio:
        tags = {'artist':str(audio['ARTIST'][0]), 'album':str(audio['ALBUM'][0]), 'duration':int(audio.info.length)}
        if 'ALBUMARTIST' in audio:
            tags['albumartist']=str(audio['ALBUMARTIST'][0])
        if 'GENRE' in audio:
            tags['genres']=[]
            for g in audio['GENRE']:
                tags['genres'].append(str(g))
        _LOGGER.debug('OGG File:%s Meta:%s' % (path, json.dumps(tags)))
        return tags

    _LOGGER.debug('File:%s Meta:NONE' % path)
    return None