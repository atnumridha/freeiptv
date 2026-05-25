#!/usr/bin/env python3
"""Refresh the public IPTV playlist and optionally push it to GitHub."""

from __future__ import annotations

import argparse
import http.client
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
    "https://iptv-org.github.io/iptv/countries/in.m3u",
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
            "Defaults to IPTV-org India plus PiratesTv combined."
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


def first_media_uri(manifest: str, base_url: str) -> str:
    for line in manifest.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return urljoin(base_url, stripped)
    return ""


def probe_channel(channel: Channel, timeout: float) -> ProbeResult:
    started = time.monotonic()
    checked_url = channel.url
    try:
        headers = request_headers(channel)
        manifest, manifest_url = fetch_text(channel.url, headers, timeout)
        if "#EXTM3U" not in manifest[:4096].upper():
            raise ValueError("response is not an M3U/HLS playlist")

        for _ in range(3):
            stream_uri = first_stream_uri(manifest, manifest_url)
            if stream_uri:
                checked_url = stream_uri
                manifest, manifest_url = fetch_text(stream_uri, headers, timeout)
                continue

            media_uri = first_media_uri(manifest, manifest_url)
            if not media_uri:
                raise ValueError("playlist has no playable media URI")
            if is_hls_url(media_uri):
                checked_url = media_uri
                manifest, manifest_url = fetch_text(media_uri, headers, timeout)
                continue

            checked_url = media_uri
            fetch_bytes(media_uri, headers, timeout)
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
        "potential_duplicate_records": potential_duplicate_records,
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
) -> None:
    source_list = "\n".join(f"- `{source}`" for source in sources)
    content = f"""# freeiptv

Filtered public IPTV playlists.

## Combined Playlist

Playlist:

```text
{RAW_PLAYLIST_URL}
```

Sources:

{source_list}

Last generated result:

- Checked HLS streams: {checked}
- Working channels: {working}
- Duplicate stream URLs skipped: {duplicate_count}
- Potential duplicate channels skipped: {potential_duplicate_count}
- Probe mode: HLS segment probe
- Worker threads: {workers}

The playlist refresh runs daily through GitHub Actions and can also be run
manually with:

```sh
python3 scripts/refresh_playlist.py --push
```

The probe report is stored at `reports/in-report.json`.
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
) -> tuple[list[ProbeResult], int, list[dict[str, str]]]:
    seen: dict[tuple[str, str], ProbeResult] = {}
    unique: list[ProbeResult] = []
    duplicate_records: list[dict[str, str]] = []

    for result in sorted(results, key=lambda item: item.channel.index):
        if not result.ok:
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

    return unique, len(duplicate_records), duplicate_records


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
    published_results, potential_duplicate_count, potential_duplicate_records = (
        dedupe_working_results(results)
    )
    checked = len(results)
    working = len(published_results)

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
    )

    print(
        f"Wrote {working} working channels to {args.output}; "
        f"skipped {potential_duplicate_count} potential duplicate channels.",
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
