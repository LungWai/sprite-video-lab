# Sprite Video Lab Reliability and Security Repair Design

Date: 2026-09-02
Status: Approved in chat; pending written-spec review

## Context

Sprite Video Lab is a local-first Python HTTP application with a static HTML,
CSS, and JavaScript frontend. The current branch contains a large upstream
rewrite plus a local `production_context` passthrough that was restored during
the merge at `ece54f6`.

The review reproduced failures across the server request boundary, runtime
cleanup, Python compatibility, Windows portable packaging, and responsive UI.
The repair must address those failures as one coherent reliability pass rather
than treating each symptom independently.

The original pasted reviewer report is not present in the retained transcript.
This specification therefore records every requirement established through
source inspection and runtime reproduction. A final audit must also compare any
re-pasted reviewer report without narrowing the scope defined here.

## Goals

1. Make the documented Python 3.10+ runtime work on Python 3.13 and 3.14.
2. Make HTTP parsing and partial media delivery deterministic for valid and
   malformed requests.
3. Protect the privileged local API from DNS rebinding, cross-origin browser
   requests, path traversal, oversized request bodies, and arbitrary file
   execution through the file-browser endpoint.
4. Make cleanup delete all current generated output formats while preserving
   unrelated user files and remaining safe across macOS path aliases.
5. Make the Windows portable bundle place and validate its runtime at the path
   used by the launcher, and replace fixed startup sleeps with readiness checks.
6. Verify downloaded Real-ESRGAN code before extraction or execution.
7. Preserve `production_context` through processing with regression coverage.
8. Repair the confirmed responsive and interaction defects without redesigning
   the product or changing its processing workflow.
9. Leave the existing processing, preview, export, AI opt-in, and external
   output-path behavior intact.

## Non-Goals

- Replacing the standard-library HTTP server with a web framework.
- Refactoring the large processing pipeline solely for style.
- Bundling AI model weights.
- Adding accounts, remote access, telemetry, or a cloud service.
- Changing matte algorithms, output formats, or image quality settings except
  where a regression test proves current behavior is broken.
- Rebuilding the visual identity or replacing established product assets.

## Confirmed Defects and Acceptance Requirements

### R1: Baseline Tests

The current suite has three failures: two optional Hugging Face imports are
mocked incorrectly, and cleanup compares resolved children to an unresolved
macOS root. The repaired suite must pass without optional AI packages installed.

Evidence: the full supported-runtime test suite exits zero, including isolated
tests with fake optional modules.

### R2: Production Context

`production_id`, `scene_id`, `shot_id`, and `shot_version_id` must be projected
from `/api/process` into the saved and returned job manifest when non-empty.
Preview requests must remain unchanged.

Evidence: a regression test exercises the route projection and a processing
test verifies the persisted manifest.

### R3: Python 3.13+ Multipart Compatibility

Remove `cgi.FieldStorage`, which no longer exists in Python 3.13. Add
`python-multipart>=0.0.32,<0.1` as a base dependency and import it through the
unambiguous `python_multipart` package name. All three multipart routes must use
one shared parser adapter:

- `/api/upload`
- `/api/import-animation`
- `/api/line-cleaner-process`

The adapter must:

- require a valid non-negative `Content-Length`;
- reject bodies above `SPRITE_VIDEO_LAB_MAX_UPLOAD_BYTES`, whose default is
  8 GiB;
- reject more than 4096 parts;
- spool file bodies larger than 1 MiB to temporary files;
- cap individual multipart header count and size using parser-supported limits;
- decode field names and filenames as UTF-8 with a Latin-1 fallback;
- expose the existing `filename`, `type`, and `file` contract to processing code;
- detect incomplete multipart bodies;
- close every temporary file on success and failure.

JSON request bodies must be limited to 1 MiB, decode as UTF-8 JSON objects, and
return a structured client error for invalid length, media type, encoding, JSON,
or top-level type. Requests larger than the configured limit return HTTP 413;
multipart requests without a usable length return HTTP 411.

Evidence: tests submit real browser-shaped multipart bodies for all field/file
patterns and exercise missing, malformed, incomplete, excessive, and oversized
inputs on Python 3.13 or newer.

### R4: HTTP Range Semantics

Media delivery supports exactly one byte range. A pure range parser must handle:

- closed ranges such as `bytes=0-15`;
- open-ended ranges such as `bytes=16-`;
- suffix ranges such as `bytes=-16` as the final 16 bytes;
- end positions beyond the file by clamping to the last byte;
- zero-length files and unsatisfiable start positions;
- arbitrarily long numerals without crashing;
- malformed or unsupported/multiple ranges without an uncaught exception.

Malformed, unknown-unit, and unsupported multiple ranges are ignored and served
as HTTP 200. A syntactically valid but unsatisfiable byte range returns HTTP 416
with `Content-Range: bytes */<size>` and an empty body. Satisfiable ranges return
HTTP 206 with exact `Content-Range` and `Content-Length` values.

Evidence: unit and live HTTP tests verify headers and exact response bytes for
each case, including the previously failing suffix and malformed requests.

### R5: Local Request Boundary

The default application remains local-first. Every request must validate the
`Host` header before routing:

- loopback bindings allow `127.0.0.1`, `localhost`, and `::1`;
- a specific non-wildcard bind address is also allowed;
- extra names can be provided through `SPRITE_VIDEO_LAB_ALLOWED_HOSTS`;
- wildcard binding does not implicitly trust arbitrary hostnames;
- malformed or unapproved hosts return HTTP 421.

For browser-originating state-changing requests, an `Origin` header, when
present, must exactly match the canonical request host and port. A mismatched,
opaque, or malformed origin returns HTTP 403. Requests without `Origin` remain
available to local CLI clients after Host validation.

All responses receive `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin`, and
this Content Security Policy:

`default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; connect-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'`

The inline-style allowance is required by the existing dynamic color swatches
and position markers; scripts remain self-only. JSON responses additionally use
`Cache-Control: no-store`.

Evidence: live HTTP tests prove hostile Host and Origin values cannot read
runtime information or invoke POST APIs, while same-origin browser and local CLI
requests continue to work.

### R6: Identifier and Path Safety

Upload, job, preview, line-cleaner, and scale-processing identifiers must be
single safe path components. Accept ASCII alphanumerics followed by ASCII
alphanumerics, dot, underscore, or hyphen, with a maximum length of 128. Reject
empty values, `.`/`..`, separators, percent escapes, control characters, and
platform-specific separator variants before any filesystem join.

`/api/open-path` must accept existing directories only, and only when the
resolved directory is within the managed work root or configured export root.
This preserves all current UI calls while preventing arbitrary files or
executables from being opened.

Evidence: tests reproduce and then reject
`/media/upload/../jobs/<job-id>`, cover Windows-style separators, and prove valid
generated IDs and configured external export directories still work.

### R7: Runtime Cleanup

Resolve the configured export root before comparing it with resolved children.
Recognize and remove these timestamped generated directories from an external
output root:

- legacy `*-export` directories;
- legacy `*-magic-(half|quarter|eighth)-frames` directories;
- current `*-scale-(full|half|quarter|eighth)-` directories for `frames`,
  `sprite_sheet`, `mov`, and `gif`.

Never remove unrelated files, unrelated directories, symlinks resolving outside
the configured root, the settings file, or downloaded copies. A suspicious
generated-looking path that resolves outside the root must fail closed.

Evidence: temporary-directory tests cover macOS `/var` versus `/private/var`,
every current output name, legacy names, unrelated content, and escaping links.

### R8: Real-ESRGAN Download Integrity

Keep the existing official release URL and pin the downloaded archive to:

`abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d`

Stream the download while hashing it, enforce a 64 MiB maximum archive size
above the known 45,474,481-byte artifact, and compare with
`hmac.compare_digest` before opening the ZIP. A mismatch or oversized response
must delete the temporary artifact, leave the installation target unchanged,
and return a clear error. Existing ZIP traversal and required-file checks remain.

Evidence: tests cover the accepted digest, altered bytes, oversized streams,
ZIP traversal, and absence of target mutation on failure.

### R9: Windows Portable Layout

The builder must copy the contents of `PythonHome` into
`runtime\python`, not copy the Python source directory underneath
`runtime`. Keep tree-container copying for the CorridorKey source where that
layout is intentional.

Before creating the ZIP, validate at minimum:

- `runtime\python\python.exe`;
- `runtime\ffmpeg\ffmpeg.exe` and `ffprobe.exe`;
- `server.py` and the portable launcher;
- imports of `PIL` and `python_multipart` using the bundled interpreter.

The builder must fail before archive creation when any contract is missing. A
portable-layout contract test must keep builder and launcher paths aligned.

Evidence: static contract tests pass on all platforms. The script records a
clear validation failure on a deliberately incomplete fixture. A real Windows
bundle run remains the authoritative platform acceptance test when a Windows
runner is available.

### R10: Launcher Readiness

Replace the fixed two-second delay in both Windows launchers with bounded HTTP
polling of `/api/app-version`. Open the browser only after a successful response.
After the timeout, print an actionable error and exit non-zero rather than
opening a failed page. Keep existing stale-process cleanup and environment
selection behavior.

Evidence: script contract checks verify the health endpoint, bounded retries,
success path, and failure exit. Windows execution is verified when a Windows
runner is available.

### R11: Responsive UI and Interaction State

Main UI fixes:

- hide the native upload input with an accessible visually-hidden pattern while
  preserving label activation, keyboard activation, drag/drop, and JS `.click()`;
- at 760 px and below, make the top bar non-sticky and render source metadata as
  a compact horizontally scrollable row instead of four stacked rows;
- maintain at least 44 by 44 CSS-pixel targets for primary mobile buttons,
  compact action buttons, selects, number inputs, and checkbox/radio labels;
- use fixed breakpoint typography rather than viewport-scaled panel headings;
- keep the page free of horizontal overflow at 320 px, 390 px, and desktop;
- update workflow rail `.active` and `aria-current="step"` based on the visible
  workflow section, ignoring hidden sections;
- serve the existing product PNG at `/favicon.ico` and reference it from both
  HTML documents.

The changes must preserve the current high-contrast product style, desktop
density, reduced-motion behavior, focus indicators, processing controls, and
empty custom-animation workbench.

Evidence: browser screenshots and DOM measurements at 390x844 and 1440x900;
keyboard upload activation; successful import/process/export smoke flow; no
console errors, favicon 404, overlap, or document-level horizontal overflow.

## Architecture

### Request Boundary Layer

Keep `AppHandler` as the single HTTP adapter, but extract small pure helpers for
Host parsing, Origin matching, body-length validation, runtime-ID validation,
and byte-range parsing. `do_GET` and `do_POST` call the boundary checks before
route-specific work. Route handlers continue returning the existing JSON shapes.

Multipart parsing is isolated behind a context-managed adapter. Downstream media
functions receive file-like objects matching their current interface and do not
depend directly on the third-party parser.

### Filesystem Boundary Layer

All runtime directory helper functions validate identifiers before joining paths.
Root-containment checks operate only on resolved roots and resolved candidates.
Opening a directory uses one shared allowlist helper rather than route-local path
logic.

### Artifact Boundary Layer

Downloaded executable archives are untrusted until their digest is verified.
Portable bundle creation similarly treats its output layout as untrusted until
the launcher contract and base imports have been checked.

### Frontend State Layer

The workflow rail remains ordinary anchor navigation. A small observer/controller
owns its active and `aria-current` state; processing state does not get duplicated
inside navigation code. CSS changes are mobile-scoped except for replacing the
viewport-scaled panel heading size and hiding the redundant native file input.

## Data and Error Flow

1. HTTP request arrives.
2. Host validation runs for every method and path.
3. Origin validation runs for state-changing API requests when Origin is present.
4. Route-specific body parsing enforces framing and size before reading.
5. Runtime identifiers are validated before manifest or directory lookup.
6. Domain processing runs with its existing inputs and output schemas.
7. Responses include common security headers and structured JSON errors for APIs.

Expected client status codes:

- 400: malformed IDs, JSON, multipart syntax, or parameters;
- 403: mismatched browser Origin or disallowed open-path target;
- 404: missing media, manifests, static files, or directories;
- 411: required multipart request length missing;
- 413: configured request-body limit exceeded;
- 415: unsupported request media type;
- 416: syntactically valid but unsatisfiable byte range;
- 421: unapproved or malformed Host;
- 409: existing busy-operation conflicts.

Unexpected internal exceptions must not close the connection without a response.
Current human-readable domain errors remain intact where they are already part of
the UI contract.

## Implementation Sequence

Each batch follows red-green-refactor and is independently reviewable:

1. Repair baseline tests and add `production_context` regressions.
2. Add pure HTTP and identifier tests, then boundary helpers and Range handling.
3. Add multipart request tests, then replace `cgi.FieldStorage` and add the base
   dependency.
4. Add cleanup and open-path tests, then repair filesystem boundaries.
5. Add archive-integrity tests, then implement verified Real-ESRGAN download.
6. Add portable/launcher contract tests, then repair PowerShell and batch files.
7. Add UI state/static checks, then implement HTML/CSS/JS fixes.
8. Run full automated and live browser verification, re-review the complete diff,
   and fix any newly discovered defect before completion.

## Verification Matrix

Automated:

- supported Python test suite on Python 3.13 or newer;
- tests with optional AI modules absent;
- real multipart requests against an ephemeral server;
- exact Range response bytes and headers;
- Host, Origin, traversal, body-limit, CSP, and open-path integration tests;
- cleanup and checksum failure isolation tests;
- production-context persistence test;
- portable and launcher contract tests;
- `python -m py_compile` for Python sources;
- `node --check app/app.js` and line-cleaner JavaScript;
- `git diff --check`.

Runtime smoke:

- start with an isolated temporary work directory;
- load root, CSS, JS, favicon, runtime info, and output-path APIs;
- import a real image through the browser;
- preview/process it and verify returned production context;
- export frames and request media with closed, open-ended, and suffix ranges;
- confirm hostile Host/Origin requests are rejected;
- confirm cleanup preserves unrelated external output files.

Visual and interaction:

- screenshots at 1440x900 and 390x844 plus a 320 px overflow measurement;
- verify the mobile header does not remain pinned over content;
- verify no redundant native chooser is visible;
- verify workflow active state changes while scrolling;
- verify keyboard focus, upload activation, mobile targets, status text, and no
  incoherent overlap.

Platform caveat:

The macOS workspace cannot execute the Windows portable bundle. Static contract
checks and fail-fast validation are required here; a successful build and launch
on Windows is the final platform-specific evidence and must not be claimed from
macOS-only results.

## Completion Criteria

The repair is complete only when:

1. Every requirement R1-R11 has direct evidence in the verification matrix.
2. The full supported-runtime suite passes from a clean dependency environment.
3. The live API and browser smoke flows pass against the final code.
4. A final security, functionality, responsive-UI, and diff review finds no open
   high- or medium-severity issue in the changed surfaces.
5. Any unavailable Windows-only acceptance step is reported explicitly rather
   than represented as passed.
6. The worktree contains no accidental runtime artifacts, caches, screenshots,
   or unrelated changes.
