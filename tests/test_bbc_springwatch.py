import json
import unittest

from bbc_springwatch import choose_playback, parse_streams


def page_with_initial_data(payload):
    encoded = json.dumps(json.dumps(payload))[1:-1]
    return f'<script>window.__INITIAL_DATA__="{encoded}";</script>'


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


if __name__ == "__main__":
    unittest.main()
