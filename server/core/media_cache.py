"""Reusable prepared media. Projects receive their own hard link or copy."""
import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "output" / ".cache" / "media"
LOCK = threading.RLock()

def fingerprint(source, settings):
    path = Path(source).resolve()
    stat = path.stat()
    return hashlib.sha256(json.dumps([str(path), stat.st_size, stat.st_mtime_ns, settings], sort_keys=True).encode()).hexdigest()

def materialize(source, destination, selection, gains):
    from server.core.ffmpeg_tools import prepare_audio_tracks
    from server.core import processing_trace as trace
    key = fingerprint(source, ['prepared-v1', selection, gains or []])
    with LOCK:
        ROOT.mkdir(parents=True, exist_ok=True)
        cached = ROOT / (key + '.mkv')
        hit = cached.exists() and cached.stat().st_size > 0
        if not hit:
            partial = ROOT / (key + '.part.mkv')
            try:
                prepare_audio_tracks(source, str(partial), selection, gains=gains)
                partial.replace(cached)
            finally:
                partial.unlink(missing_ok=True)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError('Prepared source destination already exists')
        try:
            os.link(cached, destination)
            method = 'hardlink'
        except OSError:
            shutil.copy2(cached, destination)
            method = 'copy'
        trace.event('media.cache', hit=hit, key=key, materialization=method, path=str(destination))
        return str(destination)

def inventory():
    files = []
    if ROOT.exists() and not ROOT.is_symlink():
        for path in ROOT.iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix in ('.mkv', '.wav'):
                try:files.append((path, path.stat().st_size))
                except OSError:pass
    return files

def status():
    files = inventory()
    return dict(bytes=sum(size for _,size in files), files=len(files), path=str(ROOT),
                note='Cache file sizes, not guaranteed reclaimable disk space. Project hard links keep their media alive.')

def clear():
    # Never recursively delete. Only direct, owned cache files; originals and project sources stay intact.
    if not LOCK.acquire(blocking=False):
        raise RuntimeError('Media is being prepared. Wait for it to finish before clearing the cache.')
    try:
        deleted = skipped = 0
        root = ROOT.resolve()
        for path, _ in inventory():
            if path.resolve().parent != root or path.is_symlink():
                skipped += 1
                continue
            try:path.unlink();deleted += 1
            except OSError:skipped += 1
        return dict(deleted=deleted, skipped=skipped, **status())
    finally:LOCK.release()
