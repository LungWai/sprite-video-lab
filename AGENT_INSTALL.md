# Agent Installation Guide

This guide is for coding agents setting up Sprite Video Lab for a user. Do not ask the user to run these steps manually unless a machine-level installer or credential prompt blocks automation.

## Scope

- Target OS: Windows.
- Project root: the cloned `sprite-video-lab` repository.
- Runtime: local Python HTTP server, ffmpeg/ffprobe, optional AI matting runtime, optional Real-ESRGAN line-cleaner runtime.
- User-facing URL after setup: `http://127.0.0.1:8894`.

## 1. Inspect The Workspace

```powershell
git status --short
Get-Content VERSION -Encoding utf8
python --version
```

Preserve unrelated local changes. Do not delete `work/`, model caches, or external tool folders unless the user explicitly requests cleanup.

## 2. Install The Base Python Runtime

Create a local virtual environment when `.venv` does not already exist:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Verify the base server imports:

```powershell
.\.venv\Scripts\python.exe -m py_compile server.py
```

## 3. Provide ffmpeg And ffprobe

First check whether both binaries are already available:

```powershell
ffmpeg -version
ffprobe -version
```

If not on `PATH`, set `SPRITE_VIDEO_LAB_FFMPEG_DIR` to a directory that contains both `ffmpeg.exe` and `ffprobe.exe` before starting the server:

```powershell
$env:SPRITE_VIDEO_LAB_FFMPEG_DIR = "D:\ffmpeg\bin"
```

The app also checks its built-in fallback path used by the maintainer machine, but agents should not rely on that path on other computers.

## 4. Optional AI Matting Runtime

Install this manually only when the user explicitly asks for AI setup. The WebApp otherwise waits until the user selects BiRefNet or CorridorKey and confirms the in-app installation prompt:

```powershell
.\setup_ai_runtime.bat
```

Recommended external cache layout:

```powershell
$env:SPRITE_VIDEO_LAB_AI_MODEL_CACHE = "E:\sprite-video-lab-models\huggingface"
$env:SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT = "E:\sprite-video-lab-models\EZ-CorridorKey"
```

If `setup_ai_runtime.bat` creates or expects a separate Python runtime, start the app with that runtime instead of `.venv`:

```powershell
$env:SPRITE_VIDEO_LAB_PYTHON = "E:\sprite-video-lab-models\venv\Scripts\python.exe"
```

BiRefNet downloads model files on first use. Treat the first AI preview as an installation verification step.

## 5. Optional Real-ESRGAN Line Cleaner Runtime

When the user confirms **先做平滑处理**, the WebApp checks this runtime and automatically downloads the official Windows portable package if it is missing. Preview and batch processing stay disabled until installation succeeds. The automatic target is:

- `<configured work dir>\tools\realesrgan-ncnn-vulkan\realesrgan-ncnn-vulkan.exe`

The download only starts after the checkbox confirmation and the install API also requires `confirmed: true`. For a manual or shared installation, put the portable package in one of these locations:

- `tools\realesrgan-ncnn-vulkan\realesrgan-ncnn-vulkan.exe`
- `work\tools\realesrgan-ncnn-vulkan\realesrgan-ncnn-vulkan.exe`
- any directory on `PATH`

The model files must be under a `models` directory next to the binary, or in a directory named by `SPRITE_VIDEO_LAB_REALESRGAN_MODEL_DIR`:

```powershell
$env:SPRITE_VIDEO_LAB_REALESRGAN_BIN = "D:\tools\realesrgan-ncnn-vulkan\realesrgan-ncnn-vulkan.exe"
$env:SPRITE_VIDEO_LAB_REALESRGAN_MODEL_DIR = "D:\tools\realesrgan-ncnn-vulkan\models"
```

Required files:

- `realesrgan-x4plus-anime.param`
- `realesrgan-x4plus-anime.bin`

## 6. Start Or Restart The Server

Use one server process on one port. Stop stale Sprite Video Lab Python servers first:

```powershell
$project = (Get-Location).Path
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -and
    $_.CommandLine -like "*sprite-video-lab*server.py*"
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Start the preferred Python runtime:

```powershell
$python = if ($env:SPRITE_VIDEO_LAB_PYTHON) { $env:SPRITE_VIDEO_LAB_PYTHON } else { ".\.venv\Scripts\python.exe" }
$env:SPRITE_VIDEO_LAB_HOST = "127.0.0.1"
$env:SPRITE_VIDEO_LAB_PORT = "8894"
Start-Process -FilePath $python `
  -ArgumentList @("server.py", "--serve", "--host", "127.0.0.1", "--port", "8894") `
  -WorkingDirectory (Get-Location).Path `
  -WindowStyle Hidden
```

Verify:

```powershell
Invoke-WebRequest http://127.0.0.1:8894/ -UseBasicParsing -TimeoutSec 10
Get-NetTCPConnection -LocalPort 8894 -ErrorAction SilentlyContinue |
  Where-Object { $_.State -eq "Listen" }
```

## 7. Smoke Tests

Run the base syntax check:

```powershell
.\.venv\Scripts\python.exe -m py_compile server.py
```

Open these URLs after the server is listening:

- Main app: `http://127.0.0.1:8894/`
- Line cleaner experiment: `http://127.0.0.1:8894/app/line-cleaner-experiment.html`

If the user reports wrong behavior after a restart, check for duplicate listeners:

```powershell
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -and
    $_.CommandLine -like "*sprite-video-lab*server.py*"
  } |
  Select-Object ProcessId, ExecutablePath, CommandLine
```

There should be only one active server for the requested port.
