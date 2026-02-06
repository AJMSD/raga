import argparse
import os
import dotenv

try:
    from mutagen import File as MutagenFile
except Exception:
    MutagenFile = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "DownloadedMusic")
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
HASH_CACHE_FILENAME = ".audio_hashes.txt"

ARTISTS = [
]

AUDIO_EXTS = {".mp3", ".m4a", ".webm", ".opus", ".aac", ".flac", ".wav"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
IGNORED_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}


def normalize(text):
    if not text:
        return ""
    cleaned = []
    for ch in text.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch.isspace():
            cleaned.append(" ")
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def load_base_dir():
    dotenv.load_dotenv(ENV_PATH)
    env_path = os.getenv("DESTINATION_FOLDER", "").strip()
    if env_path:
        if not os.path.isabs(env_path):
            env_path = os.path.abspath(os.path.join(SCRIPT_DIR, env_path))
        return env_path
    return DEFAULT_OUTPUT_FOLDER


def build_artist_index(artists):
    entries = []
    for name in artists:
        norm = normalize(name)
        entries.append(
            {
                "name": name,
                "norm": norm,
                "compact": norm.replace(" ", ""),
                "tokens": norm.split(),
            }
        )
    return entries


def text_matches_artist(text, entry):
    text_norm = normalize(text)
    if not text_norm:
        return False
    if entry["compact"] and entry["compact"] in text_norm.replace(" ", ""):
        return True
    tokens = entry["tokens"]
    if not tokens:
        return False
    text_tokens = text_norm.split()
    if len(text_tokens) < len(tokens):
        return False
    for idx in range(len(text_tokens) - len(tokens) + 1):
        if text_tokens[idx : idx + len(tokens)] == tokens:
            return True
    return False


def find_artist_match(candidates, artist_entries):
    for candidate in candidates:
        for entry in artist_entries:
            if text_matches_artist(candidate, entry):
                return entry["name"]
    return None


def collect_audio_files(base_dir):
    for root, _, files in os.walk(base_dir):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext in AUDIO_EXTS:
                yield os.path.join(root, filename)


def cleanup_empty_folders(base_dir, dry_run):
    removed = 0
    base_abs = os.path.abspath(base_dir)
    for root, dirs, files in os.walk(base_dir, topdown=False):
        if os.path.abspath(root) == base_abs:
            continue
        existing_dirs = [
            name for name in dirs if os.path.isdir(os.path.join(root, name))
        ]
        if existing_dirs:
            continue

        cover_files = []
        other_files = []
        for filename in files:
            lower = filename.lower()
            if lower in IGNORED_FILES:
                continue
            if lower == "cover.jpg":
                cover_files.append(filename)
                continue
            other_files.append(filename)

        if other_files:
            continue

        if dry_run:
            print(f"[DRY-RUN] Remove folder: {root}")
            removed += 1
            continue

        for filename in cover_files:
            try:
                os.remove(os.path.join(root, filename))
            except OSError as exc:
                print(f"Failed to remove {os.path.join(root, filename)}: {exc}")
        try:
            os.rmdir(root)
            removed += 1
        except OSError as exc:
            print(f"Failed to remove folder {root}: {exc}")
    return removed


def rename_album_art(base_dir, dry_run):
    renamed = 0
    skipped = 0
    for root, _, files in os.walk(base_dir):
        for filename in files:
            base, ext = os.path.splitext(filename)
            if ext and ext.lower() not in IMAGE_EXTS:
                continue
            if base.lower() != "album_art":
                continue
            src = os.path.join(root, filename)
            target = os.path.join(root, f"cover{ext}")
            if os.path.exists(target):
                skipped += 1
                if dry_run:
                    print(f"[DRY-RUN] Skip rename (target exists): {src} -> {target}")
                continue
            if dry_run:
                print(f"[DRY-RUN] Rename: {src} -> {target}")
            else:
                try:
                    os.rename(src, target)
                    print(f"Renamed: {src} -> {target}")
                except OSError as exc:
                    print(f"Failed to rename {src}: {exc}")
                    continue
            renamed += 1
    return renamed, skipped


def load_hash_cache(cache_path):
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
                cache[rel_path] = (file_hash, size_text, mtime_text)
    except OSError as exc:
        print(f"Failed to read hash cache: {exc}")
    return cache


def save_hash_cache(cache_path, cache):
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            for rel_path in sorted(cache.keys()):
                file_hash, size_text, mtime_text = cache[rel_path]
                f.write(f"{file_hash}\t{rel_path}\t{size_text}\t{mtime_text}\n")
    except OSError as exc:
        print(f"Failed to write hash cache: {exc}")


def prune_hash_cache(base_dir, dry_run):
    cache_path = os.path.join(base_dir, HASH_CACHE_FILENAME)
    cache = load_hash_cache(cache_path)
    if not cache:
        return 0
    removed = 0
    for rel_path in list(cache.keys()):
        abs_path = os.path.join(base_dir, rel_path)
        if not os.path.exists(abs_path):
            removed += 1
            del cache[rel_path]
    if removed and dry_run:
        print(f"[DRY-RUN] Would update hash cache: {cache_path}")
        return removed
    if removed:
        save_hash_cache(cache_path, cache)
    return removed


def extract_artist_tags(path):
    if MutagenFile is None:
        return []
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return []
    if not audio or not getattr(audio, "tags", None):
        return []
    tags = audio.tags
    artists = []
    for key in ("artist", "albumartist", "album_artist"):
        value = tags.get(key)
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            artists.extend(value)
        else:
            artists.append(str(value))
    return [artist for artist in artists if artist]


def main():
    parser = argparse.ArgumentParser(
        description="Remove songs for specific artists and delete empty album folders."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview deletions without modifying files.",
    )
    args = parser.parse_args()

    base_dir = load_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        print(f"Base folder not found: {base_dir}")
        return 1

    artist_entries = build_artist_index(ARTISTS)
    per_artist = {name: 0 for name in ARTISTS}
    removed_files = 0

    renamed_images, skipped_images = rename_album_art(base_dir, args.dry_run)

    for path in collect_audio_files(base_dir):
        rel_path = os.path.relpath(path, base_dir)
        parts = rel_path.split(os.sep)
        filename = os.path.splitext(parts[-1])[0]
        candidates = [filename] + parts[:-1]
        matched = None
        for artist_text in extract_artist_tags(path):
            matched = find_artist_match([artist_text], artist_entries)
            if matched:
                break
        if not matched:
            matched = find_artist_match(candidates, artist_entries)
        if not matched:
            continue

        if args.dry_run:
            print(f"[DRY-RUN] Remove file: {path} (matched {matched})")
        else:
            try:
                os.remove(path)
                print(f"Removed: {path} (matched {matched})")
            except OSError as exc:
                print(f"Failed to remove {path}: {exc}")
                continue

        removed_files += 1
        per_artist[matched] = per_artist.get(matched, 0) + 1

    removed_folders = cleanup_empty_folders(base_dir, args.dry_run)
    removed_hashes = prune_hash_cache(base_dir, args.dry_run)

    print("\nSummary")
    print(f"Base folder: {base_dir}")
    print(f"Audio files removed: {removed_files}")
    for artist in ARTISTS:
        print(f"- {artist}: {per_artist.get(artist, 0)}")
    print(f"Album art renamed: {renamed_images}")
    if skipped_images:
        print(f"Album art skipped (cover exists): {skipped_images}")
    print(f"Folders removed (empty or cover-only): {removed_folders}")
    print(f"Hash cache entries removed: {removed_hashes}")
    if args.dry_run:
        print("Dry run complete. No files were deleted.")
    if MutagenFile is None:
        print("Note: mutagen not installed; matching used filenames/folders only.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
