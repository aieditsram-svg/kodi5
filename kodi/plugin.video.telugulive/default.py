"""Telugu Live - Kodi video add-on: live Indian TV in every language, HD first, with automatic failover.

Menu
  HD channels      only channels with an HD stream, split by language
  All channels     everything in one list
  <Language>       Telugu, Hindi, Tamil, Kannada, Malayalam, ...
The playlist is downloaded fresh every time a list is opened. When you open a channel the add-on
tests its streams from your own network and plays the best working one; service.py switches to
the next one if a stream drops while playing.
"""
import json
import sys
import time
import urllib.parse

import xbmcaddon
import xbmcgui
import xbmcplugin

import pvr_setup
import resolver

ADDON = xbmcaddon.Addon()
HANDLE = int(sys.argv[1])
BASE = sys.argv[0]
ARGS = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
ALL = "*"


def build_url(**params):
    return BASE + "?" + urllib.parse.urlencode(params)


def hd_minimum():
    try:
        return int(ADDON.getSetting("min_quality") or 720)
    except ValueError:
        return 720


def load_channels():
    """Playlist entries. With 'show unverified' on, this is the full list, which also holds links the
    GitHub server could not reach (for example streams that only work inside India)."""
    show_all = ADDON.getSetting("show_unverified") != "false"
    order = ["list_all", "list_working"] if show_all else ["list_working", "list_all"]
    channels, error = None, None
    for setting in order:
        try:
            channels = resolver.parse_m3u(resolver.fetch_text(ADDON.getSetting(setting).strip()))
            break
        except Exception as exc:
            error = exc
    if channels is None:
        xbmcgui.Dialog().notification("Telugu Live", "Playlist error: %s" % error,
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        channels = []
    # Optional playlist of your own, mixed in with the public channels
    extra = ADDON.getSetting("extra_playlist").strip()
    if extra:
        try:
            mine = resolver.parse_m3u(resolver.read_playlist(extra))
            for ch in mine:
                lang = ch["attrs"].get("tvg-language") or ch["attrs"].get("group-title") or "My playlist"
                ch["attrs"]["tvg-language"] = lang
            channels = mine + channels
        except Exception as exc:
            xbmcgui.Dialog().notification("Telugu Live", "Extra playlist not loaded: %s" % exc,
                                          xbmcgui.NOTIFICATION_WARNING, 5000)
    return channels


def languages_of(channels):
    return sorted({c["attrs"].get("tvg-language") or c["attrs"].get("group-title") or "Other" for c in channels})


def language_of(ch):
    return ch["attrs"].get("tvg-language") or ch["attrs"].get("group-title") or "Other"


def one_per_channel(channels):
    """The playlist holds several links per channel; show each channel once (best quality)."""
    best = {}
    for ch in channels:
        key = resolver.base_name(ch["name"])
        if key not in best or resolver.ch_height(ch) > resolver.ch_height(best[key]):
            best[key] = ch
    return list(best.values())


def view(channels, lang, hd_only):
    items = [c for c in channels if lang == ALL or language_of(c) == lang]
    if hd_only:
        items = [c for c in items if resolver.ch_height(c) >= hd_minimum()]
    return one_per_channel(items)


def add_folder(label, **params):
    xbmcplugin.addDirectoryItem(HANDLE, build_url(**params), xbmcgui.ListItem(label=label), True)


def main_menu(channels):
    hd_total = len(view(channels, ALL, True))
    add_folder("[B]HD channels[/B]  (%d)" % hd_total, action="langs", hd="1")
    add_folder("[B]All channels[/B]  (%d)" % len(view(channels, ALL, False)), action="list", lang=ALL, hd="0")
    counts = {lang: len(view(channels, lang, False)) for lang in languages_of(channels)}
    # Telugu first, then the biggest languages, "Other" last
    order = sorted(counts, key=lambda l: (l == "Other", l != "Telugu", -counts[l], l))
    for lang in order:
        add_folder("%s  (%d)" % (lang, counts[lang]), action="list", lang=lang, hd="0")
    add_folder("[COLOR gray]Load all channels into Kodi's TV section[/COLOR]", action="pvr")
    xbmcplugin.endOfDirectory(HANDLE)


def hd_languages(channels):
    add_folder("[B]All languages[/B]  (%d)" % len(view(channels, ALL, True)), action="list", lang=ALL, hd="1")
    counts = {lang: len(view(channels, lang, True)) for lang in languages_of(channels)}
    order = sorted((l for l in counts if counts[l]), key=lambda l: (l == "Other", l != "Telugu", -counts[l], l))
    for lang in order:
        add_folder("%s  (%d)" % (lang, counts[lang]), action="list", lang=lang, hd="1")
    xbmcplugin.endOfDirectory(HANDLE)


def list_channels(channels, lang, hd_only):
    for ch in view(channels, lang, hd_only):
        name = resolver.clean_name(ch["name"])
        h = resolver.ch_height(ch)
        label = "%s  [COLOR gray]%dp[/COLOR]" % (name, h) if h else name
        item = xbmcgui.ListItem(label=label)
        logo = ch["attrs"].get("tvg-logo")
        if logo:
            item.setArt({"thumb": logo, "icon": logo})
        item.setInfo("video", {"title": name, "genre": ch["attrs"].get("x-category", "")})
        item.setProperty("IsPlayable", "true")
        key = resolver.base_name(ch["name"])
        xbmcplugin.addDirectoryItem(HANDLE, build_url(action="play", key=key, label=name), item, False)
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE)
    xbmcplugin.endOfDirectory(HANDLE)


def play(key, label):
    tried = []
    ch = resolver.first_working(key, tried)
    if ch is None:
        xbmcgui.Dialog().notification("Telugu Live", "No working stream for %s right now" % label,
                                      xbmcgui.NOTIFICATION_WARNING, 5000)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    # Remember what is playing so service.py can fail over if the stream drops.
    win = xbmcgui.Window(10000)
    win.setProperty("telugulive.key", key)
    win.setProperty("telugulive.tried", json.dumps(tried))
    win.setProperty("telugulive.attempts", "0")
    win.setProperty("telugulive.started", str(time.time()))
    xbmcplugin.setResolvedUrl(HANDLE, True, xbmcgui.ListItem(path=resolver.stream_path(ch)))


def main():
    action = ARGS.get("action")
    if action == "play":
        play(ARGS["key"], ARGS.get("label", ARGS["key"]))
        return
    if action == "pvr":
        ok, message = pvr_setup.configure(ADDON.getSetting("pvr_playlist").strip())
        xbmcgui.Dialog().ok("Telugu Live", message)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    channels = load_channels()
    if action == "list":
        list_channels(channels, ARGS.get("lang", ALL), ARGS.get("hd") == "1")
    elif action == "langs":
        hd_languages(channels)
    else:
        main_menu(channels)


main()
