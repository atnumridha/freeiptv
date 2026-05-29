#!/usr/bin/env python3
"""Refresh, screenshot-validate, and optionally push the playlist in one command."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from refresh_playlist import DEFAULT_SOURCES
from refresh_playlist import commit_and_push


SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the public IPTV playlist from one or more M3U URLs/files, "
            "then capture playback screenshots for the generated playlist."
        )
    )
    parser.add_argument(
        "sources",
        nargs="*",
        help="Optional input M3U URLs or local files. Replaces defaults unless --include-default-sources is used.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Input M3U URL or local file. May be provided more than once.",
    )
    parser.add_argument(
        "--source-list",
        action="append",
        default=[],
        help="Text file containing one M3U URL/file path per line. Blank lines and # comments are ignored.",
    )
    parser.add_argument(
        "--include-default-sources",
        action="store_true",
        help="Append provided sources to the default Bengali, Hindi, Marathi, and PiratesTv sources.",
    )
    parser.add_argument("--output", default="in.m3u", help="Output M3U path. Default: in.m3u")
    parser.add_argument(
        "--report",
        default="reports/in-report.json",
        help="HLS probe JSON report path.",
    )
    parser.add_argument(
        "--screenshot-report",
        default="reports/screenshot-report.json",
        help="Playback screenshot JSON report path.",
    )
    parser.add_argument(
        "--screenshots",
        default="screenshots/latest",
        help="Directory where playback screenshots are written.",
    )
    parser.add_argument(
        "--readme",
        default="README.md",
        help="README path to update with latest run counts.",
    )
    parser.add_argument(
        "--refresh-workers",
        type=int,
        default=24,
        help="Concurrent HLS probe worker threads. Use 0 for auto. Default: 24",
    )
    parser.add_argument(
        "--refresh-timeout",
        type=float,
        default=60.0,
        help="Network timeout budget per HLS channel probe in seconds. Default: 60",
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
        help="Process timeout per screenshot attempt in seconds. Default: 90",
    )
    parser.add_argument(
        "--capture-seconds",
        type=float,
        default=2.0,
        help="Initial playback position for frame capture. Default: 2",
    )
    parser.add_argument(
        "--retry-capture-seconds",
        type=float,
        default=20.0,
        help="Retry playback position if the initial capture fails. Default: 20",
    )
    parser.add_argument(
        "--filter-by-screenshot",
        action="store_true",
        help="Rewrite the final playlist to only channels that produced screenshots.",
    )
    parser.add_argument(
        "--require-all-screenshots",
        action="store_true",
        help="Fail if any playlist entry cannot produce screenshot evidence.",
    )
    parser.add_argument(
        "--skip-screenshots",
        action="store_true",
        help="Only refresh the playlist and HLS probe report.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Commit and push generated playlist, reports, screenshots, and README changes.",
    )
    return parser.parse_args()


def load_source_list(path: str) -> list[str]:
    sources: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            sources.append(stripped)
    return sources


def resolve_sources(args: argparse.Namespace) -> list[str]:
    provided = list(args.sources) + list(args.source)
    for source_list in args.source_list:
        provided.extend(load_source_list(source_list))

    if not provided:
        return list(DEFAULT_SOURCES)
    if args.include_default_sources:
        return [*DEFAULT_SOURCES, *provided]
    return provided


def run_command(command: list[str]) -> int:
    print(" ".join(command), flush=True)
    completed = subprocess.run(command, check=False)
    return completed.returncode


def run_refresh(args: argparse.Namespace, sources: list[str]) -> int:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "refresh_playlist.py"),
        "--output",
        args.output,
        "--report",
        args.report,
        "--readme",
        args.readme,
        "--workers",
        str(args.refresh_workers),
        "--timeout",
        str(args.refresh_timeout),
    ]
    for source in sources:
        command.extend(["--source", source])
    return run_command(command)


def run_screenshot_capture(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "capture_channel_screenshots.py"),
        "--playlist",
        args.output,
        "--readme",
        args.readme,
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
    if args.filter_by_screenshot:
        command.extend(["--filtered-output", args.output])
    if args.require_all_screenshots:
        command.append("--require-all")
    return run_command(command)


def push_changes(args: argparse.Namespace) -> int:
    paths = [args.readme, args.output, args.report]
    if not args.skip_screenshots:
        paths.extend([args.screenshot_report, args.screenshots])
    return commit_and_push(paths)


def main() -> int:
    args = parse_args()
    sources = resolve_sources(args)

    refresh_status = run_refresh(args, sources)
    if refresh_status != 0:
        return refresh_status

    if not args.skip_screenshots:
        capture_status = run_screenshot_capture(args)
        if capture_status != 0:
            return capture_status

    if args.push:
        return push_changes(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
