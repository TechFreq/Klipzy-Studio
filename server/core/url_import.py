"""Bounded direct-media downloads. Platform pages require official export workflows."""
import http.client
import ipaddress
import json
import math
import shutil
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from server.core import proc

MAX_BYTES = 10 * 1024**3
RESERVE_BYTES = 512 * 1024**2
MAX_SECONDS = 3600
POLICY_VERSION = "2026-09-18"

class ImportFailure(ValueError):
    pass

class ImportCancelled(Exception):
    pass

def parse_url(url):
    try:
        parsed = urlsplit(url)
        if (len(url) > 8192 or any(ord(c) < 33 for c in url) or
            parsed.scheme != "https" or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.port not in (None, 443) or parsed.fragment):
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError):
        raise ImportFailure("Use a direct HTTPS video link without login credentials or a fragment.") from None
    if any(host == h or host.endswith("." + h) for h in ("youtube.com", "youtu.be", "youtube-nocookie.com", "googlevideo.com")):
        raise ImportFailure("For YouTube, download your own upload with YouTube Studio or Google Takeout, then choose Upload file. YouTube page and playback URLs are not supported.")
    if host in ("twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"):
        raise ImportFailure("For Twitch pages, use the authorized download in Creator Dashboard, then Upload file. Direct authorized media-download links are supported; account connection is not configured yet.")
    return parsed, host

def public_addresses(host):
    try:
        entries = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(item[4][0] for item in entries))
        if not addresses or any((not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast or ipaddress.ip_address(ip).is_reserved) or '%' in ip for ip in addresses):
            raise ImportFailure("The link must point to a public internet server, not a local or private address.")
        return addresses
    except (socket.gaierror, ValueError) as exc:
        if isinstance(exc, ImportFailure):
            raise
        raise ImportFailure("Could not resolve a public server for that link.") from None

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the address we validated, retaining hostname/certificate checks.
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise

def open_response(url):
    parsed, host = parse_url(url)
    addresses = public_addresses(host)
    connection = PinnedHTTPSConnection(host, addresses[0])
    try:
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection.request("GET", target, headers={"User-Agent": "Klipzy-Studio/1.1", "Accept-Encoding": "identity"})
        return connection, connection.getresponse()
    except BaseException:
        connection.close()
        raise

def probe_download(path):
    with path.open("rb") as stream:
        head = stream.read(32)
    # Limit containers before probing; disallow text playlists and external protocols.
    if not (head[4:8] == b"ftyp" or head.startswith(b"\x1aE\xdf\xa3") or (head[:4] == b"RIFF" and head[8:12] == b"AVI ")):
        raise ImportFailure("This is not a supported MP4, MOV, MKV, WebM or AVI media file. Download the video file rather than its webpage.")
    result = proc.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,avi", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ImportFailure("The downloaded file could not be verified as a video.")
    try:
        info = json.loads(result.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0 or not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
            raise ValueError()
    except (ValueError, TypeError):
        raise ImportFailure("The downloaded file has no usable video stream or duration.") from None
    return ".mp4" if head[4:8] == b"ftyp" else ".avi" if head[:4] == b"RIFF" else ".mkv"

def download(url, folder, cancel, progress):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    partial = folder / "download.part"
    started = time.monotonic()
    received = 0
    def check():
        if cancel.is_set():
            raise ImportCancelled()
        if time.monotonic() - started > MAX_SECONDS:
            raise ImportFailure("Download timed out. Retry with a fresh direct video link.")
    try:
        for hop in range(6):
            check()
            connection, response = open_response(url)
            try:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location or hop == 5:
                        raise ImportFailure("The link has too many redirects or a missing destination.")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ImportFailure(f"Download server returned HTTP {response.status}. Check permissions or obtain a fresh link.")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise ImportFailure("Compressed HTTP downloads are not supported. Use a direct media file.")
                length = response.getheader("Content-Length")
                try:
                    total = int(length) if length is not None else None
                except ValueError:
                    raise ImportFailure("The server returned an invalid file size.") from None
                if total is not None and (total <= 0 or total > MAX_BYTES):
                    raise ImportFailure("The video must be non-empty and no larger than 10 GiB.")
                if shutil.disk_usage(folder).free < (total or 0) + RESERVE_BYTES:
                    raise ImportFailure("Not enough disk space for this import; keep at least 512 MiB free.")
                with partial.open("xb") as stream:
                    while True:
                        check()
                        chunk = response.read1(256 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > MAX_BYTES:
                            raise ImportFailure("Download exceeded the 10 GiB limit.")
                        if shutil.disk_usage(folder).free < len(chunk) + RESERVE_BYTES:
                            raise ImportFailure("Disk space is low. Free space before retrying.")
                        stream.write(chunk)
                        progress("downloading", received, total, received / max(.1, time.monotonic() - started))
                if total is not None and received != total:
                    raise ImportFailure("The download ended early. Retry with a fresh link.")
                check()
                progress("verifying", received, total, 0)
                extension = probe_download(partial)
                check()
                final = folder / ("imported-video" + extension)
                partial.rename(final)
                return str(final.resolve())
            finally:
                response.close()
                connection.close()
        raise ImportFailure("Could not follow the video link.")
    finally:
        partial.unlink(missing_ok=True)
        try:
            folder.rmdir()  # Only empty staging directories; never delete user media.
        except OSError:
            pass
