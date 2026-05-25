# freeiptv

Filtered working channels from PiratesTv plus Bengali, Hindi, and Marathi feeds.

## Combined Working Playlist

Playlist:

```text
https://raw.githubusercontent.com/atnumridha/freeiptv/main/in.m3u
```

Sources:

- `https://iptv-org.github.io/iptv/languages/ben.m3u`
- `https://iptv-org.github.io/iptv/languages/hin.m3u`
- `https://iptv-org.github.io/iptv/languages/mar.m3u`
- `https://raw.githubusercontent.com/FunctionError/PiratesTv/main/combined_playlist.m3u`

Last generated result:

- Checked HLS streams: 668
- Working channels: 351
- Screenshot playback captures: 351/351
- Duplicate stream URLs skipped: 64
- Potential duplicate channels skipped: 33
- Manual exclusions skipped: 0
- Incompatible fMP4 HLS streams skipped: 6
- IP-literal HLS streams skipped: 135
- Probe mode: HLS segment probe
- Worker threads: 24

The playlist refresh runs daily through GitHub Actions and can also be run
manually with:

```sh
python3 scripts/refresh_playlist.py --push
python3 scripts/capture_channel_screenshots.py --playlist in.m3u --readme README.md
```

The probe report is stored at `reports/in-report.json`. Playback screenshots
are stored under `screenshots/latest`, with capture details at
`reports/screenshot-report.json`.
