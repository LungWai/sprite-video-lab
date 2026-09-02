# Changelog

## Unreleased - 2026-09-02

### Fixes
- Restore multipart uploads on Python 3.13+ by replacing the removed `cgi` module with a shared `python-multipart` adapter for `/api/upload`, `/api/import-animation`, and `/api/line-cleaner-process`, with spooled temporary files that are always closed.
- Project `production_id`, `scene_id`, `shot_id`, and `shot_version_id` from `/api/process` into the returned and persisted job manifest.
- Serve exactly one HTTP byte range with correct suffix, open-ended, clamped, and unsatisfiable (`416`) handling, and ignore malformed or multiple ranges instead of crashing.
- Resolve the configured export root before runtime cleanup so macOS `/var` symlinks no longer skip generated `*-export`, `*-magic-*-frames`, and `*-scale-*` directories, while unrelated files, symlinks, and settings are never removed.
- Copy the Windows portable Python runtime into `runtime\python` and validate the bundle contract (`python.exe`, `ffmpeg.exe`, `ffprobe.exe`, `server.py`, launcher, `PIL`, `python_multipart`) before creating the archive (verified by static contract tests; Windows run pending).
- Replace the fixed two-second launcher delay with bounded polling of `/api/app-version`; the browser opens only after the server responds and the launcher exits non-zero with a clear message on timeout (verified by static contract tests; Windows run pending).
- Fix the responsive UI: accessible visually-hidden upload input, non-sticky mobile top bar, horizontally scrollable source metadata, 44px mobile touch targets, fixed breakpoint typography, no horizontal overflow at 320px and 390px, workflow rail state driven by the visible section, and `/favicon.ico` served from the product icon.
- Repair the baseline test suite so it passes without optional AI packages installed.

### Security
- Validate the request `Host` header before routing (loopback names, the bound address, and `SPRITE_VIDEO_LAB_ALLOWED_HOSTS`; `421` otherwise) and require a browser `Origin`, when present, to exactly match the request host and port (`403` otherwise).
- Send `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `Cross-Origin-Resource-Policy: same-origin` on every response, and `Cache-Control: no-store` on JSON responses.
- Bound request bodies: JSON bodies are limited to 1 MiB and must be UTF-8 JSON objects, multipart bodies require a valid `Content-Length` (`411` otherwise) and honour `SPRITE_VIDEO_LAB_MAX_UPLOAD_BYTES` (`413` above the limit), with part, header, and field-size caps.
- Validate upload, job, preview, line-cleaner, and scale identifiers as single safe path components before any filesystem join, and restrict `/api/open-path` to existing directories inside the managed work root or configured export root.
- Verify the Real-ESRGAN Windows download against a pinned SHA-256 while streaming, enforce a 64 MiB size cap, and delete the temporary artifact without touching the installation target on mismatch.

## Unreleased - 2026-08-21

### Features
- Make the CorridorKey Chroma coarse-mask path inherit the current Chroma key mode, every manually selected background color, and the Chroma tolerance.
- Keep the Chroma tolerance shared when switching between Chroma preview and CorridorKey with a Chroma coarse mask, while leaving the BiRefNet coarse-mask path independent.

### Documentation
- Add a README workflow explaining how to preview and tune a multi-color Chroma coarse mask before switching to CorridorKey.
- Add screenshots for manual green-screen sampling and the resulting Chroma color/tolerance settings.

## 0.2.0 - 2026-06-07

### Features
- Add GIF input support with generated MP4 previews for browser playback and frame extraction.
- Add automatic BiRefNet fallback to the general model when the selected model produces a weak or nearly empty alpha mask.
- Add an experimental line-cleaner page with Lanczos shrinking and optional Real-ESRGAN anime processing.
- Add persisted frame-boundary payload fields for selected segment processing.

### Fixes
- Tighten segment preview playback so the selected end frame no longer shows an extra frame.
- Clamp single-frame preview sampling to the selected segment.

### Documentation
- Replace manual human installation steps with an agent-focused installation guide.
- Document GIF input support and the experimental line-cleaner entry point.

## 0.1.1 - 2026-05-15

### Documentation
- Add a full English usage guide alongside the Chinese guide.
- Expand both guides with user-facing BiRefNet, BiRefNet + Luma, subject-protection preset, and CorridorKey workflows.
- Add language links between the English and Chinese guides.

## 0.1.0 - 2026-05-15

### Features
- Add Luma subject-protection presets for BiRefNet + Luma workflows.
- Add preview post-processing for green residue and semi-transparent edge pixels.
- Add batch post-processing options for processed frame outputs.
- Add reverse animation preview and reverse-order export.
- Improve CorridorKey handling for large GPU post-processing workloads.

### Documentation
- Add a detailed Chinese usage guide covering setup, workflows, tuning, export, and troubleshooting.
