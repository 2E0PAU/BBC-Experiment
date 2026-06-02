from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable

import requests

logger = logging.getLogger(__name__)


class BBCSpringwatchError(RuntimeError):
    """Raised when the BBC Springwatch page or media selector cannot be read."""


@dataclass(frozen=True)
class Playback:
    transfer_format: str
    url: str
    supplier: str | None = None
    priority: int | None = None


@dataclass(frozen=True)
class Stream:
    title: str
    vpid: str
    pid: str | None
    synopsis: str
    image_url: str | None
    status: str | None
    availability_type: str | None
    schedule_start: str | None
    schedule_end: str | None
    lead_media: bool
    official_url: str
    playback: list[Playback]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["playback"] = [asdict(item) for item in self.playback]
        return data


class BBCSpringwatchClient:
    official_live_url = "https://www.bbc.co.uk/live/cqxpyv5y48yt"
    media_selector_url = (
        "https://open.live.bbc.co.uk/mediaselector/6/select/version/2.0/"
        "vpid/{vpid}/format/json/mediaset/pc/vmt/any/proto/https"
    )

    def __init__(self, session: requests.Session | None = None, cache_seconds: int = 60):
        self.session = session or requests.Session()
        self.cache_seconds = cache_seconds
        self._cache: tuple[float, list[Stream]] | None = None
        self._lock = threading.Lock()

    def get_streams(self) -> list[Stream]:
        with self._lock:
            if self._cache and time.time() - self._cache[0] < self.cache_seconds:
                return self._cache[1]

        # Network work is done without holding the lock so concurrent requests
        # are not serialised behind a slow BBC response.
        streams = self._build_streams()

        with self._lock:
            self._cache = (time.time(), streams)
        return streams

    def _build_streams(self) -> list[Stream]:
        html = self._fetch_text(self.official_live_url)
        parsed = parse_streams(html, self.official_live_url)
        if not parsed:
            raise BBCSpringwatchError(
                "No Springwatch wildlife camera streams were found on the BBC live page."
            )

        playback_by_vpid = self._resolve_playback(parsed)
        streams = [
            Stream(**asdict(stream), playback=playback_by_vpid.get(stream.vpid, []))
            for stream in parsed
        ]
        logger.info("Discovered %d Springwatch stream(s) on the BBC live page.", len(streams))
        return streams

    def _resolve_playback(self, parsed: list[ParsedStream]) -> dict[str, list[Playback]]:
        """Fetch playback for each stream in parallel, tolerating per-stream failures.

        A failure for one camera (geo-block, expired media, transient 5xx) must not
        take down the whole wall, so each error is logged and that stream is returned
        with empty playback for the frontend to handle gracefully.
        """
        playback_by_vpid: dict[str, list[Playback]] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(parsed))) as executor:
            futures = {
                executor.submit(self._get_playback, stream.vpid): stream for stream in parsed
            }
            for future in as_completed(futures):
                stream = futures[future]
                try:
                    playback_by_vpid[stream.vpid] = future.result()
                except BBCSpringwatchError as exc:
                    logger.warning(
                        "Could not resolve playback for %r (%s): %s",
                        stream.title,
                        stream.vpid,
                        exc,
                    )
                    playback_by_vpid[stream.vpid] = []
        return playback_by_vpid

    def _fetch_text(self, url: str) -> str:
        try:
            response = self.session.get(
                url,
                timeout=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; BBCSpringwatchDashboard/1.0; "
                        "+https://www.bbc.co.uk/live/cqxpyv5y48yt)"
                    )
                },
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise BBCSpringwatchError(f"Could not fetch {url}: {exc}") from exc
        return response.text

    def _get_playback(self, vpid: str) -> list[Playback]:
        url = self.media_selector_url.format(vpid=vpid)
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise BBCSpringwatchError(f"Could not fetch BBC playback data for {vpid}: {exc}") from exc

        return choose_playback(payload)


@dataclass(frozen=True)
class ParsedStream:
    title: str
    vpid: str
    pid: str | None
    synopsis: str
    image_url: str | None
    status: str | None
    availability_type: str | None
    schedule_start: str | None
    schedule_end: str | None
    lead_media: bool
    official_url: str


def parse_streams(html: str, official_url: str) -> list[ParsedStream]:
    data = extract_initial_data(html)
    streams: list[ParsedStream] = []
    seen_vpids: set[str] = set()

    for node in walk_dicts(data):
        version = node.get("version")
        if not isinstance(version, dict):
            continue

        vpid = version.get("vpid")
        title = node.get("title")
        if not isinstance(vpid, str) or not isinstance(title, str):
            continue

        synopsis = _synopsis(node.get("synopses"))
        availability_type = _string_or_none(version.get("availabilityType"))
        is_springwatch = title.lower().startswith("springwatch")
        is_wildlife_camera = "springwatch wildlife cameras" in synopsis.lower()
        is_webcast = availability_type == "webcast"

        if not (is_webcast and (is_springwatch or is_wildlife_camera)):
            continue
        if vpid in seen_vpids:
            continue

        seen_vpids.add(vpid)
        schedule = version.get("schedule") if isinstance(version.get("schedule"), dict) else {}
        streams.append(
            ParsedStream(
                title=title,
                vpid=vpid,
                pid=_pid_from_urn(node.get("urn")),
                synopsis=synopsis,
                image_url=_image_url(node.get("imageUrlTemplate")),
                status=_string_or_none(version.get("status")),
                availability_type=availability_type,
                schedule_start=_normalise_datetime(schedule.get("start")),
                schedule_end=_normalise_datetime(schedule.get("end")),
                lead_media=bool(node.get("leadMedia", False)),
                official_url=official_url,
            )
        )

    streams.sort(key=lambda item: (not item.lead_media, item.title.lower()))
    return streams


def extract_initial_data(html: str) -> dict[str, Any]:
    marker = "window.__INITIAL_DATA__"
    start = html.find(marker)
    if start == -1:
        raise BBCSpringwatchError("BBC page did not contain window.__INITIAL_DATA__.")

    equals = html.find("=", start + len(marker))
    if equals == -1:
        raise BBCSpringwatchError("BBC page did not contain window.__INITIAL_DATA__.")

    value_start = equals + 1
    while value_start < len(html) and html[value_start] in " \t\r\n":
        value_start += 1
    if value_start >= len(html):
        raise BBCSpringwatchError("BBC page data was empty.")

    # BBC ships this either as a JSON string whose contents are themselves JSON
    # (escaped form) or as a bare JSON object literal. raw_decode handles both,
    # correctly stopping at the end of the value and ignoring the trailing ";"
    # and the rest of the page (no brace-matching needed).
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(html, value_start)
        payload = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise BBCSpringwatchError("BBC page data could not be decoded.") from exc

    if not isinstance(payload, dict):
        raise BBCSpringwatchError("BBC page data was not a JSON object.")
    return payload


def choose_playback(payload: dict[str, Any]) -> list[Playback]:
    connections: list[Playback] = []
    for media in payload.get("media", []):
        if not isinstance(media, dict):
            continue
        for connection in media.get("connection", []):
            if not isinstance(connection, dict):
                continue
            if connection.get("transferFormat") != "hls":
                continue
            href = connection.get("href")
            if not isinstance(href, str):
                continue
            connections.append(
                Playback(
                    transfer_format="hls",
                    url=href,
                    supplier=_string_or_none(connection.get("supplier")),
                    priority=_int_or_none(connection.get("priority")),
                )
            )

    connections.sort(key=lambda item: item.priority if item.priority is not None else 9999)
    return connections


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def _synopsis(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("short", "medium", "long"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _image_url(template: Any) -> str | None:
    if not isinstance(template, str) or not template.strip():
        return None
    url = template.replace("$recipe", "960xn")
    if url.startswith("//"):
        return f"https:{url}"
    if not url.startswith("http"):
        return f"https://{url.lstrip('/')}"
    return url


def _pid_from_urn(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.rsplit(":", 1)[-1] or None


def _normalise_datetime(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
