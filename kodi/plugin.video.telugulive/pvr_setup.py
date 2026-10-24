"""One-click setup of Kodi's PVR IPTV Simple Client, so every channel shows up under TV / Live TV.

What it does
  1. installs the PVR IPTV Simple Client from Kodi's own repository if it is missing,
  2. points it at this repository's one-link-per-channel playlist (Telugu only, or all languages),
  3. restarts the client so the channels load right away.
It only touches its own "Telugu Live" entry; any other IPTV Simple Client setup you have is left alone.
"""
import json
import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

import resolver

PVR_ID = "pvr.iptvsimple"
INSTANCE_NAME = "Telugu Live"
ADDON = xbmcaddon.Addon()

# file name inside the repository, by what the person picked
LISTS = {"telugu": "telugu_pvr.m3u", "all": "channels_pvr.m3u"}


def log(message):
    xbmc.log("[Telugu Live] " + message, xbmc.LOGINFO)


def kodi_major():
    match = re.match(r"\s*(\d+)", xbmc.getInfoLabel("System.BuildVersion") or "")
    return int(match.group(1)) if match else 21


def client_installed():
    return xbmc.getCondVisibility("System.HasAddon(%s)" % PVR_ID)


def set_client_enabled(enabled):
    request = {"jsonrpc": "2.0", "id": 1, "method": "Addons.SetAddonEnabled",
               "params": {"addonid": PVR_ID, "enabled": enabled}}
    xbmc.executeJSONRPC(json.dumps(request))


def ensure_client(monitor):
    """Install the IPTV Simple Client if needed. Kodi asks the person to confirm the download."""
    if client_installed():
        return True
    xbmc.executebuiltin("InstallAddon(%s)" % PVR_ID)
    for _ in range(120):  # up to two minutes: confirmation dialog + download
        if monitor.waitForAbort(1):
            return False
        if client_installed():
            return True
    return False


def playlist_url(scope):
    """Same folder as the 'all channels' playlist set in this add-on's settings."""
    base = (ADDON.getSetting("list_all") or "").strip().rsplit("/", 1)[0]
    return "%s/%s" % (base, LISTS[scope])


def client_settings(url):
    return [
        ("m3uPathType", "1"),            # 1 = remote address (URL)
        ("m3uUrl", url),
        ("m3uCache", "false"),           # always read the fresh list
        ("m3uRefreshMode", "1"),         # repeated refresh ...
        ("m3uRefreshIntervalMins", "60"),  # ... every hour
        ("startNum", "1"),
        ("numberByOrder", "true"),
    ]


def addon_data_dir():
    folder = xbmcvfs.translatePath("special://profile/addon_data/%s/" % PVR_ID)
    os.makedirs(folder, exist_ok=True)
    return folder


def write_instance(url):
    """Kodi 20 and newer: one instance-settings-N.xml per IPTV Simple Client setup."""
    folder = addon_data_dir()
    mine, used = None, []
    for name in sorted(os.listdir(folder)):
        match = re.match(r"instance-settings-(\d+)\.xml$", name)
        if not match:
            continue
        used.append(int(match.group(1)))
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                if ">%s<" % INSTANCE_NAME in handle.read():
                    mine = name
        except OSError:
            continue
    if mine is None:
        mine = "instance-settings-%d.xml" % (max(used) + 1 if used else 1)
    values = [("kodi_addon_instance_name", INSTANCE_NAME), ("kodi_addon_instance_enabled", "true")]
    values += client_settings(url)
    rows = "\n".join('    <setting id="%s">%s</setting>' % (key, escape(val)) for key, val in values)
    with open(os.path.join(folder, mine), "w", encoding="utf-8") as handle:
        handle.write('<settings version="2">\n%s\n</settings>\n' % rows)
    return mine


def write_single_settings(url):
    """Kodi 19: one settings.xml for the whole client; existing values are kept except the ones we set."""
    path = os.path.join(addon_data_dir(), "settings.xml")
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        root = ET.Element("settings", {"version": "2"})
    for key, val in client_settings(url):
        node = next((n for n in root.findall("setting") if n.get("id") == key), None)
        if node is None:
            node = ET.SubElement(root, "setting", {"id": key})
        node.attrib.pop("default", None)
        node.text = val
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return "settings.xml"


def run():
    monitor = xbmc.Monitor()
    dialog = xbmcgui.Dialog()
    choice = dialog.select("Live TV setup (IPTV Simple Client)",
                           ["Telugu channels only", "All languages (every channel)"])
    if choice < 0:
        return
    scope = "telugu" if choice == 0 else "all"
    url = playlist_url(scope)

    # Check the list is really published before touching any settings.
    try:
        count = resolver.fetch_text(url, timeout=20).count("#EXTINF")
    except Exception as exc:
        dialog.ok("Telugu Live", "Could not download the channel list:[CR]%s[CR][CR]%s" % (url, exc))
        return
    if count == 0:
        dialog.ok("Telugu Live", "The channel list is empty right now. Try again in a few minutes.")
        return

    if not ensure_client(monitor):
        dialog.ok("Telugu Live",
                  "PVR IPTV Simple Client is not installed yet.[CR]Install it from Add-ons > Install from "
                  "repository > PVR clients, then run this setup again.")
        return

    set_client_enabled(True)
    try:
        written = write_instance(url) if kodi_major() >= 20 else write_single_settings(url)
    except OSError as exc:
        dialog.ok("Telugu Live", "Could not write the IPTV Simple Client settings:[CR]%s" % exc)
        return
    log("wrote %s for %s" % (written, url))

    # Restart the client so it reads the new settings.
    set_client_enabled(False)
    monitor.waitForAbort(2)
    set_client_enabled(True)
    dialog.ok("Telugu Live",
              "Done: %d channels are loading into Live TV.[CR]Open TV > Channels in a minute. The list "
              "refreshes by itself every hour." % count)
