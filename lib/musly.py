'''
Musly - access libmusly functions
(c) 2017 R.S.U. / GPL v3 / https://www.nexus0.net/pub/sw/lmsmusly
(c) 2020 Caig Drummond - modified for use in musly-server
'''

import ctypes, math, random, pickle, sqlite3, logging
from collections import namedtuple
from sys import version_info
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process, Pipe
from . import metadata_db

if version_info < (3, 2):
    exit('Python 3 required')

__version__ = '0.0.3'

_LOGGER = logging.getLogger(__name__)
MUSLY_DECODER = b"libav"
MUSLY_METHOD = b"timbre"

MuslyTracksAdded = namedtuple("MuslyTracksAdded", "paths mtracks mtrackids")


class MuslyJukebox(ctypes.Structure):
    _fields_ = [("method", ctypes.c_void_p),
                ("method_name", ctypes.c_char_p),
                ("decoder", ctypes.c_void_p),
                ("decoder_name", ctypes.c_char_p)]


# This function is invoked when anlyzing tracks multi-threaded. libmusly does not seem to be
# thread safe - so we create a new process per-file to perform the analysis
def analyze_audiofile(pipe, libmusly, index, db_path, abs_path, extract_len, extract_start):
    musly = Musly(libmusly, True)
    result = musly.analyze_file(index, -1, db_path, abs_path, extract_len, extract_start)
    pipe.send({'ok':result['ok'], 'index':result['index'], 'track':pickle.dumps(bytes(result['mtrack']), protocol=4)})
    pipe.close()


class Musly(object):
    def __init__(self, libmusly, quiet=False):
        try:
            libresample = libmusly.replace('libmusly.', 'libmusly_resample.')
            ctypes.CDLL(libresample)
            if not quiet:
                _LOGGER.debug("Using: %s" % libresample)
        except:
            pass
        self.libmusly = libmusly
        self.mus = ctypes.CDLL(libmusly)
        if not quiet:
            _LOGGER.debug("Using: %s" % libmusly)

        # setup func calls

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
        
        if not quiet:
            _LOGGER.debug("musly init done")


    def jukebox_off(self):
        self.mus.musly_jukebox_poweroff (self.mj)


    def get_jukebox_binsize(self):
        return self.mus.musly_jukebox_binsize(self.mj, 1, -1)


    def write_jukebox(self, path):
        _LOGGER.debug("write_jukebox: jukebox path: {}".format(path))
        if self.mus.musly_jukebox_tofile(self.mj, ctypes.c_char_p(bytes(path, 'utf-8'))) == -1:
            _LOGGER.error("musly_jukebox_tofile failed (path: {})".format(path))
            return False
        return True


    def read_jukebox(self, path):
        _LOGGER.debug("Read jukebox: {}".format(path))
        #localmj = self.mus.musly_jukebox_poweron(self.method, self.decoder)
        localmj = self.mus.musly_jukebox_fromfile(ctypes.c_char_p(bytes(path, 'utf-8')))
        if localmj == None:
            _LOGGER.error("Failed to read juebox: {}".format(path))
            return None
        else:
            _LOGGER.info("Loaded {} tracks".format(self.mus.musly_jukebox_trackcount(localmj)))
            if self.mus.musly_jukebox_trackcount(localmj) == -1:
                return None
        return localmj


    def get_jukebox_from_file(self, path):
        localmj = self.read_jukebox(path)
        if localmj == None:
            return None
        numtracks = self.mus.musly_jukebox_trackcount(localmj)
        mtrackids_type = ctypes.c_int * numtracks
        mtrackids = mtrackids_type()
        #int musly_jukebox_gettrackids (musly_jukebox *  jukebox,musly_trackid *  trackids)
        self.mus.musly_jukebox_gettrackids.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(mtrackids_type)]
        if self.mus.musly_jukebox_gettrackids(localmj, ctypes.pointer(mtrackids)) == -1:
            _LOGGER.error("Failed to get track IDs from jukebox")
            return None
        self.jukebox_off()
        self.mj = localmj
        return mtrackids


    def get_track_db(self, scursor, path):
        scursor.execute('SELECT vals FROM tracks WHERE file=?', (path,))
        row = scursor.fetchone()
        if (row == None):
            _LOGGER.debug("Culd not find {} in DB".format(path))
            return None
        else:
            mtrack = self.mtrack_type()
            smt_c = ctypes.c_char_p(pickle.loads(row[0]))
            smt_f = ctypes.cast(smt_c, ctypes.POINTER(ctypes.c_float))
            ctypes.memmove(mtrack, smt_f, self.mtracksize)
            return mtrack


    def get_alltracks_db(self, scursor):
        scursor.execute('SELECT count(vals) FROM tracks')
        numtracks = scursor.fetchone()[0]
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

        return (paths, mtracks)


    def analyze_file(self, index, total, db_path, abs_path, extract_len, extract_start):
        mtrack = self.mtrack_type()
        if total>0:
            _LOGGER.debug("[{}/{} {}%] Analyze: {}".format(index+1, total, int((index+1)*100/total), db_path))
        if self.mus.musly_track_analyze_audiofile(self.mj, abs_path.encode(), extract_len, extract_start, mtrack) == -1:
            _LOGGER.error("musly_track_analyze_audiofile failed for {}".format(abs_path))
            return {'ok':False, 'index':index, 'mtrack':mtrack}
        else:
            return {'ok':True, 'index':index, 'mtrack':mtrack}


    def analyze_file_proc(self, index, total, db_path, abs_path, extract_len, extract_start):
        _LOGGER.debug("[{}/{} {}%] Analyze: {}".format(index+1, total, int((index+1)*100/total), db_path))
        pout, pin = Pipe(duplex=False)
        p = Process(target=analyze_audiofile, args=(pin, self.libmusly, index, db_path, abs_path, extract_len, extract_start))
        p.start()
        r = pout.recv()
        p.terminate()
        p.join()
        return r


    def analyze_files(self, meta_db, allfiles, extract_len = 60, extract_start = -48, num_threads=8):
        numtracks = len(allfiles)
        _LOGGER.info("Have {} files to analyze".format(numtracks))
        _LOGGER.info("Extraction length: {}s extraction start: {}s".format(extract_len, extract_start))
        
        futures_list = []
        inserts_since_commit = 0
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            for i in range(numtracks):
                if num_threads>1 and numtracks>1:
                    futures = executor.submit(self.analyze_file_proc, i, numtracks, allfiles[i]['db'], allfiles[i]['abs'], extract_len, extract_start)
                else:
                    futures = executor.submit(self.analyze_file, i, numtracks, allfiles[i]['db'], allfiles[i]['abs'], extract_len, extract_start)
                futures_list.append(futures)
            for future in futures_list:
                try:
                    result = future.result()
                    if result['ok']:
                        if 'mtrack' in result:
                            meta_db.get_cursor().execute('INSERT INTO tracks (file, vals) VALUES (?, ?)', (allfiles[result['index']]['db'], pickle.dumps(bytes(result['mtrack']), protocol=4)))
                        else:
                            meta_db.get_cursor().execute('INSERT INTO tracks (file, vals) VALUES (?, ?)', (allfiles[result['index']]['db'], result['track']))
                        inserts_since_commit += 1
                        if inserts_since_commit >= 500:
                            inserts_since_commit = 0
                            meta_db.commit()
                except Exception as e:
                    _LOGGER.debug("Thread exception? - %s" % str(e))
                    pass



    def add_tracks(self, mtracks, num_style_tracks_required, styletracks_method, meta_db):
        numtracks = len(mtracks)
        mtrackids_type = ctypes.c_int * numtracks
        mtrackids = mtrackids_type()
        mtracks_type = (ctypes.POINTER(self.mtrack_type)) * numtracks
        _LOGGER.debug("Numtracks = {}".format(numtracks))

        style_tracks = []
        if numtracks > num_style_tracks_required:
            if styletracks_method == 'albums':
                _LOGGER.debug('Select style track from each album')
                for album in meta_db.get_albums():
                    track = meta_db.get_sample_track(album)
                    if track is not None:
                        style_tracks.append(track-1) # SQLite rowids start from 1, we want from 0

                # If too many choose a random somple from these
                _LOGGER.debug('Num album style tracks: %d, required style tracks: %d' % (len(style_tracks), num_style_tracks_required))
                if len(style_tracks)>num_style_tracks_required:
                    _LOGGER.debug('Selecting %d random tracks from album style tracks' % num_style_tracks_required)
                    style_tracks = random.sample(style_tracks, k=num_style_tracks_required)
                # if too few, then add some random from remaining
                elif len(style_tracks)<num_style_tracks_required:
                    _LOGGER.debug('Choosing another %d tracks from DB' % (num_style_tracks_required-len(style_tracks)))
                    others = meta_db.get_other_sample_tracks(num_style_tracks_required-len(style_tracks), style_tracks)
                    for i in others:
                        style_tracks.append(i)
            elif styletracks_method == 'genres':
                genres = meta_db.get_genres()
                genres = sorted(genres, key=lambda k: -1*k['count'])
                for genre in genres:
                    if len(style_tracks)>=num_style_tracks_required:
                        break
                    amount = int(((genre['count']*1.0)/numtracks)*num_style_tracks_required)
                    if amount<1:
                        amount=1
                    _LOGGER.debug('Choosing %d %s track(s)' % (amount, genre['genre']))
                    tracks = meta_db.get_sample_genre_tracks(genre['genre'], amount)
                    if tracks is not None:
                        for track in tracks:
                            style_tracks.append(track)
                            if len(style_tracks)>=num_style_tracks_required:
                                break

                if len(style_tracks)<num_style_tracks_required:
                    _LOGGER.debug('Choosing another %d tracks from DB' % (num_style_tracks_required-len(style_tracks)))
                    others = meta_db.get_other_sample_tracks(num_style_tracks_required-len(style_tracks), style_tracks)
                    for i in others:
                        style_tracks.append(i)

        num_style_tracks = len(style_tracks)
        if num_style_tracks>0:
            _LOGGER.debug("Using subset (%d of %d) for setmusicstyle (chosen from meta db)" % (num_style_tracks, numtracks))

            snumtracks = num_style_tracks
            smtracks_type = (ctypes.POINTER(self.mtrack_type)) * num_style_tracks
            smtracks = smtracks_type()

            for i in range(num_style_tracks):
                smtracks[i] = mtracks[style_tracks[i]]
        elif numtracks > num_style_tracks_required:
            _LOGGER.debug("Using subset (%d of %d) for setmusicstyle" % (num_style_tracks_required, numtracks))
            snumtracks = num_style_tracks_required
            sample = random.sample(range(numtracks), k=num_style_tracks_required)
            smtracks_type = (ctypes.POINTER(self.mtrack_type)) * num_style_tracks_required
            smtracks = smtracks_type()
            i = 0
            for s in sample:
                smtracks[i] = mtracks[s]
                i += 1
        else:
            _LOGGER.debug("Using all tracks (%d) for setmusicstyle" % (numtracks))
            smtracks_type = mtracks_type
            smtracks = mtracks
            snumtracks = numtracks
        
        # int musly_jukebox_setmusicstyle (musly_jukebox * jukebox, musly_track **  tracks, int  num_tracks
        self.mus.musly_jukebox_setmusicstyle.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(smtracks_type), ctypes.c_int ]
        #int musly_jukebox_addtracks (musly_jukebox *  jukebox, musly_track **  tracks, musly_trackid *  trackids, int  num_tracks, int  generate_ids
        self.mus.musly_jukebox_addtracks.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(mtracks_type), ctypes.POINTER(mtrackids_type), ctypes.c_int, ctypes.c_int]

        if (self.mus.musly_jukebox_setmusicstyle(self.mj, ctypes.pointer(smtracks), ctypes.c_int(snumtracks)) == -1) :
            _LOGGER.error("musly_jukebox_setmusicstyle")
            return None
        else:
            if self.mus.musly_jukebox_addtracks(self.mj, ctypes.pointer(mtracks), ctypes.pointer(mtrackids), ctypes.c_int(numtracks), ctypes.c_int(1)) == -1:
                _LOGGER.error("musly_jukebox_addtracks")
                return None
            
        _LOGGER.info("Added {} tracks".format(numtracks))
        return mtrackids


    def get_similars(self, mtracks, mtrackids, seedtrackid):
        numtracks = len(mtracks)
        mtrackids_type = ctypes.c_int * numtracks
        mtracks_type = (ctypes.POINTER(self.mtrack_type)) * numtracks
        msims_type = ctypes.c_float * numtracks
        msims = msims_type()
        # int musly_jukebox_similarity (musly_jukebox *  jukebox, musly_track *  seed_track, musly_trackid  seed_trackid, musly_track **  tracks, musly_trackid *  trackids, int  num_tracks, float *  similarities 
        self.mus.musly_jukebox_similarity.argtypes = [ctypes.POINTER(MuslyJukebox), ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.POINTER(mtracks_type), ctypes.POINTER(mtrackids_type), ctypes.c_int, ctypes.POINTER(msims_type) ]

        seedtrack = mtracks[seedtrackid].contents

        if (self.mus.musly_jukebox_similarity(self.mj, seedtrack, ctypes.c_int(seedtrackid), ctypes.pointer(mtracks), ctypes.pointer(mtrackids), ctypes.c_int(numtracks), ctypes.pointer(msims))) == -1:
            _LOGGER.error("musly_jukebox_similarity")
            return None

        rtracks=[]
        for i in mtrackids:
            #_LOGGER.debug("get_similars: mtrack id: {:3} sim: {:8.6f}".format(i, msims[i]))
            rtracks.append({'id':i, 'sim':msims[i]})
        return sorted(rtracks, key=lambda k: k['sim'])

