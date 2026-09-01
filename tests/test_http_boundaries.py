import json
import os
import unittest
from http import HTTPStatus
from unittest import mock

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


class RequestBoundaryHelperTests(unittest.TestCase):
    def test_content_length_accepts_absent_and_decimal_values(self):
        self.assertEqual(server.parse_content_length(None, required=False, maximum=100), 0)
        self.assertEqual(server.parse_content_length(" 00042 ", required=True, maximum=100), 42)

    def test_content_length_rejects_missing_malformed_and_oversized_values(self):
        cases = (
            (None, True, 100, HTTPStatus.LENGTH_REQUIRED),
            ("-1", False, 100, HTTPStatus.BAD_REQUEST),
            ("12x", False, 100, HTTPStatus.BAD_REQUEST),
            ("101", False, 100, HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
            ("9" * 10_000, False, 100, HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
        )
        for value, required, maximum, expected_status in cases:
            with self.subTest(value=value), self.assertRaises(server.RequestError) as caught:
                server.parse_content_length(value, required=required, maximum=maximum)
            self.assertEqual(caught.exception.status, expected_status)

    def test_request_hosts_canonicalize_loopback_ipv4_ipv6_names_and_ports(self):
        allowed = server.allowed_request_hosts("192.0.2.10")

        for host in (
            "localhost",
            "LOCALHOST.",
            "127.0.0.1:8894",
            "[::1]",
            "[0:0:0:0:0:0:0:1]:8894",
            "192.0.2.10:7777",
        ):
            with self.subTest(host=host):
                self.assertTrue(server.request_host_allowed(host, allowed))

    def test_wildcard_bind_does_not_authorize_arbitrary_hosts(self):
        for bind_host in ("0.0.0.0", "::", "[::]"):
            with self.subTest(bind_host=bind_host):
                allowed = server.allowed_request_hosts(bind_host)
                self.assertFalse(server.request_host_allowed("attacker.invalid:8894", allowed))
                self.assertFalse(server.request_host_allowed("192.0.2.40:8894", allowed))

    def test_configured_allowed_hosts_support_names_ipv4_ipv6_and_explicit_ports(self):
        with mock.patch.dict(
            os.environ,
            {
                "SPRITE_VIDEO_LAB_ALLOWED_HOSTS": (
                    "studio.local., 192.0.2.25:9000, [2001:db8::25]:9001"
                )
            },
        ):
            allowed = server.allowed_request_hosts("0.0.0.0")

        self.assertTrue(server.request_host_allowed("STUDIO.LOCAL:1234", allowed))
        self.assertTrue(server.request_host_allowed("192.0.2.25:9000", allowed))
        self.assertFalse(server.request_host_allowed("192.0.2.25:9001", allowed))
        self.assertTrue(server.request_host_allowed("[2001:0db8::25]:9001", allowed))

    def test_malformed_request_hosts_fail_closed(self):
        allowed = server.allowed_request_hosts("127.0.0.1")
        for host in (
            None, "", "bad host", "localhost:", "localhost:bad", "user@localhost", "localhost/path", "::1",
            "127.000.000.001:8894",
        ):
            with self.subTest(host=host):
                self.assertFalse(server.request_host_allowed(host, allowed))

    def test_origin_must_match_scheme_host_and_effective_port(self):
        self.assertTrue(server.origin_matches_request("http://LOCALHOST.:80", "localhost"))
        self.assertTrue(server.origin_matches_request("http://[0:0:0:0:0:0:0:1]:8894", "[::1]:8894"))
        self.assertFalse(server.origin_matches_request("http://localhost:", "localhost"))
        for origin in (
            "null",
            "not-an-origin",
            "http://localhost:",
            "https://localhost:8894",
            "http://localhost:8895",
            "http://attacker.invalid:8894",
            "http://localhost:8894/path",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(server.origin_matches_request(origin, "localhost:8894"))

    def test_upload_limit_configuration_requires_a_positive_decimal(self):
        with mock.patch.dict(os.environ, {"SPRITE_VIDEO_LAB_MAX_UPLOAD_BYTES": "001024"}):
            self.assertEqual(server.configured_max_upload_bytes(), 1024)
        for value in ("", "0", "-1", "1.5", "9" * 100):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"SPRITE_VIDEO_LAB_MAX_UPLOAD_BYTES": value}
            ), self.assertRaisesRegex(ValueError, "SPRITE_VIDEO_LAB_MAX_UPLOAD_BYTES"):
                server.configured_max_upload_bytes()


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

    def test_duplicate_range_fields_return_full_file(self):
        status, headers, body = self.request(
            "GET",
            "/media/upload/range-fixture",
            headers=[("Range", "bytes=0-15"), ("Range", "bytes=16-")],
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Length"], "100")
        self.assertEqual(body, self.media_bytes)


class RequestBoundaryHttpTests(LiveServerTestCase):
    security_headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Content-Security-Policy": (
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; connect-src 'self'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; "
            "script-src 'self'; style-src 'self' 'unsafe-inline'"
        ),
    }

    def assert_security_headers(self, headers):
        for name, expected in self.security_headers.items():
            self.assertEqual(headers.get(name), expected, name)

    def test_host_is_required_unique_well_formed_and_allowed(self):
        requests = (
            {"skip_host": True},
            {"headers": {"Host": "attacker.invalid:8894"}},
            {"headers": {"Host": "localhost:bad"}},
            {"headers": [("Host", f"127.0.0.1:{self.port}"), ("Host", f"localhost:{self.port}")]},
        )
        for kwargs in requests:
            with self.subTest(kwargs=kwargs):
                status, headers, payload = self.request("GET", "/api/runtime-info", **kwargs)
                self.assertEqual(status, 421)
                self.assert_security_headers(headers)
                self.assertFalse(json.loads(payload)["ok"])

    def test_loopback_host_canonical_forms_are_allowed(self):
        for host in (f"localhost.:{self.port}", f"127.0.0.1:{self.port}", f"[::1]:{self.port}"):
            with self.subTest(host=host):
                status, _, payload = self.request("GET", "/api/runtime-info", headers={"Host": host})
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(payload)["ok"])

    def test_post_origin_must_match_host_when_present(self):
        cases = (
            ("http://attacker.invalid:8894", 403),
            ("null", 403),
            ("not-an-origin", 403),
            (f"https://127.0.0.1:{self.port}", 403),
            (f"http://127.0.0.1:{self.port}", 200),
        )
        for origin, expected in cases:
            with self.subTest(origin=origin):
                status, _, payload = self.request(
                    "POST",
                    "/api/realesrgan-status",
                    body=b"{}",
                    headers={
                        "Host": f"127.0.0.1:{self.port}",
                        "Origin": origin,
                        "Content-Type": "application/json",
                    },
                )
                self.assertEqual(status, expected, payload)
                self.assertEqual(json.loads(payload)["ok"], expected == 200)

    def test_post_without_origin_is_allowed_for_local_cli_clients(self):
        status, _, payload = self.request(
            "POST",
            "/api/realesrgan-status",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])

    def test_json_body_status_codes_and_empty_object_default(self):
        cases = (
            (b"{}", {"Content-Type": "text/plain"}, 415),
            (b"\xff", {"Content-Type": "application/json"}, 400),
            (b"[]", {"Content-Type": "application/json"}, 400),
            (b"{", {"Content-Type": "application/json"}, 400),
            (b"", {"Content-Type": "application/json", "Content-Length": "-1"}, 400),
            (b"", {"Content-Type": "application/json", "Content-Length": "12x"}, 400),
            (
                b"",
                {"Content-Type": "application/json", "Content-Length": str((1024 * 1024) + 1)},
                413,
            ),
            (b"", {"Content-Type": "application/json"}, 200),
        )
        for body, headers, expected in cases:
            with self.subTest(headers=headers, body=body[:10]):
                status, response_headers, payload = self.request(
                    "POST", "/api/realesrgan-status", body=body, headers=headers
                )
                self.assertEqual(status, expected, payload)
                self.assertEqual(response_headers.get("Cache-Control"), "no-store")
                self.assert_security_headers(response_headers)
                self.assertEqual(json.loads(payload)["ok"], expected == 200)

    def test_duplicate_content_length_and_transfer_encoding_are_rejected(self):
        cases = (
            [("Content-Type", "application/json"), ("Content-Length", "2"), ("Content-Length", "2")],
            [("Content-Type", "application/json"), ("Content-Length", "2"), ("Content-Length", "3")],
            [("Content-Type", "application/json"), ("Transfer-Encoding", "chunked")],
        )
        for headers in cases:
            with self.subTest(headers=headers):
                status, _, payload = self.request(
                    "POST", "/api/realesrgan-status", body=b"{}", headers=headers
                )
                self.assertEqual(status, 400)
                self.assertFalse(json.loads(payload)["ok"])

    def test_open_path_only_invokes_opener_for_managed_directory(self):
        managed = self.work_root / "exports" / "approved"
        managed.mkdir(parents=True)
        external = self.root / "external"
        external.mkdir()
        external_file = external / "file.txt"
        external_file.write_text("not a directory", encoding="utf-8")

        with mock.patch.object(server, "open_path_in_file_browser") as opener:
            for path, expected in ((managed, 200), (external, 400), (external_file, 400)):
                with self.subTest(path=path):
                    body = json.dumps({"path": str(path)}).encode("utf-8")
                    status, _, payload = self.request(
                        "POST", "/api/open-path", body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    self.assertEqual(status, expected)
                    self.assertEqual(json.loads(payload)["ok"], expected == 200)

            opener.assert_called_once_with(managed)

    def test_unnormalized_media_traversal_gets_400_response(self):
        job_id = "job-secret"
        secret = server.JOBS_DIR / job_id / "source.png"
        secret.parent.mkdir(parents=True)
        secret.write_bytes(b"secret")

        status, headers, payload = self.request(
            "GET", f"/media/upload/../jobs/{job_id}/source.png"
        )

        self.assertEqual(status, 400)
        self.assertNotEqual(payload, b"secret")
        self.assert_security_headers(headers)

    def test_success_and_framework_error_responses_have_security_headers(self):
        for path, expected in (("/api/runtime-info", 200), ("/missing", 404)):
            with self.subTest(path=path):
                status, headers, _ = self.request("GET", path)
                self.assertEqual(status, expected)
                self.assert_security_headers(headers)
        status, headers, _ = self.request("GET", "/api/runtime-info")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")


class ProcessRouteHttpTests(LiveServerTestCase):
    def test_process_projects_all_production_context_fields(self):
        payload = {
            "upload_id": "upload-7",
            "production_id": " production-7 ",
            "scene_id": "scene-12",
            "shot_id": "shot-3",
            "shot_version_id": "shot-3-v2",
        }
        expected_context = {
            "production_id": "production-7",
            "scene_id": "scene-12",
            "shot_id": "shot-3",
            "shot_version_id": "shot-3-v2",
        }
        with (
            mock.patch.object(server, "output_scale_from_upload_payload", return_value=1.0),
            mock.patch.object(server, "process_video_to_job", return_value={"job_id": "job-7"}) as process,
        ):
            status, _, response = self.request(
                "POST", "/api/process", body=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response), {"ok": True, "job": {"job_id": "job-7"}})
        self.assertEqual(process.call_args.kwargs["production_context"], expected_context)
