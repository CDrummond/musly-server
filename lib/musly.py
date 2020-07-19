'''
Musly - access libmusly functions
(c) 2017 R.S.U. / GPL v3 / https://www.nexus0.net/pub/sw/lmsmusly
'''

import ctypes, math, random, pickle, sqlite3, logging, os
from collections import namedtuple
from sys import version_info
from concurrent.futures import ThreadPoolExecutor

if version_info < (3, 2):
    exit('Python 3 required')

__version__ = '0.0.3'

_LOGGER = logging.getLogger(__name__)
MUSLY_DECODER = b"libav"
MUSLY_METHOD = b"timbre"
MUSLY_MUSTYLENUMTRACKS = 1000

MuslyTracksAdded = namedtuple("MuslyTracksAdded", "paths mtracks mtrackids")

class MuslyJukebox(ctypes.Structure):
    _fields_ = [("method", ctypes.c_void_p),
                ("method_name", ctypes.c_char_p),
                ("decoder", ctypes.c_void_p),
                ("decoder_name", ctypes.c_char_p)]

class Musly(object):
    def __init__(self, libmusly):
        ctypes.CDLL(libmusly.replace('libmusly.so', 'libmusly_resample.so'))
        self.mus = ctypes.CDLL(libmusly)

        # setup func calls
        self.mus.musly_debug.argtypes = [ctypes.c_int]
        self.mus.musly_version.restype = ctypes.c_char_p
        self.mus.musly_jukebox_listmethods.restype = ctypes.c_char_p
        self.mus.musly_jukebox_listdecoders.restype = ctypes.c_char_p
        self.mus.musly_jukebox_aboutmethod.argtypes = [ctypes.POINTER(MuslyJukebox)]
        self.mus.musly_jukebox_aboutmethod.restype = ctypes.c_char_p

        self.mus.musly_jukebox_trackcount.argtypes = [ctypes.POINTER(MuslyJukebox)]
        # int musly_track_size (musly_jukebox *  jukebox   )
        self.mus.musly_track_size.argtypes = [ctypes.POINTER(MuslyJukebox)]
        # int musly_track_binsize (musly_jukebox *  jukebox) 
        self.mus.musly_track_binsize.argtypes = [ctypes.POINTER(MuslyJukebox)]
        # int musly_track_tobin (musly_jukebox *  jukebox,musly_track *  from_track, unsigned char *  to_buffer
        self.mus.musly_track_tobin.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(ctypes.c_float), ctypes.c_char_p ]
        # int musly_track_frombin (musly_jukebox *  jukebox, unsigned char *  from_buffer, musly_track *  to_track
        self.mus.musly_track_frombin.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.c_char_p, ctypes.POINTER(ctypes.c_float)]
        # int musly_jukebox_binsize (musly_jukebox *  jukebox, int  header, int  num_tracks
        self.mus.musly_jukebox_binsize.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.c_int, ctypes.c_int ]
        #int musly_jukebox_tofile (musly_jukebox * jukebox, const char *  filename)
        self.mus.musly_jukebox_tofile.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.c_char_p ]
        # musly_jukebox* musly_jukebox_fromfile (const char *  filename)
        self.mus.musly_jukebox_fromfile.argtypes = [ctypes.c_char_p ]
        self.mus.musly_jukebox_fromfile.restype = ctypes.POINTER(MuslyJukebox)

        # musly_jukebox* musly_jukebox_poweron (const char *  method, const char *  decoder)
        self.mus.musly_jukebox_poweron.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.mus.musly_jukebox_poweron.restype = ctypes.POINTER(MuslyJukebox)
        self.mus.musly_jukebox_poweroff.argtypes =[ctypes.POINTER(MuslyJukebox) ]

        # int musly_track_analyze_audiofile (musly_jukebox *  jukebox, const char *  audiofile, float excerpt_length, float  excerpt_start, musly_track *  track 
        self.mus.musly_track_analyze_audiofile.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.c_char_p, ctypes.c_float, ctypes.c_float, ctypes.POINTER(ctypes.c_float)]

        # init
        self.decoder = ctypes.c_char_p(MUSLY_DECODER);
        self.method = ctypes.c_char_p(MUSLY_METHOD)

        self.mj = self.mus.musly_jukebox_poweron(self.method, self.decoder)
        self.mtrackbinsize = self.mus.musly_track_binsize(self.mj)
        self.mtracksize = self.mus.musly_track_size(self.mj)
        self.mtrack_type = ctypes.c_float * math.ceil(self.mtracksize/ctypes.sizeof(ctypes.c_float()))
        
        self.stats = {}
        self.stats['aboutmethod'] = self.mus.musly_jukebox_aboutmethod(self.mj).decode()
        self.stats['musly_version'] = self.mus.musly_version().decode()
        self.stats['numtracks'] = 0
        self.stats['simtracks'] = 0
        _LOGGER.debug("musly init done")

    def jukebox_off(self):
        self.mus.musly_jukebox_poweroff (self.mj)

    def get_stats(self):
        return self.stats
    def set_stat(self, sname, svalue):
        self.stats[sname] = svalue
    def get_stat(self, sname):
        return self.stats[sname]
    def increase_stat(self, sname, svalue):
        self.stats[sname] += svalue

    def get_numtracks(self):
        return self.mus.musly_jukebox_trackcount(self.mj)

    def get_jukebox_binsize(self):
        return self.mus.musly_jukebox_binsize(self.mj, 1, -1)
    def write_jukebox(self, path):
        _LOGGER.debug("write_jukebox: jukebox path: {}".format(path))
        if self.mus.musly_jukebox_tofile(self.mj, ctypes.c_char_p(bytes(path, 'utf-8'))) == -1:
            _LOGGER.error("musly_jukebox_tofile failed (path: {})".format(path))
            return False
        return True
    def read_jukebox(self, path):
        _LOGGER.debug("read_jukebox: jukebox path: {}".format(path))
        #localmj = self.mus.musly_jukebox_poweron(self.method, self.decoder)
        localmj = self.mus.musly_jukebox_fromfile(ctypes.c_char_p(bytes(path, 'utf-8')))
        if localmj == None:
            _LOGGER.error("musly_jukebox_fromfile failed (path: {})".format(path))
            return None
        else:
            _LOGGER.info("read_jukebox: musly_jukebox_fromfile: loaded {} tracks".format(self.mus.musly_jukebox_trackcount(localmj)))
            if self.mus.musly_jukebox_trackcount(localmj) == -1:
                return None
        return localmj
    def get_jukebox_from_file(self, path):
        localmj = self.read_jukebox(path)
        if localmj == None:
            _LOGGER.error("get_jukebox_from_file failed at read_jukebox()")
            return None
        numtracks = self.mus.musly_jukebox_trackcount(localmj)
        mtrackids_type = ctypes.c_int * numtracks
        mtrackids = mtrackids_type()
        #int musly_jukebox_gettrackids (musly_jukebox *  jukebox,musly_trackid *  trackids)
        self.mus.musly_jukebox_gettrackids.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(mtrackids_type)]
        if self.mus.musly_jukebox_gettrackids(localmj, ctypes.pointer(mtrackids)) == -1:
            _LOGGER.error("get_jukebox_from_file failed at musly_jukebox_gettrackids")
            return None
        _LOGGER.debug("get_jukebox_from_file: success")
        self.jukebox_off()
        self.mj = localmj
        return mtrackids

    def get_track_db(self, scursor, path):
        scursor.execute('SELECT vals FROM tracks WHERE file=?', (path,))
        row = scursor.fetchone()
        if(row == None):
            _LOGGER.debug("get_track_db: not found: {}".format(path))
            return None
        else:
            mtrack = self.mtrack_type()
            smt_c = ctypes.c_char_p(pickle.loads(row[0]))
            smt_f = ctypes.cast(smt_c, ctypes.POINTER(ctypes.c_float))
            ctypes.memmove(mtrack, smt_f, self.mtracksize)
            _LOGGER.debug("get_track_db: found: {} ({})".format(path, repr(mtrack)))
            return mtrack


    def get_alltracks_db(self, scursor):
        scursor.execute('SELECT count(vals) FROM tracks')
        numtracks = scursor.fetchone()[0]
        _LOGGER.debug("get_tracks_db: {} rows".format(numtracks))
        mtrack = self.mtrack_type()
        mtracks_type = (ctypes.POINTER(self.mtrack_type)) * numtracks
        mtracks = mtracks_type()

        scursor.execute('SELECT file, vals FROM tracks')
        i = 0
        paths = [None] * numtracks
        for row in scursor:
#            _LOGGER.debug("get_alltracks_db [{:4}]: {}".format(i, row[0]))
            paths[i] = row[0]
            smt_c = ctypes.c_char_p(pickle.loads(row[1]))
            smt_f = ctypes.cast(smt_c, ctypes.POINTER(ctypes.c_float))
            ctypes.memmove(mtrack, smt_f, self.mtracksize)
            mtracks[i] = ctypes.pointer(mtrack)
            mtrack = self.mtrack_type()
            i += 1

        if len(paths) > 0:
            self.set_stat('commonpath', os.path.commonpath(paths))
        else:
            self.set_stat('commonpath', 'N/A')

        return (paths, mtracks)


    def analyze_file(self, index, db_path, abs_path, extract_len, extract_start):
        mtrack = self.mtrack_type()
        _LOGGER.debug("analyze_files: analyze: {}".format(db_path))
        if self.mus.musly_track_analyze_audiofile(self.mj, abs_path.encode(), extract_len, extract_start, mtrack) == -1:
            _LOGGER.error("musly_track_analyze_audiofile failed for {}".format(abs_path))
            return {'ok':False, 'index':index, 'mtrack':mtrack}
        else:
            return {'ok':True, 'index':index, 'mtrack':mtrack}

                
    def analyze_files(self, scursor, allfiles, get_from_db = False, extract_len = 120, extract_start = -48, num_threads=8):
        numtracks = len(allfiles)
        _LOGGER.info("analyze_files: {} files to analyze".format(numtracks))
        _LOGGER.info("analyze_files: extraction length: {}s extraction start: {}s".format(extract_len, extract_start))

        mtracks_type = (ctypes.POINTER(self.mtrack_type)) * numtracks
        analyzed_tracks = mtracks_type()
        
        futures_list = []
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            for i in range(numtracks):
                futures = executor.submit(self.analyze_file, i, allfiles[i]['db'], allfiles[i]['abs'], extract_len, extract_start)
                futures_list.append(futures)
            for future in futures_list:
                try:
                    result = future.result()
                    if result['ok']:
                        scursor.execute('INSERT INTO tracks VALUES (NULL,?,NULL,NULL,NULL,?)', (allfiles[result['index']]['db'], pickle.dumps(bytes(result['mtrack']), protocol=4)))
                    analyzed_tracks[result['index']]=ctypes.pointer(result['mtrack'])
                except Exception as e:
                    _LOGGER.debug("Thread exception? - %s" % str(e))
                    pass
        return analyzed_tracks


    def add_tracks(self, mtracks):
        numtracks = len(mtracks)
        mtrackids_type = ctypes.c_int * numtracks
        mtrackids = mtrackids_type()
        mtracks_type = (ctypes.POINTER(self.mtrack_type)) * numtracks
        _LOGGER.debug("add_tracks: numtracks = {}".format(numtracks))
        _LOGGER.debug("add_tracks: mtracks = {}".format(repr(mtracks)))

        if numtracks > MUSLY_MUSTYLENUMTRACKS:
            _LOGGER.debug("add_tracks: using subset for setmusicstyle")
            snumtracks = MUSLY_MUSTYLENUMTRACKS
            sample = random.sample(range(numtracks), k=MUSLY_MUSTYLENUMTRACKS)
            smtracks_type = (ctypes.POINTER(self.mtrack_type)) * MUSLY_MUSTYLENUMTRACKS
            smtracks = smtracks_type()
            i = 0
            for s in sample:
                #_LOGGER.debug("add_tracks: subset setmusicstyle: {}".format(s))
                smtracks[i] = mtracks[s]
                i += 1
        else:
            smtracks_type = mtracks_type
            smtracks = mtracks
            snumtracks = numtracks
        smtracks_type = mtracks_type
        smtracks = mtracks
        snumtracks = numtracks
        
        # int musly_jukebox_setmusicstyle (musly_jukebox * jukebox, musly_track **  tracks, int  num_tracks
        self.mus.musly_jukebox_setmusicstyle.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(smtracks_type), ctypes.c_int ]
        #int musly_jukebox_addtracks (musly_jukebox *  jukebox, musly_track **  tracks, musly_trackid *  trackids, int  num_tracks, int  generate_ids
        self.mus.musly_jukebox_addtracks.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(mtracks_type), ctypes.POINTER(mtrackids_type), ctypes.c_int, ctypes.c_int]

        if (self.mus.musly_jukebox_setmusicstyle(self.mj, ctypes.pointer(smtracks), ctypes.c_int(snumtracks)) == -1) :
            _LOGGER.error("musly_jukebox_setmusicstyle")
            return False
        else:
            if self.mus.musly_jukebox_addtracks(self.mj, ctypes.pointer(mtracks), ctypes.pointer(mtrackids), ctypes.c_int(numtracks), ctypes.c_int(1)) == -1:
                _LOGGER.error("musly_jukebox_addtracks")
                return None
            
        _LOGGER.info("add_tracks: added {} tracks".format(numtracks))
        self.set_stat('numtracks', self.stats['numtracks'] + numtracks)
        return mtrackids


    def get_similars(self, mtracks, mtrackids, seedtrackid, rnumtracks):
        numtracks = len(mtracks)
        mtrackids_type = ctypes.c_int * numtracks
        mtracks_type = (ctypes.POINTER(self.mtrack_type)) * numtracks
        msims_type = ctypes.c_float * numtracks
        msims = msims_type()
        rsims_type = ctypes.c_float * rnumtracks
        rsims = rsims_type()
        rtrackids_type = ctypes.c_int * rnumtracks
        rtrackids = rtrackids_type()
        # int musly_jukebox_similarity (musly_jukebox *  jukebox, musly_track *  seed_track, musly_trackid  seed_trackid, musly_track **  tracks, musly_trackid *  trackids, int  num_tracks, float *  similarities 
        self.mus.musly_jukebox_similarity.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.POINTER(mtracks_type), ctypes.POINTER(mtrackids_type), ctypes.c_int, ctypes.POINTER(msims_type) ]
        # musly_findmin(const float* values, const musly_trackid* ids, int count, float* min_values, musly_trackid* min_ids, int min_count, int ordered) 
        self.mus.musly_findmin.argtypes = [ctypes.POINTER(msims_type), ctypes.POINTER(mtrackids_type), ctypes.c_int, ctypes.POINTER(rsims_type), ctypes.POINTER(rtrackids_type), ctypes.c_int, ctypes.c_int ]

        seedtrack = mtracks[seedtrackid].contents

#        for t in mtracks:
#            _LOGGER.debug("get_similars: mtrack = {}".format(repr(t.contents)))

        _LOGGER.debug("get_similars: seedtrack = {} numres={}".format(repr(seedtrack), rnumtracks))

        if (self.mus.musly_jukebox_similarity(self.mj, seedtrack, ctypes.c_int(seedtrackid), ctypes.pointer(mtracks), ctypes.pointer(mtrackids), ctypes.c_int(numtracks), ctypes.pointer(msims))) == -1:
            _LOGGER.error("musly_jukebox_similarity")
            return (None, None)
        else:
#            for i in mtrackids:
#                _LOGGER.debug("get_similars: mtrack id: {:3} sim: {:8.6f}".format(i, msims[i]))
            if (self.mus.musly_findmin(ctypes.pointer(msims), ctypes.pointer(mtrackids), ctypes.c_int(numtracks),  ctypes.pointer(rsims), ctypes.pointer(rtrackids), ctypes.c_int(rnumtracks), ctypes.c_int(1))) == -1:
                _LOGGER.error("musly_findmin")
                return (None, None)

        self.set_stat('simtracks', self.stats['simtracks'] + rnumtracks)
        return (rtrackids, rsims)
