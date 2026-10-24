"""Background service: if a Telugu Live stream fails during playback, switch to the next working link."""
import json
import time

import xbmc
import xbmcaddon
import xbmcgui

import pvr_setup
import resolver

WIN = xbmcgui.Window(10000)
MAX_SWITCHES = 4


class FailoverPlayer(xbmc.Player):
    last_switch = 0.0

    def onPlayBackError(self):
        key = WIN.getProperty("telugulive.key")
        if not key:
            return  # something else was playing
        attempts = int(WIN.getProperty("telugulive.attempts") or 0)
        if attempts >= MAX_SWITCHES:
            xbmcgui.Dialog().notification("Telugu Live", "No working backup stream found",
                                          xbmcgui.NOTIFICATION_WARNING, 5000)
            self._clear()
            return
        try:
            tried = json.loads(WIN.getProperty("telugulive.tried") or "[]")
        except ValueError:
            tried = []
        ch = resolver.first_working(key, tried)
        WIN.setProperty("telugulive.tried", json.dumps(tried))
        if ch is None:
            xbmcgui.Dialog().notification("Telugu Live", "No working backup stream found",
                                          xbmcgui.NOTIFICATION_WARNING, 5000)
            self._clear()
            return
        WIN.setProperty("telugulive.attempts", str(attempts + 1))
        FailoverPlayer.last_switch = time.time()
        path = resolver.stream_path(ch)
        self.play(path, xbmcgui.ListItem(label=ch["name"], path=path))
        xbmcgui.Dialog().notification("Telugu Live", "Switched to a backup stream",
                                      xbmcgui.NOTIFICATION_INFO, 3000)

    def onPlayBackStopped(self):
        self._forget_if_user_stopped()

    def onPlayBackEnded(self):
        self._forget_if_user_stopped()

    def _forget_if_user_stopped(self):
        # A stop right after a failover is part of the switch, not the user leaving the channel.
        if time.time() - FailoverPlayer.last_switch > 8:
            self._clear()

    @staticmethod
    def _clear():
        for name in ("key", "tried", "attempts", "started"):
            WIN.clearProperty("telugulive." + name)


def offer_tv_setup(monitor):
    """Once, a little after Kodi has started: offer to load all channels into Kodi's TV section."""
    addon = xbmcaddon.Addon("plugin.video.telugulive")
    if addon.getSetting("pvr_prompted") == "true":
        return
    if monitor.waitForAbort(20):
        return
    addon.setSetting("pvr_prompted", "true")  # ask only once; the add-on menu can run it again any time
    if xbmcgui.Dialog().yesno(
            "Telugu Live",
            "Load all channels into Kodi's TV section?\n\n"
            "This sets up IPTV Simple Client with the Telugu Live playlist, grouped by language."):
        ok, message = pvr_setup.configure(addon.getSetting("pvr_playlist").strip())
        xbmcgui.Dialog().ok("Telugu Live", message)


if __name__ == "__main__":
    monitor = xbmc.Monitor()
    player = FailoverPlayer()
    offer_tv_setup(monitor)
    while not monitor.abortRequested():
        if monitor.waitForAbort(2):
            break
