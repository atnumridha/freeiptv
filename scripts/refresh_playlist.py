#!/usr/bin/env python3
"""Refresh the public IPTV playlist and optionally push it to GitHub."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import ssl
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


DEFAULT_SOURCES = (
    "https://iptv-org.github.io/iptv/languages/ben.m3u",
    "https://iptv-org.github.io/iptv/languages/hin.m3u",
    "https://iptv-org.github.io/iptv/languages/mar.m3u",
    "https://raw.githubusercontent.com/FunctionError/PiratesTv/main/combined_playlist.m3u",
)
RAW_PLAYLIST_URL = "https://raw.githubusercontent.com/atnumridha/freeiptv/main/in.m3u"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MAX_AUTO_WORKERS = 32
GROUP_PRIORITY = {
    "news": 0,
    "movie": 1,
    "movies": 1,
    "entertainment": 2,
}
DEFAULT_GROUP_RANK = 3
MIN_MEDIA_SEGMENTS_TO_VERIFY = 2
MANUAL_EXCLUDED_TVG_IDS = {
    "bigmagic.in@sd",
}
MANUAL_EXCLUDED_NAMES = {
    "big magic",
}
ssl_warning_printed = False


@dataclass(frozen=True)
class Channel:
    index: int
    source: str
    source_index: int
    tags: tuple[str, ...]
    url: str
    name: str
    tvg_id: str
    groups: tuple[str, ...]
    headers: dict[str, str]


@dataclass(frozen=True)
class ProbeResult:
    channel: Channel
    ok: bool
    elapsed_ms: int
    checked_url: str
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh in.m3u from the configured public IPTV playlists."
    )
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help=(
            "Input M3U URL or local file. May be provided more than once. "
            "Defaults to PiratesTv plus IPTV-org Bengali, Hindi, and Marathi feeds."
        ),
    )
    parser.add_argument("--output", default="in.m3u", help="Output M3U path.")
    parser.add_argument(
        "--report",
        default="reports/in-report.json",
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--readme",
        default="README.md",
        help="README path to update with latest run counts.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=24,
        help="Concurrent worker threads. Use 0 for auto. Default: 24",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Network timeout per request in seconds. Default: 10",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print progress every N completed checks. Default: 50",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Commit and push changed playlist files after refresh.",
    )
    return parser.parse_args()


def load_text(source: str, timeout: float) -> str:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = Request(source, headers={"User-Agent": DEFAULT_USER_AGENT})
        with open_url(request, timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    return Path(source).read_text(encoding="utf-8", errors="replace")


def open_url(request: Request, timeout: float):
    try:
        return urlopen(request, timeout=timeout)
    except URLError as error:
        if not is_ssl_certificate_error(error):
            raise
        print_ssl_warning()
        return urlopen(
            request,
            timeout=timeout,
            context=ssl._create_unverified_context(),
        )


def is_ssl_certificate_error(error: URLError) -> bool:
    reason = getattr(error, "reason", None)
    return isinstance(reason, ssl.SSLCertVerificationError)


def print_ssl_warning() -> None:
    global ssl_warning_printed
    if ssl_warning_printed:
        return
    ssl_warning_printed = True
    print(
        "Python SSL certificate verification failed locally; retrying HTTPS "
        "requests without certificate verification for this refresh.",
        file=sys.stderr,
        flush=True,
    )


def parse_m3u(text: str, source: str) -> tuple[str, list[Channel]]:
    lines = [line.strip() for line in text.splitlines()]
    header = "#EXTM3U"
    start_at = 0
    if lines and lines[0].upper().startswith("#EXTM3U"):
        header = lines[0]
        start_at = 1

    pending_tags: list[str] = []
    channels: list[Channel] = []
    for line in lines[start_at:]:
        if not line:
            continue
        if line.startswith("#"):
            pending_tags.append(line)
            continue

        channels.append(
            Channel(
                index=len(channels),
                source=source,
                source_index=len(channels),
                tags=tuple(pending_tags),
                url=line,
                name=extract_name(pending_tags),
                tvg_id=extract_tvg_id(pending_tags),
                groups=extract_groups(pending_tags),
                headers=extract_headers(pending_tags),
            )
        )
        pending_tags = []

    return header, channels


def extract_name(tags: Iterable[str]) -> str:
    for tag in tags:
        if tag.upper().startswith("#EXTINF"):
            _, _, name = tag.rpartition(",")
            return name.strip() or "Unnamed channel"
    return "Unnamed channel"


def extract_tvg_id(tags: Iterable[str]) -> str:
    for tag in tags:
        if not tag.upper().startswith("#EXTINF"):
            continue
        match = re.search(r'tvg-id="([^"]*)"', tag, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def extract_groups(tags: Iterable[str]) -> tuple[str, ...]:
    for tag in tags:
        if not tag.upper().startswith("#EXTINF"):
            continue
        match = re.search(r'group-title="([^"]*)"', tag, flags=re.IGNORECASE)
        if not match:
            match = re.search(r"group-title=([^,\s]+)", tag, flags=re.IGNORECASE)
        if not match:
            continue
        groups = [
            group.strip()
            for group in match.group(1).split(";")
            if group.strip()
        ]
        return tuple(groups)
    return ()


def extract_headers(tags: Iterable[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for tag in tags:
        lower = tag.lower()
        if lower.startswith("#extvlcopt:"):
            option = tag.split(":", 1)[1]
            key, separator, value = option.partition("=")
            if separator:
                record_header(headers, key.strip(), value.strip())
            continue

        for key, value in re.findall(r'([\w-]+)="([^"]*)"', tag):
            record_header(headers, key.strip(), value.strip())
        for key, value in re.findall(r"([\w-]+)=([^,\s]+)", tag):
            record_header(headers, key.strip(), value.strip())

    return headers


def record_header(headers: dict[str, str], key: str, value: str) -> None:
    normalized = key.lower()
    if normalized in {"http-user-agent", "user-agent"}:
        headers["User-Agent"] = value
    elif normalized in {"http-referrer", "http-referer", "referer", "referrer"}:
        headers["Referer"] = value


def is_hls_url(url: str) -> bool:
    return ".m3u8" in urlparse(url).path.lower()


def request_headers(channel: Channel) -> dict[str, str]:
    headers = {
        "Accept": "*/*",
        "User-Agent": channel.headers.get("User-Agent", DEFAULT_USER_AGENT),
    }
    if "Referer" in channel.headers:
        headers["Referer"] = channel.headers["Referer"]
    return headers


def fetch_text(url: str, headers: dict[str, str], timeout: float) -> tuple[str, str]:
    request = Request(url, headers=headers)
    with open_url(request, timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        return body, response.geturl()


def fetch_bytes(url: str, headers: dict[str, str], timeout: float) -> None:
    headers_with_range = dict(headers)
    headers_with_range["Range"] = "bytes=0-4095"
    request = Request(url, headers=headers_with_range)
    with open_url(request, timeout) as response:
        if not response.read(4096):
            raise ValueError("media segment returned no bytes")


def first_stream_uri(manifest: str, base_url: str) -> str:
    lines = [line.strip() for line in manifest.splitlines() if line.strip()]
    for offset, line in enumerate(lines):
        if line.upper().startswith("#EXT-X-STREAM-INF"):
            for candidate in lines[offset + 1 :]:
                if not candidate.startswith("#"):
                    return urljoin(base_url, candidate)
    return ""


def is_ip_literal_url(url: str) -> bool:
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def reject_ip_literal_url(url: str, description: str) -> None:
    if is_ip_literal_url(url):
        raise ValueError(f"IP-literal HLS {description} rejected for IPTV compatibility")


def first_media_uri(manifest: str, base_url: str) -> str:
    for line in manifest.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return urljoin(base_url, stripped)
    return ""


def media_segment_uris(manifest: str, base_url: str, limit: int) -> list[str]:
    segments: list[str] = []
    for line in manifest.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        segments.append(urljoin(base_url, stripped))
        if len(segments) >= limit:
            break
    return segments


def is_fragmented_mp4_playlist(manifest: str, segments: list[str]) -> bool:
    upper = manifest.upper()
    if "#EXT-X-MAP" in upper:
        return True
    return any(urlparse(segment).path.casefold().endswith(".m4s") for segment in segments)


def probe_channel(channel: Channel, timeout: float) -> ProbeResult:
    started = time.monotonic()
    checked_url = channel.url
    try:
        headers = request_headers(channel)
        reject_ip_literal_url(channel.url, "origin")
        manifest, manifest_url = fetch_text(channel.url, headers, timeout)
        reject_ip_literal_url(manifest_url, "origin")
        if "#EXTM3U" not in manifest[:4096].upper():
            raise ValueError("response is not an M3U/HLS playlist")

        for _ in range(3):
            stream_uri = first_stream_uri(manifest, manifest_url)
            if stream_uri:
                reject_ip_literal_url(stream_uri, "variant")
                checked_url = stream_uri
                manifest, manifest_url = fetch_text(stream_uri, headers, timeout)
                reject_ip_literal_url(manifest_url, "variant")
                continue

            media_uri = first_media_uri(manifest, manifest_url)
            if not media_uri:
                raise ValueError("playlist has no playable media URI")
            if is_hls_url(media_uri):
                reject_ip_literal_url(media_uri, "media playlist")
                checked_url = media_uri
                manifest, manifest_url = fetch_text(media_uri, headers, timeout)
                reject_ip_literal_url(manifest_url, "media playlist")
                continue

            segment_uris = media_segment_uris(
                manifest,
                manifest_url,
                MIN_MEDIA_SEGMENTS_TO_VERIFY,
            )
            if is_fragmented_mp4_playlist(manifest, segment_uris):
                raise ValueError("fragmented MP4 HLS rejected for IPTV compatibility")
            if len(segment_uris) < MIN_MEDIA_SEGMENTS_TO_VERIFY:
                raise ValueError("playlist has too few media segments")
            for segment_uri in segment_uris:
                reject_ip_literal_url(segment_uri, "segment")

            checked_url = segment_uris[-1]
            for segment_uri in segment_uris:
                fetch_bytes(segment_uri, headers, timeout)
            return make_result(channel, True, started, checked_url)

        raise ValueError("nested playlist depth exceeded")
    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
        ValueError,
        http.client.IncompleteRead,
    ) as error:
        return make_result(channel, False, started, checked_url, str(error))


def make_result(
    channel: Channel,
    ok: bool,
    started: float,
    checked_url: str,
    error: str = "",
) -> ProbeResult:
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return ProbeResult(channel, ok, elapsed_ms, checked_url, error)


def resolve_worker_count(requested: int, stream_count: int) -> int:
    if requested > 0:
        return requested
    cpu_count = os.cpu_count() or 4
    return max(1, min(stream_count or 1, MAX_AUTO_WORKERS, cpu_count * 4))


def write_playlist(path: Path, header: str, results: list[ProbeResult]) -> None:
    sorted_results = sorted(
        (result for result in results if result.ok),
        key=playlist_sort_key,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write(header.rstrip() + "\n")
        for result in sorted_results:
            for tag in result.channel.tags:
                output.write(tag.rstrip() + "\n")
            output.write(result.channel.url.rstrip() + "\n")


def playlist_sort_key(result: ProbeResult) -> tuple[int, str, str, int]:
    channel = result.channel
    return (
        group_rank(channel.groups),
        primary_group_name(channel.groups),
        channel.name.casefold(),
        channel.index,
    )


def group_rank(groups: tuple[str, ...]) -> int:
    if not groups:
        return DEFAULT_GROUP_RANK
    return GROUP_PRIORITY.get(groups[0].casefold(), DEFAULT_GROUP_RANK)


def primary_group_name(groups: tuple[str, ...]) -> str:
    if not groups:
        return "~"
    return groups[0].casefold()


def write_report(
    path: Path,
    sources: list[str],
    workers: int,
    results: list[ProbeResult],
    published_results: list[ProbeResult],
    skipped: int,
    duplicate_count: int,
    potential_duplicate_count: int,
    potential_duplicate_records: list[dict[str, str]],
    manual_excluded_count: int,
    manual_excluded_records: list[dict[str, str]],
    incompatible_hls_count: int,
    ip_literal_hls_count: int,
    source_summaries: list[dict[str, int | str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sources": sources,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "probe_mode": "hls",
        "worker_threads": workers,
        "checked": len(results),
        "working_before_duplicate_filter": sum(1 for result in results if result.ok),
        "working": len(published_results),
        "failed": sum(1 for result in results if not result.ok),
        "skipped_non_m3u8": skipped,
        "skipped_duplicate_urls": duplicate_count,
        "skipped_potential_duplicate_channels": potential_duplicate_count,
        "skipped_manual_exclusions": manual_excluded_count,
        "skipped_incompatible_hls": incompatible_hls_count,
        "skipped_ip_literal_hls": ip_literal_hls_count,
        "potential_duplicate_records": potential_duplicate_records,
        "manual_excluded_records": manual_excluded_records,
        "source_summaries": source_summaries,
        "results": [
            {
                "index": result.channel.index,
                "source": result.channel.source,
                "source_index": result.channel.source_index,
                "name": result.channel.name,
                "tvg_id": result.channel.tvg_id,
                "groups": list(result.channel.groups),
                "url": result.channel.url,
                "ok": result.ok,
                "published": result in published_results,
                "elapsed_ms": result.elapsed_ms,
                "checked_url": result.checked_url,
                "error": result.error,
            }
            for result in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def update_readme(
    path: Path,
    sources: list[str],
    workers: int,
    checked: int,
    working: int,
    duplicate_count: int,
    potential_duplicate_count: int,
    manual_excluded_count: int,
    incompatible_hls_count: int,
    ip_literal_hls_count: int,
) -> None:
    source_list = "\n".join(f"- `{source}`" for source in sources)
    content = f"""# freeiptv

Filtered IPTV playlist built from Bengali, Hindi, Marathi, and selected public
M3U sources. The build checks HLS streams, removes duplicates, sorts channels by
group, captures playback screenshots, and publishes the final playlist through
GitHub raw URLs.

## Combined Working Playlist

Use this playlist URL in IPTV players:

```text
{RAW_PLAYLIST_URL}
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

{source_list}

## Latest Build Stats

- Checked HLS streams: {checked}
- Working channels: {working}
- Duplicate stream URLs skipped: {duplicate_count}
- Potential duplicate channels skipped: {potential_duplicate_count}
- Manual exclusions skipped: {manual_excluded_count}
- Incompatible fMP4 HLS streams skipped: {incompatible_hls_count}
- IP-literal HLS streams skipped: {ip_literal_hls_count}
- Probe mode: HLS segment probe
- Worker threads: {workers}

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
- Sorts channels by group with News first, then Movies, then Entertainment, then
  the remaining groups alphabetically.

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
python3 scripts/build_playlist.py --refresh-workers 24 --refresh-timeout 10 --capture-workers 8 --capture-timeout 90 --capture-seconds 2 --retry-capture-seconds 20
```

If the generated playlist, reports, README, or screenshots change, the workflow
commits and pushes the updates back to `main`.
"""
    path.write_text(content, encoding="utf-8")


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def commit_and_push(paths: list[str]) -> int:
    run_git(["git", "add", *paths])
    diff = run_git(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("No playlist changes to commit.", flush=True)
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commit = run_git(["git", "commit", "-m", f"Refresh India IPTV playlist ({timestamp})"])
    if commit.returncode != 0:
        print(commit.stderr or commit.stdout, file=sys.stderr)
        return commit.returncode

    push = run_git(["git", "push"])
    if push.returncode != 0:
        print(push.stderr or push.stdout, file=sys.stderr)
        return push.returncode

    print(commit.stdout.strip(), flush=True)
    print(push.stdout.strip(), flush=True)
    return 0


def load_sources(sources: list[str], timeout: float) -> tuple[list[Channel], int, list[dict[str, int | str]]]:
    all_streams: list[Channel] = []
    skipped_non_hls = 0
    source_summaries: list[dict[str, int | str]] = []

    for source in sources:
        text = load_text(source, timeout)
        _, channels = parse_m3u(text, source)
        streams = [channel for channel in channels if is_hls_url(channel.url)]
        skipped = len(channels) - len(streams)
        for channel in streams:
            all_streams.append(replace(channel, index=len(all_streams)))
        skipped_non_hls += skipped
        source_summaries.append(
            {
                "source": source,
                "channels": len(channels),
                "hls_streams": len(streams),
                "skipped_non_m3u8": skipped,
            }
        )

    return all_streams, skipped_non_hls, source_summaries


def dedupe_streams(streams: list[Channel]) -> tuple[list[Channel], int]:
    seen: set[str] = set()
    unique: list[Channel] = []
    duplicate_count = 0
    for channel in streams:
        key = channel.url.strip()
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        unique.append(channel)
    return unique, duplicate_count


def dedupe_working_results(
    results: list[ProbeResult],
) -> tuple[list[ProbeResult], int, list[dict[str, str]], int, list[dict[str, str]]]:
    seen: dict[tuple[str, str], ProbeResult] = {}
    unique: list[ProbeResult] = []
    duplicate_records: list[dict[str, str]] = []
    manual_excluded_records: list[dict[str, str]] = []

    for result in sorted(results, key=lambda item: item.channel.index):
        if not result.ok:
            continue
        if is_manually_excluded(result.channel):
            manual_excluded_records.append(
                {
                    "name": result.channel.name,
                    "tvg_id": result.channel.tvg_id,
                    "url": result.channel.url,
                    "source": result.channel.source,
                }
            )
            continue
        key = potential_duplicate_key(result.channel)
        if key in seen:
            kept = seen[key]
            duplicate_records.append(
                {
                    "key_type": key[0],
                    "key": key[1],
                    "kept": kept.channel.name,
                    "skipped": result.channel.name,
                    "skipped_url": result.channel.url,
                }
            )
            continue
        seen[key] = result
        unique.append(result)

    return (
        unique,
        len(duplicate_records),
        duplicate_records,
        len(manual_excluded_records),
        manual_excluded_records,
    )


def is_manually_excluded(channel: Channel) -> bool:
    if channel.tvg_id.casefold() in MANUAL_EXCLUDED_TVG_IDS:
        return True
    return normalize_channel_name(channel.name) in MANUAL_EXCLUDED_NAMES


def incompatible_hls_result_count(results: list[ProbeResult]) -> int:
    return sum(
        1
        for result in results
        if "fragmented MP4 HLS" in result.error
    )


def ip_literal_hls_result_count(results: list[ProbeResult]) -> int:
    return sum(
        1
        for result in results
        if "IP-literal HLS" in result.error
    )


def potential_duplicate_key(channel: Channel) -> tuple[str, str]:
    if channel.tvg_id:
        return ("tvg_id", channel.tvg_id.casefold())
    normalized_name = normalize_channel_name(channel.name)
    if normalized_name:
        return ("name", normalized_name)
    return ("url", channel.url.strip().casefold())


def normalize_channel_name(name: str) -> str:
    normalized = re.sub(r"\[[^\]]*\]", " ", name)
    normalized = re.sub(r"\([^)]*\)", " ", normalized)
    normalized = re.sub(r"\b(4k|uhd|fhd|hd|sd)\b", " ", normalized, flags=re.IGNORECASE)
    normalized = normalized.encode("ascii", errors="ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.casefold())
    return " ".join(normalized.split())


def refresh(args: argparse.Namespace) -> tuple[int, int, int]:
    sources = args.source or list(DEFAULT_SOURCES)
    loaded_streams, skipped, source_summaries = load_sources(sources, args.timeout)
    streams, duplicate_count = dedupe_streams(loaded_streams)
    workers = resolve_worker_count(args.workers, len(streams))

    print(
        f"Loaded {len(sources)} sources; checking {len(streams)} unique HLS streams "
        f"across {workers} worker threads; skipped {duplicate_count} duplicate URLs.",
        flush=True,
    )

    results: list[ProbeResult] = []
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="iptv-refresh",
    ) as executor:
        futures = {
            executor.submit(probe_channel, channel, args.timeout): channel
            for channel in streams
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            channel = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                results.append(
                    ProbeResult(
                        channel=channel,
                        ok=False,
                        elapsed_ms=0,
                        checked_url=channel.url,
                        error=f"unexpected probe error: {error}",
                    )
                )
            if (
                args.progress_every > 0
                and (completed % args.progress_every == 0 or completed == len(futures))
            ):
                working_so_far = sum(1 for result in results if result.ok)
                print(f"Checked {completed}/{len(futures)}; working {working_so_far}.", flush=True)

    results.sort(key=lambda result: result.channel.index)
    (
        published_results,
        potential_duplicate_count,
        potential_duplicate_records,
        manual_excluded_count,
        manual_excluded_records,
    ) = dedupe_working_results(results)
    checked = len(results)
    working = len(published_results)
    incompatible_hls_count = incompatible_hls_result_count(results)
    ip_literal_hls_count = ip_literal_hls_result_count(results)

    write_playlist(Path(args.output), "#EXTM3U", published_results)
    write_report(
        Path(args.report),
        sources,
        workers,
        results,
        published_results,
        skipped,
        duplicate_count,
        potential_duplicate_count,
        potential_duplicate_records,
        manual_excluded_count,
        manual_excluded_records,
        incompatible_hls_count,
        ip_literal_hls_count,
        source_summaries,
    )
    update_readme(
        Path(args.readme),
        sources,
        workers,
        checked,
        working,
        duplicate_count,
        potential_duplicate_count,
        manual_excluded_count,
        incompatible_hls_count,
        ip_literal_hls_count,
    )

    print(
        f"Wrote {working} working channels to {args.output}; "
        f"skipped {potential_duplicate_count} potential duplicate channels "
        f"and {manual_excluded_count} manual exclusions.",
        flush=True,
    )
    return checked, working, workers


def main() -> int:
    args = parse_args()
    if args.workers < 0:
        print("--workers must be 0 for auto or a positive thread count", file=sys.stderr)
        return 2

    refresh(args)
    if args.push:
        return commit_and_push([args.output, args.report, args.readme])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
