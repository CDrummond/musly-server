# Musy API Server

Simple python3 API server to create a mix of music tracks for LMS.

## Musly

This service uses the [Musly audio music similarity library](https://github.com/dominikschnitzer/musly)
This library needs to be compiled, and `config.json` (from this API project)
updated to store the location of this library. This repo contains pre-built
built versions for:

1. Fedora32 64-bit
2. Raspbian Buster 32-bit

## Analysing Tracks

Before starting the server your music needs to be analysed with Musly. This is
accomplished via:

```
./musly-server.py --analyse /path/to/music/folder
```

This takes about 50 minutes to process 20k tracks. The process anylsys tracks,
add them to musly, intialises musly's 'jukebox' style with 1000 random tracks,
and extracts certain tags. If re-run new tracks will be added, and old
(non-existant) will be removed. Pass `--keep-old` to keep these old tracks.

To analyse the musly path stored in the config file, the following shortcut can
be used:

```
./musly-server.py --analyse m
```

### CUE files

If the analysis locates a music file with a similarly named CUE file (e.g.
`artist/album/album name.flac` and `artist/album/album name.cue`) then it will
read the track listing from the LMS db file and use `ffmpeg` to split the
music file into temporary 128kbps MP3 files for analysis. The files are removed
once analysis is complete.

## Similarity API 

The API server can be installed as a Systemd service, or started manually:

```
./musly-server.py
```

...when the service starts, it will confirm that the number of tracks in its
SQLite database is the same as the number in the 'Musly jukebox'. If the
number differs, the jukebox is recreated.

Only 1 API is currently supported:

```
http://HOST:11000/api/similar?track=/path/of/track&track=/path/of/another/track&count=10&filtergenre=1&min=30&max=600&filterxmas=1
```
...this will get 10 similar tracks to those supplied.

If `filtergenre=1` is supplied then only tracks whose genre matches a
pre-configured set of genres (mapped from seed tracks) will be used. e.g. if
`["Heavy Metal", "Metal", "Power Metal"]` is defined in the config, and a seed
tack's genre has `Metal` then only tracks with one of these 3 genres will be
considered.

If `filterxmas=1` is supplied, then tracks with 'Christmas' or 'Xmas' in their
genres will be excluded - unless it is December.

`min` and `max` can be used to set the minimum, and maximum, duration (in
seconds) of tracks to be considered.

`previous` may be used to list tracks currently in the play queue. This
parameter, like `track`, may be repeated multiple times. These tracks will be
used to filter chosen tracks on artist, or album, to prevent duplicates.

`maxsim` (range 0..100) can be used to set the maximum similarity factor. A
factor of 0 would imply the track is identical, 100 completely different. 75 is
the default value.

`shuffle` if set to `1` will cause extra tracks to be located, this list
shuffled, and then the desired `count` tracks taken from this shuffled list.

The API will try query Musly for 25 times the specified `count` tracks (default
of 5) for each supplied seed track. (This is to allow for filtering on genre,
etc). Initally the API will ignore musly tracks from the same artist or album of
the seed tracks (and any previous in the list, any albums from the 25
`previous` tracks, or albums from the last 15 `previous` tracks). If, because of
this filtering, there are less than the requested amount then the highest
similarity tracks from the filtered-out lists are chosen. Finally all tracks are
sorted by similarity, with the most similar first.

Metadata for tracks is stored in an SQLite database, this has an `ignore` column
which if set to `1` will cause the API to not use this track if it is returned
as a similar track by musly. In this way you can exclude specific tracks from
being added to mixes - but if they are already in the queue, then they can sill
be used as seed tracks.

This API is intended to be used by [LMS Music Similarity Plugin](https://github.com/CDrummond/lms-musicsimilarity)

Genres are configured via the `genres` section of `config.json`, using the
following syntax:

```
{
 "genres:[
  [ "Rock", "Hard Rock", "Metal" ],
  [ "Pop", "Dance", "R&B"]
 ]
}
```

If a seed track has `Hard Rock` as its genre, then only tracks with `Rock`,
`Hard Rock`, or `Metal` will be allowed. If a seed track has a genre that is not
listed here then any track returned by Musly, that does not cotaiain any genre
lsited here, will be considered acceptable. Therefore, if seed is `Pop` then
a `Hard Rock` track would not be considered.

### HTTP Post

Alternatively, the API may be accessed via a HTTP POST call. To do this, the
params of the call are passed as a JSON object. eg.

```
{
 "track":["/path/trackA.mp3", "/path/trackB.mp3"],
 "filtergenre":1,
 "count":10
}
```

## Configuration

The sever reads its configuration from a JSON file (default name is `config.json`).
This has the following format:

```
{
 "libmusly":"lib/x86-64/fedora32/libmusly.so",
 "paths":{
  "db":"/home/user/.local/share/musly/",
  "musly":"/home/Music/",
  "lms":"/media/Music/",
  "tmp":"/tmp/"
 },
 "lmsdb":"/path/to/lms/Cache/library.db",
 "genres":[
  ["Alternative Rock", "Classic Rock", "Folk/Rock", "Hard Rock", "Indie Rock", "Punk Rock", "Rock"],
  ["Dance", "Disco", "Hip-Hop", "Pop", "Pop/Folk", "Pop/Rock", "R&B", "Reggae", "Soul", "Trance"],
  ["Gothic Metal", "Heavy Metal", "Power Metal", "Progressive Metal", "Progressive Rock", "Symphonic Metal", "Symphonic Power Metal"]
 ],
 "ignoregenre":["Artist"],
 "normalize":{
  "artist":["feet", "ft", "featuring"],
  "album":["deluxe edition", "remastered"],
  "title"["demo", "radio edit"]
 },
 "port":10000,
 "host":"0.0.0.0",
 "threads":8,
 "jukeboxtracks":1000
}
```

* `libmusly` should contain the path the musy shared library - path is relative
to `musly-server.py`
* `paths.db` should be the path where the SQLite and jukebox files created by
this app can be written
* `paths.musly` should be the path where musly can access your music files. This
can be different to `path.lms` if you are running analysis on a different
machine to where you would run the script as the API server. This script will
only store the paths relative to this location - eg. `paths.musly=/home/music/`
then `/home/music/A/b.mp3` will be stored as `A/b.mp3`.
* `paths.musly` should be the path where LMS access your music files. The API
server will remove this path from API calls, so that it can look up tracks in
its database by their relative path.
* `paths.tmp` When analysing music, this script will create a temporary folder
to hold separate CUE file tracks. The path passed here needs to be writable.
This config item is only used for analysis.
* `lmsdb` During analysis, this script will also analyse individual CUE tracks.
To do this it needs access to the LMS database file to know the position of each
track, etc. This config item should hole the path to the LMS database file. This
is only required for analysis, and only if you have CUE files. `ffmpeg` is
required to split tracks.
* `genres` This is as described above.
* `ignoregenre` List of artists where genre filtering (excluding christmas)
should be ignored. To apply to all artists, use '*' - e.g. `"ignoregenre":"*"`
* `normalize.artist` List of strings to split artist names, e.g. "A ft. B"
becomes "A" (periods are automatically removed)
* `normalize.album` List of strings to remove from album names.
* `normalize.title` List of strings to remove from titles.
* `port` This is the port number the API is accessible on.
* `host` IP addres on which the API will listen on. Use `0.0.0.0` to listen on
all interfaces on your network.
* `threads` Number of threads to use during analysis phase. This controls how
many calls to `ffmpeg` are made concurrently, and how many concurrent tracks
musly is asked to analyse. Defaults to CPU count, if not set.
* `styletracks` A  subset of tracks is passed to musly's `setmusicstyle`
function, by default 1000 random tracks is chosen. This config item can be used
to alter this. Note, however, the larger the number here the longer it takes to
for this call to complete. As a rough guide it takes ~1min per 1000 tracks.
If you change this config item after the jukebox is written you will need to
delete the jukebox file and restart the server.


## Credits

`lib/musly.py` (which is used as a python interface to the musly library) is
taken, and modified, from [Musly Integration for the Logitech Media Server](https://www.nexus0.net/pub/sw/lmsmusly)
