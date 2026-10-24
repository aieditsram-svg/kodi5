#!/usr/bin/env python3
"""Build multi-language live-TV playlists containing only working streams.

Pipeline
  1. Fetch every source in sources.txt (optionally enriched with the iptv-org database:
     language, logo, category; adult and closed channels are dropped)
  2. De-duplicate by stream URL (a stream shared by several languages keeps all of them)
  3. Test every stream, retry once on network errors
  4. Keep recently-working streams for a short grace period so one bad check never interrupts playback
  5. Write the playlists, but only when the channel lists really changed

Outputs
  channels.m3u        working streams, all languages (group-title = language)
  channels_hd.m3u     best working stream per channel at HD (720p+ by default)
  channels_best.m3u   best working stream per channel at any quality (verified links only)
  channels_pvr.m3u    one link per channel for Kodi's IPTV Simple Client: verified links first, and channels
                      that only work inside India are kept too (telugu_pvr.m3u = Telugu only)
  channels_all.m3u    every stream found, tested or not (includes links this server could not reach,
                      for example streams that only work inside India)
  languages/<name>.m3u  working streams per language
  telugu.m3u, telugu_hd.m3u, telugu_all.m3u   the same three lists for Telugu only (older links keep working)
  status.json         counts per language and the list of dead links
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DB_URL = "https://raw.githubusercontent.com/iptv-org/database/master/data"
EXTINF_RE = re.compile(r"^#EXTINF:(-?\d+)\s*(.*?),(.*)$")
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
# "(1080p)", "(1080i)" and any trailing "[Geo-blocked]" / "[Not 24/7]" tags
QUALITY_TAG_RE = re.compile(r"\s*\((\d{3,4})[pi]\)(?:\s*\[[^\]]*\])*\s*$")
TRAILING_TAGS_RE = re.compile(r"(?:\s*\[[^\]]*\])+\s*$")

LANG_NAMES = {
    "tel": "Telugu", "hin": "Hindi", "tam": "Tamil", "kan": "Kannada", "mal": "Malayalam",
    "ben": "Bengali", "mar": "Marathi", "guj": "Gujarati", "pan": "Punjabi", "ori": "Odia",
    "ory": "Odia", "asm": "Assamese", "urd": "Urdu", "bho": "Bhojpuri", "eng": "English",
    "nep": "Nepali", "kok": "Konkani", "gom": "Konkani", "san": "Sanskrit", "kas": "Kashmiri",
    "bgc": "Haryanvi", "mai": "Maithili", "raj": "Rajasthani", "hne": "Chhattisgarhi",
    "sat": "Santali", "brx": "Bodo", "doi": "Dogri", "mni": "Manipuri",
}  # any other code (foreign languages, sign language) is grouped under "Other"


# ----------------------------------------------------------------------------- fetching / parsing

def http_get(url: str, timeout: int = 20, max_bytes: int | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(max_bytes) if max_bytes else resp.read()
        return resp.status, dict(resp.headers), body


def parse_m3u(text: str) -> list[dict]:
    channels: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            m = EXTINF_RE.match(line)
            if not m:
                cur = None
                continue
            cur = {"name": m.group(3).strip(), "attrs": dict(ATTR_RE.findall(m.group(2))),
                   "extra": [], "url": None, "languages": []}
        elif line.startswith("#EXTVLCOPT") or line.startswith("#KODIPROP"):
            if cur is not None:
                cur["extra"].append(line)
        elif line.startswith("#"):
            continue
        elif cur is not None:
            cur["url"] = line
            channels.append(cur)
            cur = None
    return channels


def load_sources(path: Path) -> list[tuple[str, dict]]:
    """Each line: URL  [| option=value ...]

    group=India          keep only channels whose group-title matches
    keywords=a,b         keep only channels whose name contains one of the words
    language=Telugu      tag every channel from this source with a language
    enrich=iptv-org      look up language, logo and category in the iptv-org database
                         (the source must carry iptv-org style tvg-id values)
    """
    sources = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, *parts = [p.strip() for p in line.split("|")]
        opts: dict[str, list[str]] = {}
        for part in parts:
            key, _, val = part.partition("=")
            opts[key.strip().lower()] = [v.strip() for v in val.split(",") if v.strip()]
        sources.append((url, opts))
    return sources


def keep(ch: dict, opts: dict) -> bool:
    groups, words = opts.get("group"), opts.get("keywords")
    if groups and (ch["attrs"].get("group-title", "") or "").lower() not in [g.lower() for g in groups]:
        return False
    if words and not any(w.lower() in ch["name"].lower() for w in words):
        return False
    return True


def load_db() -> dict | None:
    """iptv-org database: per-feed languages, per-channel category/logo, adult and closed flags."""
    try:
        def table(name: str) -> list[dict]:
            _, _, body = http_get(f"{DB_URL}/{name}.csv", timeout=90)
            return list(csv.DictReader(io.StringIO(body.decode("utf-8", errors="replace"))))

        feeds = {f["channel"] + "@" + f["id"]: f for f in table("feeds")}
        channels = {c["id"]: c for c in table("channels")}
        logos: dict[str, str] = {}
        for lg in table("logos"):
            if lg["in_use"].upper() == "TRUE":
                logos.setdefault(lg["channel"], lg["url"])
        print(f"[db] {len(channels)} channels, {len(feeds)} feeds, {len(logos)} logos")
        return {"feeds": feeds, "channels": channels, "logos": logos}
    except Exception as exc:
        print(f"[db] FAILED to load iptv-org database: {exc}", file=sys.stderr)
        return None


def language_names(codes: str) -> list[str]:
    out: list[str] = []
    for code in codes.split(";"):
        code = code.strip().lower()
        if code:
            name = LANG_NAMES.get(code, "Other")
            if name not in out:
                out.append(name)
    return out


def enrich(ch: dict, db: dict | None) -> dict | None:
    """Add languages / logo / category from the database. Returns None for adult or closed channels."""
    tid = ch["attrs"].get("tvg-id", "")
    cid = tid.partition("@")[0]
    if not db or not cid:
        return ch
    info = db["channels"].get(cid)
    if info and (info.get("is_nsfw", "").upper() == "TRUE" or info.get("closed")):
        return None
    feed = db["feeds"].get(tid)
    if feed and feed.get("languages"):
        ch["languages"] = language_names(feed["languages"])
    if info and info.get("categories"):
        ch["attrs"]["x-category"] = info["categories"].split(";")[0]
    logo = db["logos"].get(cid)
    if logo and not ch["attrs"].get("tvg-logo"):
        ch["attrs"]["tvg-logo"] = logo
    return ch


def fetch_sources(sources: list[tuple[str, dict]]) -> list[dict]:
    db = load_db() if any("enrich" in o for _, o in sources) else None
    all_channels: list[dict] = []
    for url, opts in sources:
        try:
            _, _, body = http_get(url, timeout=60)
        except Exception as exc:  # keep going if one source is down
            print(f"[source] FAILED {url}: {exc}", file=sys.stderr)
            continue
        chans = [c for c in parse_m3u(body.decode("utf-8", errors="replace")) if keep(c, opts)]
        forced = opts.get("language", [])
        kept = []
        for c in chans:
            if "enrich" in opts:
                c = enrich(c, db)
                if c is None:
                    continue
            if forced:
                c["languages"] = [forced[0]]
            kept.append(c)
        print(f"[source] {len(kept):4d}/{len(chans):4d} channels kept  <- {url}")
        all_channels.extend(kept)
    return all_channels


def dedupe(channels: list[dict]) -> list[dict]:
    """One entry per stream URL. A stream listed for several languages keeps all of them."""
    by_url: dict[str, dict] = {}
    for ch in channels:
        url = ch["url"]
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        if url in by_url:
            for lang in ch["languages"]:
                if lang not in by_url[url]["languages"]:
                    by_url[url]["languages"].append(lang)
            continue
        by_url[url] = ch
    for ch in by_url.values():
        if not ch["languages"]:
            ch["languages"] = ["Other"]
    return list(by_url.values())


# ----------------------------------------------------------------------------- link checking

def check_stream(ch: dict, timeout: int) -> dict:
    """Probe one stream. Returns {ok, status, ms, reason, height}."""
    url = ch["url"]
    start = time.time()
    try:
        status, headers, body = http_get(url, timeout=timeout, max_bytes=64 * 1024)
        ms = int((time.time() - start) * 1000)
        if status >= 400:
            return {"ok": False, "status": status, "ms": ms, "reason": f"HTTP {status}"}
        ctype = headers.get("Content-Type", "").lower()
        looks_hls = ".m3u8" in url.lower() or "mpegurl" in ctype
        height = 0
        if looks_hls:
            text = body.decode("utf-8", errors="replace")
            if "#EXTM3U" not in text[:200]:
                return {"ok": False, "status": status, "ms": ms, "reason": "not a valid HLS manifest"}
            if "#EXTINF" not in text and "#EXT-X-STREAM-INF" not in text:
                return {"ok": False, "status": status, "ms": ms, "reason": "empty HLS manifest"}
            heights = [int(h) for h in re.findall(r"RESOLUTION=\d+x(\d+)", text)]
            height = max(heights) if heights else 0
        elif "text/html" in ctype:
            return {"ok": False, "status": status, "ms": ms, "reason": "returned HTML page"}
        return {"ok": True, "status": status, "ms": ms, "reason": "ok", "height": height}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "ms": int((time.time() - start) * 1000),
                "reason": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "status": 0, "ms": int((time.time() - start) * 1000),
                "reason": type(exc).__name__}


def check_with_retry(ch: dict, timeout: int) -> dict:
    """One quick retry for network hiccups. A definite HTTP 4xx answer is not retried."""
    result = check_stream(ch, timeout)
    if not result["ok"] and not result["reason"].startswith("HTTP 4"):
        time.sleep(1.5)
        retry = check_stream(ch, timeout)
        if retry["ok"]:
            return retry
    return result


def check_all(channels: list[dict], workers: int, timeout: int) -> None:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(check_with_retry, ch, timeout): ch for ch in channels}
        for fut in as_completed(futures):
            futures[fut]["result"] = fut.result()


def load_state(path: Path) -> dict:
    try:
        return {k: int(v) for k, v in json.loads(path.read_text()).items()}
    except Exception:
        return {}


def write_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


# ----------------------------------------------------------------------------- quality / output

def base_name(name: str) -> str:
    """Channel identity without quality tags, [Geo-blocked] style tags and a trailing 'HD'."""
    n = QUALITY_TAG_RE.sub("", name)
    n = TRAILING_TAGS_RE.sub("", n).strip()
    n = re.sub(r"\s+hd$", "", n, flags=re.I)
    return n.strip().lower()


def channel_height(ch: dict) -> int:
    """Best known height: real resolution from the stream manifest, else the (1080p) tag in the name."""
    real = ch.get("result", {}).get("height", 0)
    m = QUALITY_TAG_RE.search(ch["name"])
    return max(real, int(m.group(1)) if m else 0)


def pick_hd(working: list[dict], min_height: int) -> list[dict]:
    """HD channels only, one (the best) stream per channel."""
    best: dict[str, dict] = {}
    for ch in working:
        h = channel_height(ch)
        if h < min_height:
            continue
        key = base_name(ch["name"])
        if key not in best or h > channel_height(best[key]):
            best[key] = ch
    return list(best.values())


def pick_pvr(channels: list[dict], working: list[dict]) -> list[dict]:
    """One link per (language, channel) for players that cannot switch links, e.g. Kodi's IPTV Simple Client.

    A link that passed the server check always wins. Otherwise the best unverified link is used, so channels
    that only work inside India still appear: the server that tests links runs outside India.
    """
    ok = {c["url"] for c in working}
    best: dict[tuple[str, str], tuple[tuple[bool, int], dict]] = {}
    for ch in channels:
        for lang in ch["languages"]:
            key = (lang, base_name(ch["name"]))
            score = (ch["url"] in ok, channel_height(ch))
            if key not in best or score > best[key][0]:
                best[key] = (score, ch)
    picks = []
    for (lang, _), (_, ch) in best.items():
        name = TRAILING_TAGS_RE.sub("", QUALITY_TAG_RE.sub("", ch["name"])).strip() or ch["name"]
        picks.append({**ch, "languages": [lang], "name": name})
    return picks


def render_m3u(channels: list[dict], header_note: str, only_language: str | None = None) -> str:
    """One entry per (stream, language); group-title is the language so players separate them."""
    rows = []
    for ch in channels:
        for lang in ch["languages"]:
            if only_language and lang != only_language:
                continue
            rows.append((lang, ch["name"].lower(), ch))
    rows.sort(key=lambda r: (r[0], r[1]))
    lines = ["#EXTM3U", f"# {header_note}"]
    for lang, _, ch in rows:
        attrs = {k: v for k, v in ch["attrs"].items() if k not in ("group-title", "tvg-language", "height")}
        attrs["tvg-language"] = lang
        attrs["group-title"] = lang
        h = channel_height(ch)
        if h:
            attrs["height"] = str(h)  # lets players and the Kodi add-on filter by quality
        attr_text = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        lines.append(f"#EXTINF:-1 {attr_text},{ch['name']}")
        lines.extend(ch["extra"])
        lines.append(ch["url"])
    return "\n".join(lines) + "\n"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "other"


# ----------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=str(ROOT / "sources.txt"))
    ap.add_argument("--out-dir", default=str(ROOT))
    ap.add_argument("--workers", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=8)
    ap.add_argument("--hd-height", type=int, default=720,
                    help="Minimum stream height for the *_hd playlists (720 = HD, 1080 = full HD only)")
    ap.add_argument("--grace", type=int, default=3,
                    help="A previously working channel stays in the playlist until it has failed this "
                         "many checks in a row (3 checks = about 15 min at a 5-minute schedule)")
    ap.add_argument("--min-working", type=int, default=1,
                    help="Abort without overwriting files if fewer streams work than this "
                         "(protects against a network outage wiping the playlist)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    channels = dedupe(fetch_sources(load_sources(Path(args.sources))))
    print(f"[build] {len(channels)} unique streams to check")
    if not channels:
        print("No channels fetched; leaving existing playlists untouched.", file=sys.stderr)
        return 1

    state_path = out_dir / "state.json"
    state = load_state(state_path)  # url -> consecutive failed checks, only for streams that worked before
    check_all(channels, args.workers, args.timeout)

    working, dead, new_state = [], [], {}
    for c in channels:
        url, res = c["url"], c["result"]
        if res["ok"]:
            new_state[url] = 0
            working.append(c)
        elif url in state and state[url] + 1 < args.grace:
            # Failed, but it worked recently: keep it listed so a short blip doesn't interrupt playback.
            new_state[url] = state[url] + 1
            res["grace"] = True
            working.append(c)
        else:
            dead.append(c)
    in_grace = sum(1 for c in working if c["result"].get("grace"))
    print(f"[build] working: {len(working)} (incl. {in_grace} in grace period)  dead: {len(dead)}")

    if len(working) < args.min_working:
        print("Too few working streams (likely a network problem); not overwriting playlists.", file=sys.stderr)
        return 1

    hd = pick_hd(working, args.hd_height)
    languages = sorted({lang for c in channels for lang in c["languages"]})
    write_if_changed(state_path, json.dumps(new_state, indent=0, sort_keys=True) + "\n")

    # Only rewrite the playlists when the channel lists really changed. Otherwise a 5-minute
    # schedule would push a commit on every run just for a timestamp.
    fingerprint = hashlib.md5("\n".join(
        sorted(f"{c['url']}@{channel_height(c)}@{','.join(c['languages'])}" for c in working)
        + ["--all--"] + sorted(f"{c['url']}@{','.join(c['languages'])}" for c in channels)
    ).encode()).hexdigest()
    status_path = out_dir / "status.json"
    try:
        old_fp = json.loads(status_path.read_text()).get("fingerprint")
    except Exception:
        old_fp = None
    if (old_fp == fingerprint and (out_dir / "channels.m3u").exists()
            and (out_dir / "channels_pvr.m3u").exists() and (out_dir / "telugu_pvr.m3u").exists()):
        print("[build] no change in channel lists; nothing to write")
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for fname, items, note in (
        ("channels.m3u", working, "Working channels, all languages"),
        ("channels_hd.m3u", hd, f"Working HD channels ({args.hd_height}p+), all languages"),
        # One link per channel: the right list for players without failover, e.g. Kodi's IPTV Simple Client
        ("channels_best.m3u", pick_hd(working, 0), "Working channels, one best link per channel"),
        ("channels_all.m3u", channels, "All channels found (links not verified from this server included)"),
    ):
        write_if_changed(out_dir / fname, render_m3u(items, f"{note}, updated {now}"))

    for lang in languages:
        write_if_changed(out_dir / "languages" / f"{slug(lang)}.m3u",
                         render_m3u(working, f"Working {lang} channels, updated {now}", only_language=lang))
    # One link per channel, verified links first, India-only links included: for Kodi's IPTV Simple Client
    pvr = pick_pvr(channels, working)
    write_if_changed(out_dir / "channels_pvr.m3u", render_m3u(
        pvr, f"One link per channel for Kodi IPTV Simple Client, all languages, updated {now}"))
    write_if_changed(out_dir / "telugu_pvr.m3u", render_m3u(
        pvr, f"One link per channel for Kodi IPTV Simple Client, Telugu, updated {now}", only_language="Telugu"))
    # Older Telugu-only links keep working
    for fname, items, note in (
        ("telugu.m3u", working, "Working Telugu channels"),
        ("telugu_hd.m3u", hd, f"Working Telugu HD channels ({args.hd_height}p+)"),
        ("telugu_all.m3u", channels, "All Telugu channels found"),
    ):
        write_if_changed(out_dir / fname, render_m3u(items, f"{note}, updated {now}", only_language="Telugu"))

    per_language = {}
    for lang in languages:
        per_language[lang] = {
            "total": sum(1 for c in channels if lang in c["languages"]),
            "working": sum(1 for c in working if lang in c["languages"]),
            "hd": sum(1 for c in hd if lang in c["languages"]),
        }
    report = {
        "updated_at": now,
        "fingerprint": fingerprint,
        "total": len(channels),
        "working": len(working),
        "in_grace_period": in_grace,
        "dead": len(dead),
        "hd_channels": len(hd),
        "languages": per_language,
        "dead_channels": [
            {"name": c["name"], "url": c["url"], "reason": c["result"]["reason"]} for c in dead
        ],
    }
    status_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"[build] HD channels: {len(hd)}   languages: {len(languages)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
