# freeiptv

Filtered IPTV playlist built from Bengali, Hindi, Marathi, and selected public
M3U sources. The build checks HLS streams, removes duplicates, sorts channels by
group, captures playback screenshots, and publishes the final playlist through
GitHub raw URLs.

## Combined Working Playlist

Use this playlist URL in IPTV players:

```text
https://raw.githubusercontent.com/atnumridha/freeiptv/main/in.m3u
```

## South Asia and Cricket Playlist

This generated playlist is separate from `in.m3u`. It is built by scraping
public playlist indexes and web-searched public M3U sources, then keeping only
India, Pakistan, Bangladesh, and cricket-broadcast candidates that pass both HLS
probing and screenshot validation. Arabic and Arab-region channels are excluded,
and the main list is restricted to Hindi, Bengali/Bangla, and Marathi channels.
English channels are allowed but sorted after the Indian-language sections.
Channels outside News, Movies, Entertainment, Music, Sports, Infotainment, and
Horror are removed. Recognizable Indian TV brands are promoted within each
category before the remaining channels in that category.
The free-playlist article links were reviewed as candidate inputs. IPTV-Org and
Free-TV were already in the build path; DistroTV is now included with a stricter
same-name duplicate guard. The other static article playlists either produced no
in-scope channels, only duplicates, or were unavailable during validation.

```text
https://raw.githubusercontent.com/atnumridha/freeiptv/main/south_asia_cricket.m3u
```

Run it with:

```sh
python3 scripts/scrape_south_asia_cricket_playlist.py
```

Generated files:

- `south_asia_cricket.m3u`: final screenshot-verified playlist.
- `reports/south-asia-cricket-report.json`: source, probe, duplicate, and
  filtering report.
- `reports/south-asia-cricket-screenshot-report.json`: screenshot validation
  report.
- `reports/south-asia-cricket-channels.md`: final working channel list.
- `screenshots/south-asia-cricket/*.jpg`: screenshot evidence for each retained
  channel.

## Current Sources

- `manual:always-included`
- `https://iptv-org.github.io/iptv/languages/ben.m3u`
- `https://iptv-org.github.io/iptv/languages/hin.m3u`
- `https://iptv-org.github.io/iptv/languages/mar.m3u`
- `https://raw.githubusercontent.com/FunctionError/PiratesTv/main/combined_playlist.m3u`

## Latest Build Stats

- Checked HLS streams: 685
- Published channels: 360
- Duplicate stream URLs skipped: 82
- Potential duplicate channels skipped: 30
- Manual exclusions skipped: 0
- Incompatible fMP4 HLS streams skipped: 5
- IP-literal HLS streams skipped: 137
- Always-included failed probes published: 2
- Probe mode: HLS segment probe
- Worker threads: 24

## One Command Build

Run the full refresh, validation, screenshot capture, report update, commit, and
push with:

```sh
python3 scripts/build_playlist.py --push
```

The same command is used by the scheduled GitHub Actions workflow. GitHub
Actions installs `ffmpeg` automatically before running the build.

## Adding Future Playlists

Pass new playlist URLs or local `.m3u` files with repeated `--source` values:

```sh
python3 scripts/build_playlist.py --source playlist.m3u --source https://example.com/playlist.m3u --push
```

You can also pass sources positionally:

```sh
python3 scripts/build_playlist.py playlist.m3u https://example.com/playlist.m3u --push
```

When custom sources are provided, they replace the default source list. To add
custom sources while keeping the defaults, use:

```sh
python3 scripts/build_playlist.py --include-default-sources --source extra.m3u --push
```

For many sources, put one URL or file path per line in a text file. Blank lines
and lines starting with `#` are ignored.

```sh
python3 scripts/build_playlist.py --source-list sources.txt --push
```

## Validation Rules

The build keeps only HLS `.m3u8` streams during the first refresh stage. It then:

- Probes HLS manifests and verifies media segments are reachable.
- Removes exact duplicate stream URLs.
- Removes likely duplicate channels by `tvg-id` or normalized channel name.
- Applies manual exclusions for known bad channel identities.
- Rejects fMP4 HLS playlists because many IPTV players fail on them.
- Rejects raw IP-literal HLS origins, variants, media playlists, and segments
  for better player compatibility.
- Sorts channels by group with News first, pins Aaj Tak HD first, promotes top
  Indian news brands such as ABP, Republic, TV9, News18, India TV, NDTV, and
  Zee, then keeps Sports and Movies ahead of general entertainment/other groups.

Screenshot validation is currently evidence-only by default. It does not remove
a channel from `in.m3u` unless `--filter-by-screenshot` is explicitly provided.
This avoids dropping slow-loading channels too aggressively.

## Screenshot Capture

`scripts/capture_channel_screenshots.py` uses `ffmpeg` and `ffprobe` with
multiple workers.

- First capture attempt: frame at 2 seconds.
- Retry capture attempt: frame at 20 seconds if the first attempt fails.
- Audio-only streams are verified with `ffprobe` and get an audio evidence JPEG.
- Screenshots are written to `screenshots/latest`.
- Capture details are written to `reports/screenshot-report.json`.

To force screenshot failures to remove channels from the final playlist:

```sh
python3 scripts/build_playlist.py --filter-by-screenshot --push
```

To fail the build if any channel cannot produce screenshot evidence:

```sh
python3 scripts/build_playlist.py --require-all-screenshots
```

## Generated Files

- `in.m3u`: final public playlist.
- `reports/in-report.json`: HLS probe report with source, duplicate, and failure
  details.
- `reports/screenshot-report.json`: playback screenshot report.
- `screenshots/latest/*.jpg`: latest playback evidence images.
- `README.md`: latest source list, counts, and usage notes.

## Local Requirements

- Python 3.
- `ffmpeg` and `ffprobe` for screenshot capture.
- Git remote access if using `--push`.

On macOS with Homebrew:

```sh
brew install ffmpeg
```

## Automation

The GitHub Actions workflow runs daily and can also be started manually from the
Actions tab. It runs:

```sh
python3 scripts/build_playlist.py --refresh-workers 24 --refresh-timeout 60 --capture-workers 8 --capture-timeout 90 --capture-seconds 2 --retry-capture-seconds 20
```

If the generated playlist, reports, README, or screenshots change, the workflow
commits and pushes the updates back to `main`.
