# freeiptv

Filtered public IPTV playlists.

## Combined Playlist

Playlist:

```text
https://raw.githubusercontent.com/atnumridha/freeiptv/main/in.m3u
```

Sources:

- `https://iptv-org.github.io/iptv/countries/in.m3u`
- `https://raw.githubusercontent.com/FunctionError/PiratesTv/main/combined_playlist.m3u`

Last generated result:

- Checked HLS streams: 992
- Working channels: 644
- Duplicate stream URLs skipped: 48
- Probe mode: HLS segment probe
- Worker threads: 24

The playlist refresh runs daily through GitHub Actions and can also be run
manually with:

```sh
python3 scripts/refresh_playlist.py --push
```

The probe report is stored at `reports/in-report.json`.
