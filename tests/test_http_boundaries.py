import errno
import http.client
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import server
from tests.http_test_support import LiveServerTestCase, build_multipart_body


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
        self.assertFalse(server.origin_matches_request("http://localhost:0", "localhost"))
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

    def test_large_ranges_stream_exact_bytes_in_bounded_reads(self):
        chunk_limit = 1024 * 1024
        large_size = (3 * chunk_limit) + 17
        large_bytes = bytes(index % 251 for index in range(large_size))
        upload = server.upload_dir("large-range-fixture")
        upload.mkdir(parents=True)
        large_path = upload / "large.mp4"
        large_path.write_bytes(large_bytes)
        (upload / "manifest.json").write_text(
            json.dumps({"source_path": str(large_path), "media_type": "video"}),
            encoding="utf-8",
        )
        target = large_path.resolve()
        read_sizes = []
        original_open = Path.open

        class RecordingHandle:
            def __init__(self, handle):
                self._handle = handle

            def read(self, size=-1):
                read_sizes.append(size)
                return self._handle.read(size)

            def __enter__(self):
                self._handle.__enter__()
                return self

            def __exit__(self, *exc_info):
                return self._handle.__exit__(*exc_info)

            def __getattr__(self, name):
                return getattr(self._handle, name)

        def recording_open(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            if path == target and (args[:1] == ("rb",) or kwargs.get("mode") == "rb"):
                return RecordingHandle(handle)
            return handle

        cases = (
            ("bytes=0-", 0, large_size - 1),
            (f"bytes={chunk_limit + 5}-{(2 * chunk_limit) + 40}", chunk_limit + 5, (2 * chunk_limit) + 40),
        )
        for header, start, end in cases:
            with self.subTest(range=header):
                read_sizes.clear()
                with mock.patch.object(Path, "open", recording_open):
                    status, headers, body = self.request(
                        "GET",
                        "/media/upload/large-range-fixture",
                        headers={"Range": header},
                    )

                expected = large_bytes[start:end + 1]
                self.assertEqual(status, 206)
                self.assertEqual(headers["Content-Range"], f"bytes {start}-{end}/{large_size}")
                self.assertEqual(headers["Content-Length"], str(len(expected)))
                self.assertEqual(len(body), len(expected))
                self.assertEqual(body, expected)
                self.assertTrue(read_sizes, "media handle reads were not observed")
                self.assertLessEqual(max(read_sizes), chunk_limit, read_sizes)

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


class FaviconHttpTests(LiveServerTestCase):
    """The product icon is served at /favicon.ico as a cacheable, non-ranged PNG."""

    icon_path = server.ROOT_DIR / "sprite_video_lab_icon.png"

    def test_favicon_serves_the_product_icon_png_with_day_cache(self):
        status, headers, body = self.request("GET", "/favicon.ico")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "public, max-age=86400")
        self.assertEqual(headers["Content-Length"], str(self.icon_path.stat().st_size))
        self.assertEqual(body, self.icon_path.read_bytes())

    def test_favicon_ignores_range_requests(self):
        status, headers, body = self.request("GET", "/favicon.ico", headers={"Range": "bytes=0-15"})

        self.assertEqual(status, 200)
        self.assertNotIn("Content-Range", headers)
        self.assertNotIn("Accept-Ranges", headers)
        self.assertEqual(body, self.icon_path.read_bytes())


class FileResponseFailureHttpTests(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.file_path = self.work_root / "unstable.bin"
        self.file_path.write_bytes(b"unstable-content")

    def request_file(self):
        try:
            return self.request("GET", "/work/unstable.bin")
        except (http.client.HTTPException, OSError) as exc:
            self.fail(f"GET closed without a controlled response: {exc}")

    def test_file_disappearing_during_open_returns_404_before_success_headers(self):
        original_open = Path.open
        target = self.file_path.resolve()

        def disappearing_open(path, *args, **kwargs):
            if path == target:
                raise FileNotFoundError("file disappeared")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", new=disappearing_open):
            status, headers, payload = self.request_file()

        self.assertEqual(status, 404)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertFalse(json.loads(payload)["ok"])

    def test_file_permission_failure_returns_403_before_success_headers(self):
        original_open = Path.open
        target = self.file_path.resolve()

        def unreadable_open(path, *args, **kwargs):
            if path == target:
                raise PermissionError("file is unreadable")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", new=unreadable_open):
            status, headers, payload = self.request_file()

        self.assertEqual(status, 403)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertFalse(json.loads(payload)["ok"])

    def test_file_descriptor_metadata_failure_returns_error_before_success_headers(self):
        with mock.patch.object(server.os, "fstat", side_effect=OSError("metadata unavailable")):
            status, headers, payload = self.request_file()

        self.assertEqual(status, 500)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertFalse(json.loads(payload)["ok"])

    def test_directory_path_remains_a_controlled_404(self):
        directory = self.work_root / "not-a-file"
        directory.mkdir()

        status, _, _ = self.request("GET", "/work/not-a-file")

        self.assertEqual(status, 404)

    def test_symlink_loop_returns_controlled_400(self):
        loop = self.work_root / "loop"
        try:
            loop.symlink_to(loop.name)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        try:
            status, headers, payload = self.request("GET", "/work/loop")
        except (http.client.HTTPException, OSError) as exc:
            self.fail(f"GET closed without a controlled response: {exc}")

        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertFalse(json.loads(payload)["ok"])

    def test_resolve_eloop_maps_to_400_without_remapping_other_oserrors(self):
        class FailingPath:
            def __init__(self, error_number):
                self.error_number = error_number

            def resolve(self, *, strict):
                self.assert_strict = strict
                raise OSError(self.error_number, "resolve failed")

        handler = object.__new__(server.AppHandler)
        cases = (
            (errno.ELOOP, HTTPStatus.BAD_REQUEST),
            (errno.EIO, HTTPStatus.INTERNAL_SERVER_ERROR),
        )
        for error_number, expected_status in cases:
            path = FailingPath(error_number)
            with self.subTest(error_number=error_number), self.assertRaises(server.RequestError) as caught:
                handler.serve_file(path)
            self.assertTrue(path.assert_strict)
            self.assertEqual(caught.exception.status, expected_status)


class IPv6ServerFactoryHttpTests(unittest.TestCase):
    def _require_ipv6_loopback(self):
        probe = None
        try:
            probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            probe.bind(("::1", 0))
        except OSError as exc:
            self.skipTest(f"IPv6 loopback unavailable: {exc}")
        finally:
            if probe is not None:
                probe.close()

    def test_ipv6_probe_skips_when_socket_cannot_be_created(self):
        with mock.patch.object(socket, "socket", side_effect=OSError("IPv6 unsupported")):
            with self.assertRaisesRegex(unittest.SkipTest, "IPv6 loopback unavailable"):
                self._require_ipv6_loopback()

    def test_ipv6_probe_closes_socket_after_loopback_bind_failure(self):
        probe = mock.Mock()
        probe.bind.side_effect = OSError("IPv6 loopback unavailable")

        with mock.patch.object(socket, "socket", return_value=probe):
            with self.assertRaisesRegex(unittest.SkipTest, "IPv6 loopback unavailable"):
                self._require_ipv6_loopback()

        probe.close.assert_called_once_with()

    def test_factory_serves_ipv6_wildcard_bind_over_loopback(self):
        self._require_ipv6_loopback()

        try:
            httpd = server.create_http_server("::", 0)
        except OSError as exc:
            self.fail(f"IPv6 server factory failed: {exc}")
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("::1", httpd.server_address[1], timeout=5)
        try:
            connection.putrequest("GET", "/api/runtime-info", skip_host=True)
            connection.putheader("Host", f"[::1]:{httpd.server_address[1]}")
            connection.endheaders()
            response = connection.getresponse()
            payload = response.read()

            self.assertEqual(httpd.address_family, socket.AF_INET6)
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(payload)["ok"])
        finally:
            connection.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


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


class MultipartHttpTests(LiveServerTestCase):
    boundary = "----SpriteVideoLabBoundary7MA4YWxkTrZu0gW"

    class RecordingBody(io.BytesIO):
        def __init__(self, value):
            super().__init__(value)
            self.read_sizes = []

        def read(self, size=-1):
            self.read_sizes.append(size)
            return super().read(size)

    def multipart_headers(self, boundary=None):
        return {"Content-Type": f"multipart/form-data; boundary={boundary or self.boundary}"}

    def parser_handler(self, body, *, content_length=None, content_type=None, stream=None):
        raw_headers = (
            f"Content-Type: {content_type or 'multipart/form-data; boundary=' + self.boundary}\r\n"
            f"Content-Length: {len(body) if content_length is None else content_length}\r\n"
            "\r\n"
        ).encode("latin-1")
        handler = object.__new__(server.AppHandler)
        handler.headers = http.client.parse_headers(io.BytesIO(raw_headers))
        handler.rfile = stream or io.BytesIO(body)
        handler.server = SimpleNamespace(max_upload_bytes=8 * 1024 * 1024 * 1024)
        return handler

    def test_upload_route_preserves_repeated_video_files_and_utf8_filenames(self):
        parts = [
            {
                "name": "video",
                "filename": "clip-01.mp4",
                "content_type": "video/mp4",
                "data": b"first-video",
            },
            {
                "name": "video",
                "filename": "\u7247\u6bb5-02.mov",
                "content_type": "video/quicktime",
                "data": b"second-video",
            },
        ]
        captured = {}

        def inspect_files(files):
            captured["files"] = files
            captured["positions"] = [item.file.tell() for item in files]
            captured["payloads"] = [item.file.read() for item in files]
            return {"upload_id": "upload-test"}

        with mock.patch.object(server, "register_uploaded_media", side_effect=inspect_files) as register:
            status, _, payload = self.request(
                "POST",
                "/api/upload",
                body=build_multipart_body(self.boundary, parts),
                headers=self.multipart_headers(),
            )

        self.assertEqual(status, 200, payload)
        self.assertEqual(json.loads(payload)["upload"]["upload_id"], "upload-test")
        register.assert_called_once()
        self.assertEqual([item.filename for item in captured["files"]], ["clip-01.mp4", "\u7247\u6bb5-02.mov"])
        self.assertEqual([item.type for item in captured["files"]], ["video/mp4", "video/quicktime"])
        self.assertEqual(captured["positions"], [0, 0])
        self.assertEqual(captured["payloads"], [b"first-video", b"second-video"])
        self.assertTrue(all(item.file.closed for item in captured["files"]))

    def test_animation_route_preserves_repeated_frames_and_latin1_filename_fallback(self):
        latin1_filename = "caf\u00e9-02.png"
        parts = [
            {
                "name": "frames",
                "filename": "frame-01.png",
                "content_type": "image/png",
                "data": b"first-frame",
            },
            {
                "name": "frames",
                "filename": latin1_filename,
                "content_type": "image/png",
                "data": b"second-frame",
            },
        ]
        body = build_multipart_body(self.boundary, parts).replace(
            latin1_filename.encode("utf-8"),
            latin1_filename.encode("latin-1"),
            1,
        )
        captured = {}

        def inspect_files(files):
            captured["files"] = files
            captured["positions"] = [item.file.tell() for item in files]
            captured["payloads"] = [item.file.read() for item in files]
            return {"job_id": "animation-test"}

        with mock.patch.object(server, "import_animation_frames_to_job", side_effect=inspect_files):
            status, _, payload = self.request(
                "POST",
                "/api/import-animation",
                body=body,
                headers=self.multipart_headers(),
            )

        self.assertEqual(status, 200, payload)
        self.assertEqual(json.loads(payload)["job"]["job_id"], "animation-test")
        self.assertEqual([item.filename for item in captured["files"]], ["frame-01.png", latin1_filename])
        self.assertEqual(captured["positions"], [0, 0])
        self.assertEqual(captured["payloads"], [b"first-frame", b"second-frame"])
        self.assertTrue(all(item.file.closed for item in captured["files"]))

    def test_line_cleaner_route_uses_first_repeated_fields_and_existing_clamps(self):
        parts = [
            {
                "name": "frames",
                "filename": "frame.png",
                "content_type": "image/png",
                "data": b"frame-data",
            },
            {"name": "method", "data": b"classic"},
            {"name": "method", "data": b"realesrgan_anime"},
            {"name": "output_scale", "data": b"1.75"},
            {"name": "alpha_cutoff", "data": b"-4"},
            {"name": "sharpen_percent", "data": b"999"},
            {"name": "color_count", "data": b"1"},
        ]
        captured = {}

        def inspect_files(files, **kwargs):
            captured["files"] = files
            captured["position"] = files[0].file.tell()
            captured["payload"] = files[0].file.read()
            captured["kwargs"] = kwargs
            return {"run_id": "cleaner-test"}

        with mock.patch.object(server, "process_line_cleaner_frames", side_effect=inspect_files):
            status, _, payload = self.request(
                "POST",
                "/api/line-cleaner-process",
                body=build_multipart_body(self.boundary, parts),
                headers=self.multipart_headers(),
            )

        self.assertEqual(status, 200, payload)
        self.assertEqual(json.loads(payload)["result"]["run_id"], "cleaner-test")
        self.assertEqual(captured["position"], 0)
        self.assertEqual(captured["payload"], b"frame-data")
        self.assertEqual(
            captured["kwargs"],
            {
                "method": "classic",
                "scale": 1.75,
                "alpha_cutoff": 0,
                "sharpen_percent": 300,
                "color_count": 2,
            },
        )
        self.assertTrue(captured["files"][0].file.closed)

    def test_parser_reads_exact_length_in_mib_chunks_and_spools_large_file(self):
        self.assertTrue(
            hasattr(server.AppHandler, "read_multipart_form"),
            "shared multipart parser adapter is missing",
        )
        large_payload = b"x" * ((1024 * 1024) + 1)
        body = build_multipart_body(
            self.boundary,
            [
                {"name": "mode", "data": b"first"},
                {"name": "mode", "data": b"second"},
                {
                    "name": "video",
                    "filename": "large.bin",
                    "content_type": "application/octet-stream",
                    "data": large_payload,
                },
            ],
        )
        stream = self.RecordingBody(body + b"unread-trailer")
        handler = self.parser_handler(body, stream=stream)

        with handler.read_multipart_form() as form:
            upload = form.files("video")[0]
            resource = upload.file
            self.assertEqual(form.getfirst("mode"), "first")
            self.assertEqual(upload.filename, "large.bin")
            self.assertEqual(upload.type, "application/octet-stream")
            self.assertEqual(resource.tell(), 0)
            self.assertTrue(resource._rolled, "spool did not roll to disk after exceeding 1 MiB")
            self.assertNotIsInstance(resource._file, io.BytesIO)
            self.assertEqual(resource.read(), large_payload)
            self.assertEqual(stream.read_sizes, [1024 * 1024, len(body) - (1024 * 1024)])

        self.assertTrue(resource.closed)
        self.assertEqual(stream.read(), b"unread-trailer")

    def test_incomplete_parser_closes_files_exposed_by_completed_parts(self):
        self.assertTrue(
            hasattr(server.AppHandler, "read_multipart_form") and hasattr(server, "UploadedFormFile"),
            "context-managed multipart adapter is missing",
        )
        body = build_multipart_body(
            self.boundary,
            [
                {
                    "name": "video",
                    "filename": "complete.mp4",
                    "content_type": "video/mp4",
                    "data": b"complete",
                },
                {
                    "name": "video",
                    "filename": "incomplete.mp4",
                    "content_type": "video/mp4",
                    "data": b"incomplete",
                },
            ],
        )
        closing_boundary = f"--{self.boundary}--\r\n".encode("ascii")
        incomplete_body = body[: -len(closing_boundary)]
        handler = self.parser_handler(incomplete_body)
        uploaded_form_file = server.UploadedFormFile
        exposed_resources = []

        def retain_resource(*args, **kwargs):
            uploaded = uploaded_form_file(*args, **kwargs)
            exposed_resources.append(uploaded.file)
            return uploaded

        with mock.patch.object(server, "UploadedFormFile", side_effect=retain_resource):
            with self.assertRaises(server.RequestError) as caught:
                handler.read_multipart_form()

        self.assertEqual(caught.exception.status, HTTPStatus.BAD_REQUEST)
        self.assertTrue(exposed_resources)
        self.assertTrue(all(resource.closed for resource in exposed_resources))

    def test_incomplete_current_file_part_closes_its_spooled_resource(self):
        payload = b"x" * ((1024 * 1024) + 1)
        body = build_multipart_body(
            self.boundary,
            [
                {
                    "name": "video",
                    "filename": "incomplete.bin",
                    "content_type": "application/octet-stream",
                    "data": payload,
                }
            ],
        )
        closing_boundary = f"--{self.boundary}--\r\n".encode("ascii")
        incomplete_body = body[: -len(closing_boundary)]
        handler = self.parser_handler(incomplete_body)
        opened_resources = []

        def open_spool():
            resource = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
            opened_resources.append(resource)
            return resource

        handler._open_multipart_spool = open_spool
        with self.assertRaises(server.RequestError) as caught:
            handler.read_multipart_form()

        self.assertEqual(caught.exception.status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(len(opened_resources), 1, "the current file resource was not request-owned")
        self.assertTrue(opened_resources[0].closed)

    def test_parser_accepts_exact_aggregate_non_file_field_limit(self):
        field_limit = 1024 * 1024
        body = build_multipart_body(
            self.boundary,
            [
                {"name": "first", "data": b"a" * (field_limit // 2)},
                {"name": "second", "data": b"b" * (field_limit // 2)},
            ],
        )

        with self.parser_handler(body).read_multipart_form() as form:
            self.assertEqual(len(form.getfirst("first")), field_limit // 2)
            self.assertEqual(len(form.getfirst("second")), field_limit // 2)

    def test_multipart_route_rejects_aggregate_field_data_above_limit(self):
        field_limit = 1024 * 1024
        body = build_multipart_body(
            self.boundary,
            [
                {"name": "first", "data": b"a" * (field_limit // 2)},
                {"name": "second", "data": b"b" * ((field_limit // 2) + 1)},
            ],
        )

        with mock.patch.object(
            server,
            "register_uploaded_media",
            return_value={"upload_id": "must-not-run"},
        ) as register:
            status, response_headers, payload = self.request(
                "POST",
                "/api/upload",
                body=body,
                headers=self.multipart_headers(),
            )

        self.assertEqual(status, 413, payload)
        self.assertEqual(response_headers.get("Content-Type"), "application/json; charset=utf-8")
        self.assertFalse(json.loads(payload)["ok"])
        register.assert_not_called()

    def test_multipart_routes_reject_invalid_framing_with_structured_statuses(self):
        self.assertTrue(
            hasattr(server.AppHandler, "read_multipart_form"),
            "shared multipart parser adapter is missing",
        )
        complete = build_multipart_body(
            self.boundary,
            [{"name": "video", "filename": "clip.mp4", "data": b"video"}],
        )
        incomplete = complete[:-4]
        cases = (
            (b"not-multipart", {"Content-Type": "application/octet-stream"}, 415),
            (b"not-multipart", {"Content-Type": "multipart/form-data"}, 400),
            (b"", self.multipart_headers(), 411),
            (b"", {**self.multipart_headers(), "Content-Length": "-1"}, 400),
            (b"", {**self.multipart_headers(), "Content-Length": "12x"}, 400),
            (incomplete, self.multipart_headers(), 400),
        )

        for body, headers, expected_status in cases:
            with self.subTest(headers=headers, body=body[:20]):
                status, response_headers, payload = self.request(
                    "POST",
                    "/api/upload",
                    body=body,
                    headers=headers,
                )
                self.assertEqual(status, expected_status, payload)
                self.assertEqual(response_headers.get("Content-Type"), "application/json; charset=utf-8")
                self.assertFalse(json.loads(payload)["ok"])

    def test_multipart_route_rejects_advertised_body_over_configured_limit(self):
        body = build_multipart_body(
            self.boundary,
            [{"name": "video", "filename": "clip.mp4", "data": b"video"}],
        )
        self.httpd.max_upload_bytes = len(body) - 1

        with mock.patch.object(
            server,
            "register_uploaded_media",
            return_value={"upload_id": "must-not-run"},
        ) as register:
            status, _, payload = self.request(
                "POST",
                "/api/upload",
                body=body,
                headers=self.multipart_headers(),
            )

        self.assertEqual(status, 413, payload)
        self.assertFalse(json.loads(payload)["ok"])
        register.assert_not_called()

    def test_parser_accepts_multipart_media_type_case_insensitively(self):
        body = build_multipart_body(self.boundary, [{"name": "mode", "data": b"first"}])
        handler = self.parser_handler(body, content_type=f"Multipart/Form-Data; boundary={self.boundary}")

        with handler.read_multipart_form() as form:
            self.assertEqual(form.getfirst("mode"), "first")

    def test_parsed_form_close_closes_every_resource_even_when_one_raises(self):
        first = mock.Mock()
        failing = mock.Mock()
        failing.close.side_effect = OSError("close failed")
        last = mock.Mock()

        server.ParsedMultipartForm({}, {}, [first, failing, last]).close()

        first.close.assert_called_once()
        failing.close.assert_called_once()
        last.close.assert_called_once()

    def test_multipart_route_accepts_exactly_4096_parts(self):
        body = build_multipart_body(
            self.boundary,
            [
                *({"name": "field", "data": b"x"} for _ in range(4095)),
                {"name": "video", "filename": "clip.mp4", "data": b"video"},
            ],
        )

        with mock.patch.object(
            server,
            "register_uploaded_media",
            return_value={"upload_id": "upload-4096"},
        ) as register:
            status, _, payload = self.request(
                "POST",
                "/api/upload",
                body=body,
                headers=self.multipart_headers(),
            )

        self.assertEqual(status, 200, payload)
        self.assertEqual(json.loads(payload)["upload"]["upload_id"], "upload-4096")
        register.assert_called_once()

    def test_multipart_route_rejects_more_than_4096_parts(self):
        body = build_multipart_body(
            self.boundary,
            [
                *({"name": "field", "data": b"x"} for _ in range(4096)),
                {"name": "video", "filename": "clip.mp4", "data": b"video"},
            ],
        )

        with mock.patch.object(
            server,
            "register_uploaded_media",
            return_value={"upload_id": "must-not-run"},
        ) as register:
            status, _, payload = self.request(
                "POST",
                "/api/upload",
                body=body,
                headers=self.multipart_headers(),
            )

        self.assertEqual(status, 400, payload)
        self.assertFalse(json.loads(payload)["ok"])
        register.assert_not_called()

    def test_multipart_route_enforces_per_part_header_count_and_size_limits(self):
        prefix = f"--{self.boundary}\r\n".encode("ascii")
        suffix = f"\r\nvalue\r\n--{self.boundary}--\r\n".encode("ascii")
        disposition = b'Content-Disposition: form-data; name="video"; filename="clip.mp4"\r\n'
        too_many_headers = prefix + disposition + b"".join(
            f"X-Test-{index}: value\r\n".encode("ascii") for index in range(8)
        ) + suffix
        oversized_header = prefix + disposition + b"X-Large: " + (b"x" * 4224) + b"\r\n" + suffix

        for body in (too_many_headers, oversized_header):
            with self.subTest(body_length=len(body)), mock.patch.object(
                server,
                "register_uploaded_media",
                return_value={"upload_id": "must-not-run"},
            ) as register:
                status, _, payload = self.request(
                    "POST",
                    "/api/upload",
                    body=body,
                    headers=self.multipart_headers(),
                )
                self.assertEqual(status, 400, payload)
                self.assertFalse(json.loads(payload)["ok"])
                register.assert_not_called()

    def test_default_multipart_body_limit_is_eight_gibibytes(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server.configured_max_upload_bytes(), 8 * 1024 * 1024 * 1024)


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
