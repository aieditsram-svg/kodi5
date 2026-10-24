"""Shared helpers: fetch playlists fresh, test streams, find working alternatives."""
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import xbmcaddon

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Kodi"
EXTINF_RE = re.compile(r"^#EXTINF:(-?\d+)\s*(.*?),(.*)$")
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
# "(1080p)" / "(1080i)" plus any trailing "[Geo-blocked]" style tags
QUALITY_RE = re.compile(r"\s*\((\d{3,4})[pi]\)(?:\s*\[[^\]]*\])*\s*$")
TAGS_RE = re.compile(r"(?:\s*\[[^\]]*\])+\s*$")
BATCH = 6  # streams tested at the same time when looking for a working one


def clean_name(name):
    """Channel name without quality and [Geo-blocked] style tags."""
    return TAGS_RE.sub("", QUALITY_RE.sub("", name)).strip()


def base_name(name):
    """Channel identity: same channel in SD and HD (or different quality tags) gets the same key."""
    return re.sub(r"\s+hd$", "", clean_name(name), flags=re.I).strip().lower()


def height(name):
    m = QUALITY_RE.search(name)
    return int(m.group(1)) if m else 0


def ch_height(ch):
    """Stream height: the playlist's own height attribute if present, else the (1080p) tag in the name."""
    try:
        return max(int(ch["attrs"].get("height", 0)), height(ch["name"]))
    except ValueError:
        return height(ch["name"])


def fetch_text(url, fresh=True, timeout=20):
    """Download a playlist. A changing query string bypasses CDN caches so links are never stale."""
    if fresh:
        url += ("&" if "?" in url else "?") + "t=%d" % int(time.time())
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def read_playlist(source, fresh=True):
    """Playlist text from a web address or from a file on this device (path or special:// path)."""
    source = source.strip()
    if source.lower().startswith(("http://", "https://")):
        return fetch_text(source, fresh)
    import xbmcvfs
    handle = xbmcvfs.File(source)
    try:
        data = handle.read()
    finally:
        handle.close()
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("utf-8", errors="replace")
    return data


def parse_m3u(text):
    channels, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            m = EXTINF_RE.match(line)
            cur = None
            if m:
                cur = {"name": m.group(3).strip(), "attrs": dict(ATTR_RE.findall(m.group(2))), "headers": {}}
        elif line.startswith("#EXTVLCOPT:") and cur is not None:
            key, _, val = line[len("#EXTVLCOPT:"):].partition("=")
            if key == "http-referrer":
                cur["headers"]["Referer"] = val
            elif key == "http-user-agent":
                cur["headers"]["User-Agent"] = val
        elif line.startswith("#"):
            continue
        elif cur is not None:
            cur["url"] = line
            channels.append(cur)
            cur = None
    return channels


def stream_path(ch):
    """URL in the form Kodi wants, with any required headers appended after a pipe."""
    if not ch["headers"]:
        return ch["url"]
    hdr = "&".join("%s=%s" % (k, urllib.parse.quote(v)) for k, v in ch["headers"].items())
    return ch["url"] + "|" + hdr


def probe(ch, timeout=5):
    """Quick check, from this device's own network, that a stream answers like a real stream."""
    headers = {"User-Agent": UA}
    headers.update(ch["headers"])
    req = urllib.request.Request(ch["url"], headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return False
            ctype = (resp.headers.get("Content-Type") or "").lower()
            body = resp.read(16 * 1024)
    except Exception:
        return False
    if ".m3u8" in ch["url"].lower() or "mpegurl" in ctype:
        text = body.decode("utf-8", errors="replace")
        return "#EXTM3U" in text[:200] and ("#EXTINF" in text or "#EXT-X-STREAM-INF" in text)
    return "text/html" not in ctype


def playlist_urls():
    """Your own extra playlist first (if set), then the working list, then the list of everything found
    (which includes links only your network can reach). A channel's links from all of them are pooled,
    so if one of your links fails the add-on moves on to the public ones."""
    addon = xbmcaddon.Addon("plugin.video.telugulive")
    urls = []
    for setting in ("extra_playlist", "list_working", "list_all"):
        url = addon.getSetting(setting).strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def candidates(key, tried, urls=None):
    """Untried streams for a channel: best quality first, working list before the full list."""
    seen = set(tried)
    for url in (urls if urls is not None else playlist_urls()):
        try:
            chans = parse_m3u(read_playlist(url))
        except Exception:
            continue
        matches = [c for c in chans if base_name(c["name"]) == key and c["url"] not in seen]
        matches.sort(key=lambda c: -ch_height(c))
        for ch in matches:
            if ch["url"] not in seen:
                seen.add(ch["url"])
                yield ch


def first_working(key, tried, urls=None):
    """Return the best stream that passes a live test, recording every attempt in `tried`.

    Streams are tested a few at a time, so a channel with several dead links starts quickly.
    """
    pending = candidates(key, tried, urls)
    while True:
        batch = []
        for ch in pending:
            batch.append(ch)
            if len(batch) == BATCH:
                break
        if not batch:
            return None
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            results = list(pool.map(probe, batch))
        for ch, ok in zip(batch, results):
            tried.append(ch["url"])
        for ch, ok in zip(batch, results):  # batch is already ordered best quality first
            if ok:
                return ch
