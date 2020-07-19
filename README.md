# Musy API Server

Simple python3 API server to create a mix of music tracks for LMS.

## Musly

This service uses the [Musly audio music similarity library](https://github.com/dominikschnitzer/musly)
This library needs to be compiled, and `config.json` (from this API project)
updated to store the location of this library. This repo contains pre-built
buit versions for:

1. Fedora32 64-bit
2. Raspbian Buster 32-bit

## Analysing Tracks

Before starting the server your music needs to be analysed with Musly. This is
accomplished via:

```
./musly-server.py --analyse /path/to/music/folder
```

This takes about 1 hour to process 20k tracks. However, about 20 minutes of that
is creating the 'Musly jukebox' file. If re-run only new tracks will be added,
but the juekbox will need to be recreated, and hence _another_ 20 minutes. To
remove tracks you will need to use an SQLite browser to manually remove entries.

## Similarity API 

The API server can be installed as a Systemd service, or started manually:

```
./musly-server.py
```

...when the service starts, it will confirm that the number of traks in its
SQLite database is the same as the number in the 'Musly jukebox'. If the
number differs, the jukebox is recreated (which can take ~20mins)

Only 1 API is currently supported:

```
http://HOST:11000/api/similar?track=/path/of/track&track=/path/of/another/track&count=10
```
...this will get 10 similar tracks to those supplied. The API will try query
Musly for 5 times the specified `count` tracks (default of 5) for each supplied
seed track. Initally the API will ignore musly tracks from the same artist or
album of the seed tracks (and any previous in the list). If, because of this
filtering, there are less than the requested amount then the highest similarty
tracks from the filtered-out list are chosen. Finally all tracks are sorted by
similarity, with the most similar first.

This API is intended to be used by [LMS Musly DSTM Mixer](https://github.com/CDrummond/lms-muslymixer)

## Credits

`lib/musly.py` (which is used as a python interface to the musly library) is
taken, and modified, from [Musly Integration for the Logitech Media Server](https://www.nexus0.net/pub/sw/lmsmusly)
