"""Load the playlist into Kodi's own TV section by configuring the IPTV Simple Client (pvr.iptvsimple).

What it does, in order:
  1. installs pvr.iptvsimple from Kodi's official repository if it is missing
  2. disables it for a moment, so Kodi does not overwrite the settings file
  3. points a dedicated "Telugu Live" instance (Kodi 20 and newer) or the single client (Kodi 19)
     at the playlist address
  4. enables it again, so the channels appear under TV

Nothing else about the user's PVR setup is changed: on Kodi 20+ any existing instance is left alone.
"""
import json
import os
import re
import time
from xml.sax.saxutils import escape

import xbmc
import xbmcaddon
import xbmcvfs

PVR_ID = "pvr.iptvsimple"
INSTANCE_NAME = "Telugu Live"
REFRESH_MINUTES = 60


def _rpc(method, params):
    reply = xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    try:
        return json.loads(reply)
    except ValueError:
        return {}


def has_pvr():
    return bool(xbmc.getCondVisibility("System.HasAddon(%s)" % PVR_ID))


def ensure_installed(timeout=120):
    """Install the client if needed. Kodi may ask the user to confirm; we wait for it."""
    if has_pvr():
        return True
    xbmc.executebuiltin("InstallAddon(%s)" % PVR_ID)
    monitor = xbmc.Monitor()
    waited = 0
    while waited < timeout and not has_pvr():
        if monitor.waitForAbort(2):
            break
        waited += 2
    return has_pvr()


def _set_enabled(enabled):
    _rpc("Addons.SetAddonEnabled", {"addonid": PVR_ID, "enabled": enabled})


def _major_version():
    try:
        return int(xbmcaddon.Addon(PVR_ID).getAddonInfo("version").split(".")[0])
    except Exception:
        return 21


def _data_dir():
    path = xbmcvfs.translatePath("special://profile/addon_data/%s/" % PVR_ID)
    os.makedirs(path, exist_ok=True)
    return path


def _instance_xml(url):
    return (
        '<settings version="2">\n'
        '    <setting id="kodi_addon_instance_name">%s</setting>\n'
        '    <setting id="kodi_addon_instance_enabled">true</setting>\n'
        '    <setting id="m3uPathType">1</setting>\n'
        '    <setting id="m3uUrl">%s</setting>\n'
        '    <setting id="m3uCache">false</setting>\n'
        '    <setting id="m3uRefreshMode">1</setting>\n'
        '    <setting id="m3uRefreshIntervalMins">%d</setting>\n'
        '</settings>\n' % (escape(INSTANCE_NAME), escape(url), REFRESH_MINUTES))


def _write_instance(url):
    """Kodi 20+: one file per instance. Reuse ours if present, otherwise add the next free number."""
    folder = _data_dir()
    numbers, ours = [], None
    for name in os.listdir(folder):
        m = re.match(r"instance-settings-(\d+)\.xml$", name)
        if not m:
            continue
        numbers.append(int(m.group(1)))
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                if ('>%s<' % escape(INSTANCE_NAME)) in fh.read():
                    ours = int(m.group(1))
        except OSError:
            pass
    number = ours if ours is not None else (max(numbers) + 1 if numbers else 1)
    with open(os.path.join(folder, "instance-settings-%d.xml" % number), "w", encoding="utf-8") as fh:
        fh.write(_instance_xml(url))


def _write_legacy(url):
    """Kodi 19: the client has a single settings set."""
    addon = xbmcaddon.Addon(PVR_ID)
    addon.setSetting("m3uPathType", "1")
    addon.setSetting("m3uUrl", url)
    addon.setSetting("m3uCache", "false")


def configure(url):
    """Returns (ok, message)."""
    if not ensure_installed():
        return False, "Could not install IPTV Simple Client. Install it from Add-ons > PVR clients, then try again."
    monitor = xbmc.Monitor()
    _set_enabled(False)
    monitor.waitForAbort(2)
    try:
        if _major_version() >= 20:
            _write_instance(url)
        else:
            _write_legacy(url)
    except Exception as exc:
        _set_enabled(True)
        return False, "Could not write the IPTV Simple Client settings: %s" % exc
    _set_enabled(True)
    return True, "Done. The channels load in a minute: open TV > Channels, and use Channel groups to pick a language."
