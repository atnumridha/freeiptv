# freeiptv

Filtered working channels from the PiratesTv combined playlist.

## PiratesTv Working Playlist

Playlist:

```text
https://raw.githubusercontent.com/atnumridha/freeiptv/main/in.m3u
```

Sources:

- `https://raw.githubusercontent.com/FunctionError/PiratesTv/main/combined_playlist.m3u`

Last generated result:

- Checked HLS streams: 257
- Working channels: 192
- Duplicate stream URLs skipped: 39
- Potential duplicate channels skipped: 35
- Probe mode: HLS segment probe
- Worker threads: 24

The playlist refresh runs daily through GitHub Actions and can also be run
manually with:

```sh
python3 scripts/refresh_playlist.py --push
```

The probe report is stored at `reports/in-report.json`.
