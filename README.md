# Raga

A small utility that downloads music as MP3s using Spotify for metadata and yt-dlp for audio. It supports
four input modes and keeps your library organized while skipping instrumental versions and deduplicating
audio across the entire destination folder.

## What it does

- **songs.txt**: resolves each entry to a Spotify **single** and downloads it into its own folder with
  `cover.jpg` and the single tracklist.
- **album.txt**: downloads each album into its own folder with `cover.jpg` and the album tracklist.
- **playlist.txt**: downloads each playlist into `Playlists/<Playlist Name>/` and writes `playlist.m3u`
  in track order for playlist-friendly library views (for example, Jellyfin).
- **artist.txt**: downloads *all* albums and singles for each artist into per-album folders with art.
- **No instrumentals**: filters obvious instrumental/karaoke results by name.
- **Deduplication**: skips any new download whose audio content already exists anywhere in the destination.
- If an artist returns no results, check the `country` used in Spotify queries; some artists are more visible in their local market.

## Requirements

- Python 3.9+
- ffmpeg available on PATH (for MP3 conversion and metadata)

## Install

```bash
pip install -r requirements.txt
```

## Configuration (.env)

Create a `.env` file alongside `song_retriever.py`:

```
CLIENT_ID=your_spotify_client_id
CLIENT_SECRET=your_spotify_client_secret
DESTINATION_FOLDER=C:/Music/DownloadedMusic
```

Notes:
- `DESTINATION_FOLDER` can be absolute or relative to the script folder.
- If omitted, it defaults to `DownloadedMusic` inside the project folder.
- Set `DEBUG=1` to enable verbose debug logging.

## Caching
To speed up deduplication, the script writes a hidden cache file named `.audio_hashes txt` in the destination folder. It stores file hashes and timestamps so subsequent runs avoid re-hashing every file. Delete this file to force a full rebuild.

## Input files (pick one)

Place **one** of these files next to `song_retriever.py`:

### songs.txt
```txt
[
  "Blinding Lights, The Weeknd",
  "bad guy, Billie Eilish",
  "Halo, Beyoncé",
  "3n3Ppam7vgaVa1iaRUc9Lp",
  "spotify:track:3n3Ppam7vgaVa1iaRUc9Lp",
  "https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp"
]
```

You can use a song name, Spotify track ID, Spotify URI, or Spotify track URL.
If an artist is provided with a song name or ID, the script validates that the song includes that artist.
Each song entry resolves to a single release and downloads in album-style layout.

### album.txt
```txt
[
  "After Hours, The Weeknd",
  "Random Access Memories, Daft Punk",
  "1989",
  "4aawyAB9vmqN3uQ7FjRGTy",
  "spotify:album:4aawyAB9vmqN3uQ7FjRGTy",
  "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"
]
```

You can use an album name, Spotify album ID, Spotify URI, or Spotify album URL.
If an artist is provided with an album name or ID, the script validates that the album includes that artist.

### playlist.txt
```txt
[
  "Lofi Beats",
  "4rnleEAOdmFAbRcNCgZMpY",
  "spotify:playlist:4rnleEAOdmFAbRcNCgZMpY",
  "https://open.spotify.com/playlist/4rnleEAOdmFAbRcNCgZMpY",
  "Lofi Beats, spotify"
]
```

You can use a playlist name, Spotify playlist ID, Spotify URI, or Spotify playlist URL.
If an owner is provided with a playlist name or ID, the script validates that owner by Spotify user ID or display name.

### artist.txt
```txt
[
  "The Weeknd",
  "Daft Punk",
  "Beyoncé",
  "1Xyo4u8uXC1ZmMpatF05PJ",
  "spotify:artist:1Xyo4u8uXC1ZmMpatF05PJ",
  "https://open.spotify.com/artist/1Xyo4u8uXC1ZmMpatF05PJ"
]
```

You can use an artist name, Spotify artist ID, Spotify URI, or Spotify artist URL.

## Run

```bash
python song_retriever.py
```

## Outputs

- Albums: `DESTINATION_FOLDER/Album Name/`
  - `cover.jpg`
  - `01 - Track Name.mp3`, ...
- Singles (from `songs.txt`): `DESTINATION_FOLDER/Single Name/`
  - `cover.jpg`
  - `01 - Track Name.mp3`, ...
- Playlists: `DESTINATION_FOLDER/Playlists/Playlist Name/`
  - `cover.jpg`
  - `playlist.m3u`
  - `001 - Artist - Track.mp3`, ...
