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
http://HOST:11000/api/similar?track=/path/of/track&track=/path/of/another/track&count=10&filtergenre=1&min=30&max=600&ignore=/path/to/ignore&filterxmas=1&exclude=ArtistA&exclude=ArtistB
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

`ignore` may be used to list tracks to ignore (e.g. tracks that are already in
the queue). This parameter, like `track`, may be repeated multiple times.

`exclude` may be used to lsit artists to ignpre. This parameter, like `track`,
may be repeated multiple times.

The API will try query Musly for 25 times the specified `count` tracks (default
of 5) for each supplied seed track. (This is to allow for filtering on genre,
etc). Initally the API will ignore musly tracks from the same artist or album of
the seed tracks (and any previous in the list). If, because of this filtering,
there are less than the requested amount then the highest similarty tracks from
the filtered-out list are chosen. Finally all tracks are sorted by similarity,
with the most similar first.

This API is intended to be used by [LMS Musly DSTM Mixer](https://github.com/CDrummond/lms-muslymixer)

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

## Credits

`lib/musly.py` (which is used as a python interface to the musly library) is
taken, and modified, from [Musly Integration for the Logitech Media Server](https://www.nexus0.net/pub/sw/lmsmusly)
