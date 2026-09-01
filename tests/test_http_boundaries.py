import json
import unittest

import server
from tests.http_test_support import LiveServerTestCase


class ByteRangeTests(unittest.TestCase):
    def test_closed_open_and_suffix_ranges(self):
        self.assertEqual(server.parse_single_byte_range("bytes=0-15", 100), server.ByteRange(0, 15))
        self.assertEqual(server.parse_single_byte_range("bytes=16-", 100), server.ByteRange(16, 99))
        self.assertEqual(server.parse_single_byte_range("bytes=-16", 100), server.ByteRange(84, 99))
        self.assertEqual(server.parse_single_byte_range("bytes=90-200", 100), server.ByteRange(90, 99))

    def test_invalid_or_unsupported_ranges_are_ignored(self):
        for value in ("items=0-1", "bytes=abc-def", "bytes=0-1,4-5", "bytes=-", "bytes =0-1"):
            with self.subTest(value=value):
                self.assertIsNone(server.parse_single_byte_range(value, 100))

    def test_valid_unsatisfiable_ranges_raise(self):
        for value, size in (("bytes=100-", 100), ("bytes=8-2", 100), ("bytes=-0", 100), ("bytes=0-0", 0)):
            with self.subTest(value=value), self.assertRaises(server.UnsatisfiableRange):
                server.parse_single_byte_range(value, size)

    def test_huge_numerals_do_not_trigger_integer_conversion_limits(self):
        huge = "9" * 10_000
        with self.assertRaises(server.UnsatisfiableRange):
            server.parse_single_byte_range(f"bytes={huge}-", 100)
        self.assertEqual(server.parse_single_byte_range(f"bytes=-{huge}", 100), server.ByteRange(0, 99))


class MediaRangeHttpTests(LiveServerTestCase):
    media_bytes = bytes(range(100))

    def setUp(self):
        super().setUp()
        upload = server.upload_dir("range-fixture")
        upload.mkdir(parents=True)
        self.media_path = upload / "fixture.mp4"
        self.media_path.write_bytes(self.media_bytes)
        (upload / "manifest.json").write_text(
            json.dumps({"source_path": str(self.media_path), "media_type": "video"}),
            encoding="utf-8",
        )

    def test_closed_range_returns_exact_media_bytes_and_headers(self):
        status, headers, body = self.request("GET", "/media/upload/range-fixture", headers={"Range": "bytes=0-15"})

        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 0-15/100")
        self.assertEqual(headers["Content-Length"], "16")
        self.assertEqual(body, bytes(range(16)))

    def test_open_range_returns_exact_media_bytes_and_headers(self):
        status, headers, body = self.request("GET", "/media/upload/range-fixture", headers={"Range": "bytes=16-"})

        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 16-99/100")
        self.assertEqual(headers["Content-Length"], "84")
        self.assertEqual(body, bytes(range(16, 100)))

    def test_suffix_range_returns_exact_media_bytes_and_headers(self):
        status, headers, body = self.request("GET", "/media/upload/range-fixture", headers={"Range": "bytes=-16"})

        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 84-99/100")
        self.assertEqual(headers["Content-Length"], "16")
        self.assertEqual(body, bytes(range(84, 100)))

    def test_huge_start_returns_empty_416_response(self):
        status, headers, body = self.request(
            "GET",
            "/media/upload/range-fixture",
            headers={"Range": f"bytes={'9' * 10_000}-"},
        )

        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], "bytes */100")
        self.assertEqual(headers["Content-Length"], "0")
        self.assertEqual(body, b"")

    def test_malformed_and_multiple_ranges_return_full_file(self):
        for value in ("items=0-1", "bytes=abc-def", "bytes=0-1,4-5", "bytes =0-1"):
            with self.subTest(value=value):
                status, headers, body = self.request(
                    "GET",
                    "/media/upload/range-fixture",
                    headers={"Range": value},
                )

                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Length"], "100")
                self.assertEqual(body, self.media_bytes)
