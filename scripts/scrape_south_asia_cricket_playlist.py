#!/usr/bin/env python3
"""Build a screenshot-verified South Asia and cricket IPTV playlist."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from refresh_playlist import Channel
from refresh_playlist import ProbeResult
from refresh_playlist import dedupe_streams
from refresh_playlist import dedupe_working_results
from refresh_playlist import is_hls_url
from refresh_playlist import load_text
from refresh_playlist import probe_channel
from refresh_playlist import resolve_worker_count
from refresh_playlist import write_playlist


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PLAYLIST_INDEX = "https://raw.githubusercontent.com/iptv-org/iptv/master/PLAYLISTS.md"
DEFAULT_OUTPUT = "south_asia_cricket.m3u"
DEFAULT_REPORT = "reports/south-asia-cricket-report.json"
DEFAULT_SCREENSHOT_REPORT = "reports/south-asia-cricket-screenshot-report.json"
DEFAULT_SCREENSHOT_DIR = "screenshots/south-asia-cricket"
DEFAULT_CHANNEL_LIST = "reports/south-asia-cricket-channels.md"
SOUTH_ASIA_COUNTRIES = ("in", "pk", "bd")
WEB_SEARCHED_SOURCE_RECORDS = (
    {
        "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
        "reason": "web-searched",
        "note": "Free-TV global public playlist",
    },
    {
        "url": "https://gist.githubusercontent.com/grtesdwq/2be8e71010caa5c1d8dfd50a527aeba9/raw/iptv.m3u",
        "reason": "web-searched",
        "note": "Pakistan and India GitHub gist found by web search",
    },
    {
        "url": "https://raw.githubusercontent.com/FunctionError/PiratesTv/main/combined_playlist.m3u",
        "reason": "web-searched",
        "note": "regional playlist found by web search and prior validation",
    },
    {
        "url": "https://raw.githubusercontent.com/dark-N00B/IPtv/master/new_indian_tv.m3u",
        "reason": "country-web:in",
        "note": "Indian playlist found by web search",
    },
    {
        "url": "https://raw.githubusercontent.com/Shadmanislam/bdiptv/master/BD%20IPTV.m3u",
        "reason": "country-web:bd",
        "note": "Bangladesh playlist found by web search",
    },
    {
        "url": "https://raw.githubusercontent.com/sacuar/MyIPTV/main/Play1.m3u",
        "reason": "web-searched",
        "note": "Bangla and Hindi playlist found by web search",
    },
)
CRICKET_KEYWORDS = (
    "cricket",
    "willow",
    "ptv sports",
    "geo super",
    "a sports",
    "asports",
    "ten sports",
    "t sports",
    "tsports",
    "sony sports",
    "sony ten",
    "sports18",
    "star sports",
    "sky sports cricket",
    "fox cricket",
    "supersport cricket",
    "dd sports",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape public IPTV playlist indexes, keep India/Pakistan/Bangladesh "
            "channels plus cricket sports candidates, then screenshot-filter the "
            "final playlist."
        )
    )
    parser.add_argument(
        "--playlist-index",
        default=DEFAULT_PLAYLIST_INDEX,
        help="PLAYLISTS.md URL or local file to scrape for source playlist links.",
    )
    parser.add_argument(
        "--country",
        action="append",
        default=[],
        help="Country code to include fully. Default: in, pk, bd. May be repeated.",
    )
    parser.add_argument(
        "--extra-source",
        action="append",
        default=[],
        help="Extra M3U URL or local file to include before filtering.",
    )
    parser.add_argument(
        "--extra-source-list",
        action="append",
        default=[],
        help="Text file containing one extra M3U URL/file per line.",
    )
    parser.add_argument(
        "--no-web-searched-sources",
        action="store_true",
        help="Do not include the additional public sources found through web search.",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Final M3U output path.")
    parser.add_argument("--report", default=DEFAULT_REPORT, help="Probe report JSON path.")
    parser.add_argument(
        "--screenshot-report",
        default=DEFAULT_SCREENSHOT_REPORT,
        help="Screenshot report JSON path.",
    )
    parser.add_argument(
        "--screenshots",
        default=DEFAULT_SCREENSHOT_DIR,
        help="Directory for screenshot evidence.",
    )
    parser.add_argument(
        "--channel-list",
        default=DEFAULT_CHANNEL_LIST,
        help="Markdown file containing the final working channel list.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=24,
        help="Concurrent HLS probe workers. Use 0 for auto. Default: 24",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Network timeout per HLS probe request. Default: 10",
    )
    parser.add_argument(
        "--capture-workers",
        type=int,
        default=8,
        help="Concurrent ffmpeg screenshot workers. Default: 8",
    )
    parser.add_argument(
        "--capture-timeout",
        type=float,
        default=90.0,
        help="Process timeout per screenshot attempt. Default: 90",
    )
    parser.add_argument(
        "--capture-seconds",
        type=float,
        default=2.0,
        help="Initial playback position for screenshot capture. Default: 2",
    )
    parser.add_argument(
        "--retry-capture-seconds",
        type=float,
        default=20.0,
        help="Retry playback position if first capture fails. Default: 20",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print HLS probe progress every N completed checks. Default: 50",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Commit and push generated playlist, reports, screenshots, and channel list.",
    )
    return parser.parse_args()


def scrape_playlist_sources(index_source: str, countries: tuple[str, ...], timeout: float) -> list[dict[str, str]]:
    index_text = load_text(index_source, timeout)
    discovered: list[dict[str, str]] = []
    seen: set[str] = set()

    for country in countries:
        path = f"countries/{country}.m3u"
        matches = find_playlist_urls(index_text, path)
        if not matches:
            matches = [playlist_url(path)]
        for url in matches:
            add_source(discovered, seen, url, f"country:{country.casefold()}")

    for url in find_playlist_urls(index_text, "categories/sports.m3u"):
        add_source(discovered, seen, url, "cricket-sports")

    if not any(source["reason"] == "cricket-sports" for source in discovered):
        add_source(
            discovered,
            seen,
            playlist_url("categories/sports.m3u"),
            "cricket-sports",
        )

    return discovered


def web_searched_sources() -> list[dict[str, str]]:
    return [dict(record) for record in WEB_SEARCHED_SOURCE_RECORDS]


def find_playlist_urls(index_text: str, path: str) -> list[str]:
    escaped_path = re.escape(path)
    absolute_pattern = rf"https://iptv-org\.github\.io/iptv/{escaped_path}"
    urls = re.findall(absolute_pattern, index_text, flags=re.IGNORECASE)
    if urls:
        return sorted(set(urls))
    if re.search(escaped_path, index_text, flags=re.IGNORECASE):
        return [playlist_url(path)]
    return []


def playlist_url(path: str) -> str:
    return f"https://iptv-org.github.io/iptv/{path}"


def add_source(discovered: list[dict[str, str]], seen: set[str], url: str, reason: str) -> None:
    key = f"{reason}:{url}"
    if key in seen:
        return
    seen.add(key)
    discovered.append({"url": url, "reason": reason})


def load_source_list(path: str) -> list[str]:
    sources: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            sources.append(stripped)
    return sources


def load_candidate_channels(
    source_records: list[dict[str, str]],
    extra_sources: list[str],
    timeout: float,
) -> tuple[list[Channel], list[dict[str, int | str]]]:
    channels: list[Channel] = []
    summaries: list[dict[str, int | str]] = []

    for record in source_records:
        source = record["url"]
        reason = record["reason"]
        parsed_channels, error = parse_source_channels(source, timeout)
        if error:
            summaries.append(
                {
                    "source": source,
                    "reason": reason,
                    "note": record.get("note", ""),
                    "channels": 0,
                    "selected_hls_channels": 0,
                    "error": error,
                }
            )
            continue
        selected = select_channels(parsed_channels, reason)
        append_channels(channels, selected)
        summaries.append(
            {
                "source": source,
                "reason": reason,
                "note": record.get("note", ""),
                "channels": len(parsed_channels),
                "selected_hls_channels": len(selected),
            }
        )

    for source in extra_sources:
        parsed_channels, error = parse_source_channels(source, timeout)
        if error:
            summaries.append(
                {
                    "source": source,
                    "reason": "extra",
                    "channels": 0,
                    "selected_hls_channels": 0,
                    "error": error,
                }
            )
            continue
        selected = select_channels(parsed_channels, "extra")
        append_channels(channels, selected)
        summaries.append(
            {
                "source": source,
                "reason": "extra",
                "channels": len(parsed_channels),
                "selected_hls_channels": len(selected),
            }
        )

    return channels, summaries


def parse_source_channels(source: str, timeout: float) -> tuple[list[Channel], str]:
    from refresh_playlist import parse_m3u

    try:
        _, channels = parse_m3u(load_text(source, timeout), source)
        return channels, ""
    except Exception as error:
        return [], str(error)


def select_channels(channels: list[Channel], reason: str) -> list[Channel]:
    selected: list[Channel] = []
    for channel in channels:
        if not is_hls_url(channel.url):
            continue
        if reason == "cricket-sports" and not is_cricket_candidate(channel):
            continue
        if reason != "cricket-sports" and not is_allowed_channel(channel):
            continue
        selected.append(channel)
    return selected


def is_allowed_channel(channel: Channel) -> bool:
    return is_south_asia_candidate(channel) or is_cricket_candidate(channel)


def is_south_asia_candidate(channel: Channel) -> bool:
    text = " ".join((channel.name, " ".join(channel.groups)))
    normalized = normalize_text(text)
    markers = (
        "india",
        "indian",
        "hindi",
        "marathi",
        "bangla",
        "bengali",
        "bangladesh",
        "pakistan",
        "pakistani",
        "urdu",
    )
    if any(marker in normalized for marker in markers):
        return True
    tvg_id_base = channel.tvg_id.casefold().split("@", 1)[0]
    return tvg_id_base.endswith(".in") or tvg_id_base.endswith(".pk") or tvg_id_base.endswith(".bd")


def is_cricket_candidate(channel: Channel) -> bool:
    text = " ".join(
        (
            channel.name,
            channel.tvg_id,
            " ".join(channel.groups),
            " ".join(channel.tags),
        )
    )
    normalized = normalize_text(text)
    return any(has_phrase(normalized, keyword) for keyword in CRICKET_KEYWORDS)


def has_phrase(normalized: str, phrase: str) -> bool:
    escaped = re.escape(normalize_text(phrase))
    return re.search(rf"(?<!\S){escaped}(?!\S)", normalized) is not None


def normalize_text(value: str) -> str:
    normalized = value.encode("ascii", errors="ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.casefold())
    return " ".join(normalized.split())


def append_channels(target: list[Channel], channels: list[Channel]) -> None:
    for channel in channels:
        target.append(replace(channel, index=len(target)))


def probe_candidates(
    streams: list[Channel],
    workers: int,
    timeout: float,
    progress_every: int,
) -> list[ProbeResult]:
    resolved_workers = resolve_worker_count(workers, len(streams))
    print(
        f"Checking {len(streams)} candidate HLS streams across {resolved_workers} workers.",
        flush=True,
    )

    results: list[ProbeResult] = []
    with ThreadPoolExecutor(
        max_workers=resolved_workers,
        thread_name_prefix="south-asia-cricket",
    ) as executor:
        futures = {executor.submit(probe_channel, channel, timeout): channel for channel in streams}
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
            if progress_every > 0 and (completed % progress_every == 0 or completed == len(futures)):
                working = sum(1 for result in results if result.ok)
                print(f"Checked {completed}/{len(futures)}; working {working}.", flush=True)

    results.sort(key=lambda result: result.channel.index)
    return results


def write_probe_report(
    path: Path,
    index_source: str,
    source_summaries: list[dict[str, int | str]],
    duplicate_urls: int,
    potential_duplicates: int,
    potential_duplicate_records: list[dict[str, str]],
    manual_exclusions: int,
    manual_exclusion_records: list[dict[str, str]],
    results: list[ProbeResult],
    published_results: list[ProbeResult],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "playlist_index": index_source,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checked": len(results),
        "working_before_duplicate_filter": sum(1 for result in results if result.ok),
        "working_after_probe": len(published_results),
        "skipped_duplicate_urls": duplicate_urls,
        "skipped_potential_duplicate_channels": potential_duplicates,
        "skipped_manual_exclusions": manual_exclusions,
        "source_summaries": source_summaries,
        "potential_duplicate_records": potential_duplicate_records,
        "manual_exclusion_records": manual_exclusion_records,
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
                "published_after_probe": result in published_results,
                "elapsed_ms": result.elapsed_ms,
                "checked_url": result.checked_url,
                "error": result.error,
            }
            for result in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_screenshot_filter(args: argparse.Namespace, playlist: str) -> int:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "capture_channel_screenshots.py"),
        "--playlist",
        playlist,
        "--filtered-output",
        playlist,
        "--output-dir",
        args.screenshots,
        "--report",
        args.screenshot_report,
        "--workers",
        str(args.capture_workers),
        "--timeout",
        str(args.capture_timeout),
        "--capture-seconds",
        str(args.capture_seconds),
        "--retry-capture-seconds",
        str(args.retry_capture_seconds),
    ]
    print(" ".join(command), flush=True)
    return subprocess.run(command, check=False).returncode


def write_channel_list(path: Path, playlist_path: Path, screenshot_report_path: Path) -> list[str]:
    from refresh_playlist import parse_m3u

    _, channels = parse_m3u(playlist_path.read_text(encoding="utf-8"), str(playlist_path))
    screenshot_lookup = load_screenshot_lookup(screenshot_report_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# South Asia and Cricket Working Channels",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Playlist: `{playlist_path}`",
        f"Working channels: {len(channels)}",
        "",
    ]
    for index, channel in enumerate(channels, start=1):
        screenshot = screenshot_lookup.get(channel.url, "")
        group = channel.groups[0] if channel.groups else "Ungrouped"
        lines.append(f"{index}. {channel.name} | {group} | {channel.url}")
        if screenshot:
            lines.append(f"   - Screenshot: `{screenshot}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [channel.name for channel in channels]


def load_screenshot_lookup(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        result["url"]: result["screenshot"]
        for result in payload.get("results", [])
        if result.get("ok")
    }


def commit_and_push(paths: list[str]) -> int:
    from refresh_playlist import commit_and_push as refresh_commit_and_push

    return refresh_commit_and_push(paths)


def main() -> int:
    args = parse_args()
    countries = tuple(country.casefold() for country in (args.country or SOUTH_ASIA_COUNTRIES))
    extra_sources = list(args.extra_source)
    for source_list in args.extra_source_list:
        extra_sources.extend(load_source_list(source_list))

    source_records = scrape_playlist_sources(args.playlist_index, countries, args.timeout)
    if not args.no_web_searched_sources:
        source_records.extend(web_searched_sources())
    candidates, source_summaries = load_candidate_channels(source_records, extra_sources, args.timeout)
    unique_candidates, duplicate_urls = dedupe_streams(candidates)

    results = probe_candidates(
        unique_candidates,
        args.workers,
        args.timeout,
        args.progress_every,
    )
    (
        published_results,
        potential_duplicates,
        potential_duplicate_records,
        manual_exclusions,
        manual_exclusion_records,
    ) = dedupe_working_results(results)

    output = Path(args.output)
    write_playlist(output, "#EXTM3U", published_results)
    write_probe_report(
        Path(args.report),
        args.playlist_index,
        source_summaries,
        duplicate_urls,
        potential_duplicates,
        potential_duplicate_records,
        manual_exclusions,
        manual_exclusion_records,
        results,
        published_results,
    )

    capture_status = run_screenshot_filter(args, str(output))
    if capture_status != 0:
        return capture_status

    channel_names = write_channel_list(
        Path(args.channel_list),
        output,
        Path(args.screenshot_report),
    )
    print(f"Wrote {len(channel_names)} screenshot-verified channels to {output}.", flush=True)
    print(f"Working channel list: {args.channel_list}", flush=True)
    for channel_name in channel_names:
        print(f"- {channel_name}", flush=True)

    if args.push:
        return commit_and_push(
            [
                args.output,
                args.report,
                args.screenshot_report,
                args.screenshots,
                args.channel_list,
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
