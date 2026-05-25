#!/usr/bin/env python3
"""Capture playback screenshots for playlist channels with a slower retry."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from refresh_playlist import DEFAULT_USER_AGENT
from refresh_playlist import ProbeResult
from refresh_playlist import parse_m3u
from refresh_playlist import request_headers
from refresh_playlist import write_playlist


@dataclass(frozen=True)
class CaptureResult:
    index: int
    name: str
    url: str
    ok: bool
    screenshot: str
    elapsed_ms: int
    capture_seconds: float
    attempts: int
    capture_mode: str
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use ffmpeg to play each channel for a short period and capture one "
            "screenshot. Channels that cannot produce a screenshot can be "
            "filtered out of the final playlist."
        )
    )
    parser.add_argument("--playlist", default="in.m3u", help="Input playlist path.")
    parser.add_argument(
        "--output-dir",
        default="screenshots/latest",
        help="Directory where screenshots are written.",
    )
    parser.add_argument(
        "--report",
        default="reports/screenshot-report.json",
        help="JSON screenshot report path.",
    )
    parser.add_argument(
        "--filtered-output",
        default="",
        help="Optional M3U output path containing only screenshot-passing channels.",
    )
    parser.add_argument(
        "--readme",
        default="",
        help="Optional README path to update with screenshot validation counts.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Concurrent ffmpeg captures. Default: 6",
    )
    parser.add_argument(
        "--capture-seconds",
        type=float,
        default=2.0,
        help="Decode this many seconds before capturing a frame. Default: 2",
    )
    parser.add_argument(
        "--retry-capture-seconds",
        type=float,
        default=20.0,
        help=(
            "If the first capture fails, retry at this playback position. "
            "Use 0 to disable. Default: 20"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="Process timeout per channel in seconds. Default: 90",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=640,
        help="Screenshot width in pixels. Default: 640",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Capture only the first N channels. Useful for smoke tests.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="Print progress every N completed captures. Default: 25",
    )
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="Return a failure exit code if any channel cannot produce a screenshot.",
    )
    return parser.parse_args()


def capture_channel(
    ffmpeg: str,
    ffprobe: str,
    channel,
    output_dir: Path,
    capture_seconds: float,
    retry_capture_seconds: float,
    timeout: float,
    width: int,
) -> CaptureResult:
    started = time.monotonic()
    first = capture_channel_once(
        ffmpeg,
        channel,
        output_dir,
        capture_seconds,
        timeout,
        width,
        1,
    )
    if first.ok or retry_capture_seconds <= 0 or retry_capture_seconds == capture_seconds:
        return first

    retry = capture_channel_once(
        ffmpeg,
        channel,
        output_dir,
        retry_capture_seconds,
        timeout,
        width,
        2,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if retry.ok:
        return CaptureResult(
            index=retry.index,
            name=retry.name,
            url=retry.url,
            ok=True,
            screenshot=retry.screenshot,
            elapsed_ms=elapsed_ms,
            capture_seconds=retry.capture_seconds,
            attempts=retry.attempts,
            capture_mode=retry.capture_mode,
        )

    audio_only = capture_audio_only_stream(
        ffmpeg,
        ffprobe,
        channel,
        output_dir,
        started,
        retry_capture_seconds,
        3,
        timeout,
        width,
    )
    if audio_only.ok:
        return audio_only

    return CaptureResult(
        index=retry.index,
        name=retry.name,
        url=retry.url,
        ok=False,
        screenshot=retry.screenshot,
        elapsed_ms=elapsed_ms,
        capture_seconds=retry.capture_seconds,
        attempts=retry.attempts,
        capture_mode=retry.capture_mode,
        error=(
            f"{capture_seconds:g}s attempt failed: {first.error}; "
            f"{retry_capture_seconds:g}s attempt failed: {retry.error}"
        ),
    )


def capture_channel_once(
    ffmpeg: str,
    channel,
    output_dir: Path,
    capture_seconds: float,
    timeout: float,
    width: int,
    attempts: int,
) -> CaptureResult:
    started = time.monotonic()
    screenshot = output_dir / f"{channel.index + 1:04d}-{slugify(channel.name)}.jpg"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    if screenshot.exists():
        screenshot.unlink()

    headers = request_headers(channel)
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rw_timeout",
        str(int(timeout * 1_000_000)),
        "-allowed_segment_extensions",
        "ALL",
        "-extension_picky",
        "0",
        "-user_agent",
        headers.get("User-Agent", DEFAULT_USER_AGENT),
    ]
    if "Referer" in headers:
        command.extend(["-headers", f"Referer: {headers['Referer']}\r\n"])
    command.extend(
        [
            "-i",
            channel.url,
            "-ss",
            str(capture_seconds),
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "4",
            str(screenshot),
        ]
    )

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout + capture_seconds + 5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return make_result(
            channel,
            False,
            screenshot,
            started,
            capture_seconds,
            attempts,
            "ffmpeg timed out",
        )

    if completed.returncode != 0:
        return make_result(
            channel,
            False,
            screenshot,
            started,
            capture_seconds,
            attempts,
            last_error(completed),
        )
    if not screenshot.exists() or screenshot.stat().st_size == 0:
        return make_result(
            channel,
            False,
            screenshot,
            started,
            capture_seconds,
            attempts,
            "ffmpeg produced no screenshot",
        )

    return make_result(channel, True, screenshot, started, capture_seconds, attempts)


def capture_audio_only_stream(
    ffmpeg: str,
    ffprobe: str,
    channel,
    output_dir: Path,
    started: float,
    capture_seconds: float,
    attempts: int,
    timeout: float,
    width: int,
) -> CaptureResult:
    screenshot = output_dir / f"{channel.index + 1:04d}-{slugify(channel.name)}.jpg"
    if screenshot.exists():
        screenshot.unlink()
    if not is_audio_only_stream(ffprobe, channel, timeout):
        return CaptureResult(
            index=channel.index,
            name=channel.name,
            url=channel.url,
            ok=False,
            screenshot=str(screenshot),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            capture_seconds=capture_seconds,
            attempts=attempts,
            capture_mode="video",
            error="stream did not expose a video frame and was not audio-only",
        )

    height = max(2, int(width * 9 / 16))
    if height % 2:
        height += 1
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"smptebars=s={width}x{height}:d=1",
        "-frames:v",
        "1",
        "-q:v",
        "4",
        str(screenshot),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CaptureResult(
            index=channel.index,
            name=channel.name,
            url=channel.url,
            ok=False,
            screenshot=str(screenshot),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            capture_seconds=capture_seconds,
            attempts=attempts,
            capture_mode="audio",
            error="audio-only placeholder generation timed out",
        )

    if completed.returncode != 0:
        return CaptureResult(
            index=channel.index,
            name=channel.name,
            url=channel.url,
            ok=False,
            screenshot=str(screenshot),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            capture_seconds=capture_seconds,
            attempts=attempts,
            capture_mode="audio",
            error=last_error(completed),
        )
    if not screenshot.exists() or screenshot.stat().st_size == 0:
        return CaptureResult(
            index=channel.index,
            name=channel.name,
            url=channel.url,
            ok=False,
            screenshot=str(screenshot),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            capture_seconds=capture_seconds,
            attempts=attempts,
            capture_mode="audio",
            error="audio-only placeholder generation produced no screenshot",
        )

    return CaptureResult(
        index=channel.index,
        name=channel.name,
        url=channel.url,
        ok=True,
        screenshot=str(screenshot),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        capture_seconds=capture_seconds,
        attempts=attempts,
        capture_mode="audio",
    )


def is_audio_only_stream(ffprobe: str, channel, timeout: float) -> bool:
    headers = request_headers(channel)
    probe_timeout = min(timeout, 30.0)
    command = [
        ffprobe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-rw_timeout",
        str(int(probe_timeout * 1_000_000)),
        "-allowed_segment_extensions",
        "ALL",
        "-extension_picky",
        "0",
        "-user_agent",
        headers.get("User-Agent", DEFAULT_USER_AGENT),
    ]
    if "Referer" in headers:
        command.extend(["-headers", f"Referer: {headers['Referer']}\r\n"])
    command.extend(["-show_entries", "stream=codec_type", "-of", "json", channel.url])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=probe_timeout + 5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    if completed.returncode != 0:
        return False

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return False

    stream_types = {
        stream.get("codec_type")
        for stream in payload.get("streams", [])
        if isinstance(stream, dict)
    }
    return "audio" in stream_types and "video" not in stream_types


def make_result(
    channel,
    ok: bool,
    screenshot: Path,
    started: float,
    capture_seconds: float,
    attempts: int,
    error: str = "",
) -> CaptureResult:
    return CaptureResult(
        index=channel.index,
        name=channel.name,
        url=channel.url,
        ok=ok,
        screenshot=str(screenshot),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        capture_seconds=capture_seconds,
        attempts=attempts,
        capture_mode="video",
        error=error,
    )


def last_error(completed: subprocess.CompletedProcess[str]) -> str:
    output = (completed.stderr or completed.stdout or "").strip().splitlines()
    return output[-1] if output else f"ffmpeg exited {completed.returncode}"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return slug[:80] or "channel"


def write_report(path: Path, playlist: str, results: list[CaptureResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "playlist": playlist,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checked": len(results),
        "captured": sum(1 for result in results if result.ok),
        "failed": sum(1 for result in results if not result.ok),
        "results": [result.__dict__ for result in results],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_filtered_playlist(path: Path, header: str, channels: list, results: list[CaptureResult]) -> None:
    ok_indexes = {result.index for result in results if result.ok}
    probe_results = [
        ProbeResult(channel=channel, ok=True, elapsed_ms=0, checked_url=channel.url)
        for channel in channels
        if channel.index in ok_indexes
    ]
    write_playlist(path, header, probe_results)


def update_readme(path: Path, checked: int, captured: int) -> None:
    content = path.read_text(encoding="utf-8")
    content = replace_or_insert_stat(
        content,
        "Screenshot playback captures",
        f"{captured}/{checked}",
        after_label="Working channels",
    )
    path.write_text(content, encoding="utf-8")


def replace_or_insert_stat(content: str, label: str, value: str, after_label: str) -> str:
    line = f"- {label}: {value}"
    pattern = re.compile(rf"^- {re.escape(label)}: .*$", flags=re.MULTILINE)
    if pattern.search(content):
        return pattern.sub(line, content)

    after_pattern = re.compile(rf"(^- {re.escape(after_label)}: .*$)", flags=re.MULTILINE)
    if after_pattern.search(content):
        return after_pattern.sub(rf"\1\n{line}", content)
    return content.rstrip() + "\n" + line + "\n"


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        print("--workers must be at least 1", file=sys.stderr)
        return 2

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg is required but was not found in PATH", file=sys.stderr)
        return 2
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        print("ffprobe is required but was not found in PATH", file=sys.stderr)
        return 2

    header, channels = parse_m3u(Path(args.playlist).read_text(encoding="utf-8"), args.playlist)
    if args.limit:
        channels = channels[: args.limit]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_file in output_dir.glob("*.jpg"):
        old_file.unlink()

    results: list[CaptureResult] = []
    print(
        f"Capturing screenshots for {len(channels)} channels with {args.workers} workers.",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="iptv-shot") as executor:
        futures = {
            executor.submit(
                capture_channel,
                ffmpeg,
                ffprobe,
                channel,
                output_dir,
                args.capture_seconds,
                args.retry_capture_seconds,
                args.timeout,
                args.width,
            ): channel
            for channel in channels
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            if (
                args.progress_every > 0
                and (completed % args.progress_every == 0 or completed == len(futures))
            ):
                captured = sum(1 for item in results if item.ok)
                print(f"Captured {captured}/{completed} screenshots.", flush=True)

    results.sort(key=lambda result: result.index)
    write_report(Path(args.report), args.playlist, results)
    if args.filtered_output:
        write_filtered_playlist(Path(args.filtered_output), header, channels, results)
    if args.readme:
        update_readme(Path(args.readme), len(results), sum(1 for result in results if result.ok))

    captured = sum(1 for result in results if result.ok)
    print(f"Captured {captured}/{len(results)} screenshots.", flush=True)
    if captured == 0 and results:
        return 1
    if args.require_all and captured != len(results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
