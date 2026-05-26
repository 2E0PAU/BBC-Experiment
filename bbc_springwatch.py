from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable

import requests


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

    def get_streams(self) -> list[Stream]:
        if self._cache and time.time() - self._cache[0] < self.cache_seconds:
            return self._cache[1]

        html = self._fetch_text(self.official_live_url)
        streams = []
        for stream in parse_streams(html, self.official_live_url):
            playback = self._get_playback(stream.vpid)
            streams.append(
                Stream(
                    title=stream.title,
                    vpid=stream.vpid,
                    pid=stream.pid,
                    synopsis=stream.synopsis,
                    image_url=stream.image_url,
                    status=stream.status,
                    availability_type=stream.availability_type,
                    schedule_start=stream.schedule_start,
                    schedule_end=stream.schedule_end,
                    lead_media=stream.lead_media,
                    official_url=stream.official_url,
                    playback=playback,
                )
            )

        if not streams:
            raise BBCSpringwatchError("No Springwatch wildlife camera streams were found on the BBC live page.")

        self._cache = (time.time(), streams)
        return streams

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
    match = re.search(r'window\.__INITIAL_DATA__="((?:\\.|[^"\\])*)";', html, re.DOTALL)
    if not match:
        raise BBCSpringwatchError("BBC page did not contain window.__INITIAL_DATA__.")

    try:
        decoded = json.loads(f'"{match.group(1)}"')
        payload = json.loads(decoded)
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
