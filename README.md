# freeiptv

Filtered public IPTV playlists for Bengali, Hindi, and Marathi channels.

## Bengali, Hindi, and Marathi Playlist

Playlist:

```text
https://raw.githubusercontent.com/atnumridha/freeiptv/main/in.m3u
```

Sources:

- `https://iptv-org.github.io/iptv/languages/ben.m3u`
- `https://iptv-org.github.io/iptv/languages/hin.m3u`
- `https://iptv-org.github.io/iptv/languages/mar.m3u`

Last generated result:

- Checked HLS streams: 422
- Working channels: 200
- Duplicate stream URLs skipped: 14
- Potential duplicate channels skipped: 0
- Probe mode: HLS segment probe
- Worker threads: 24

The playlist refresh runs daily through GitHub Actions and can also be run
manually with:

```sh
python3 scripts/refresh_playlist.py --push
```

The probe report is stored at `reports/in-report.json`.
