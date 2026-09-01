# Sprite Video Lab Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair every confirmed functional, compatibility, security, packaging, and responsive-UI defect while preserving Sprite Video Lab's existing processing workflows.

**Architecture:** Keep the current standard-library server and static frontend, but add small pure boundary helpers inside `server.py`, a context-managed multipart adapter, fail-fast Windows packaging checks, and a small workflow navigation controller. Add focused test modules around the existing large processing test suite so each subsystem has an explicit contract and can be reviewed independently.

**Tech Stack:** Python 3.10+, `http.server`, Pillow, `python-multipart`, `unittest`/pytest-compatible tests, vanilla HTML/CSS/JavaScript, PowerShell, Windows batch, Chrome DevTools Protocol.

**Spec:** `docs/superpowers/specs/2026-09-02-sprite-video-lab-reliability-security-repair-design.md`

## Global Constraints

- Support Python 3.10 through 3.14; remove every dependency on `cgi`.
- Require `python-multipart>=0.0.32,<0.1` and import it as `python_multipart`.
- Default browser-upload limit: 8 GiB via `SPRITE_VIDEO_LAB_MAX_UPLOAD_BYTES`.
- JSON limit: 1 MiB; multipart part limit: 4096; memory spool threshold: 1 MiB.
- Runtime IDs are one ASCII path component, at most 128 characters.
- Real-ESRGAN archive SHA-256: `abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d`.
- Real-ESRGAN archive download ceiling: 64 MiB.
- Never delete or open a path outside the resolved managed work or configured export roots.
- Preserve response JSON shapes, processing algorithms, AI opt-in behavior, export formats, and external output roots.
- Mobile interactive targets are at least 44 by 44 CSS pixels at 760 px and below.
- Windows execution claims require a Windows runner; macOS static checks are reported separately.
- Do not commit runtime work directories, virtual environments, screenshots, caches, or temporary downloads.

## File Map

- Modify `server.py`: request/body/range/identifier helpers, multipart adapter, cleanup, verified download, favicon route, server configuration.
- Modify `requirements.txt`: add the secured multipart parser dependency.
- Modify `tests/test_ai_matte_sizing.py`: optional-module isolation and `production_context` regression.
- Create `tests/http_test_support.py`: isolated live HTTP server fixture and multipart-body builder.
- Create `tests/test_http_boundaries.py`: Range, Host, Origin, JSON, traversal, multipart, security-header, and favicon tests.
- Create `tests/test_runtime_safety.py`: identifier, cleanup, open-path, and Real-ESRGAN integrity tests.
- Create `tests/test_windows_contracts.py`: builder/launcher layout and readiness contracts.
- Create `tests/test_ui_contracts.py`: static accessibility/responsive/navigation contracts.
- Create `wait_for_server.ps1`: shared bounded startup readiness probe.
- Modify `build_portable_bundle.ps1`: content-copy semantics and pre-archive validation.
- Modify `start_sprite_video_lab.bat`: shared readiness probe.
- Modify `start_sprite_video_lab_portable.bat`: shared readiness probe.
- Modify `app/index.html`: favicon, upload-input class, workflow ARIA state.
- Modify `app/line-cleaner-experiment.html`: favicon.
- Modify `app/styles.css`: hidden input, fixed heading sizes, compact mobile header, mobile targets.
- Modify `app/app.js`: workflow active-state controller.
- Modify `README.md`, `AGENT_INSTALL.md`, and `CHANGELOG.md`: runtime/security configuration and repair notes.

## Requirement Coverage

| Requirement | Implementation tasks | Direct evidence |
| --- | --- | --- |
| R1 Baseline tests | 1, 2 | optional-module tests, cleanup tests, full Python 3.13+ suite |
| R2 Production context | 1, 4, 9 | persisted-manifest test, live route projection test, final API smoke |
| R3 Multipart compatibility | 4, 5 | framing/JSON tests and real multipart tests on Python 3.13+ |
| R4 Range semantics | 3, 9 | pure parser cases and exact live HTTP bytes/headers |
| R5 Local request boundary | 4, 9 | Host, Origin, headers, JSON limits, and live hostile-request checks |
| R6 Identifier/path safety | 2, 4, 9 | pure ID/path tests plus traversal and open-path HTTP tests |
| R7 Runtime cleanup | 2, 9 | every generated name, path-alias, unrelated-content, and escaping-link tests |
| R8 Real-ESRGAN integrity | 6 | digest, size, traversal, and target-isolation tests |
| R9 Portable layout | 7 | builder/launcher contract tests and explicit Windows caveat |
| R10 Launcher readiness | 7 | bounded-probe, success/open, and failure-exit script contracts |
| R11 Responsive UI/state | 8, 9 | static contracts plus desktop/mobile browser measurements and screenshots |

---

### Task 1: Stabilize the Existing Suite and Lock Production Context

**Files:**
- Modify: `tests/test_ai_matte_sizing.py:1-8,628-660,984-1060`

**Interfaces:**
- Consumes: existing `server.download_birefnet_snapshot`, `server.download_corridorkey_checkpoint`, and `server.process_video_to_job`.
- Produces: deterministic optional-dependency tests and a persisted `production_context` contract used by the Task 4 route test and final API smoke test.

- [ ] **Step 1: Add an isolated fake Hugging Face module helper**

Add `sys` and `types` imports and this helper above the test class:

```python
import sys
import types


def fake_huggingface_hub(**functions):
    module = types.ModuleType("huggingface_hub")
    for name, function in functions.items():
        setattr(module, name, function)
    return mock.patch.dict(sys.modules, {"huggingface_hub": module})
```

- [ ] **Step 2: Replace patches that require an installed optional package**

Use explicit mocks supplied through the fake module:

```python
snapshot_download = mock.Mock()
with (
    fake_huggingface_hub(snapshot_download=snapshot_download),
    mock.patch.object(server, "require_ai_runtime_for_components"),
):
    server.download_birefnet_snapshot()
```

Apply the same pattern with `hf_hub_download = mock.Mock()` for the CorridorKey test.

- [ ] **Step 3: Run the two tests and confirm they pass without the dependency**

Run:

```bash
python3 -m unittest \
  tests.test_ai_matte_sizing.AiMatteSizingTests.test_birefnet_download_limits_snapshot_to_required_hr_files \
  tests.test_ai_matte_sizing.AiMatteSizingTests.test_corridorkey_download_fetches_pinned_selected_checkpoint
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 4: Extend the existing batch-processing test with production context**

Pass a concrete context to `process_video_to_job`:

```python
production_context = {
    "production_id": "production-7",
    "scene_id": "scene-12",
    "shot_id": "shot-3",
    "shot_version_id": "shot-3-v2",
}
result = server.process_video_to_job(
    # keep the existing arguments
    production_context=production_context,
)
self.assertEqual(result["production_context"], production_context)
self.assertEqual(
    json.loads(server.job_manifest_path(result["job_id"]).read_text(encoding="utf-8"))["production_context"],
    production_context,
)
```

- [ ] **Step 5: Run the characterization test**

Run:

```bash
python3 -m unittest tests.test_ai_matte_sizing.AiMatteSizingTests.test_batch_esr_smoothing_happens_before_matte
```

Expected: PASS, proving the restored merge behavior before other server edits.

- [ ] **Step 6: Commit the test stabilization**

```bash
git add tests/test_ai_matte_sizing.py
git commit -m "test: stabilize AI mocks and production context"
```

### Task 2: Enforce Runtime Identifier and Filesystem Boundaries

**Files:**
- Create: `tests/test_runtime_safety.py`
- Modify: `server.py:53-55,218-262,1390-1405,2856-2877,3230-3242,4046-4048,5103-5111,5751-5758`

**Interfaces:**
- Produces: `validate_runtime_id(value: object, label: str) -> str` and `is_openable_directory(path: Path) -> bool`.
- Consumes: existing `is_within_root`, `configured_exports_dir`, runtime directory constants, and manifest helpers.

- [ ] **Step 1: Write failing identifier tests**

Create `tests/test_runtime_safety.py` with:

```python
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class RuntimeSafetyTests(unittest.TestCase):
    def test_runtime_id_accepts_generated_and_test_ids(self):
        self.assertEqual(server.validate_runtime_id("20260902-120000-abcd", "upload"), "20260902-120000-abcd")
        self.assertEqual(server.validate_runtime_id("export-formats", "job"), "export-formats")

    def test_runtime_id_rejects_path_syntax(self):
        for value in ("", ".", "..", "../jobs/x", r"..\jobs\x", "%2e%2e", "a/b", "a\x00b", "a" * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.validate_runtime_id(value, "upload")
```

- [ ] **Step 2: Write failing cleanup and open-directory tests**

Add tests that create an external export root containing all current names:

```python
generated_names = [
    "20260902-120000-abcd-export",
    "20260902-120001-abcd-magic-half-frames",
    *[
        f"20260902-120002-abcd-scale-{variant}-{format_name}"
        for variant in ("full", "half", "quarter", "eighth")
        for format_name in ("frames", "sprite_sheet", "mov", "gif")
    ],
]
```

Assert all generated directories are removed, while `downloaded-copy.mov`,
`manual-output`, and the settings file remain. Patch `configured_exports_dir`
to return a path whose resolved spelling differs from its lexical spelling when
the platform provides one. Create a generated-looking symlink whose target is
outside the export root; assert cleanup fails closed and preserves the outside
directory (skip only when the platform cannot create symlinks). Add
`is_openable_directory` assertions for the work root, external export root, an
ordinary external directory, and a file.

- [ ] **Step 3: Run the new module and observe missing behavior**

Run:

```bash
python3 -m unittest tests.test_runtime_safety.RuntimeSafetyTests -v
```

Expected: FAIL because `validate_runtime_id` and `is_openable_directory` do not
exist and current scale directories are not matched.

- [ ] **Step 4: Implement strict runtime IDs and use them before joins**

Add near the runtime constants:

```python
RUNTIME_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_runtime_id(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if normalized in {"", ".", ".."} or not RUNTIME_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid {label} id")
    return normalized
```

Call it in `upload_dir`, `job_dir`, `preview_dir`, `line_cleaner_dir`, and
`load_magic_manifest` before constructing any path. Keep arbitrary readable test
IDs valid; do not require only timestamp-generated IDs.

- [ ] **Step 5: Repair cleanup matching and resolved-root comparison**

Replace the generated directory pattern with:

```python
GENERATED_EXPORT_DIR_PATTERN = re.compile(
    r"^\d{8}-\d{6}-[0-9a-f]{4}-(?:"
    r"export|"
    r"magic-(?:half|quarter|eighth)-frames|"
    r"scale-(?:full|half|quarter|eighth)-(?:frames|sprite_sheet|mov|gif)"
    r")$"
)
```

Resolve `export_root` once before comparisons:

```python
export_root = configured_exports_dir().resolve()
if export_root != EXPORTS_DIR.resolve() and export_root.exists():
    # retain the existing fail-closed child checks
```

- [ ] **Step 6: Implement the directory-open allowlist and route guard**

Add:

```python
def is_openable_directory(path: Path) -> bool:
    target = path.expanduser().resolve()
    roots = (WORK_DIR.resolve(), configured_exports_dir().resolve())
    return target.is_dir() and any(is_within_root(target, root) for root in roots)
```

In `/api/open-path`, reject a target for which this returns false before calling
`open_path_in_file_browser`.

- [ ] **Step 7: Run focused and existing cleanup tests**

Run:

```bash
python3 -m unittest \
  tests.test_runtime_safety.RuntimeSafetyTests \
  tests.test_ai_matte_sizing.AiMatteSizingTests.test_clear_runtime_files_requires_confirmation_and_stays_inside_managed_dirs -v
```

Expected: PASS.

- [ ] **Step 8: Commit filesystem safety**

```bash
git add server.py tests/test_runtime_safety.py
git commit -m "fix: enforce runtime filesystem boundaries"
```

### Task 3: Correct Single-Range Media Delivery

**Files:**
- Create: `tests/http_test_support.py`
- Create: `tests/test_http_boundaries.py`
- Modify: `server.py:20-22,5795-5842`

**Interfaces:**
- Produces: `ByteRange(start: int, end: int)`, `UnsatisfiableRange`, and `parse_single_byte_range(header: str | None, file_size: int) -> ByteRange | None`.
- Produces test helper: `LiveServerTestCase.request(method, path, body=b"", headers=None)`.

- [ ] **Step 1: Write pure failing Range tests**

In `tests/test_http_boundaries.py`, assert:

```python
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
```

Also assert a 10,000-digit start is unsatisfiable without leaking Python's
integer-conversion exception, while a 10,000-digit suffix covers the full file.

- [ ] **Step 2: Run the pure tests and verify the interface is absent**

Run:

```bash
python3 -m unittest tests.test_http_boundaries.ByteRangeTests -v
```

Expected: FAIL with missing `parse_single_byte_range`.

- [ ] **Step 3: Implement the pure parser**

Add:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int


class UnsatisfiableRange(ValueError):
    pass


SINGLE_BYTE_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


def _decimal_above(value: str, ceiling: int) -> int:
    normalized = value.lstrip("0") or "0"
    ceiling_text = str(ceiling)
    if len(normalized) > len(ceiling_text):
        return ceiling + 1
    return int(normalized)


def parse_single_byte_range(header: str | None, file_size: int) -> ByteRange | None:
    if not header or "," in header:
        return None
    match = SINGLE_BYTE_RANGE_PATTERN.fullmatch(header)
    if not match:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if file_size <= 0:
        raise UnsatisfiableRange(header)
    if not first:
        suffix = _decimal_above(last, file_size)
        if suffix == 0:
            raise UnsatisfiableRange(header)
        return ByteRange(max(0, file_size - suffix), file_size - 1)
    start = _decimal_above(first, file_size)
    end = file_size - 1 if not last else min(_decimal_above(last, file_size), file_size - 1)
    if start >= file_size or start > end:
        raise UnsatisfiableRange(header)
    return ByteRange(start, end)
```

- [ ] **Step 4: Add a reusable isolated HTTP fixture**

Create `tests/http_test_support.py` with a `LiveServerTestCase` that uses an
`ExitStack` so every server global is restored:

```python
class LiveServerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.work_root = self.root / "work"
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
        self.patch_stack = ExitStack()
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
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            server.AppHandler,
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.patch_stack.close()
        self.temp_dir.cleanup()
```

Provide `request` using `http.client.HTTPConnection`, always defaulting Host to
`127.0.0.1:<port>`, and restore every patched global in `tearDown`.

- [ ] **Step 5: Write live failing Range response tests**

Create a known 100-byte media file and upload manifest, then verify exact bytes,
`Content-Range`, and `Content-Length` for closed/open/suffix requests. Verify a
huge start returns 416 with `Content-Range: bytes */100` and zero response bytes.
Verify malformed and multiple requests return 200 with all 100 bytes.

- [ ] **Step 6: Route `serve_file` through the parser**

Replace direct integer parsing with `parse_single_byte_range`. For
`UnsatisfiableRange`, send an explicit empty response:

```python
self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
self.send_header("Content-Range", f"bytes */{file_size}")
self.send_header("Content-Length", "0")
self.end_headers()
return
```

For `ByteRange`, retain the current 206 stream logic using its inclusive bounds.
For `None`, retain the full 200 response.

- [ ] **Step 7: Run pure and live Range tests**

Run:

```bash
python3 -m unittest \
  tests.test_http_boundaries.ByteRangeTests \
  tests.test_http_boundaries.MediaRangeHttpTests -v
```

Expected: PASS with no server traceback.

- [ ] **Step 8: Commit Range correctness**

```bash
git add server.py tests/http_test_support.py tests/test_http_boundaries.py
git commit -m "fix: implement robust media byte ranges"
```

### Task 4: Harden Host, Origin, JSON, and Error Responses

**Files:**
- Modify: `server.py:13-22,61-68,378-387,5410-5773,5845-5855`
- Modify: `tests/http_test_support.py`
- Modify: `tests/test_http_boundaries.py`
- Modify: `README.md:216-227`

**Interfaces:**
- Produces: `RequestError(status: HTTPStatus, message: str)`, `parse_content_length`, `allowed_request_hosts`, `request_host_allowed`, and `origin_matches_request`.
- Produces: `SpriteVideoLabHTTPServer` with `allowed_hosts: frozenset[str]` and `create_http_server(host, port)`.
- Consumes: `LiveServerTestCase` from Task 3 and runtime/path helpers from Task 2.

- [ ] **Step 1: Write failing Host and Origin integration tests**

Add tests that send:

```python
status, _, payload = self.request("GET", "/api/runtime-info", headers={"Host": "attacker.invalid:8894"})
self.assertEqual(status, 421)

status, _, payload = self.request(
    "POST", "/api/realesrgan-status", body=b"{}",
    headers={
        "Host": f"127.0.0.1:{self.port}",
        "Origin": "http://attacker.invalid:8894",
        "Content-Type": "application/json",
    },
)
self.assertEqual(status, 403)
```

Also prove missing/malformed Host values return 421, same-origin POST succeeds,
and a local CLI POST without Origin succeeds. Add pure helper cases for IPv4,
bracketed IPv6, trailing dots, explicit ports, a specific bind address, wildcard
bind behavior, and `SPRITE_VIDEO_LAB_ALLOWED_HOSTS`. Extend the HTTP helper with
a `skip_host`/raw-header path so a genuinely absent Host can be sent without
`http.client` adding one. Add a `ProcessRouteHttpTests` case that patches
`output_scale_from_upload_payload` and `process_video_to_job`, submits all four
production fields to `/api/process`, and asserts the mock receives this exact
non-empty dictionary:

```python
{
    "production_id": "production-7",
    "scene_id": "scene-12",
    "shot_id": "shot-3",
    "shot_version_id": "shot-3-v2",
}
```

- [ ] **Step 2: Write failing JSON and common-header tests**

Cover malformed/negative `Content-Length`, non-JSON media type, invalid UTF-8,
array top-level JSON, and a body over 1 MiB. An absent JSON body is treated as
`{}`; multipart missing-length behavior is tested as HTTP 411 in Task 5. Assert
400, 415, and 413 as specified. Exercise `/api/open-path` with a managed
directory, an external directory, and a file, asserting only the managed
directory reaches the patched OS opener. Send
`/media/upload/../jobs/<job-id>/source.png` without path normalization and assert
HTTP 400 rather than media disclosure. Assert all successful and error responses
contain `nosniff`, `no-referrer`, same-origin CORP, and the exact CSP. Assert JSON
responses contain `Cache-Control: no-store`.

- [ ] **Step 3: Run the new boundary cases and confirm current exposure**

Run:

```bash
python3 -m unittest tests.test_http_boundaries.RequestBoundaryHttpTests -v
```

Expected: FAIL because hostile Host/Origin requests currently return 200 and
oversized/invalid bodies do not receive the specified status codes.

- [ ] **Step 4: Implement request error and framing helpers**

Add:

```python
class RequestError(ValueError):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def parse_content_length(value: str | None, *, required: bool, maximum: int) -> int:
    normalized = "" if value is None else value.strip()
    if not normalized:
        if required:
            raise RequestError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        return 0
    if not normalized.isascii() or not normalized.isdecimal():
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
    significant = normalized.lstrip("0") or "0"
    if len(significant) > len(str(maximum)):
        raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
    length = int(significant)
    if length > maximum:
        raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
    return length
```

Define `MAX_JSON_BODY_BYTES = 1024 * 1024` and parse the upload limit from
`SPRITE_VIDEO_LAB_MAX_UPLOAD_BYTES`, rejecting invalid environment values at
startup with a clear `ValueError`.

- [ ] **Step 5: Implement canonical Host and Origin helpers**

Use `ipaddress.ip_address` and `urlsplit` to normalize bracketed IPv6, optional
ports, trailing hostname dots, and malformed authorities. `allowed_request_hosts`
always includes the three loopback names, includes a specific bind host, and
merges comma-separated `SPRITE_VIDEO_LAB_ALLOWED_HOSTS`; wildcard bind values do
not add a wildcard permission.

Attach the result to `SpriteVideoLabHTTPServer.allowed_hosts`. At the start of
every `do_GET` and `do_POST`, reject missing, malformed, or unapproved Host with
421. For POST, compare any present Origin's scheme/host/port tuple to Host and
reject mismatch or `null` with 403.

Update `LiveServerTestCase` to construct the server through
`server.create_http_server("127.0.0.1", 0)` so integration tests exercise the
same allowlist initialization as production. Replace the direct
`ThreadingHTTPServer` construction in `run_server` with this factory as well.

- [ ] **Step 6: Add common security headers once**

Override `end_headers`:

```python
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; connect-src 'self'; "
    "img-src 'self' blob: data:; media-src 'self' blob:; "
    "script-src 'self'; style-src 'self' 'unsafe-inline'"
)


def end_headers(self) -> None:
    self.send_header("X-Content-Type-Options", "nosniff")
    self.send_header("Referrer-Policy", "no-referrer")
    self.send_header("Cross-Origin-Resource-Policy", "same-origin")
    self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    super().end_headers()
```

Add `Cache-Control: no-store` in `send_json` unless already provided.

- [ ] **Step 7: Make JSON reading bounded and typed**

Require `application/json`, read exactly the validated length, decode UTF-8,
parse JSON, and require `dict`:

```python
def read_json_body(self) -> dict:
    media_type = self.headers.get_content_type()
    if media_type != "application/json":
        raise RequestError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "application/json is required")
    length = parse_content_length(self.headers.get("Content-Length"), required=False, maximum=MAX_JSON_BODY_BYTES)
    raw = self.rfile.read(length) if length else b"{}"
    if len(raw) != length:
        raise RequestError(HTTPStatus.BAD_REQUEST, "incomplete request body")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RequestError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    return payload
```

Catch `RequestError` before the generic exception in POST and return its status.
Wrap GET route work so invalid IDs and missing manifests return a response rather
than closing the connection.

- [ ] **Step 8: Update configuration documentation**

Add `SPRITE_VIDEO_LAB_ALLOWED_HOSTS` and
`SPRITE_VIDEO_LAB_MAX_UPLOAD_BYTES` to the README environment table. State that
wildcard binding needs an explicit allowed host and that default loopback use
needs no configuration.

- [ ] **Step 9: Run all HTTP boundary tests**

Run:

```bash
python3 -m unittest tests.test_http_boundaries -v
```

Expected: PASS; hostile Host/Origin requests no longer disclose or mutate state.

- [ ] **Step 10: Commit request-boundary security**

```bash
git add server.py tests/http_test_support.py tests/test_http_boundaries.py README.md
git commit -m "fix: harden local HTTP request boundaries"
```

### Task 5: Replace `cgi.FieldStorage` with Bounded Multipart Parsing

**Files:**
- Modify: `requirements.txt`
- Modify: `server.py:1-25,3933-3940,5465-5555`
- Modify: `tests/http_test_support.py`
- Modify: `tests/test_http_boundaries.py`
- Modify: `AGENT_INSTALL.md:19-35`

**Interfaces:**
- Produces: `UploadedFormFile(filename: str, type: str, file: BinaryIO)` and context-managed `ParsedMultipartForm` with `files(key)` and `getfirst(key, default)`.
- Produces: `AppHandler.read_multipart_form() -> ParsedMultipartForm`.
- Consumes: `parse_content_length`, `RequestError`, and the 8 GiB upload limit from Task 4.

- [ ] **Step 1: Add real multipart-body construction to the HTTP test support**

Add:

```python
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
```

- [ ] **Step 2: Write failing parser and route tests**

Cover repeated `video` files, repeated `frames`, UTF-8 filenames, Latin-1
fallback filenames, line-cleaner text fields, a 1 MiB + 1 byte file that is
spooled off-memory, 4097 parts, a non-multipart media type, missing boundary,
missing length, incomplete final boundary, and an advertised body over the
configured limit. For successful route tests, patch the processing function and
inspect the adapter's filename, type, seek position, and field values. Retain a
reference to each exposed file object and assert it is closed after both normal
context exit and parser failure.

- [ ] **Step 3: Confirm Python 3.13 currently cannot import the server**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/svl-plan-pycache python3.13 -c "import server"
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cgi'`.

- [ ] **Step 4: Add the secured base dependency and remove `cgi`**

Change `requirements.txt` to:

```text
Pillow>=10.0.0
python-multipart>=0.0.32,<0.1
```

Replace `import cgi` with `BinaryIO` from `typing` plus imports from
`python_multipart` and `python_multipart.multipart`. Do not import the ambiguous
legacy `multipart` namespace.

- [ ] **Step 5: Implement the multipart adapter**

Use dataclasses and a context manager:

```python
@dataclass
class UploadedFormFile:
    filename: str
    type: str
    file: BinaryIO


class ParsedMultipartForm:
    def __init__(self, fields, file_fields, resources):
        self._fields = fields
        self._file_fields = file_fields
        self._resources = resources

    def files(self, key: str) -> list[UploadedFormFile]:
        return list(self._file_fields.get(key, ()))

    def getfirst(self, key: str, default=None):
        values = self._fields.get(key)
        return values[0] if values else default

    def close(self) -> None:
        for resource in reversed(self._resources):
            resource.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()
```

Build `FormParser` with `MAX_BODY_SIZE`, `MAX_MEMORY_FILE_SIZE`,
`MAX_HEADER_COUNT`, and `MAX_HEADER_SIZE`. Count callbacks before storing each
part, seek file objects to zero, preserve parser resources until the context
exits, read exactly `Content-Length` bytes in 1 MiB chunks, require the parser's
end callback, and close all accumulated resources on any exception.

- [ ] **Step 6: Convert all three routes to the shared context manager**

Use:

```python
with self.read_multipart_form() as form:
    result = register_uploaded_media(form.files("video"))
```

Use the equivalent `frames` call for animation import. For line cleaner, pass
`form.files("frames")` and retain the existing `getfirst` defaults and clamps.
Delete `field_storage_items` after all callers are gone.

- [ ] **Step 7: Create a clean Python 3.13 verification environment**

Run:

```bash
python3.13 -m venv /tmp/svl-repair-venv
/tmp/svl-repair-venv/bin/python -m pip install -r requirements.txt pytest
```

Expected: Pillow and `python-multipart` install successfully.

- [ ] **Step 8: Run multipart tests on Python 3.13**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/svl-repair-pycache \
  /tmp/svl-repair-venv/bin/python -m unittest \
  tests.test_http_boundaries.MultipartHttpTests -v
```

Expected: PASS, including temporary-resource cleanup cases.

- [ ] **Step 9: Run the whole suite on Python 3.13**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/svl-repair-pycache \
  /tmp/svl-repair-venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 10: Update installation verification and commit**

Change `AGENT_INSTALL.md` to require Python 3.10+ and verify both imports:

```powershell
.\.venv\Scripts\python.exe -c "import PIL, python_multipart, server"
```

Then commit:

```bash
git add requirements.txt server.py tests/http_test_support.py tests/test_http_boundaries.py AGENT_INSTALL.md
git commit -m "fix: support bounded multipart uploads on Python 3.13"
```

### Task 6: Verify Real-ESRGAN Before Extraction

**Files:**
- Modify: `server.py:1-25,175-184,4121-4178`
- Modify: `tests/test_runtime_safety.py`

**Interfaces:**
- Produces: `copy_verified_download(response: BinaryIO, destination: Path, expected_sha256: str, max_bytes: int) -> int`.
- Consumes: existing `download_realesrgan_windows_package`, ZIP path validation, and install lock.

- [ ] **Step 1: Write failing verified-download tests**

Add a context-manager response fixture backed by `io.BytesIO`. Assert known
bytes are written only for their matching digest, altered bytes raise
`RuntimeError`, a stream over the supplied maximum raises, and no destination
file survives either failure. Assert the helper returns the byte count on
success.

- [ ] **Step 2: Write failing install-isolation and ZIP-traversal tests**

Patch `urlopen` to return an invalid archive stream, patch the work root, invoke
`install_realesrgan_runtime(True)`, and assert the final
`work/tools/realesrgan-ncnn-vulkan` target does not exist. Repeat with a partial
target containing a sentinel file and assert the entire target snapshot remains
unchanged. Create a ZIP containing `../outside.txt`, call
`extract_realesrgan_package`, and assert
`test_realesrgan_archive_rejects_unsafe_paths` raises without writing outside its
extraction root.

- [ ] **Step 3: Run the integrity tests and observe current acceptance**

Run:

```bash
/tmp/svl-repair-venv/bin/python -m unittest \
  tests.test_runtime_safety.RealEsrganIntegrityTests -v
```

Expected: FAIL because no digest or byte ceiling is enforced.

- [ ] **Step 4: Implement streaming size and digest verification**

Import `hashlib` and `hmac`, then add constants and helper:

```python
REAL_ESRGAN_WINDOWS_PACKAGE_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
REAL_ESRGAN_WINDOWS_PACKAGE_MAX_BYTES = 64 * 1024 * 1024


def copy_verified_download(response, destination: Path, expected_sha256: str, max_bytes: int) -> int:
    digest = hashlib.sha256()
    written = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise RuntimeError("download exceeds the allowed size")
                digest.update(chunk)
                output.write(chunk)
        if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
            raise RuntimeError("download checksum mismatch")
        return written
    except Exception:
        destination.unlink(missing_ok=True)
        raise
```

Use it inside `download_realesrgan_windows_package` before any ZIP open. Reject
an advertised `Content-Length` above 64 MiB early when present, but still enforce
the streamed ceiling.

- [ ] **Step 5: Run integrity and existing ZIP safety tests**

Run:

```bash
/tmp/svl-repair-venv/bin/python -m unittest \
  tests.test_runtime_safety.RealEsrganIntegrityTests \
  tests.test_runtime_safety.RealEsrganArchiveSafetyTests.test_realesrgan_archive_rejects_unsafe_paths -v
```

Expected: PASS.

- [ ] **Step 6: Commit download integrity**

```bash
git add server.py tests/test_runtime_safety.py
git commit -m "fix: verify Real-ESRGAN downloads"
```

### Task 7: Repair Portable Layout and Startup Readiness

**Files:**
- Create: `wait_for_server.ps1`
- Create: `tests/test_windows_contracts.py`
- Modify: `build_portable_bundle.ps1:41-51,86-126,157-164`
- Modify: `start_sprite_video_lab.bat:38-50`
- Modify: `start_sprite_video_lab_portable.bat:39-46`

**Interfaces:**
- Produces: PowerShell `Copy-TreeContents`, `Assert-RequiredPath`, and shared readiness script parameters `HostName`, `Port`, `TimeoutSeconds`, `OpenBrowser`.
- Consumes: `/api/app-version` from the existing server and `runtime\python` from the portable launcher contract.

- [ ] **Step 1: Write failing cross-platform script contract tests**

Create tests that read the scripts as text and assert:

```python
self.assertIn("Copy-TreeContents -Source $pythonHomeResolved -Destination $pythonRuntimeRoot", builder)
self.assertNotIn("Copy-Tree -Source $pythonHomeResolved -Destination $runtimeRoot", builder)
self.assertIn('"wait_for_server.ps1"', builder)
self.assertIn("python_multipart", builder)
for launcher in (standard_launcher, portable_launcher):
    self.assertNotIn("timeout /t 2", launcher.lower())
    self.assertIn("wait_for_server.ps1", launcher)
    self.assertIn("if errorlevel 1", launcher.lower())
```

Also assert the portable launcher's `%RUNTIME_ROOT%\python\python.exe` matches
the builder's `$pythonRuntimeRoot` destination and that both ffmpeg executables
are validated. Assert the readiness script contains `/api/app-version`, a
bounded deadline, 250 ms polling, wildcard-to-loopback normalization,
`Start-Process` only inside the HTTP-200 branch, and explicit `exit 0`/`exit 1`
paths. Assert each launcher delegates browser opening to `-OpenBrowser` and does
not retain a separate `start "" http://...` browser command (the `start` command
that launches the server console remains).

- [ ] **Step 2: Run the contract module and observe current failures**

Run:

```bash
/tmp/svl-repair-venv/bin/python -m unittest tests.test_windows_contracts -v
```

Expected: FAIL on Python copy destination, fixed sleeps, missing readiness script,
and missing pre-archive validation.

- [ ] **Step 3: Create the bounded readiness probe**

Create `wait_for_server.ps1`:

```powershell
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [Parameter(Mandatory = $true)][int]$Port,
    [int]$TimeoutSeconds = 30,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$normalizedHost = $HostName.Trim()
if ($normalizedHost.StartsWith("[") -and $normalizedHost.EndsWith("]")) {
    $normalizedHost = $normalizedHost.Substring(1, $normalizedHost.Length - 2)
}
$probeHost = switch ($normalizedHost) {
    "0.0.0.0" { "127.0.0.1" }
    "::" { "::1" }
    default { $normalizedHost }
}
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$hostForUrl = if ($probeHost.Contains(":")) { "[$probeHost]" } else { $probeHost }
$uri = "http://${hostForUrl}:$Port/api/app-version"

do {
    try {
        $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            if ($OpenBrowser) {
                try {
                    Start-Process -FilePath $uri -ErrorAction Stop
                } catch {
                    Write-Host "Sprite Video Lab is ready, but the browser could not be opened: $($_.Exception.Message)" -ForegroundColor Red
                    exit 1
                }
            }
            exit 0
        }
    } catch {
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)

Write-Host "Sprite Video Lab did not become ready at $uri within $TimeoutSeconds seconds." -ForegroundColor Red
exit 1
```

- [ ] **Step 4: Replace sleeps in both launchers**

After starting the server, call:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wait_for_server.ps1" -HostName "%SPRITE_VIDEO_LAB_HOST%" -Port "%SPRITE_VIDEO_LAB_PORT%" -TimeoutSeconds 30 -OpenBrowser
if errorlevel 1 (
  echo Sprite Video Lab failed to start. Check the server window for details.
  exit /b 1
)
```

- [ ] **Step 5: Copy Python contents into the launcher path**

Add:

```powershell
function Copy-TreeContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Ensure-Directory -PathValue $Destination
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}
```

Call it with `$pythonHomeResolved` and `$pythonRuntimeRoot`. Retain `Copy-Tree`
for the CorridorKey container directory.

- [ ] **Step 6: Add fail-fast bundle validation before compression**

Include `wait_for_server.ps1` in `$projectFiles`. Before `Compress-Archive`, check
the exact required paths with `Test-Path -PathType Leaf`, then run:

```powershell
& (Join-Path $pythonRuntimeRoot "python.exe") -c "import PIL, python_multipart"
if ($LASTEXITCODE -ne 0) {
    throw "Bundled Python cannot import PIL and python_multipart."
}
```

Validate both `ffmpeg.exe` and `ffprobe.exe`. Do not create the ZIP after any
failure.

- [ ] **Step 7: Run script contract tests**

Run:

```bash
/tmp/svl-repair-venv/bin/python -m unittest tests.test_windows_contracts -v
```

Expected: PASS. Record that this is static macOS evidence, not a Windows launch.

- [ ] **Step 8: Commit Windows reliability changes**

```bash
git add wait_for_server.ps1 build_portable_bundle.ps1 start_sprite_video_lab.bat start_sprite_video_lab_portable.bat tests/test_windows_contracts.py
git commit -m "fix: validate portable layout and server readiness"
```

### Task 8: Repair Responsive UI, Workflow State, and Favicon

**Files:**
- Create: `tests/test_ui_contracts.py`
- Modify: `app/index.html:3-8,51-68,94-109`
- Modify: `app/line-cleaner-experiment.html:3-8`
- Modify: `app/styles.css:155-174,218-248,458-500,2355-2510`
- Modify: `app/app.js:142-165,2160-2230,2774-2830`
- Modify: `server.py:5427-5464`

**Interfaces:**
- Produces: `syncWorkflowRail()`, `scheduleWorkflowRailSync()`, and browser-visible `.visually-hidden-input` behavior.
- Consumes: existing section IDs, rail `href` values, `applyUpload`, `renderJob`, and product icon `sprite_video_lab_icon.png`.

- [ ] **Step 1: Write failing static UI contracts**

Create tests that assert:

```python
self.assertIn('rel="icon"', index_html)
self.assertIn('href="/favicon.ico"', index_html)
self.assertIn('aria-current="step"', index_html)
self.assertIn('class="visually-hidden-input"', index_html)
self.assertIn(".visually-hidden-input", styles)
self.assertNotIn("font-size: clamp(28px, 3.6vw, 52px)", styles)
self.assertIn("position: static", mobile_media_block)
self.assertIn("syncWorkflowRail", app_js)
```

Also assert the line-cleaner document references the favicon and the server has a
hard-coded `/favicon.ico` route.

- [ ] **Step 2: Run static UI tests and confirm all defects are represented**

Run:

```bash
/tmp/svl-repair-venv/bin/python -m unittest tests.test_ui_contracts -v
```

Expected: FAIL on favicon, visible file input, viewport-scaled heading, mobile
sticky header, and workflow controller.

- [ ] **Step 3: Update HTML semantics**

Add `<link rel="icon" type="image/png" href="/favicon.ico">` to both documents.
Set `aria-current="step"` on the initial import rail item. Add
`visually-hidden-input` to `#uploadInput` while preserving its ID, label `for`,
accept list, and `multiple` attribute.

- [ ] **Step 4: Add the workflow active-state controller**

Add a frame-coalesced scroll/resize controller:

```javascript
let workflowRailFrame = null;

function syncWorkflowRail() {
  const items = Array.from(document.querySelectorAll(".rail-item"));
  const candidates = items
    .map((item) => ({ item, section: document.querySelector(item.getAttribute("href")) }))
    .filter(({ section }) => section && !section.hidden && section.offsetParent !== null);
  if (!candidates.length) return;
  const marker = window.scrollY + Math.min(window.innerHeight * 0.3, 180);
  const active = candidates.reduce((selected, candidate) => {
    const top = candidate.section.getBoundingClientRect().top + window.scrollY;
    return top <= marker ? candidate : selected;
  }, candidates[0]);
  items.forEach((item) => {
    const isActive = item === active.item;
    item.classList.toggle("active", isActive);
    if (isActive) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
}

function scheduleWorkflowRailSync() {
  if (workflowRailFrame !== null) return;
  workflowRailFrame = window.requestAnimationFrame(() => {
    workflowRailFrame = null;
    syncWorkflowRail();
  });
}
```

Bind it on DOM ready, scroll, and resize. Call `scheduleWorkflowRailSync()` after
`showAnimationWorkbench`, `applyUpload`, and `renderJob` change section visibility.

- [ ] **Step 5: Implement accessible hiding and fixed typography**

Add:

```css
.visually-hidden-input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}

h2 {
  font-size: 34px;
}
```

Use 30 px at the 1180 px breakpoint and 26 px at the 760 px breakpoint. Do not
use viewport units for font size.

- [ ] **Step 6: Compact the mobile top bar and enforce touch targets**

Inside the 760 px media block, add:

```css
.topbar { position: static; }
.source-strip {
  display: flex;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scrollbar-width: thin;
}
.source-strip > div { flex: 0 0 132px; }

.primary-button,
.magic-button,
.ghost-button,
.icon-button,
.choice-button,
.compact-button,
.text-button,
.small-link,
input[type="text"],
input[type="number"],
select,
.checkbox-row,
.magic-realesrgan-option,
.magic-resize-option {
  min-height: 44px;
}

.icon-button,
.clear-runtime-button {
  width: 44px;
  min-width: 44px;
}
```

Keep document width constrained; horizontal scrolling belongs only to the source
metadata strip. The icon and clear-runtime controls must measure 44 by 44 on
mobile.

- [ ] **Step 7: Serve the existing icon without duplicating a binary asset**

In `do_GET`, before generic static routing:

```python
if parsed.path == "/favicon.ico":
    self.serve_file(ROOT_DIR / "sprite_video_lab_icon.png", content_type="image/png", cache_control="public, max-age=86400")
    return
```

- [ ] **Step 8: Run static checks**

Run:

```bash
/tmp/svl-repair-venv/bin/python -m unittest tests.test_ui_contracts -v
node --check app/app.js
node --check app/line-cleaner-experiment.js
```

Expected: all commands pass.

- [ ] **Step 9: Commit the UI repair**

```bash
git add app/index.html app/line-cleaner-experiment.html app/styles.css app/app.js server.py tests/test_ui_contracts.py
git commit -m "fix: repair responsive workflow interactions"
```

### Task 9: Complete Integrated Verification and Final Review

**Files:**
- Modify: `CHANGELOG.md:1-15`
- Modify as defects require: only files already named in Tasks 1-8

**Interfaces:**
- Consumes: all contracts and helpers from Tasks 1-8.
- Produces: requirement-by-requirement evidence for R1-R11 and a clean final worktree.

- [ ] **Step 1: Record the repair under Unreleased**

Add concise Fixes and Security bullets covering Python 3.13 uploads, request
boundaries, byte ranges, cleanup, verified Real-ESRGAN download, portable layout,
readiness polling, and responsive navigation. Do not bump `VERSION` because this
is still the Unreleased section.

- [ ] **Step 2: Run the complete supported-runtime suite**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/svl-repair-pycache \
  /tmp/svl-repair-venv/bin/python -m pytest -q
PYTHONPYCACHEPREFIX=/tmp/svl-repair-pycache \
  /tmp/svl-repair-venv/bin/python -m py_compile server.py tools/apply_alpha_aware_despill.py
node --check app/app.js
node --check app/line-cleaner-experiment.js
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 3: Start an isolated final server**

Confirm port 8895 is free (or select another free loopback port), create a fresh
work root with `mktemp -d`, and retain its exact path for cleanup:

```bash
SVL_FINAL_WORK="$(mktemp -d /tmp/svl-final-work.XXXXXX)"
SPRITE_VIDEO_LAB_WORK_DIR="$SVL_FINAL_WORK" \
  /tmp/svl-repair-venv/bin/python server.py --serve --host 127.0.0.1 --port 8895
```

Keep the process in a managed terminal session and stop it before handoff.

- [ ] **Step 4: Run live API acceptance checks**

Verify root, CSS, JS, favicon, runtime info, and output path return expected
status/content types. Import `sprite_video_lab_icon.png` using multipart, process
it with no matte and concrete production fields, assert returned/persisted
context, export frames, and verify closed/open/suffix byte responses. Send hostile
Host and Origin requests and assert 421/403. Send the traversal URL with
`curl --path-as-is` and assert it is rejected.

- [ ] **Step 5: Run browser desktop acceptance**

Launch headless Chrome with a temporary profile and CDP. At 1440x900:

- load `http://127.0.0.1:8895/`;
- confirm no console or failed-network errors;
- use the real upload input to import the icon;
- process the single image and render the result frame;
- scroll through all visible workflow sections and assert exactly one rail item
  has `.active` and `aria-current="step"`;
- confirm `/favicon.ico` is 200;
- capture `/tmp/svl-final-desktop.png`.

- [ ] **Step 6: Run browser mobile acceptance**

At exact 390x844 and then 320x700:

- assert `document.documentElement.scrollWidth === window.innerWidth`;
- assert `.topbar` computed position is `static`;
- assert `#uploadInput` has a one-pixel clipped box and the native chooser is not
  visible;
- activate the dropzone with keyboard Enter and confirm the input click handler;
- measure the listed mobile controls and assert both dimensions are at least 44
  where applicable;
- scroll 1200 px and confirm the header does not cover the viewport;
- capture `/tmp/svl-final-mobile.png`.

- [ ] **Step 7: Review security and behavior against the spec**

Read the final diff and map every R1-R11 item to a passing test, HTTP response,
browser measurement, or explicit Windows caveat. Search for remaining `cgi`,
unvalidated ID joins, direct Range integer parsing, fixed two-second waits,
unverified archive extraction, visible `#uploadInput`, and stale hard-coded rail
state. Treat any missing evidence as incomplete and fix it before proceeding.

- [ ] **Step 8: Verify repository hygiene**

Run:

```bash
git status --short
find . -maxdepth 3 -type d -name __pycache__ -o -name .pytest_cache
```

Remove only artifacts created by this repair run. Confirm no `work/`, browser
profiles, screenshots, archives, or virtual environments are staged. Stop the
managed server, then remove the exact `SVL_FINAL_WORK` directory and the two
`/tmp/svl-final-*.png` screenshots created by Steps 5-6.

- [ ] **Step 9: Commit changelog and verification-driven fixes**

```bash
git add CHANGELOG.md
git commit -m "docs: record reliability and security repairs"
```

If verification produced code changes beyond the changelog, inspect
`git diff --name-only` and stage only the exact files changed by this repair.
Never use a broad directory add and never create an empty commit.

- [ ] **Step 10: Run the final completion commands after the last commit**

Run the complete suite, compile checks, JavaScript syntax checks, `git diff
--check`, and `git status --short --branch` again. Report the Windows portable
build as unverified unless it was actually executed on Windows. Do not claim the
goal complete until every non-Windows acceptance item has current passing output
and the final review has no open high- or medium-severity finding.
