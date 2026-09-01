import http.client
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import server


def build_multipart_body(boundary, parts):
    chunks = []
    for part in parts:
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        disposition = f'Content-Disposition: form-data; name="{part["name"]}"'
        if part.get("filename") is not None:
            disposition += f'; filename="{part["filename"]}"'
        chunks.append((disposition + "\r\n").encode("utf-8"))
        if part.get("content_type"):
            chunks.append(f'Content-Type: {part["content_type"]}\r\n'.encode("ascii"))
        chunks.append(b"\r\n" + part["data"] + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks)


class LiveServerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.work_root = self.root / "work"
        self.patch_stack = ExitStack()
        self.httpd = None
        self.thread = None
        self.is_serving = False
        self.addCleanup(self._cleanup)

        paths = {
            "WORK_DIR": self.work_root,
            "DEFAULT_WORK_DIR": self.work_root,
            "UPLOADS_DIR": self.work_root / "uploads",
            "JOBS_DIR": self.work_root / "jobs",
            "EXPORTS_DIR": self.work_root / "exports",
            "PREVIEWS_DIR": self.work_root / "previews",
            "LINE_CLEANER_DIR": self.work_root / "line-cleaner",
            "MAGIC_DIR": self.work_root / "magic",
            "SETTINGS_PATH": self.work_root / "settings.json",
            "LEGACY_SETTINGS_PATH": self.root / "legacy-settings.json",
        }
        for name, value in paths.items():
            self.patch_stack.enter_context(mock.patch.object(server, name, value))
        self.patch_stack.enter_context(mock.patch.object(
            server,
            "MANAGED_RUNTIME_DIRS",
            (
                server.UPLOADS_DIR, server.JOBS_DIR, server.EXPORTS_DIR,
                server.PREVIEWS_DIR, server.LINE_CLEANER_DIR, server.MAGIC_DIR,
            ),
        ))
        server.ensure_runtime_dirs()
        self.httpd = server.create_http_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.is_serving = True
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        self._close_server()
        self.patch_stack.close()
        self.temp_dir.cleanup()

    def _close_server(self):
        if self.httpd is not None:
            if self.is_serving:
                self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None
        self.is_serving = False

    def request(self, method, path, body=b"", headers=None, *, skip_host=False):
        supplied_headers = list((headers or {}).items() if hasattr(headers, "items") else headers or ())
        framing_names = {name.lower() for name, _ in supplied_headers}
        if body and not framing_names.intersection({"content-length", "transfer-encoding"}):
            supplied_headers.append(("Content-Length", str(len(body))))
        request_headers = []
        if not skip_host and not any(name.lower() == "host" for name, _ in supplied_headers):
            request_headers.append(("Host", f"127.0.0.1:{self.port}"))
        request_headers.extend(supplied_headers)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True)
            for name, value in request_headers:
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()
