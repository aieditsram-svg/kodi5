# Live TV playlists, Kodi add-on and web player

A GitHub Actions workflow rebuilds the playlists every 5 minutes. Each run fetches the channel sources in `sources.txt`, tests every stream, and keeps the working ones, grouped by language. Dead links drop out after 3 failed checks in a row and come back automatically once they work again.

## Playlist files

All files are at `https://raw.githubusercontent.com/aieditsram-svg/kodi/main/<file>`.

| File | Use it for |
|------|-----------|
| `channels_best.m3u` | **Kodi IPTV Simple Client** and other players: one best working link per channel |
| `channels_hd.m3u` | Same, HD channels only (720p and up) |
| `channels.m3u` | Every working link (several per channel), all languages |
| `telugu_pvr.m3u` | **Kodi Live TV (IPTV Simple Client), Telugu only**: one link per channel, working links first, plus links the server could not check (India-only) so no channel is missing |
| `channels_pvr.m3u` | Same, all languages |
| `channels_all.m3u` | Everything found, including links the server could not check (some only work inside India). The web player and the Kodi add-on use this one |
| `languages/<language>.m3u` | Working links for one language (`telugu.m3u`, `hindi.m3u`, ...) |
| `status.json` | Counts per language and the list of dead links with reasons |

Channels are grouped by language (`group-title` is the language name), so players show a folder per language.

## 1. Kodi Live TV: IPTV Simple Client (all channels in TV / Channels)

**One-click setup (recommended).** Install the Telugu Live add-on (section 2), open it and choose
**Set up Live TV (IPTV Simple Client)**. It installs PVR IPTV Simple Client if needed, points it at
`telugu_pvr.m3u` (or `channels_pvr.m3u` for every language) and restarts it. Open **TV > Channels** a minute later.

**Manual setup.**

1. Add-ons > Install from repository > **PVR clients** > **PVR IPTV Simple Client** > Install.
2. Open its settings > **General**: Location = **Remote path (Internet address)**, M3U play list URL =
   `https://raw.githubusercontent.com/aieditsram-svg/kodi/main/telugu_pvr.m3u`
   (all languages: `channels_pvr.m3u`).
3. Turn off "Cache m3u at local storage", set the refresh to repeat (for example hourly).
4. Restart Kodi. Live TV > Channels shows the channels, and Channel groups shows one group per language.

`*_pvr.m3u` holds one link per channel: a link that passed the server check wins, otherwise the best
unverified link is used. The server that tests links runs outside India, so streams that only work inside
India (many ETV channels, for example) are kept instead of dropped. The PVR client plays the link it was given
and cannot switch to another one if it dies. Use the Telugu Live add-on if you want automatic failover.

If channels look stale after a change: Settings > PVR & Live TV > General > **Clear data**, then restart Kodi.

## 2. Kodi: Telugu Live add-on (instant failover and one-click Live TV setup)

1. Kodi: Settings > System > Add-ons > allow **Unknown sources**.
2. Download `https://raw.githubusercontent.com/aieditsram-svg/kodi/main/repository.telugulive.zip` to the device.
3. Add-ons > Install from zip file > pick `repository.telugulive.zip`.
4. Add-ons > Install from repository > Telugu Live Repository > Video add-ons > **Telugu Live** > Install.

The repository is read from `raw.githubusercontent.com`, so GitHub Pages is not needed for Kodi.

Menu: **Set up Live TV (IPTV Simple Client)**, **HD channels** (split by language), **All channels**, then a folder per
language. In the add-on settings, "HD means at least" switches between 720p and 1080p, and "Also show channels the
server could not verify" keeps links that only work from India visible.

## 3. Web player for a blog or website

`player.html` is a ready-made player: language filter, HD filter, search, and automatic switching to the next link when one fails. It is published at `https://aieditsram-svg.github.io/kodi/player.html` once GitHub Pages is on.

**Embed it anywhere** (Blogger, WordPress, any site that accepts HTML):

```html
<iframe src="https://aieditsram-svg.github.io/kodi/player.html"
        style="width:100%;height:720px;border:0" allow="autoplay; fullscreen" allowfullscreen></iframe>
```

On Blogger: Pages > New page > switch the editor to **HTML view** > paste the iframe > Publish.

Options go on the address: `player.html?lang=Telugu&hd=1`, or `?list=<another .m3u address>`. Change the default list at the top of the `<script>` in `player.html`.

Limits of playing in a browser:
- Browsers block `http://` streams on `https://` pages, and many stream servers refuse web players (CORS). Those channels will not play on the website but will play in Kodi or VLC. The player tries every link of a channel before giving up.
- Some channels are geo-restricted to India.

## Adding sources

Edit `sources.txt`, one playlist per line. Options after `|`: `language=Tamil`, `group=India`, `keywords=a,b`, `enrich=iptv-org`. Sources are merged, de-duplicated by stream URL and tested together.

Run it locally with `python scripts/build_playlist.py` (Python 3.9+, no dependencies).

## Notes

- GitHub starts scheduled runs a few minutes late, so a 5-minute schedule often means 5 to 15 minutes in practice.
- Only use sources that carry free-to-air or officially public streams. You are responsible for the sources you add.
