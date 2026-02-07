import os
import json
import ast
import shutil
import requests
import hashlib
import time
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import dotenv
import yt_dlp

dotenv.load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

# Spotify auth setup
client_credentials_manager = SpotifyClientCredentials(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET
)
sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "DownloadedMusic")
SONGS_FILE = os.path.join(SCRIPT_DIR, "songs.txt")
ALBUMS_FILE = os.path.join(SCRIPT_DIR, "album.txt")
PLAYLISTS_FILE = os.path.join(SCRIPT_DIR, "playlist.txt")
ARTISTS_FILE = os.path.join(SCRIPT_DIR, "artist.txt")
PLACEHOLDER_IMAGE = os.path.join(SCRIPT_DIR, "placeholder.jpg")


def get_output_folder():
    env_path = os.getenv("DESTINATION_FOLDER", "").strip()
    if env_path:
        if not os.path.isabs(env_path):
            env_path = os.path.abspath(os.path.join(SCRIPT_DIR, env_path))
        return env_path
    return DEFAULT_OUTPUT_FOLDER

AUDIO_EXTS = (".mp3", ".m4a", ".webm", ".opus")

DOWNLOAD_ATTEMPTS = 3
IMAGE_DOWNLOAD_ATTEMPTS = 3
SPOTIFY_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 30
DEBUG = os.getenv("DEBUG", "0").strip() == "1"
HASH_CACHE_FILENAME = ".audio_hashes.txt"
HASH_CACHE_STATE = None

INSTRUMENTAL_KEYWORDS = [
    "instrumental",
    "karaoke",
    "backing track",
    "no vocals",
]
SPOTIFY_ID_REGEX = r"[A-Za-z0-9]{22}"


def debug(message):
    if DEBUG:
        print(f"[DEBUG] {message}")


def download_image(url, path):
    """Download the album image at roughly 300x300."""
    if not url:
        return
    debug(f"Downloading image: {url} -> {path}")
    for attempt in range(1, IMAGE_DOWNLOAD_ATTEMPTS + 1):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
                debug(f"Image saved: {path}")
                return
            print(f"Image download failed (status {r.status_code}): {url}")
        except requests.RequestException as exc:
            print(f"Image download error (attempt {attempt}): {exc}")
        if attempt < IMAGE_DOWNLOAD_ATTEMPTS:
            time.sleep(RETRY_SLEEP_SECONDS)


def sanitize_filename(name):
    """Make filename safe for most filesystems."""
    invalid_chars = '\\/:*?"<>|'
    for ch in invalid_chars:
        name = name.replace(ch, "_")
    return name.strip()


def normalize_name(text):
    return "".join(ch for ch in text.lower().strip() if ch.isalnum() or ch.isspace())


def strip_quotes(text):
    text = text.strip()
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1].strip()
    return text


def extract_spotify_id(text, entity_type):
    value = (text or "").strip()
    if not value:
        return ""

    prefix = f"spotify:{entity_type}:"
    if value.lower().startswith(prefix):
        value = value[len(prefix):]
    else:
        match = re.search(
            rf"open\.spotify\.com/{entity_type}/({SPOTIFY_ID_REGEX})",
            value,
            re.IGNORECASE,
        )
        if match:
            value = match.group(1)

    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    if re.fullmatch(SPOTIFY_ID_REGEX, value):
        return value
    return ""


def parse_list_file(path, allow_commas_in_items):
    """Parse a list from a text file."""
    if not os.path.exists(path):
        return []
    debug(f"Parsing list file: {path}")
    content = ""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []

    for parser in (json.loads, ast.literal_eval):
        try:
            data = parser(content)
        except Exception:
            data = None
        if data is None:
            continue
        if isinstance(data, list):
            items = []
            for item in data:
                if item is None:
                    continue
                item_text = str(item).strip()
                if item_text:
                    items.append(item_text)
            return items
        if isinstance(data, str) and data.strip():
            return [data.strip()]

    text = content
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]

    if allow_commas_in_items:
        lines = [line.strip().strip(",") for line in text.splitlines() if line.strip()]
        if lines:
            return [strip_quotes(line) for line in lines]
        if text:
            return [strip_quotes(text)]
        return []

    parts = [part.strip() for part in text.split(",") if part.strip()]
    return [strip_quotes(part) for part in parts]


def is_instrumental_text(text):
    if not text:
        return False
    lower = text.lower()
    return any(keyword in lower for keyword in INSTRUMENTAL_KEYWORDS)


def yt_match_filter(info, *, incomplete):
    if incomplete:
        return None
    title = info.get("title") or ""
    if is_instrumental_text(title):
        return "instrumental or karaoke"
    return None


def unique_folder_path(base_dir, folder_name):
    candidate = os.path.join(base_dir, folder_name)
    if not os.path.exists(candidate):
        return candidate
    counter = 2
    while True:
        candidate = os.path.join(base_dir, f"{folder_name} ({counter})")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def unique_base_path(base_path):
    candidate = base_path
    counter = 2
    while any(
        os.path.exists(candidate + ext)
        for ext in AUDIO_EXTS
    ):
        candidate = f"{base_path} ({counter})"
        counter += 1
    return candidate


def unique_file_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 2
    candidate = f"{base} ({counter}){ext}"
    while os.path.exists(candidate):
        counter += 1
        candidate = f"{base} ({counter}){ext}"
    return candidate


def build_ydl_opts(out_base_path):
    debug(f"Building yt-dlp options for: {out_base_path}")
    return {
        "format": "bestaudio/best",
        "quiet": False,
        "noplaylist": True,
        "default_search": "ytsearch1",
        "match_filter": yt_match_filter,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "socket_timeout": REQUEST_TIMEOUT_SECONDS,
        "retry_sleep": RETRY_SLEEP_SECONDS,
        "outtmpl": f"{out_base_path}.%(ext)s",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {
                "key": "FFmpegMetadata"
            },
        ],
    }


def find_downloaded_file(out_base_path):
    for ext in AUDIO_EXTS:
        candidate = out_base_path + ext
        if os.path.exists(candidate):
            return candidate
    return None


def hash_file(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_audio_hash_index(base_dir):
    hashes = set()
    if not os.path.isdir(base_dir):
        return hashes, None, {}
    debug(f"Building audio hash index for: {base_dir}")
    cache_path = os.path.join(base_dir, HASH_CACHE_FILENAME)
    cache = load_hash_cache(cache_path, base_dir)
    new_cache = {}
    for root, _, files in os.walk(base_dir):
        for filename in files:
            if filename == HASH_CACHE_FILENAME:
                continue
            if not filename.lower().endswith(AUDIO_EXTS):
                continue
            path = os.path.join(root, filename)
            rel_path = os.path.relpath(path, base_dir)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            cached = cache.get(rel_path)
            if cached and cached[1] == stat.st_size and cached[2] == stat.st_mtime:
                file_hash = cached[0]
            else:
                try:
                    file_hash = hash_file(path)
                except OSError:
                    continue
            hashes.add(file_hash)
            new_cache[rel_path] = (file_hash, stat.st_size, stat.st_mtime)
    save_hash_cache(cache_path, new_cache)
    debug(f"Indexed {len(hashes)} audio files")
    return hashes, cache_path, new_cache


def load_hash_cache(cache_path, base_dir):
    cache = {}
    if not os.path.exists(cache_path):
        return cache
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t", 3)
                if len(parts) != 4:
                    continue
                file_hash, rel_path, size_text, mtime_text = parts
                try:
                    size = int(size_text)
                    mtime = float(mtime_text)
                except ValueError:
                    continue
                cache[rel_path] = (file_hash, size, mtime)
    except OSError as exc:
        debug(f"Failed to read hash cache: {exc}")
    return cache


def save_hash_cache(cache_path, cache):
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            for rel_path in sorted(cache.keys()):
                file_hash, size, mtime = cache[rel_path]
                f.write(f"{file_hash}\t{rel_path}\t{size}\t{mtime}\n")
    except OSError as exc:
        debug(f"Failed to write hash cache: {exc}")


def set_hash_cache_state(base_dir, cache_path, entries):
    global HASH_CACHE_STATE
    if not cache_path:
        HASH_CACHE_STATE = None
        return
    HASH_CACHE_STATE = {
        "base_dir": base_dir,
        "path": cache_path,
        "entries": entries,
    }


def update_hash_cache(file_path, file_hash):
    if not HASH_CACHE_STATE:
        return
    base_dir = HASH_CACHE_STATE["base_dir"]
    cache_path = HASH_CACHE_STATE["path"]
    entries = HASH_CACHE_STATE["entries"]
    try:
        rel_path = os.path.relpath(file_path, base_dir)
        stat = os.stat(file_path)
    except OSError as exc:
        debug(f"Failed to stat for cache update: {exc}")
        return
    entries[rel_path] = (file_hash, stat.st_size, stat.st_mtime)
    try:
        with open(cache_path, "a", encoding="utf-8") as f:
            f.write(f"{file_hash}\t{rel_path}\t{stat.st_size}\t{stat.st_mtime}\n")
    except OSError as exc:
        debug(f"Failed to append to hash cache: {exc}")


def spotify_call(func, *args, **kwargs):
    for attempt in range(1, SPOTIFY_ATTEMPTS + 1):
        try:
            debug(f"Spotify call attempt {attempt}: {getattr(func, '__name__', 'call')}")
            return func(*args, **kwargs)
        except Exception as exc:
            print(f"Spotify request failed (attempt {attempt}): {exc}")
            if attempt < SPOTIFY_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
    return None


def download_audio(search_query, out_base_path, known_hashes):
    ydl_opts = build_ydl_opts(out_base_path)
    debug(f"Audio search: {search_query}")
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([search_query])
            break
        except Exception as exc:
            print(f"Audio download error (attempt {attempt}): {exc}")
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
            else:
                return None
    downloaded = find_downloaded_file(out_base_path)
    if not downloaded:
        debug("No downloaded file found after yt-dlp run.")
        return None
    debug(f"Downloaded file: {downloaded}")
    try:
        file_hash = hash_file(downloaded)
    except OSError:
        return downloaded
    if file_hash in known_hashes:
        print("Duplicate audio detected, removing:", downloaded)
        try:
            os.remove(downloaded)
        except OSError:
            pass
        return None
    known_hashes.add(file_hash)
    update_hash_cache(downloaded, file_hash)
    return downloaded


def get_artist(artist_name):
    artist_key = (artist_name or "").strip()
    artist_id = extract_spotify_artist_id(artist_key)
    if artist_id:
        artist = spotify_call(sp.artist, artist_id)
        if not artist:
            print(f"No artist found for artist ID '{artist_id}'")
            return None
        return artist

    results = spotify_call(sp.search, q=f"artist:{artist_key}", type="artist", limit=1)
    if not results:
        print(f"Spotify search failed for artist '{artist_key}'")
        return None
    items = results.get("artists", {}).get("items", [])
    if not items:
        print(f"No artist found for '{artist_key}'")
        return None
    return items[0]


def get_all_albums(artist_id):
    albums = []
    seen = set()
    offset = 0
    debug(f"Fetching albums for artist ID: {artist_id}")
    while True:
        response = spotify_call(
            sp.artist_albums,
            artist_id,
            album_type="album,single",
            country="IN",
            limit=50,
            offset=offset,
        )
        if not response:
            break
        items = response.get("items", [])
        if not items:
            break
        for album in items:
            album_id = album.get("id")
            if not album_id or album_id in seen:
                continue
            seen.add(album_id)
            albums.append(album)
        if len(items) < 50:
            break
        offset += 50
    return albums


def get_album_tracks(album_id):
    tracks = []
    offset = 0
    debug(f"Fetching tracks for album ID: {album_id}")
    while True:
        response = spotify_call(sp.album_tracks, album_id, limit=50, offset=offset)
        if not response:
            break
        items = response.get("items", [])
        if not items:
            break
        tracks.extend(items)
        if len(items) < 50:
            break
        offset += 50
    return tracks


def download_album_tracks(artist_display_name, album, known_hashes, base_output_folder):
    album_name = sanitize_filename(album.get("name", "Unknown Album"))
    album_folder = unique_folder_path(base_output_folder, album_name)
    os.makedirs(album_folder, exist_ok=True)
    debug(f"Album folder: {album_folder}")

    images = album.get("images") or []
    if images:
        chosen_image = images[-2] if len(images) >= 2 else images[0]
        image_path = os.path.join(album_folder, "cover.jpg")
        download_image(chosen_image.get("url", ""), image_path)
        print(f"Downloaded album art for {album_name}")

    tracks = get_album_tracks(album.get("id"))
    if not tracks:
        print(f"No tracks found for album: {album_name}")
        return
    debug(f"Track count for album '{album_name}': {len(tracks)}")

    for track in tracks:
        track_name = track.get("name") or ""
        if not track_name:
            continue
        if is_instrumental_text(track_name):
            print(f"Skipping instrumental track: {track_name}")
            continue

        track_number = track.get("track_number") or 0
        track_safe = sanitize_filename(track_name)
        if track_number:
            base_name = f"{track_number:02d} - {track_safe}"
        else:
            base_name = track_safe
        out_base = unique_base_path(os.path.join(album_folder, base_name))

        search_query = f"{artist_display_name} - {track_name}"
        print(f"Downloading track: {track_name}")
        download_audio(search_query, out_base, known_hashes)


def download_all_albums_for_artist(artist_name, known_hashes, base_output_folder):
    artist = get_artist(artist_name)
    if not artist:
        return

    artist_display_name = artist.get("name", artist_name)
    artist_id = artist.get("id")
    print(f"Found artist: {artist_display_name} (ID: {artist_id})")

    albums = get_all_albums(artist_id)
    if not albums:
        print(f"No albums found for artist: {artist_display_name}")
        return

    print(f"Found {len(albums)} albums for {artist_display_name}")
    for album in albums:
        album_title = album.get("name", "Unknown Album")
        print(f"Downloading album: {album_title}")
        download_album_tracks(
            artist_display_name, album, known_hashes, base_output_folder
        )


def parse_song_entry(entry):
    entry = entry.strip()
    if not entry:
        return "", ""
    if "," in entry:
        song, artist = entry.split(",", 1)
        return song.strip(), artist.strip()
    return entry, ""


def parse_album_entry(entry):
    entry = entry.strip()
    if not entry:
        return "", ""
    if "," in entry:
        album, artist = entry.split(",", 1)
        return album.strip(), artist.strip()
    return entry, ""


def parse_playlist_entry(entry):
    entry = entry.strip()
    if not entry:
        return "", ""
    if "," in entry:
        playlist, owner = entry.split(",", 1)
        return playlist.strip(), owner.strip()
    return entry, ""


def extract_spotify_track_id(text):
    return extract_spotify_id(text, "track")


def extract_spotify_album_id(text):
    return extract_spotify_id(text, "album")


def extract_spotify_playlist_id(text):
    return extract_spotify_id(text, "playlist")


def extract_spotify_artist_id(text):
    return extract_spotify_id(text, "artist")


def album_has_artist(album, artist_name):
    if not artist_name:
        return True
    artists = album.get("artists") or []
    target = normalize_name(artist_name)
    if not target:
        return True
    for artist in artists:
        name = artist.get("name", "")
        if not name:
            continue
        candidate = normalize_name(name)
        if candidate == target or target in candidate:
            return True
    return False


def track_has_artist(track, artist_name):
    if not artist_name:
        return True
    artists = track.get("artists") or []
    target = normalize_name(artist_name)
    if not target:
        return True
    for artist in artists:
        name = artist.get("name", "")
        if not name:
            continue
        candidate = normalize_name(name)
        if candidate == target or target in candidate:
            return True
    return False


def playlist_has_owner(playlist, owner_name):
    if not owner_name:
        return True
    owner = playlist.get("owner") or {}
    target = normalize_name(owner_name)
    if not target:
        return True
    owner_id = normalize_name(owner.get("id", ""))
    owner_display = normalize_name(owner.get("display_name", ""))
    if owner_id and (owner_id == target or target in owner_id):
        return True
    if owner_display and (owner_display == target or target in owner_display):
        return True
    return False


def search_albums_by_name(album_name):
    albums = []
    seen = set()
    offset = 0
    debug(f"Searching albums by name: {album_name}")
    while True:
        results = spotify_call(
            sp.search, q=f"album:{album_name}", type="album", limit=50, offset=offset
        )
        if not results:
            break
        items = results.get("albums", {}).get("items", [])
        if not items:
            break
        for album in items:
            album_id = album.get("id")
            if not album_id or album_id in seen:
                continue
            seen.add(album_id)
            albums.append(album)
        if len(items) < 50:
            break
        offset += 50
    return albums


def get_album_by_name(album_name, artist_name):
    items = search_albums_by_name(album_name)
    if not items:
        print(f"No album found for '{album_name}'")
        return None
    debug(f"Found {len(items)} album candidates for '{album_name}'")
    if artist_name:
        for album in items:
            if album_has_artist(album, artist_name):
                return album
        print(f"No album found for '{album_name}' with artist '{artist_name}'")
        return None
    return items[0]


def get_album_by_id(album_id):
    album = spotify_call(sp.album, album_id)
    if not album:
        print(f"No album found for album ID '{album_id}'")
        return None
    return album


def get_track_by_id(track_id):
    track = spotify_call(sp.track, track_id)
    if not track:
        print(f"No track found for track ID '{track_id}'")
        return None
    return track


def search_playlists_by_name(playlist_name):
    playlists = []
    seen = set()
    offset = 0
    debug(f"Searching playlists by name: {playlist_name}")
    while True:
        results = spotify_call(
            sp.search,
            q=f"playlist:{playlist_name}",
            type="playlist",
            limit=50,
            offset=offset,
        )
        if not results:
            break
        items = results.get("playlists", {}).get("items", [])
        if not items:
            break
        for playlist in items:
            playlist_id = playlist.get("id")
            if not playlist_id or playlist_id in seen:
                continue
            seen.add(playlist_id)
            playlists.append(playlist)
        if len(items) < 50:
            break
        offset += 50
    return playlists


def get_playlist_by_name(playlist_name, owner_name):
    items = search_playlists_by_name(playlist_name)
    if not items:
        print(f"No playlist found for '{playlist_name}'")
        return None
    debug(f"Found {len(items)} playlist candidates for '{playlist_name}'")
    if owner_name:
        for playlist in items:
            if playlist_has_owner(playlist, owner_name):
                return playlist
        print(
            f"No playlist found for '{playlist_name}' with owner '{owner_name}'"
        )
        return None
    return items[0]


def get_playlist_by_id(playlist_id):
    playlist = spotify_call(sp.playlist, playlist_id)
    if not playlist:
        print(f"No playlist found for playlist ID '{playlist_id}'")
        return None
    return playlist


def get_playlist_tracks(playlist_id):
    tracks = []
    offset = 0
    debug(f"Fetching tracks for playlist ID: {playlist_id}")
    while True:
        response = spotify_call(
            sp.playlist_items,
            playlist_id,
            additional_types=("track",),
            limit=100,
            offset=offset,
        )
        if not response:
            break
        items = response.get("items", [])
        if not items:
            break
        for item in items:
            track = item.get("track") or {}
            if track.get("type") != "track":
                continue
            if not track.get("id"):
                continue
            tracks.append(track)
        if len(items) < 100:
            break
        offset += 100
    return tracks


def write_playlist_m3u(playlist_folder, entries):
    if not entries:
        return
    playlist_path = os.path.join(playlist_folder, "playlist.m3u")
    try:
        with open(playlist_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for title, rel_path in entries:
                f.write(f"#EXTINF:-1,{title}\n")
                f.write(f"{rel_path}\n")
        print(f"Wrote playlist file: {playlist_path}")
    except OSError as exc:
        print(f"Failed to write playlist.m3u: {exc}")


def download_playlist_tracks(playlist, known_hashes, base_output_folder):
    playlist_name = sanitize_filename(playlist.get("name", "Unknown Playlist"))
    playlists_root = os.path.join(base_output_folder, "Playlists")
    os.makedirs(playlists_root, exist_ok=True)
    playlist_folder = unique_folder_path(playlists_root, playlist_name)
    os.makedirs(playlist_folder, exist_ok=True)
    debug(f"Playlist folder: {playlist_folder}")

    images = playlist.get("images") or []
    if images:
        chosen_image = images[-2] if len(images) >= 2 else images[0]
        image_path = os.path.join(playlist_folder, "cover.jpg")
        download_image(chosen_image.get("url", ""), image_path)
        print(f"Downloaded playlist art for {playlist_name}")

    tracks = get_playlist_tracks(playlist.get("id"))
    if not tracks:
        print(f"No tracks found for playlist: {playlist_name}")
        return
    debug(f"Track count for playlist '{playlist_name}': {len(tracks)}")

    playlist_entries = []
    for index, track in enumerate(tracks, start=1):
        track_name = (track.get("name") or "").strip()
        if not track_name:
            continue
        if is_instrumental_text(track_name):
            print(f"Skipping instrumental track: {track_name}")
            continue

        artist_name_display = resolve_artist_display(track, "Unknown Artist")
        search_query = (
            f"{artist_name_display} - {track_name}"
            if artist_name_display else track_name
        )
        artist_safe = sanitize_filename(artist_name_display) if artist_name_display else "Unknown"
        track_safe = sanitize_filename(track_name)
        base_name = f"{index:03d} - {artist_safe} - {track_safe}"
        out_base = unique_base_path(os.path.join(playlist_folder, base_name))

        print(f"Downloading playlist track: {track_name}")
        downloaded_path = download_audio(search_query, out_base, known_hashes)
        if not downloaded_path:
            continue
        rel_path = os.path.relpath(downloaded_path, playlist_folder).replace("\\", "/")
        playlist_entries.append((f"{artist_name_display} - {track_name}", rel_path))

    write_playlist_m3u(playlist_folder, playlist_entries)


def download_playlists_from_list(entries, known_hashes, base_output_folder):
    for entry in entries:
        playlist_name, owner_name = parse_playlist_entry(entry)
        if not playlist_name:
            continue

        playlist_id = extract_spotify_playlist_id(playlist_name)
        if playlist_id:
            playlist = get_playlist_by_id(playlist_id)
            if not playlist:
                continue
            if owner_name and not playlist_has_owner(playlist, owner_name):
                print(
                    f"Playlist ID '{playlist_id}' does not match owner '{owner_name}'"
                )
                continue
        else:
            playlist = get_playlist_by_name(playlist_name, owner_name)
        if not playlist:
            continue

        print(f"Downloading playlist: {playlist.get('name', playlist_name)}")
        download_playlist_tracks(playlist, known_hashes, base_output_folder)


def resolve_artist_display(item, fallback):
    artists = item.get("artists") or []
    if artists:
        names = [artist.get("name", "").strip() for artist in artists if artist.get("name")]
        if names:
            return ", ".join(names)
    return fallback or "Unknown Artist"


def download_albums_from_list(entries, known_hashes, base_output_folder):
    for entry in entries:
        album_name, artist_name = parse_album_entry(entry)
        if not album_name:
            continue
        if is_instrumental_text(album_name):
            print(f"Skipping instrumental album entry: {album_name}")
            continue

        album_id = extract_spotify_album_id(album_name)
        if album_id:
            album = get_album_by_id(album_id)
            if not album:
                continue
            if artist_name and not album_has_artist(album, artist_name):
                print(f"Album ID '{album_id}' does not match artist '{artist_name}'")
                continue
        else:
            album = get_album_by_name(album_name, artist_name)
        if not album:
            continue

        artist_name_display = resolve_artist_display(album, artist_name)
        print(f"Downloading album: {album.get('name', album_name)}")
        download_album_tracks(artist_name_display, album, known_hashes, base_output_folder)


def download_songs_from_list(entries, known_hashes, base_output_folder):
    downloaded = []
    for entry in entries:
        song_name, artist_name = parse_song_entry(entry)
        if not song_name:
            continue
        track_id = extract_spotify_track_id(song_name)
        resolved_song_name = song_name
        if track_id:
            track = get_track_by_id(track_id)
            if not track:
                continue
            if artist_name and not track_has_artist(track, artist_name):
                print(f"Track ID '{track_id}' does not match artist '{artist_name}'")
                continue
            resolved_song_name = track.get("name", "").strip() or song_name
            artist_name_display = resolve_artist_display(track, artist_name)
        else:
            artist_name_display = artist_name.strip() if artist_name else ""

        if is_instrumental_text(resolved_song_name):
            print(f"Skipping instrumental song entry: {resolved_song_name}")
            continue

        search_query = (
            f"{artist_name_display} - {resolved_song_name}"
            if artist_name_display else resolved_song_name
        )

        artist_safe = sanitize_filename(artist_name_display) if artist_name_display else "Unknown"
        song_safe = sanitize_filename(resolved_song_name)
        base_filename = (
            f"{artist_safe} - {song_safe}" if artist_name_display else song_safe
        )
        out_base = unique_base_path(os.path.join(base_output_folder, base_filename))

        print(f"Downloading song: {resolved_song_name}")
        downloaded_path = download_audio(search_query, out_base, known_hashes)
        if downloaded_path:
            downloaded.append((artist_name_display, downloaded_path))

    return downloaded


def copy_placeholder_image(dest_folder):
    if not os.path.exists(PLACEHOLDER_IMAGE):
        print(f"Placeholder image not found: {PLACEHOLDER_IMAGE}")
        return
    dest_path = os.path.join(dest_folder, os.path.basename(PLACEHOLDER_IMAGE))
    if os.path.exists(dest_path):
        return
    shutil.copy2(PLACEHOLDER_IMAGE, dest_path)


def group_songs_into_artist_folders(downloaded, base_output_folder):
    artist_map = {}
    for artist_name, file_path in downloaded:
        if not artist_name or not file_path:
            continue
        artist_map.setdefault(artist_name, []).append(file_path)

    for artist_name, files in artist_map.items():
        if len(files) < 2:
            continue
        folder_name = sanitize_filename(artist_name)
        artist_folder = os.path.join(base_output_folder, folder_name)
        os.makedirs(artist_folder, exist_ok=True)

        for file_path in files:
            if not os.path.exists(file_path):
                continue
            dest_path = os.path.join(artist_folder, os.path.basename(file_path))
            if os.path.abspath(file_path) == os.path.abspath(dest_path):
                continue
            dest_path = unique_file_path(dest_path)
            shutil.move(file_path, dest_path)

        copy_placeholder_image(artist_folder)


def resolve_input_file():
    if os.path.exists(SONGS_FILE):
        if os.path.exists(ALBUMS_FILE) or os.path.exists(PLAYLISTS_FILE) or os.path.exists(ARTISTS_FILE):
            print("Multiple input files found. Using songs.txt.")
        debug(f"Resolved input file: {SONGS_FILE}")
        return "songs", SONGS_FILE
    if os.path.exists(ALBUMS_FILE):
        if os.path.exists(PLAYLISTS_FILE) or os.path.exists(ARTISTS_FILE):
            print("Multiple input files found. Using album.txt.")
        debug(f"Resolved input file: {ALBUMS_FILE}")
        return "albums", ALBUMS_FILE
    if os.path.exists(PLAYLISTS_FILE):
        if os.path.exists(ARTISTS_FILE):
            print("Both playlist.txt and artist.txt found. Using playlist.txt.")
        debug(f"Resolved input file: {PLAYLISTS_FILE}")
        return "playlists", PLAYLISTS_FILE
    if os.path.exists(ARTISTS_FILE):
        debug(f"Resolved input file: {ARTISTS_FILE}")
        return "artists", ARTISTS_FILE
    return None, None


def main():
    base_output_folder = get_output_folder()
    debug(f"Output folder: {base_output_folder}")
    os.makedirs(base_output_folder, exist_ok=True)
    known_hashes, cache_path, cache_entries = build_audio_hash_index(base_output_folder)
    set_hash_cache_state(base_output_folder, cache_path, cache_entries)
    mode, input_path = resolve_input_file()
    if not input_path:
        print("No songs.txt, album.txt, playlist.txt, or artist.txt found in the script folder.")
        return

    if mode == "songs":
        entries = parse_list_file(input_path, allow_commas_in_items=True)
        if not entries:
            print("songs.txt is empty or could not be parsed.")
            return
        downloaded = download_songs_from_list(entries, known_hashes, base_output_folder)
        group_songs_into_artist_folders(downloaded, base_output_folder)
        return
    if mode == "albums":
        entries = parse_list_file(input_path, allow_commas_in_items=True)
        if not entries:
            print("album.txt is empty or could not be parsed.")
            return
        download_albums_from_list(entries, known_hashes, base_output_folder)
        return
    if mode == "playlists":
        entries = parse_list_file(input_path, allow_commas_in_items=True)
        if not entries:
            print("playlist.txt is empty or could not be parsed.")
            return
        download_playlists_from_list(entries, known_hashes, base_output_folder)
        return

    artists = parse_list_file(input_path, allow_commas_in_items=False)
    if not artists:
        print("artist.txt is empty or could not be parsed.")
        return
    for artist_name in artists:
        if not artist_name.strip():
            continue
        download_all_albums_for_artist(artist_name, known_hashes, base_output_folder)


if __name__ == "__main__":
    main()
