# Changelog

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
