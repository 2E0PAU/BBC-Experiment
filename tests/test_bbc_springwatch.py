import json
import unittest

import requests

from bbc_springwatch import (
    BBCSpringwatchClient,
    BBCSpringwatchError,
    _image_url,
    _normalise_datetime,
    choose_playback,
    extract_initial_data,
    parse_streams,
)


def page_with_initial_data(payload):
    encoded = json.dumps(json.dumps(payload))[1:-1]
    return f'<script>window.__INITIAL_DATA__="{encoded}";</script>'


def page_with_object_literal(payload):
    # The alternative shape BBC sometimes ships: a bare object literal.
    return f"<script>window.__INITIAL_DATA__ = {json.dumps(payload)};</script>"


def springwatch_payload(*media_items):
    return {"data": {"page": {"mediaItems": list(media_items)}}}


def webcast_item(title, vpid, *, synopsis="Live Springwatch wildlife cameras."):
    return {
        "urn": f"urn:bbc:pips:pid:{vpid}",
        "title": title,
        "synopses": {"short": synopsis},
        "version": {"vpid": vpid, "availabilityType": "webcast", "status": "LIVE"},
    }


def hls_media(href, priority="10", supplier="main"):
    return {
        "media": [
            {
                "connection": [
                    {
                        "transferFormat": "hls",
                        "href": href,
                        "supplier": supplier,
                        "priority": priority,
                    }
                ]
            }
        ]
    }


class FakeResponse:
    def __init__(self, *, text="", json_data=None, status_code=200):
        self.text = text
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """Routes the live page request and per-vpid media-selector requests."""

    def __init__(self, page_html, media_map):
        self.page_html = page_html
        self.media_map = media_map  # vpid -> json payload, or "fail"

    def get(self, url, **_kwargs):
        if "/live/" in url:
            return FakeResponse(text=self.page_html)
        for vpid, result in self.media_map.items():
            if f"/vpid/{vpid}/" in url:
                if result == "fail":
                    return FakeResponse(status_code=500)
                return FakeResponse(json_data=result)
        return FakeResponse(status_code=404)


class SpringwatchParserTests(unittest.TestCase):
    def test_extracts_springwatch_webcasts(self):
        html = page_with_initial_data(
            {
                "data": {
                    "page": {
                        "mediaItems": [
                            {
                                "urn": "urn:bbc:pips:pid:l00589s0",
                                "title": "Springwatch: Multicam",
                                "synopses": {
                                    "short": "Watch four cameras at once from the live Springwatch wildlife cameras."
                                },
                                "imageUrlTemplate": "https://ichef.bbci.co.uk/images/ic/$recipe/p0nn2j4z.jpg",
                                "leadMedia": False,
                                "version": {
                                    "vpid": "l00589s3",
                                    "availabilityType": "webcast",
                                    "status": "LIVE",
                                    "schedule": {
                                        "start": "2026-05-26T08:47:31Z",
                                        "end": "2026-05-26T21:00:00Z",
                                    },
                                },
                            },
                            {
                                "title": "Unrelated Clip",
                                "version": {"vpid": "p123", "availabilityType": "on_demand"},
                            },
                        ]
                    }
                }
            }
        )

        streams = parse_streams(html, "https://www.bbc.co.uk/live/cqxpyv5y48yt")

        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0].title, "Springwatch: Multicam")
        self.assertEqual(streams[0].vpid, "l00589s3")
        self.assertEqual(streams[0].pid, "l00589s0")
        self.assertEqual(streams[0].image_url, "https://ichef.bbci.co.uk/images/ic/960xn/p0nn2j4z.jpg")

    def test_ignores_non_springwatch_pages(self):
        html = page_with_initial_data(
            springwatch_payload(
                {"title": "News at Ten", "version": {"vpid": "n1", "availabilityType": "webcast"}},
            )
        )

        self.assertEqual(parse_streams(html, "https://example.invalid"), [])

    def test_lead_media_is_sorted_first(self):
        lead = webcast_item("Springwatch: Badger Cam", "v2")
        lead["leadMedia"] = True
        html = page_with_initial_data(
            springwatch_payload(webcast_item("Springwatch: Pond Cam", "v1"), lead)
        )

        streams = parse_streams(html, "https://example.invalid")

        self.assertEqual([s.vpid for s in streams], ["v2", "v1"])

    def test_prefers_hls_playback_by_priority(self):
        playback = choose_playback(
            {
                "media": [
                    {
                        "connection": [
                            {
                                "transferFormat": "dash",
                                "href": "https://example.invalid/stream.mpd",
                                "priority": "1",
                            },
                            {
                                "transferFormat": "hls",
                                "href": "https://example.invalid/backup.m3u8",
                                "supplier": "backup",
                                "priority": "20",
                            },
                            {
                                "transferFormat": "hls",
                                "href": "https://example.invalid/main.m3u8",
                                "supplier": "main",
                                "priority": "10",
                            },
                        ]
                    }
                ]
            }
        )

        self.assertEqual([item.url for item in playback], ["https://example.invalid/main.m3u8", "https://example.invalid/backup.m3u8"])


class InitialDataTests(unittest.TestCase):
    def test_parses_escaped_string_form(self):
        html = page_with_initial_data({"hello": "world"})
        self.assertEqual(extract_initial_data(html), {"hello": "world"})

    def test_parses_object_literal_form(self):
        html = page_with_object_literal({"hello": "world"})
        self.assertEqual(extract_initial_data(html), {"hello": "world"})

    def test_object_literal_with_trailing_content(self):
        html = (
            "<script>window.__INITIAL_DATA__ = "
            '{"nested": {"a": [1, 2, 3]}};\n'
            "window.__SOMETHING_ELSE__ = 5;</script>"
        )
        self.assertEqual(extract_initial_data(html), {"nested": {"a": [1, 2, 3]}})

    def test_missing_marker_raises(self):
        with self.assertRaises(BBCSpringwatchError):
            extract_initial_data("<html>no data here</html>")

    def test_undecodable_data_raises(self):
        html = "<script>window.__INITIAL_DATA__ = not-json;</script>"
        with self.assertRaises(BBCSpringwatchError):
            extract_initial_data(html)


class HelperTests(unittest.TestCase):
    def test_image_url_expands_recipe_and_protocol(self):
        self.assertEqual(
            _image_url("//ichef.bbci.co.uk/$recipe/x.jpg"),
            "https://ichef.bbci.co.uk/960xn/x.jpg",
        )
        self.assertEqual(
            _image_url("ichef.bbci.co.uk/$recipe/x.jpg"),
            "https://ichef.bbci.co.uk/960xn/x.jpg",
        )
        self.assertIsNone(_image_url(None))
        self.assertIsNone(_image_url("   "))

    def test_normalise_datetime(self):
        self.assertEqual(
            _normalise_datetime("2026-05-26T08:47:31Z"),
            "2026-05-26T08:47:31+00:00",
        )
        self.assertEqual(_normalise_datetime("not a date"), "not a date")
        self.assertIsNone(_normalise_datetime(None))


class ClientTests(unittest.TestCase):
    def test_get_streams_degrades_when_one_playback_fails(self):
        page = page_with_initial_data(
            springwatch_payload(
                webcast_item("Springwatch: Pond Cam", "good"),
                webcast_item("Springwatch: Badger Cam", "bad"),
            )
        )
        session = FakeSession(
            page,
            {
                "good": hls_media("https://example.invalid/good.m3u8"),
                "bad": "fail",
            },
        )
        client = BBCSpringwatchClient(session=session)

        streams = {s.vpid: s for s in client.get_streams()}

        self.assertEqual(set(streams), {"good", "bad"})
        self.assertEqual(streams["good"].playback[0].url, "https://example.invalid/good.m3u8")
        self.assertEqual(streams["bad"].playback, [])

    def test_get_streams_caches_results(self):
        page = page_with_initial_data(
            springwatch_payload(webcast_item("Springwatch: Pond Cam", "good"))
        )
        session = FakeSession(page, {"good": hls_media("https://example.invalid/good.m3u8")})
        client = BBCSpringwatchClient(session=session)

        first = client.get_streams()
        second = client.get_streams()

        self.assertIs(first, second)

    def test_get_streams_raises_when_page_has_no_streams(self):
        page = page_with_initial_data(springwatch_payload())
        session = FakeSession(page, {})
        client = BBCSpringwatchClient(session=session)

        with self.assertRaises(BBCSpringwatchError):
            client.get_streams()


if __name__ == "__main__":
    unittest.main()
