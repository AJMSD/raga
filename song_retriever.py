import os
import json
import ast
import shutil
import requests
import hashlib
import time
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

INSTRUMENTAL_KEYWORDS = [
    "instrumental",
    "karaoke",
    "backing track",
    "no vocals",
]


def download_image(url, path):
    """Download the album image at roughly 300x300."""
    if not url:
        return
    for attempt in range(1, IMAGE_DOWNLOAD_ATTEMPTS + 1):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
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


def parse_list_file(path, allow_commas_in_items):
    """Parse a list from a text file."""
    if not os.path.exists(path):
        return []
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
        return hashes
    for root, _, files in os.walk(base_dir):
        for filename in files:
            if not filename.lower().endswith(AUDIO_EXTS):
                continue
            path = os.path.join(root, filename)
            try:
                file_hash = hash_file(path)
            except OSError:
                continue
            hashes.add(file_hash)
    return hashes


def spotify_call(func, *args, **kwargs):
    for attempt in range(1, SPOTIFY_ATTEMPTS + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            print(f"Spotify request failed (attempt {attempt}): {exc}")
            if attempt < SPOTIFY_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
    return None


def download_audio(search_query, out_base_path, known_hashes):
    ydl_opts = build_ydl_opts(out_base_path)
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
        return None
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
    return downloaded


def get_artist(artist_name):
    results = spotify_call(sp.search, q=f"artist:{artist_name}", type="artist", limit=1)
    if not results:
        print(f"Spotify search failed for artist '{artist_name}'")
        return None
    items = results.get("artists", {}).get("items", [])
    if not items:
        print(f"No artist found for '{artist_name}'")
        return None
    return items[0]


def get_all_albums(artist_id):
    albums = []
    seen = set()
    offset = 0
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


def search_albums_by_name(album_name):
    albums = []
    seen = set()
    offset = 0
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
    if artist_name:
        for album in items:
            if album_has_artist(album, artist_name):
                return album
        print(f"No album found for '{album_name}' with artist '{artist_name}'")
        return None
    return items[0]


def album_artist_display(album, fallback):
    artists = album.get("artists") or []
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

        album = get_album_by_name(album_name, artist_name)
        if not album:
            continue

        artist_display = album_artist_display(album, artist_name)
        print(f"Downloading album: {album.get('name', album_name)}")
        download_album_tracks(artist_display, album, known_hashes, base_output_folder)


def download_songs_from_list(entries, known_hashes, base_output_folder):
    downloaded = []
    for entry in entries:
        song_name, artist_name = parse_song_entry(entry)
        if not song_name:
            continue
        if is_instrumental_text(song_name):
            print(f"Skipping instrumental song entry: {song_name}")
            continue

        artist_display = artist_name.strip() if artist_name else ""
        search_query = (
            f"{artist_display} - {song_name}" if artist_display else song_name
        )

        artist_safe = sanitize_filename(artist_display) if artist_display else "Unknown"
        song_safe = sanitize_filename(song_name)
        base_filename = f"{artist_safe} - {song_safe}" if artist_display else song_safe
        out_base = unique_base_path(os.path.join(base_output_folder, base_filename))

        print(f"Downloading song: {song_name}")
        downloaded_path = download_audio(search_query, out_base, known_hashes)
        if downloaded_path:
            downloaded.append((artist_display, downloaded_path))

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
        if os.path.exists(ALBUMS_FILE) or os.path.exists(ARTISTS_FILE):
            print("Multiple input files found. Using songs.txt.")
        return "songs", SONGS_FILE
    if os.path.exists(ALBUMS_FILE):
        if os.path.exists(ARTISTS_FILE):
            print("Both album.txt and artist.txt found. Using album.txt.")
        return "albums", ALBUMS_FILE
    if os.path.exists(ARTISTS_FILE):
        return "artists", ARTISTS_FILE
    return None, None


def main():
    base_output_folder = get_output_folder()
    os.makedirs(base_output_folder, exist_ok=True)
    known_hashes = build_audio_hash_index(base_output_folder)
    mode, input_path = resolve_input_file()
    if not input_path:
        print("No songs.txt or artist.txt found in the script folder.")
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
