#!/usr/bin/env python3
"""Build a screenshot-verified South Asia and cricket IPTV playlist."""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from refresh_playlist import Channel
from refresh_playlist import ProbeResult
from refresh_playlist import dedupe_streams
from refresh_playlist import dedupe_working_results
from refresh_playlist import is_hls_url
from refresh_playlist import load_text
from refresh_playlist import playlist_sort_key
from refresh_playlist import probe_channel
from refresh_playlist import resolve_worker_count


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PLAYLIST_INDEX = "https://raw.githubusercontent.com/iptv-org/iptv/master/PLAYLISTS.md"
DEFAULT_OUTPUT = "south_asia_cricket.m3u"
DEFAULT_REPORT = "reports/south-asia-cricket-report.json"
DEFAULT_SCREENSHOT_REPORT = "reports/south-asia-cricket-screenshot-report.json"
DEFAULT_SCREENSHOT_DIR = "screenshots/south-asia-cricket"
DEFAULT_CHANNEL_LIST = "reports/south-asia-cricket-channels.md"
DEFAULT_KNOWN_INDIAN_CHANNEL_SOURCE = (
    "https://telelibrary.fandom.com/api.php?action=query&list=categorymembers"
    "&cmtitle=Category:TV_Channels_in_India&cmlimit=500&format=json"
)
DEFAULT_SKIP_CHANNEL_SOURCE = "https://raw.githubusercontent.com/Free-TV/IPTV/master/lists/india.md"
LANGUAGE_PLAYLIST_URL_TEMPLATE = "https://iptv-org.github.io/iptv/languages/{code}.m3u"
DEFAULT_CHANNEL_METADATA_SOURCE = "https://iptv-org.github.io/api/channels.json"
SOUTH_ASIA_COUNTRIES = ("in", "pk", "bd")
ALLOWED_LANGUAGE_CODES = ("hin", "ben", "mar", "eng")
DISALLOWED_LANGUAGE_CODES = (
    "tel",
    "tam",
    "kan",
    "mal",
    "guj",
    "pan",
    "urd",
    "ori",
    "asm",
    "bho",
    "tgl",
)
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
ARABIC_CHANNEL_NAME_EXCLUSIONS = {
    "arabica tv",
    "saudia arabia",
}
ARABIC_CHANNEL_MARKERS = (
    "arabic",
    "arabia",
    "saudi",
    "uae",
    "dubai",
    "qatar",
    "oman",
    "kuwait",
    "bahrain",
    "egypt",
    "iraq",
    "jordan",
    "lebanon",
    "syria",
    "middle east",
)
ALLOWED_LANGUAGE_MARKERS = (
    "hindi",
    "bangla",
    "bengali",
    "marathi",
    "english",
)
DISALLOWED_LANGUAGE_MARKERS = (
    "telugu",
    "tamil",
    "kannada",
    "malayalam",
    "gujarati",
    "punjabi",
    "urdu",
    "bhojpuri",
    "odia",
    "assam",
    "assamese",
    "tagalog",
)
KNOWN_ALLOWED_LANGUAGE_CHANNEL_PREFIXES = (
    "aaj tak",
    "abp news",
    "abp ananda",
    "abp ganga",
    "akd calcutta news",
    "amarujala",
    "ananda barta",
    "bansal news",
    "bharat express",
    "bharat samachar",
    "cnn news 18",
    "cnbc",
    "colors",
    "cricket gold",
    "cvr english",
    "dangal",
    "dd india",
    "dd kisan",
    "dd national",
    "dd news",
    "dd sports",
    "et now",
    "good news today",
    "goldmines",
    "hindi khabar",
    "india daily live",
    "india today",
    "india tv",
    "janta tv",
    "kolkata tv",
    "mirror now",
    "ndtv good times",
    "ndtv india",
    "ndtv madhya pradesh chhattisgarh",
    "ndtv marathi",
    "ndtv profit",
    "news 1 india",
    "news 24",
    "news nation",
    "news18 bangla",
    "news18 bihar jharkhand",
    "news18 delhi ncr jk",
    "news18 india",
    "news18 madhya pradesh",
    "news18 marathi",
    "news18 punjab haryana himachal",
    "news18 rajasthan",
    "news18 uttar pradesh",
    "newstime bangla",
    "republic bangla",
    "republic bharat",
    "republic tv",
    "rt india",
    "sadhna",
    "sansad tv",
    "sanskar",
    "satsang",
    "shemaroo",
    "sheemaroo",
    "sony pix",
    "sony sports ten 1",
    "sony sports ten 2",
    "sony sports ten 3",
    "sony wah",
    "sony yay",
    "star news",
    "star pravah",
    "svbc 4",
    "swadesh news",
    "the movie club",
    "travelxp",
    "tv brics english",
    "tv9 bangla",
    "tv9 bharatvarsh",
    "tv9 marathi",
    "weatherspy",
    "yrf music",
    "zee 24 ghanta",
    "zee 24 taas",
    "zee bharat",
    "zee bihar jharkhand",
    "zee business",
    "zee cine classic",
    "zee comedy nation",
    "zee delhi ncr haryana",
    "zee dil se",
    "zee horror nights",
    "zee madhya pradesh chhattisgarh",
    "zee news",
    "zee punjab haryana himachal",
    "zee rajasthan",
    "zee south flix",
    "zee uttar pradesh uttarakhand",
    "zoom",
)
KNOWN_DISALLOWED_LANGUAGE_CHANNEL_PREFIXES = (
    "argus news",
    "asianet",
    "balle balle",
    "cnbc bajar",
    "dd manipur",
    "dd meghalaya",
    "dd mizoram",
    "dheeran tv",
    "etv andhra pradesh",
    "etv cinema",
    "etv comedy",
    "etv josh",
    "etv life",
    "etv telangana",
    "etv telugu",
    "fateh tv",
    "joo music",
    "joomusic",
    "kairali",
    "kaumudy tv",
    "manorama",
    "mazhavil manorama",
    "namdhari",
    "ptc punjabi",
    "salaam tv",
    "sony sports ten 4",
    "star maa",
    "star suvarna",
    "svbc 2",
    "svbc 3",
    "svbc sri",
    "tehzeeb tv",
    "tv5",
    "v6 news",
    "zainabia channel",
)
KNOWN_DISALLOWED_LANGUAGE_CHANNEL_NAMES = (
    "225 tag tv 1",
    "angel tv 720p",
    "god stands tv",
    "god stands tv tagalog",
)
TOP_CHANNEL_BRAND_PRIORITY = (
    ("aaj tak",),
    ("zee",),
    ("sony", "sonly"),
    ("colors",),
    ("star",),
    ("tv9",),
    ("republic",),
    ("bbc",),
)
KNOWN_INDIAN_CHANNEL_PRIORITY = (
    ("aaj tak",),
    ("abp news",),
    ("abp ananda",),
    ("abp asmita",),
    ("abp ganga",),
    ("india today",),
    ("india tv",),
    ("cnn news 18", "cnn news18"),
    ("news18 india", "news 18 india"),
    ("ndtv india",),
    ("ndtv profit",),
    ("ndtv good times",),
    ("republic tv",),
    ("republic bharat",),
    ("republic bangla",),
    ("republic kannada",),
    ("zee news",),
    ("zee bharat",),
    ("zee business",),
    ("zee 24 taas",),
    ("zee 24 ghanta",),
    ("zee tamil news",),
    ("zee telugu news",),
    ("zee kannada news",),
    ("cnbc tv18",),
    ("cnbc awaaz",),
    ("et now",),
    ("mirror now",),
    ("news nation",),
    ("news 24",),
    ("dd news",),
    ("dd national",),
    ("dd india",),
    ("dd sports",),
    ("dd kisan",),
    ("tv9 bharatvarsh",),
    ("tv9 bangla",),
    ("tv9 marathi",),
    ("tv9 telugu",),
    ("tv9 gujarati",),
    ("asianet news",),
    ("asianet movies",),
    ("asianet plus",),
    ("asianet",),
    ("manorama news",),
    ("mazhavil manorama",),
    ("puthiya thalaimurai",),
    ("thanthi tv",),
    ("tv5 news",),
    ("tv5 kannada",),
    ("v6 news",),
    ("etv news",),
    ("etv telugu",),
    ("etv cinema",),
    ("etv comedy",),
    ("etv life",),
    ("dangal tv",),
    ("dangal 2",),
    ("sab tv",),
    ("sony sports ten",),
    ("sony pix",),
    ("sony wah",),
    ("sony marathi",),
    ("sony yay",),
    ("star maa",),
    ("star pravah",),
    ("star suvarna",),
    ("b4u movies",),
    ("b4u kadak",),
    ("b4u bhojpuri",),
    ("goldmines",),
    ("shemaroo", "sheemaroo"),
    ("epic tv",),
    ("ptc punjabi",),
    ("ptc punjabi gold",),
    ("9xm",),
    ("zoom",),
    ("yrf music",),
    ("travelxp",),
    ("sansad tv",),
    ("aastha",),
    ("sanskar",),
    ("satsang",),
    ("svbc",),
    ("animax",),
)


@dataclass(frozen=True)
class SelectionResult:
    channels: list[Channel]
    skipped_arabic_channels: int
    skipped_language_channels: int
    skipped_source_channels: int


@dataclass(frozen=True)
class SkipChannelFilter:
    names: set[str]
    tvg_ids: set[str]
    urls: set[str]


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
        "--known-indian-channel-source",
        default=DEFAULT_KNOWN_INDIAN_CHANNEL_SOURCE,
        help=(
            "Fandom category page/API URL or local JSON/HTML file used to promote "
            "known Indian TV channels to the top. Use an empty value to skip it."
        ),
    )
    parser.add_argument(
        "--channel-metadata-source",
        default=DEFAULT_CHANNEL_METADATA_SOURCE,
        help="iptv-org channels JSON URL or local file used for language hints.",
    )
    parser.add_argument(
        "--skip-channel-source",
        action="append",
        default=[],
        help=(
            "Markdown/M3U URL or local file containing channels to exclude. "
            "May be repeated."
        ),
    )
    parser.add_argument(
        "--no-default-skip-channel-source",
        action="store_true",
        help="Do not use the default Free-TV India markdown skip list.",
    )
    parser.add_argument(
        "--no-language-filter",
        action="store_true",
        help="Disable Hindi/Bengali/Marathi/English-only filtering.",
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
    language_filter: dict[str, set[str] | dict[str, str]],
    skip_filter: SkipChannelFilter,
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
        selection = select_channels(parsed_channels, reason, language_filter, skip_filter)
        append_channels(channels, selection.channels)
        summaries.append(
            {
                "source": source,
                "reason": reason,
                "note": record.get("note", ""),
                "channels": len(parsed_channels),
                "selected_hls_channels": len(selection.channels),
                "skipped_arabic_channels": selection.skipped_arabic_channels,
                "skipped_language_channels": selection.skipped_language_channels,
                "skipped_source_channels": selection.skipped_source_channels,
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
        selection = select_channels(parsed_channels, "extra", language_filter, skip_filter)
        append_channels(channels, selection.channels)
        summaries.append(
            {
                "source": source,
                "reason": "extra",
                "channels": len(parsed_channels),
                "selected_hls_channels": len(selection.channels),
                "skipped_arabic_channels": selection.skipped_arabic_channels,
                "skipped_language_channels": selection.skipped_language_channels,
                "skipped_source_channels": selection.skipped_source_channels,
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


def select_channels(
    channels: list[Channel],
    reason: str,
    language_filter: dict[str, set[str] | dict[str, str]],
    skip_filter: SkipChannelFilter,
) -> SelectionResult:
    selected: list[Channel] = []
    skipped_arabic_channels = 0
    skipped_language_channels = 0
    skipped_source_channels = 0
    for channel in channels:
        if not is_hls_url(channel.url):
            continue
        if is_arabic_channel(channel):
            skipped_arabic_channels += 1
            continue
        if is_skipped_source_channel(channel, skip_filter):
            skipped_source_channels += 1
            continue
        if language_filter and not is_allowed_language_channel(channel, language_filter):
            skipped_language_channels += 1
            continue
        if reason == "cricket-sports" and not is_cricket_candidate(channel):
            continue
        if reason != "cricket-sports" and not is_allowed_channel(channel):
            continue
        selected.append(channel)
    return SelectionResult(
        channels=selected,
        skipped_arabic_channels=skipped_arabic_channels,
        skipped_language_channels=skipped_language_channels,
        skipped_source_channels=skipped_source_channels,
    )


def load_skip_channel_filter(sources: list[str], timeout: float) -> SkipChannelFilter:
    names: set[str] = set()
    tvg_ids: set[str] = set()
    urls: set[str] = set()

    for source in sources:
        if not source.strip():
            continue
        try:
            text = load_text(resolve_github_blob_source(source), timeout)
        except Exception as error:
            print(f"Could not load skip channel source {source}: {error}", file=sys.stderr)
            continue

        if looks_like_m3u(text):
            _, channels = parse_m3u_text(text, source)
            for channel in channels:
                add_skip_channel(channel.name, channel.tvg_id, channel.url, names, tvg_ids, urls)
            continue

        for name, tvg_id, url in extract_skip_channels_from_markdown(text):
            add_skip_channel(name, tvg_id, url, names, tvg_ids, urls)

    return SkipChannelFilter(names=names, tvg_ids=tvg_ids, urls=urls)


def resolve_github_blob_source(source: str) -> str:
    return re.sub(
        r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$",
        r"https://raw.githubusercontent.com/\1/\2/\3/\4",
        source.strip(),
    )


def looks_like_m3u(text: str) -> bool:
    return text.lstrip().upper().startswith("#EXTM3U")


def parse_m3u_text(text: str, source: str) -> tuple[str, list[Channel]]:
    from refresh_playlist import parse_m3u

    return parse_m3u(text, source)


def extract_skip_channels_from_markdown(text: str) -> list[tuple[str, str, str]]:
    records: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [clean_markdown_cell(cell) for cell in stripped.strip("|").split("|")]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        name = cells[1]
        url = extract_markdown_link(stripped)
        tvg_id = cells[4]
        if name or tvg_id or url:
            records.append((name, tvg_id, url))
    return records


def clean_markdown_cell(value: str) -> str:
    cleaned = html.unescape(value)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"\[[^\]]*]\(([^)]+)\)", r"\1", cleaned)
    return cleaned.strip()


def extract_markdown_link(value: str) -> str:
    match = re.search(r"\[[^\]]*]\(([^)]+)\)", value)
    return match.group(1).strip() if match else ""


def add_skip_channel(
    name: str,
    tvg_id: str,
    url: str,
    names: set[str],
    tvg_ids: set[str],
    urls: set[str],
) -> None:
    normalized_name = canonical_skip_name(name)
    if normalized_name:
        names.add(normalized_name)
    normalized_tvg_id = normalize_tvg_id(tvg_id)
    if normalized_tvg_id:
        tvg_ids.add(normalized_tvg_id)
    if url.strip():
        urls.add(url.strip())


def is_skipped_source_channel(channel: Channel, skip_filter: SkipChannelFilter) -> bool:
    if channel.url in skip_filter.urls:
        return True
    if channel_id_base(channel) in skip_filter.tvg_ids:
        return True
    return is_skipped_channel_name(channel.name, skip_filter.names)


def is_skipped_channel_name(name: str, skip_names: set[str]) -> bool:
    normalized = canonical_skip_name(name)
    if normalized in skip_names:
        return True
    for skip_name in skip_names:
        if normalized.startswith(f"{skip_name} ") and has_only_variant_suffix(
            normalized.removeprefix(skip_name).strip()
        ):
            return True
    return False


def has_only_variant_suffix(value: str) -> bool:
    if not value:
        return True
    variant_tokens = {
        "4k",
        "7",
        "24",
        "360p",
        "480p",
        "504p",
        "576i",
        "576p",
        "720p",
        "1080p",
        "2160p",
        "blocked",
        "geo",
        "hd",
        "not",
        "sd",
        "uhd",
    }
    return all(token in variant_tokens for token in value.split())


def canonical_skip_name(name: str) -> str:
    name_without_qualifiers = re.sub(r"\([^)]*\)|\[[^]]*]", " ", name)
    return normalize_text(name_without_qualifiers)


def normalize_tvg_id(tvg_id: str) -> str:
    return tvg_id.casefold().split("@", 1)[0].strip()


def load_language_filter(
    channel_metadata_source: str,
    timeout: float,
    enabled: bool,
) -> dict[str, set[str] | dict[str, str]]:
    if not enabled:
        return {}

    allowed_ids, allowed_urls = load_language_playlist_records(ALLOWED_LANGUAGE_CODES, timeout)
    disallowed_ids, disallowed_urls = load_language_playlist_records(DISALLOWED_LANGUAGE_CODES, timeout)
    metadata = load_channel_metadata(channel_metadata_source, timeout)
    return {
        "allowed_ids": allowed_ids,
        "allowed_urls": allowed_urls,
        "disallowed_ids": disallowed_ids,
        "disallowed_urls": disallowed_urls,
        "metadata": metadata,
    }


def load_language_playlist_records(codes: tuple[str, ...], timeout: float) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    urls: set[str] = set()
    for code in codes:
        source = LANGUAGE_PLAYLIST_URL_TEMPLATE.format(code=code)
        channels, error = parse_source_channels(source, timeout)
        if error:
            print(f"Could not load language playlist {code}: {error}", file=sys.stderr)
            continue
        for channel in channels:
            base_id = channel_id_base(channel)
            if base_id:
                ids.add(base_id)
            urls.add(channel.url)
    return ids, urls


def load_channel_metadata(source: str, timeout: float) -> dict[str, str]:
    if not source.strip():
        return {}
    try:
        records = json.loads(load_text(source, timeout))
    except Exception as error:
        print(f"Could not load channel metadata source {source}: {error}", file=sys.stderr)
        return {}

    metadata: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        channel_id = str(record.get("id", "")).strip()
        if not channel_id:
            continue
        metadata[channel_id.casefold()] = " ".join(
            str(value)
            for value in (
                record.get("name", ""),
                " ".join(record.get("alt_names", []) or []),
                record.get("network", ""),
                " ".join(record.get("owners", []) or []),
                record.get("website", ""),
            )
            if value
        )
    return metadata


def is_allowed_language_channel(
    channel: Channel,
    language_filter: dict[str, set[str] | dict[str, str]],
) -> bool:
    base_id = channel_id_base(channel)
    metadata = language_filter.get("metadata", {})
    metadata_text = metadata.get(base_id.casefold(), "") if isinstance(metadata, dict) else ""
    combined = combined_language_text(channel, metadata_text)

    if has_channel_name(channel, KNOWN_DISALLOWED_LANGUAGE_CHANNEL_NAMES):
        return False
    if has_any_channel_prefix(channel, KNOWN_DISALLOWED_LANGUAGE_CHANNEL_PREFIXES):
        return False

    if has_any_language_marker(combined, ALLOWED_LANGUAGE_MARKERS):
        return True
    if is_bangladeshi_channel(channel) or contains_bengali_script(channel.name):
        return True
    if has_any_channel_prefix(channel, KNOWN_ALLOWED_LANGUAGE_CHANNEL_PREFIXES):
        return True

    if has_any_language_marker(combined, DISALLOWED_LANGUAGE_MARKERS):
        return False

    allowed_ids = language_filter.get("allowed_ids", set())
    allowed_urls = language_filter.get("allowed_urls", set())
    if base_id in allowed_ids or channel.url in allowed_urls:
        return True

    disallowed_ids = language_filter.get("disallowed_ids", set())
    disallowed_urls = language_filter.get("disallowed_urls", set())
    if base_id in disallowed_ids or channel.url in disallowed_urls:
        return False

    return False


def channel_id_base(channel: Channel) -> str:
    return channel.tvg_id.casefold().split("@", 1)[0]


def combined_language_text(channel: Channel, metadata_text: str) -> str:
    return normalize_text(
        " ".join(
            (
                channel.name,
                channel.tvg_id,
                " ".join(channel.groups),
                metadata_text,
            )
        )
    )


def has_any_language_marker(normalized: str, markers: tuple[str, ...]) -> bool:
    return any(has_phrase(normalized, marker) for marker in markers)


def has_any_channel_prefix(channel: Channel, prefixes: tuple[str, ...]) -> bool:
    normalized = normalize_text(channel.name)
    return any(has_channel_prefix(normalized, prefix) for prefix in prefixes)


def has_channel_name(channel: Channel, names: tuple[str, ...]) -> bool:
    normalized = normalize_text(channel.name)
    return any(normalized == normalize_text(name) for name in names)


def is_bangladeshi_channel(channel: Channel) -> bool:
    if channel_id_base(channel).endswith(".bd"):
        return True
    text = normalize_text(" ".join((channel.name, " ".join(channel.groups), " ".join(channel.tags))))
    return has_phrase(text, "bangladeshi")


def contains_bengali_script(value: str) -> bool:
    return any("\u0980" <= character <= "\u09ff" for character in value)


def is_allowed_channel(channel: Channel) -> bool:
    return is_south_asia_candidate(channel) or is_cricket_candidate(channel)


def is_arabic_channel(channel: Channel) -> bool:
    normalized_name = normalize_text(channel.name)
    if normalized_name in ARABIC_CHANNEL_NAME_EXCLUSIONS:
        return True

    text = " ".join(
        (
            channel.name,
            channel.tvg_id,
            " ".join(channel.groups),
            " ".join(channel.tags),
        )
    )
    normalized = normalize_text(text)
    return any(has_phrase(normalized, marker) for marker in ARABIC_CHANNEL_MARKERS)


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


def load_known_channel_aliases(source: str, timeout: float) -> tuple[tuple[str, ...], ...]:
    loaded_aliases: list[tuple[str, ...]] = []
    if source.strip():
        try:
            loaded_aliases = extract_known_channel_aliases(load_text(resolve_known_source(source), timeout))
        except Exception as error:
            print(f"Could not load known Indian channel source {source}: {error}", file=sys.stderr)

    aliases: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for alias_group in (
        list(TOP_CHANNEL_BRAND_PRIORITY)
        + loaded_aliases
        + list(KNOWN_INDIAN_CHANNEL_PRIORITY)
    ):
        normalized_key = "|".join(normalize_text(alias) for alias in alias_group)
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        aliases.append(alias_group)
    return tuple(aliases)


def resolve_known_source(source: str) -> str:
    normalized = source.strip()
    if "telelibrary.fandom.com/wiki/category" not in normalized.casefold():
        return normalized
    return DEFAULT_KNOWN_INDIAN_CHANNEL_SOURCE


def extract_known_channel_aliases(text: str) -> list[tuple[str, ...]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return extract_known_channel_aliases_from_html(text)

    members = payload.get("query", {}).get("categorymembers", [])
    aliases: list[tuple[str, ...]] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        title = str(member.get("title", "")).strip()
        if title:
            aliases.append(channel_title_aliases(title))
    return aliases


def extract_known_channel_aliases_from_html(text: str) -> list[tuple[str, ...]]:
    aliases: list[tuple[str, ...]] = []
    for title in re.findall(r'class="category-page__member-link"[^>]*>(.*?)</a>', text):
        cleaned = re.sub(r"<[^>]+>", "", html.unescape(title)).strip()
        if cleaned:
            aliases.append(channel_title_aliases(cleaned))
    return aliases


def channel_title_aliases(title: str) -> tuple[str, ...]:
    without_parentheses = re.sub(r"\s*\([^)]*\)", "", title).strip()
    aliases = [title]
    if without_parentheses and without_parentheses != title:
        aliases.append(without_parentheses)
    return tuple(aliases)


def write_priority_playlist(
    path: Path,
    header: str,
    results: list[ProbeResult],
    known_channel_aliases: tuple[tuple[str, ...], ...],
) -> None:
    sorted_results = sort_priority_results(results, known_channel_aliases)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write(header.rstrip() + "\n")
        for result in sorted_results:
            for tag in result.channel.tags:
                output.write(tag.rstrip() + "\n")
            output.write(result.channel.url.rstrip() + "\n")


def sort_priority_results(
    results: list[ProbeResult],
    known_channel_aliases: tuple[tuple[str, ...], ...],
) -> list[ProbeResult]:
    return sorted(
        (result for result in results if result.ok),
        key=lambda result: priority_sort_key(result, known_channel_aliases),
    )


def priority_sort_key(
    result: ProbeResult,
    known_channel_aliases: tuple[tuple[str, ...], ...],
) -> tuple[int, int, tuple[int, str, str, int]]:
    priority = known_indian_channel_priority(result.channel, known_channel_aliases)
    if priority >= 0:
        return (0, priority, playlist_sort_key(result))
    return (1, 0, playlist_sort_key(result))


def known_indian_channel_priority(
    channel: Channel,
    known_channel_aliases: tuple[tuple[str, ...], ...],
) -> int:
    normalized = normalize_text(channel.name)
    for index, aliases in enumerate(known_channel_aliases):
        if any(has_channel_prefix(normalized, alias) for alias in aliases):
            return index
    return -1


def has_channel_prefix(normalized: str, alias: str) -> bool:
    normalized_alias = normalize_text(alias)
    return normalized == normalized_alias or normalized.startswith(f"{normalized_alias} ")


def rewrite_playlist_with_priority(
    path: Path,
    known_channel_aliases: tuple[tuple[str, ...], ...],
) -> None:
    from refresh_playlist import parse_m3u

    header, channels = parse_m3u(path.read_text(encoding="utf-8"), str(path))
    results = [
        ProbeResult(channel=channel, ok=True, elapsed_ms=0, checked_url=channel.url)
        for channel in channels
    ]
    write_priority_playlist(path, header, results, known_channel_aliases)


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
        "skipped_arabic_channels": sum(
            int(summary.get("skipped_arabic_channels", 0)) for summary in source_summaries
        ),
        "skipped_language_channels": sum(
            int(summary.get("skipped_language_channels", 0)) for summary in source_summaries
        ),
        "skipped_source_channels": sum(
            int(summary.get("skipped_source_channels", 0)) for summary in source_summaries
        ),
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

    known_channel_aliases = load_known_channel_aliases(args.known_indian_channel_source, args.timeout)
    language_filter = load_language_filter(
        args.channel_metadata_source,
        args.timeout,
        not args.no_language_filter,
    )
    skip_sources = [] if args.no_default_skip_channel_source else [DEFAULT_SKIP_CHANNEL_SOURCE]
    skip_sources.extend(args.skip_channel_source)
    skip_filter = load_skip_channel_filter(skip_sources, args.timeout)
    source_records = scrape_playlist_sources(args.playlist_index, countries, args.timeout)
    if not args.no_web_searched_sources:
        source_records.extend(web_searched_sources())
    candidates, source_summaries = load_candidate_channels(
        source_records,
        extra_sources,
        args.timeout,
        language_filter,
        skip_filter,
    )
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
    write_priority_playlist(output, "#EXTM3U", published_results, known_channel_aliases)
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
    rewrite_playlist_with_priority(output, known_channel_aliases)

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
