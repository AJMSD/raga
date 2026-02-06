# Raga

A small utility that downloads music as MP3s using Spotify for metadata and yt-dlp for audio. It supports
three input modes and keeps your library organized while skipping instrumental versions and deduplicating
audio across the entire destination folder.

## What it does

- **songs.txt**: downloads each song to the root destination folder; if an artist has 2+ songs, it groups
  them into an artist folder and copies a placeholder image into that folder.
- **album.txt**: downloads each album into its own folder with `album_art.jpg` and the album tracklist.
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
  "Halo, Beyoncé"
]
```

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

### artist.txt
```txt
[
  "The Weeknd",
  "Daft Punk",
  "Beyoncé"
]
```

## Run

```bash
python song_retriever.py
```

## Outputs

- Albums: `DESTINATION_FOLDER/Album Name/`
  - `album_art.jpg`
  - `01 - Track Name.mp3`, ...
- Songs: `DESTINATION_FOLDER/Artist Name/` (only when 2+ songs by same artist)
  - `placeholder.jpg` is copied into that folder

## Placeholder image

Put `placeholder.jpg` next to `song_retriever.py` if you want it copied into artist folders for the
songs-only mode.
