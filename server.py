from __future__ import annotations

import argparse
import cgi
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from fractions import Fraction
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageChops, ImageFilter


ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "app"
WORK_DIR_ENV = "SPRITE_VIDEO_LAB_WORK_DIR"
DEFAULT_WORK_DIR = ROOT_DIR / "work"
_raw_work_dir = str(os.environ.get(WORK_DIR_ENV, "")).strip().strip("\"'")
if _raw_work_dir:
    WORK_DIR = Path(_raw_work_dir).expanduser()
    if not WORK_DIR.is_absolute():
        WORK_DIR = (ROOT_DIR / WORK_DIR).resolve()
    else:
        WORK_DIR = WORK_DIR.resolve()
else:
    WORK_DIR = DEFAULT_WORK_DIR
UPLOADS_DIR = WORK_DIR / "uploads"
JOBS_DIR = WORK_DIR / "jobs"
EXPORTS_DIR = WORK_DIR / "exports"
PREVIEWS_DIR = WORK_DIR / "previews"
LINE_CLEANER_DIR = WORK_DIR / "line-cleaner"
MAGIC_DIR = WORK_DIR / "magic"
SETTINGS_PATH = WORK_DIR / "settings.json"
LEGACY_SETTINGS_PATH = DEFAULT_WORK_DIR / "settings.json"
MAGIC_PREVIEW_LOCK = threading.Lock()

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8894
DEFAULT_FFMPEG_FALLBACK_ROOT = Path(r"I:\FF\Flowframes\FlowframesData\pkgs\av")
HOST_ENV = "SPRITE_VIDEO_LAB_HOST"
PORT_ENV = "SPRITE_VIDEO_LAB_PORT"
FFMPEG_DIR_ENV = "SPRITE_VIDEO_LAB_FFMPEG_DIR"
REAL_ESRGAN_BINARY_ENV = "SPRITE_VIDEO_LAB_REALESRGAN_BIN"
REAL_ESRGAN_MODEL_DIR_ENV = "SPRITE_VIDEO_LAB_REALESRGAN_MODEL_DIR"
AI_MODEL_CACHE_ENV = "SPRITE_VIDEO_LAB_AI_MODEL_CACHE"
CORRIDORKEY_ROOT_ENV = "SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT"
LANCZOS = Image.Resampling.LANCZOS
BOX = Image.Resampling.BOX
NEAREST = Image.Resampling.NEAREST
APP_VERSION_POLL_MS = 1200
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".gif"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ANIMATION_FRAME_EXTENSIONS = IMAGE_EXTENSIONS
CONTENT_TYPE_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "image/gif": ".gif",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
MOJIBAKE_REPLACEMENTS = {
    "\u677b\ufe40\u75c2": "\u8f66\u5b9d",
}
FFMPEG_ACCEL_ENV = "SPRITE_VIDEO_LAB_FFMPEG_ACCEL"
FFMPEG_ACCEL_PRIORITY = ("cuda", "qsv", "d3d11va", "dxva2")
FFMPEG_ACCEL_ALIASES = {
    "": "auto",
    "auto": "auto",
    "default": "auto",
    "gpu": "auto",
    "cpu": "cpu",
    "off": "cpu",
    "none": "cpu",
    "disabled": "cpu",
    "cuda": "cuda",
    "nvdec": "cuda",
    "qsv": "qsv",
    "d3d11va": "d3d11va",
    "dxva2": "dxva2",
}
AI_MATTE_MODEL_REPOS = {
    "birefnet-hr-matting": "ZhengPeng7/BiRefNet_HR-matting",
    "birefnet-lite-2k": "ZhengPeng7/BiRefNet_lite-2K",
    "birefnet-general": "ZhengPeng7/BiRefNet",
}
AI_MATTE_MODEL_LABELS = {
    "birefnet-hr-matting": "BiRefNet HR-matting",
    "birefnet-lite-2k": "BiRefNet lite-2K",
    "birefnet-general": "BiRefNet general",
}
AI_MATTE_MODES = {
    "none",
    "chroma",
    "birefnet",
    "corridorkey",
    "luma",
    "birefnet_corridorkey",
    "birefnet_corridorkey_key",
    "birefnet_luma",
    "birefnet_luma_key",
    "birefnet_luma_corridorkey",
}
AI_MATTE_DEVICE_ALIASES = {
    "": "auto",
    "auto": "auto",
    "gpu": "cuda",
    "cuda": "cuda",
    "cuda:0": "cuda",
    "cpu": "cpu",
}
DEFAULT_AI_MATTE_MODEL = "birefnet-hr-matting"
DEFAULT_AI_MATTE_RESOLUTION = 1024
AI_MATTE_RESOLUTION_AUTO = "auto"
AI_MATTE_MIN_RESOLUTION = 256
AI_MATTE_MAX_RESOLUTION = 2560
AI_MATTE_RESOLUTION_MULTIPLE = 32
OUTPUT_SCALE_MIN = 0.05
OUTPUT_SCALE_MAX = 2.0
CORRIDORKEY_REPO_URL = "https://github.com/nikopueringer/CorridorKey"
CORRIDORKEY_IMG_SIZE = 2048
CORRIDORKEY_GPU_DESPECKLE_PIXEL_LIMIT = 2**24
CORRIDORKEY_SCREEN_COLORS = {"auto", "green", "blue"}
CANVAS_MODES = {"auto", "square_bottom", "square_center"}
LINE_CLEANER_METHODS = {"classic", "realesrgan_anime"}
REAL_ESRGAN_ANIME_MODEL = "realesrgan-x4plus-anime"
MAGIC_CROP_PADDING = 24
MAGIC_UPSCALE = 4
MAGIC_ALPHA_LOSS_FALLBACK_RATIO = 0.05
MAGIC_VARIANTS = (
    {"key": "half", "label": "1/2", "scale": 0.5, "dir": "frames"},
    {"key": "quarter", "label": "1/4", "scale": 0.25, "dir": "frames-quarter"},
    {"key": "eighth", "label": "1/8", "scale": 0.125, "dir": "frames-eighth"},
)
MAGIC_RESIZE_MODE_DEFAULT = "hard"
MAGIC_RESIZE_MODES = {
    "hard": {"label": "硬", "resample": NEAREST},
    "soft": {"label": "软", "resample": BOX},
}

_FFMPEG_HWACCELS_CACHE: set[str] | None = None
_BIREFNET_MODEL_CACHE: dict[tuple[str, str], object] = {}
_CORRIDORKEY_ENGINE_CACHE: dict[tuple[str, str], object] = {}


def ensure_runtime_dirs() -> None:
    for directory in (APP_DIR, WORK_DIR, UPLOADS_DIR, JOBS_DIR, EXPORTS_DIR, PREVIEWS_DIR, LINE_CLEANER_DIR, MAGIC_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    migrate_legacy_settings()


def migrate_legacy_settings() -> None:
    if SETTINGS_PATH.resolve() == LEGACY_SETTINGS_PATH.resolve() or SETTINGS_PATH.exists() or not LEGACY_SETTINGS_PATH.exists():
        return
    try:
        settings = json.loads(LEGACY_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_app_settings() -> dict:
    settings_paths = [SETTINGS_PATH]
    if SETTINGS_PATH.resolve() != LEGACY_SETTINGS_PATH.resolve():
        settings_paths.append(LEGACY_SETTINGS_PATH)
    for path in settings_paths:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def save_app_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def configured_exports_dir() -> Path:
    settings = load_app_settings()
    raw = str(settings.get("export_root") or "").strip()
    if not raw:
        return EXPORTS_DIR.resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    return path.resolve()


def export_url(target_dir: Path, filename: str) -> str:
    return f"/exports/{target_dir.name}/{filename}"


def output_path_payload() -> dict:
    settings = load_app_settings()
    raw = str(settings.get("export_root") or "").strip()
    path = configured_exports_dir()
    default_path = EXPORTS_DIR.resolve()
    return {
        "path": str(path),
        "is_default": not raw or path == default_path,
        "default_path": str(default_path),
    }


def set_output_path(raw_path: str) -> dict:
    raw = str(raw_path or "").strip().strip("\"'")
    settings = load_app_settings()
    if not raw:
        settings.pop("export_root", None)
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        save_app_settings(settings)
        return output_path_payload()

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"output path is not a folder: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if path == EXPORTS_DIR.resolve():
        settings.pop("export_root", None)
        save_app_settings(settings)
        return output_path_payload()
    settings["export_root"] = str(path)
    save_app_settings(settings)
    return output_path_payload()


def choose_output_path() -> dict:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("folder picker is unavailable on this Python runtime") from exc

    current_path = configured_exports_dir()
    initial_dir = current_path if current_path.exists() else EXPORTS_DIR.resolve()
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="选择 Sprite Video Lab 输出路径",
            initialdir=str(initial_dir),
            mustexist=False,
        )
    finally:
        root.destroy()

    if not selected:
        payload = output_path_payload()
        payload["cancelled"] = True
        return payload

    payload = set_output_path(selected)
    payload["cancelled"] = False
    return payload


def configured_host(cli_host: str | None = None) -> str:
    value = str(cli_host or os.environ.get(HOST_ENV, DEFAULT_HOST)).strip()
    return value or DEFAULT_HOST


def configured_port(cli_port: int | None = None) -> int:
    if cli_port is not None:
        return cli_port
    raw = str(os.environ.get(PORT_ENV, DEFAULT_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def ffmpeg_fallback_root() -> Path | None:
    configured = str(os.environ.get(FFMPEG_DIR_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser()
    if DEFAULT_FFMPEG_FALLBACK_ROOT.exists():
        return DEFAULT_FFMPEG_FALLBACK_ROOT
    return None


def default_ai_model_cache_dir() -> Path:
    configured = str(os.environ.get(AI_MODEL_CACHE_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser()
    e_drive = Path("E:/")
    if e_drive.exists():
        return e_drive / "sprite-video-lab-models" / "huggingface"
    return WORK_DIR / "models" / "huggingface"


def default_corridorkey_root() -> Path:
    configured = str(os.environ.get(CORRIDORKEY_ROOT_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser()
    e_drive = Path("E:/")
    if e_drive.exists():
        return e_drive / "sprite-video-lab-models" / "CorridorKey"
    return WORK_DIR / "models" / "CorridorKey"


def configure_ai_model_cache() -> Path:
    cache_dir = default_ai_model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    hub_cache = cache_dir / "hub"
    hub_cache.mkdir(parents=True, exist_ok=True)
    modules_cache = cache_dir / "modules"
    modules_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_dir))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hub_cache))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_dir / "transformers"))
    os.environ.setdefault("HF_MODULES_CACHE", str(modules_cache))
    os.environ.setdefault("HF_XET_CACHE", str(cache_dir / "xet"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    return cache_dir


def clean_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(name).name).strip(".-")
    return cleaned or "video"


def repair_mojibake_text(value: str) -> str:
    repaired = value
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        repaired = repaired.replace(bad, good)
    return repaired


def repair_mojibake_path(path: Path) -> Path:
    return Path(repair_mojibake_text(str(path)))


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return cleaned or "item"


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def timestamped_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


def parse_hex_color(raw: str) -> tuple[int, int, int]:
    value = raw.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"invalid color: {raw}")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def safe_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def safe_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def normalize_ai_resolution(value) -> int:
    resolution = safe_int(value, DEFAULT_AI_MATTE_RESOLUTION)
    resolution = max(AI_MATTE_MIN_RESOLUTION, min(AI_MATTE_MAX_RESOLUTION, resolution))
    half_step = AI_MATTE_RESOLUTION_MULTIPLE // 2
    aligned = ((resolution + half_step) // AI_MATTE_RESOLUTION_MULTIPLE) * AI_MATTE_RESOLUTION_MULTIPLE
    return max(AI_MATTE_MIN_RESOLUTION, min(AI_MATTE_MAX_RESOLUTION, aligned))


def is_auto_ai_resolution(value) -> bool:
    return str(value or "").strip().lower() in {"", AI_MATTE_RESOLUTION_AUTO}


def auto_ai_resolution_for_image(image: Image.Image) -> int:
    width, height = image.size
    area_edge = math.sqrt(max(1, width) * max(1, height))
    target_edge = max(DEFAULT_AI_MATTE_RESOLUTION, area_edge)
    return normalize_ai_resolution(min(target_edge, AI_MATTE_MAX_RESOLUTION))


def resolve_ai_resolution(value, image: Image.Image) -> int:
    if is_auto_ai_resolution(value):
        return auto_ai_resolution_for_image(image)
    return normalize_ai_resolution(value)


def clamp_float(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return min(maximum, max(minimum, value))


def normalize_output_scale(value) -> float:
    return clamp_float(safe_float(value, 1.0), OUTPUT_SCALE_MIN, OUTPUT_SCALE_MAX)


def payload_has_value(payload: dict, key: str) -> bool:
    if key not in payload or payload.get(key) is None:
        return False
    return str(payload.get(key)).strip() != ""


def output_scale_from_payload(payload: dict, info: dict | None = None) -> float:
    if payload_has_value(payload, "output_scale"):
        return normalize_output_scale(payload.get("output_scale"))
    target_size = safe_int(payload.get("target_size"), 0)
    source_height = safe_int((info or {}).get("height"), 0)
    if target_size > 0 and source_height > 0:
        return normalize_output_scale(target_size / source_height)
    return 1.0


def output_scale_from_upload_payload(upload_id: str, payload: dict) -> float:
    if payload_has_value(payload, "output_scale"):
        return normalize_output_scale(payload.get("output_scale"))
    source_path, media_type = source_media_entry(upload_id)
    if media_type in {"image", "image_sequence"}:
        return 1.0
    if payload_has_value(payload, "target_size"):
        return output_scale_from_payload(payload, upload_media_info(upload_id, source_path, media_type))
    return 1.0


def target_size_from_source_height(source_height: int, output_scale: float) -> int:
    return max(8, round(max(1, source_height) * normalize_output_scale(output_scale)))


def normalize_matte_mode(raw: str, chroma_enabled: bool) -> str:
    raw_value = str(raw or "").strip().lower()
    compact_value = re.sub(r"\s+", "", raw_value)
    dash_aliases = {
        "birefnet-luma": "birefnet_luma_key",
        "birefnet-luma-key": "birefnet_luma_key",
        "birefnet-corridor": "birefnet_corridorkey_key",
        "birefnet-corridor-key": "birefnet_corridorkey_key",
        "birefnet-corridorkey": "birefnet_corridorkey_key",
        "birefnet-corridorkey-key": "birefnet_corridorkey_key",
    }
    if raw_value in dash_aliases or compact_value in dash_aliases:
        return dash_aliases.get(raw_value, dash_aliases[compact_value])
    value = raw_value.replace("-", "_")
    aliases = {
        "": "chroma" if chroma_enabled else "none",
        "off": "none",
        "disabled": "none",
        "no": "none",
        "key": "chroma",
        "color": "chroma",
        "green": "chroma",
        "green_screen": "chroma",
        "greenscreen": "chroma",
        "green_key": "chroma",
        "chroma_key": "chroma",
        "ai": "birefnet",
        "birefnet": "birefnet",
        "corridor": "corridorkey",
        "corridor_key": "corridorkey",
        "corridorkey": "corridorkey",
        "luma": "luma",
        "luma_key": "luma",
        "luminance": "luma",
        "birefnet_corridor": "birefnet_corridorkey",
        "birefnet_corridor_key": "birefnet_corridorkey",
        "birefnet_corridorkey": "birefnet_corridorkey",
        "birefnet+corridor": "birefnet_corridorkey",
        "birefnet+corridorkey": "birefnet_corridorkey",
        "birefnet_corridorkey_key": "birefnet_corridorkey_key",
        "birefnet_corridor_keyer": "birefnet_corridorkey_key",
        "birefnet_corridorkey_keyer": "birefnet_corridorkey_key",
        "birefnet_luma": "birefnet_luma",
        "birefnet+luma": "birefnet_luma",
        "birefnet_luma_key": "birefnet_luma_key",
        "birefnet_luma_keyer": "birefnet_luma_key",
        "birefnet_luma_corridorkey": "birefnet_luma_corridorkey",
        "birefnet_luma_corridor": "birefnet_luma_corridorkey",
        "birefnet_luma_corridor_key": "birefnet_luma_corridorkey",
        "birefnet_corridorkey_luma": "birefnet_luma_corridorkey",
        "birefnet_corridor_luma": "birefnet_luma_corridorkey",
        "birefnet+luma+corridor": "birefnet_luma_corridorkey",
        "birefnet+luma+corridorkey": "birefnet_luma_corridorkey",
        "birefnet+corridor+luma": "birefnet_luma_corridorkey",
        "birefnet+corridorkey+luma": "birefnet_luma_corridorkey",
        "ai_luma": "birefnet_luma",
        "ai_glow": "birefnet_luma",
    }
    mode = aliases.get(value, value)
    return mode if mode in AI_MATTE_MODES else ("chroma" if chroma_enabled else "none")


def normalize_ai_model_key(raw: str) -> str:
    value = str(raw or DEFAULT_AI_MATTE_MODEL).strip().lower()
    aliases = {
        "hr": "birefnet-hr-matting",
        "hr-matting": "birefnet-hr-matting",
        "matting": "birefnet-hr-matting",
        "lite": "birefnet-lite-2k",
        "lite-2k": "birefnet-lite-2k",
        "2k": "birefnet-lite-2k",
        "general": "birefnet-general",
        "default": "birefnet-general",
    }
    value = aliases.get(value, value)
    return value if value in AI_MATTE_MODEL_REPOS else DEFAULT_AI_MATTE_MODEL


def normalize_ai_device(raw: str) -> str:
    value = str(raw or "auto").strip().lower()
    return AI_MATTE_DEVICE_ALIASES.get(value, "auto")


def normalize_corridorkey_screen(raw: str) -> str:
    value = str(raw or "auto").strip().lower()
    return value if value in CORRIDORKEY_SCREEN_COLORS else "auto"


def normalize_canvas_mode(raw: str) -> str:
    value = str(raw or "auto").strip().lower().replace("-", "_")
    aliases = {
        "": "auto",
        "auto_width": "auto",
        "auto_center": "auto",
        "rect": "auto",
        "rectangle": "auto",
        "center": "square_center",
        "square": "square_bottom",
        "bottom": "square_bottom",
    }
    value = aliases.get(value, value)
    return value if value in CANVAS_MODES else "auto"


def resolve_corridorkey_screen(raw: str, key_rgb: tuple[int, int, int]) -> str:
    normalized = normalize_corridorkey_screen(raw)
    if normalized != "auto":
        return normalized
    return "blue" if key_rgb[2] > key_rgb[1] and key_rgb[2] >= key_rgb[0] else "green"


def resolve_ffmpeg_binary(name: str) -> str:
    direct = shutil.which(name)
    if direct:
        return direct
    fallback_root = ffmpeg_fallback_root()
    if fallback_root is not None:
        candidate = fallback_root / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(f"could not resolve {name}")


def run_process(args: list[str]) -> str:
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"command failed: {' '.join(args)}")
    return completed.stdout


def configured_ffmpeg_accel_mode() -> str:
    raw = str(os.environ.get(FFMPEG_ACCEL_ENV, "auto") or "auto").strip().lower()
    return FFMPEG_ACCEL_ALIASES.get(raw, "auto")


def available_ffmpeg_hwaccels() -> set[str]:
    global _FFMPEG_HWACCELS_CACHE
    if _FFMPEG_HWACCELS_CACHE is not None:
        return _FFMPEG_HWACCELS_CACHE

    ffmpeg = resolve_ffmpeg_binary("ffmpeg")
    try:
        output = run_process([ffmpeg, "-hide_banner", "-hwaccels"])
    except Exception:
        _FFMPEG_HWACCELS_CACHE = set()
        return _FFMPEG_HWACCELS_CACHE

    available: set[str] = set()
    for line in output.splitlines():
        value = line.strip().lower()
        if not value or value.endswith(":"):
            continue
        if re.fullmatch(r"[a-z0-9_]+", value):
            available.add(value)
    _FFMPEG_HWACCELS_CACHE = available
    return _FFMPEG_HWACCELS_CACHE


def preferred_ffmpeg_hwaccel() -> tuple[str, str | None]:
    requested = configured_ffmpeg_accel_mode()
    if requested == "cpu":
        return requested, None

    available = available_ffmpeg_hwaccels()
    if requested == "auto":
        for candidate in FFMPEG_ACCEL_PRIORITY:
            if candidate in available:
                return requested, candidate
        return requested, None

    if requested in available:
        return requested, requested
    return requested, None


def ffmpeg_accel_label(mode: str) -> str:
    return "CPU" if mode == "cpu" else f"GPU ({mode})"


def ffmpeg_accel_payload(
    requested_mode: str,
    selected_mode: str | None,
    used_mode: str,
    fallback_reason: str | None = None,
) -> dict:
    return {
        "requested_mode": requested_mode,
        "selected_mode": selected_mode,
        "used_mode": used_mode,
        "used_label": ffmpeg_accel_label(used_mode),
        "fallback_to_cpu": bool(selected_mode and used_mode == "cpu"),
        "fallback_reason": fallback_reason or "",
    }


def static_image_payload() -> dict:
    return {
        "requested_mode": "image",
        "selected_mode": "",
        "used_mode": "image",
        "used_label": "Static image",
        "fallback_to_cpu": False,
        "fallback_reason": "",
    }


def custom_animation_payload() -> dict:
    return {
        "requested_mode": "animation",
        "selected_mode": "",
        "used_mode": "animation",
        "used_label": "Custom animation frames",
        "fallback_to_cpu": False,
        "fallback_reason": "",
    }


def image_sequence_payload() -> dict:
    return {
        "requested_mode": "image_sequence",
        "selected_mode": "",
        "used_mode": "image_sequence",
        "used_label": "Image sequence",
        "fallback_to_cpu": False,
        "fallback_reason": "",
    }


def run_ffmpeg_with_auto_accel(args_builder) -> dict:
    requested_mode, selected_mode = preferred_ffmpeg_hwaccel()
    if selected_mode:
        try:
            run_process(args_builder(selected_mode))
            return ffmpeg_accel_payload(requested_mode, selected_mode, selected_mode)
        except RuntimeError as exc:
            detail = str(exc).strip()
            print(
                f"[ffmpeg] {selected_mode} decode failed, falling back to CPU: {detail}",
                file=sys.stderr,
            )
            run_process(args_builder(None))
            return ffmpeg_accel_payload(
                requested_mode,
                selected_mode,
                "cpu",
                fallback_reason=detail,
            )

    run_process(args_builder(None))
    return ffmpeg_accel_payload(requested_mode, None, "cpu")


def extract_image_frame(source_path: Path, output_path: Path) -> tuple[Path, dict]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = open_rgba_image(source_path)
    image.save(output_path)
    image.close()
    return output_path, static_image_payload()


def is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def open_rgba_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA")


def watch_targets() -> list[Path]:
    targets = [ROOT_DIR / "server.py"]
    if APP_DIR.exists():
        targets.extend(path for path in APP_DIR.rglob("*") if path.is_file())
    return sorted(set(path.resolve() for path in targets))


def current_app_version() -> str:
    mtimes = [str(path.stat().st_mtime_ns) for path in watch_targets() if path.exists()]
    if not mtimes:
        return "0"
    return max(mtimes)


def runtime_info() -> dict:
    torch_info = {"installed": False, "version": "", "cuda_available": False, "error": ""}
    try:
        import torch

        torch_info = {
            "installed": True,
            "version": str(getattr(torch, "__version__", "")),
            "cuda_available": bool(torch.cuda.is_available()),
            "error": "",
        }
    except Exception as exc:
        torch_info["error"] = str(exc)

    return {
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
        "work_dir": str(WORK_DIR),
        "torch": torch_info,
        "ai_model_cache": str(default_ai_model_cache_dir()),
        "corridorkey_root": str(default_corridorkey_root()),
    }


def watch_snapshot() -> dict[str, int]:
    snapshot: dict[str, int] = {}
    for path in watch_targets():
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except FileNotFoundError:
            continue
    return snapshot


def open_path_in_file_browser(target: Path) -> None:
    resolved = target.resolve()
    if sys.platform.startswith("win"):
        os.startfile(str(resolved))
        return
    if sys.platform == "darwin":
        subprocess.run(["open", str(resolved)], check=True)
        return
    subprocess.run(["xdg-open", str(resolved)], check=True)


def enforce_hard_alpha(image: Image.Image, cutoff: int = 128) -> Image.Image:
    rgba = image.convert("RGBA")
    hardened_pixels: list[tuple[int, int, int, int]] = []
    for r_value, g_value, b_value, alpha in rgba.getdata():
        if alpha >= cutoff:
            hardened_pixels.append((r_value, g_value, b_value, 255))
        else:
            hardened_pixels.append((0, 0, 0, 0))
    hardened = Image.new("RGBA", rgba.size)
    hardened.putdata(hardened_pixels)
    return hardened


def resize_rgba_with_premultiplied_alpha(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    rgba = image.convert("RGBA")
    red, green, blue, alpha = rgba.split()
    premultiplied_red = ImageChops.multiply(red, alpha)
    premultiplied_green = ImageChops.multiply(green, alpha)
    premultiplied_blue = ImageChops.multiply(blue, alpha)

    resized_alpha = alpha.resize(size, LANCZOS)
    resized_red = premultiplied_red.resize(size, LANCZOS)
    resized_green = premultiplied_green.resize(size, LANCZOS)
    resized_blue = premultiplied_blue.resize(size, LANCZOS)

    pixels: list[tuple[int, int, int, int]] = []
    for r_value, g_value, b_value, alpha_value in zip(
        resized_red.getdata(),
        resized_green.getdata(),
        resized_blue.getdata(),
        resized_alpha.getdata(),
    ):
        if alpha_value <= 0:
            pixels.append((0, 0, 0, 0))
            continue
        pixels.append(
            (
                min(255, int((r_value * 255 + (alpha_value // 2)) / alpha_value)),
                min(255, int((g_value * 255 + (alpha_value // 2)) / alpha_value)),
                min(255, int((b_value * 255 + (alpha_value // 2)) / alpha_value)),
                alpha_value,
            )
        )

    resized = Image.new("RGBA", size)
    resized.putdata(pixels)
    return resized


def ffprobe_json(path: Path) -> dict:
    ffprobe = resolve_ffmpeg_binary("ffprobe")
    output = run_process(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
    )
    return json.loads(output)


def parse_frame_rate(raw: str) -> float:
    if not raw or raw == "0/0":
        return 0.0
    try:
        return float(Fraction(raw))
    except Exception:
        return 0.0


def video_info(path: Path) -> dict:
    payload = ffprobe_json(path)
    streams = payload.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    width = safe_int(video_stream.get("width"), 0)
    height = safe_int(video_stream.get("height"), 0)
    fps = parse_frame_rate(str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/0"))
    duration = safe_float((payload.get("format") or {}).get("duration"), 0.0)
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "codec": str(video_stream.get("codec_name") or ""),
    }


def image_info(path: Path) -> dict:
    with Image.open(path) as image:
        width, height = image.size
        codec = str((image.format or path.suffix.removeprefix(".") or "image")).lower()
    return {
        "width": width,
        "height": height,
        "fps": 0.0,
        "duration": 0.0,
        "codec": codec,
    }


def content_type_extension(content_type: str | None) -> str:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    return CONTENT_TYPE_EXTENSIONS.get(normalized, "")


def sniff_media_extension(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    with path.open("rb") as handle:
        head = handle.read(64)
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return ".mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"BM"):
        return ".bmp"
    if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif"
    return ""


def detect_media_type(path: Path, content_type: str | None = None) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in IMAGE_EXTENSIONS:
        return "image"

    content_extension = content_type_extension(content_type)
    if content_extension in VIDEO_EXTENSIONS:
        return "video"
    if content_extension in IMAGE_EXTENSIONS:
        return "image"

    sniffed_extension = sniff_media_extension(path)
    if sniffed_extension in VIDEO_EXTENSIONS:
        return "video"
    if sniffed_extension in IMAGE_EXTENSIONS:
        return "image"

    if path.exists() and path.is_file():
        try:
            with Image.open(path):
                return "image"
        except Exception:
            pass
        try:
            ffprobe_json(path)
            return "video"
        except Exception:
            pass

    detail = path.suffix or content_type or path.name
    raise ValueError(f"unsupported media type: {detail}")


def preferred_media_extension(path: Path, media_type: str, content_type: str | None = None) -> str:
    suffix = path.suffix.lower()
    allowed = VIDEO_EXTENSIONS if media_type == "video" else IMAGE_EXTENSIONS
    if suffix in allowed:
        return suffix
    content_extension = content_type_extension(content_type)
    if content_extension in allowed:
        return content_extension
    sniffed_extension = sniff_media_extension(path)
    if sniffed_extension in allowed:
        return sniffed_extension
    return ".mp4" if media_type == "video" else ".png"


def media_info(path: Path, media_type: str | None = None) -> dict:
    resolved_type = media_type or detect_media_type(path)
    payload = video_info(path) if resolved_type == "video" else image_info(path)
    payload["media_type"] = resolved_type
    return payload


def upload_dir(upload_id: str) -> Path:
    return UPLOADS_DIR / upload_id


def upload_manifest_path(upload_id: str) -> Path:
    return upload_dir(upload_id) / "manifest.json"


def load_upload_manifest(upload_id: str) -> dict:
    path = upload_manifest_path(upload_id)
    if not path.exists():
        raise FileNotFoundError(f"upload not found: {upload_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_upload_manifest(upload_id: str, payload: dict) -> None:
    path = upload_manifest_path(upload_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def source_media_entry(upload_id: str) -> tuple[Path, str]:
    manifest = load_upload_manifest(upload_id)
    path = repair_mojibake_path(Path(manifest["source_path"]))
    if not path.exists():
        raise FileNotFoundError(f"source missing: {path}")
    media_type = str(manifest.get("media_type") or detect_media_type(path))
    return path, media_type


def source_frame_entries(upload_id: str) -> list[dict]:
    manifest = load_upload_manifest(upload_id)
    entries = manifest.get("source_frames") or []
    if entries:
        result = []
        for index, entry in enumerate(entries):
            path = repair_mojibake_path(Path(str(entry.get("path") or "")))
            if not path.exists():
                raise FileNotFoundError(f"source frame missing: {path}")
            result.append(
                {
                    "path": path,
                    "name": str(entry.get("name") or path.name),
                    "index": index,
                }
            )
        return result

    path, _ = source_media_entry(upload_id)
    return [{"path": path, "name": path.name, "index": 0}]


def image_sequence_info(entries: list[dict]) -> dict:
    max_width = 0
    max_height = 0
    total_bytes = 0
    for entry in entries:
        path = Path(entry["path"])
        with Image.open(path) as image:
            width, height = image.size
        max_width = max(max_width, width)
        max_height = max(max_height, height)
        total_bytes += path.stat().st_size
    return {
        "width": max_width,
        "height": max_height,
        "fps": 0.0,
        "duration": 0.0,
        "codec": "image-sequence",
        "frame_count": len(entries),
        "bytes": total_bytes,
        "media_type": "image_sequence",
    }


def upload_media_info(upload_id: str, source_path: Path, media_type: str) -> dict:
    if media_type == "image_sequence":
        return image_sequence_info(source_frame_entries(upload_id))
    return media_info(source_path, media_type)


def source_video_path(upload_id: str) -> Path:
    manifest = load_upload_manifest(upload_id)
    preview_path = str(manifest.get("preview_path") or "").strip()
    if preview_path:
        path = repair_mojibake_path(Path(preview_path))
        if path.exists():
            return path
    path, _ = source_media_entry(upload_id)
    return path


def build_upload_payload(
    upload_id: str,
    source_path: Path,
    display_name: str,
    media_type: str,
    preview_path: Path | None = None,
    media_info_payload: dict | None = None,
) -> dict:
    info_path = preview_path if preview_path and preview_path.exists() and media_type == "video" else source_path
    info = media_info_payload or media_info(info_path, media_type)
    info["media_type"] = media_type
    return {
        "upload_id": upload_id,
        "display_name": display_name,
        "media_url": f"/media/upload/{upload_id}",
        "video_url": f"/media/upload/{upload_id}",
        "source_path": str(source_path),
        "preview_path": str(preview_path) if preview_path else "",
        "media_type": media_type,
        "video_info": info,
        "media_info": info,
    }


def is_gif_source(path: Path) -> bool:
    if path.suffix.lower() == ".gif":
        return True
    try:
        return sniff_media_extension(path) == ".gif"
    except Exception:
        return False


def create_gif_video_preview(source_path: Path, output_path: Path) -> Path:
    ffmpeg = resolve_ffmpeg_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")
    if temp_path.exists():
        temp_path.unlink()
    if output_path.exists():
        output_path.unlink()
    run_process(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source_path),
            "-vf",
            "scale=ceil(iw/2)*2:ceil(ih/2)*2",
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            str(temp_path),
        ]
    )
    if not temp_path.exists() or temp_path.stat().st_size <= 0:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError("failed to create GIF video preview")
    temp_path.replace(output_path)
    return output_path


def register_video_from_path(source_path: Path) -> dict:
    source_path = repair_mojibake_path(source_path).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"file not found: {source_path}")
    media_type = detect_media_type(source_path)

    upload_id = timestamped_id()
    preview_path: Path | None = None
    if media_type == "video" and is_gif_source(source_path):
        preview_path = create_gif_video_preview(source_path, upload_dir(upload_id) / "preview.mp4")
    manifest = {
        "upload_id": upload_id,
        "source_path": str(source_path),
        "preview_path": str(preview_path) if preview_path else "",
        "display_name": source_path.name,
        "media_type": media_type,
        "created_at": iso_now(),
    }
    save_upload_manifest(upload_id, manifest)
    return build_upload_payload(upload_id, source_path, source_path.name, media_type, preview_path)


def register_uploaded_file(file_item) -> dict:
    filename = clean_filename(file_item.filename or "media")
    content_type = str(getattr(file_item, "type", "") or "")
    upload_id = timestamped_id()
    target_dir = upload_dir(upload_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    with target_path.open("wb") as handle:
        shutil.copyfileobj(file_item.file, handle)
    media_type = detect_media_type(target_path, content_type)
    preferred_extension = preferred_media_extension(target_path, media_type, content_type)
    if target_path.suffix.lower() not in (VIDEO_EXTENSIONS | IMAGE_EXTENSIONS):
        renamed_path = target_path.with_name(f"{target_path.name}{preferred_extension}")
        target_path.rename(renamed_path)
        target_path = renamed_path
        filename = target_path.name
    preview_path: Path | None = None
    if media_type == "video" and is_gif_source(target_path):
        preview_path = create_gif_video_preview(target_path, target_dir / "preview.mp4")
    manifest = {
        "upload_id": upload_id,
        "source_path": str(target_path),
        "preview_path": str(preview_path) if preview_path else "",
        "display_name": filename,
        "media_type": media_type,
        "created_at": iso_now(),
    }
    save_upload_manifest(upload_id, manifest)
    return build_upload_payload(upload_id, target_path, filename, media_type, preview_path)


def register_uploaded_image_sequence(file_items: list) -> dict:
    candidates = []
    for item in file_items:
        raw_filename = str(getattr(item, "filename", "") or "frame")
        display_name = Path(raw_filename.replace("\\", "/")).name or "frame"
        if not getattr(item, "file", None):
            continue
        suffix = Path(display_name).suffix.lower()
        content_type = str(getattr(item, "type", "") or "")
        if suffix not in IMAGE_EXTENSIONS and not content_type.startswith("image/"):
            raise ValueError("multiple-file import only supports image sequences")
        candidates.append((raw_filename, display_name, item))

    candidates.sort(key=lambda pair: natural_sort_key(pair[0]))
    if len(candidates) < 2:
        raise ValueError("image sequence import needs at least 2 image files")

    upload_id = timestamped_id()
    target_dir = upload_dir(upload_id)
    frames_dir = target_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    source_frames: list[dict] = []
    max_width = 0
    max_height = 0
    total_bytes = 0
    first_path: Path | None = None
    for index, (raw_filename, display_name, item) in enumerate(candidates):
        suffix = Path(display_name).suffix.lower()
        content_type = str(getattr(item, "type", "") or "")
        extension = suffix if suffix in IMAGE_EXTENSIONS else content_type_extension(content_type) or ".png"
        frame_name = f"frame_{index + 1:05d}{extension}"
        target_path = frames_dir / frame_name
        with target_path.open("wb") as handle:
            shutil.copyfileobj(item.file, handle)
        with Image.open(target_path) as image:
            width, height = image.size
            max_width = max(max_width, width)
            max_height = max(max_height, height)
        total_bytes += target_path.stat().st_size
        if first_path is None:
            first_path = target_path
        source_frames.append(
            {
                "index": index,
                "name": display_name,
                "raw_name": raw_filename,
                "path": str(target_path),
                "width": width,
                "height": height,
            }
        )

    if first_path is None:
        raise ValueError("no supported image frames found")

    display_name = f"{len(source_frames)} images ({source_frames[0]['name']} ... {source_frames[-1]['name']})"
    info = {
        "width": max_width,
        "height": max_height,
        "fps": 0.0,
        "duration": 0.0,
        "codec": "image-sequence",
        "frame_count": len(source_frames),
        "bytes": total_bytes,
        "media_type": "image_sequence",
    }
    manifest = {
        "source_path": str(first_path),
        "preview_path": "",
        "display_name": display_name,
        "media_type": "image_sequence",
        "source_frames": source_frames,
        "created_at": iso_now(),
    }
    save_upload_manifest(upload_id, manifest)
    return build_upload_payload(upload_id, first_path, display_name, "image_sequence", media_info_payload=info)


def register_uploaded_media(file_items: list) -> dict:
    items = [item for item in file_items if getattr(item, "file", None)]
    if not items:
        raise ValueError("media file missing")
    if len(items) == 1:
        return register_uploaded_file(items[0])
    return register_uploaded_image_sequence(items)


def auto_key_color(image: Image.Image) -> tuple[int, int, int]:
    rgba = image.convert("RGBA")
    chroma_color, chroma_ratio = dominant_chroma_screen_color(rgba)
    if chroma_color is not None and chroma_ratio >= 0.18:
        return chroma_color
    color, _ratio = dominant_border_key_color(rgba)
    return color


def dominant_chroma_screen_color(image: Image.Image) -> tuple[tuple[int, int, int] | None, float]:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    if width <= 0 or height <= 0:
        return None, 0.0

    pixel_count = width * height
    step = max(1, round(math.sqrt(pixel_count / 20000)))
    totals = {
        "green": [0, 0, 0, 0],
        "blue": [0, 0, 0, 0],
    }
    sampled = 0
    for y in range(0, height, step):
        for x in range(0, width, step):
            r_value, g_value, b_value, alpha = rgba.getpixel((x, y))
            if alpha < 8:
                continue
            sampled += 1
            if g_value >= 120 and g_value - max(r_value, b_value) >= 50:
                entry = totals["green"]
            elif b_value >= 120 and b_value - max(r_value, g_value) >= 50:
                entry = totals["blue"]
            else:
                continue
            entry[0] += 1
            entry[1] += r_value
            entry[2] += g_value
            entry[3] += b_value

    if sampled <= 0:
        return None, 0.0
    best = max(totals.values(), key=lambda entry: entry[0])
    count, r_total, g_total, b_total = best
    if count <= 0:
        return None, 0.0
    color = (
        round(r_total / count),
        round(g_total / count),
        round(b_total / count),
    )
    return color, count / sampled


def dominant_border_key_color(image: Image.Image) -> tuple[tuple[int, int, int], float]:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    if width <= 0 or height <= 0:
        return (0, 255, 0), 0.0

    step = max(1, min(width, height) // 128)
    coords: set[tuple[int, int]] = set()
    for x in range(0, width, step):
        coords.add((x, 0))
        coords.add((x, height - 1))
    for y in range(0, height, step):
        coords.add((0, y))
        coords.add((width - 1, y))
    coords.update({(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)})

    buckets: dict[tuple[int, int, int], list[int]] = {}
    total = 0
    for x, y in coords:
        r_value, g_value, b_value, alpha = rgba.getpixel((x, y))
        if alpha < 8:
            continue
        bucket = (r_value // 16, g_value // 16, b_value // 16)
        entry = buckets.setdefault(bucket, [0, 0, 0, 0])
        entry[0] += 1
        entry[1] += r_value
        entry[2] += g_value
        entry[3] += b_value
        total += 1

    if total <= 0 or not buckets:
        return (0, 255, 0), 0.0

    def screen_color_priority(entry: list[int]) -> int:
        count, r_total, g_total, b_total = entry
        color = (
            round(r_total / count),
            round(g_total / count),
            round(b_total / count),
        )
        high = max(color)
        low = min(color)
        spread = high - low
        if low >= 224 and spread <= 48:
            return 4
        if high <= 40 and spread <= 40:
            return 3
        if color[1] >= 96 and color[1] - max(color[0], color[2]) >= 48:
            return 2
        if color[2] >= 96 and color[2] - max(color[0], color[1]) >= 48:
            return 2
        return 0

    screen_candidates = [
        item for item in buckets.items()
        if (item[1][0] / total) >= 0.12 and screen_color_priority(item[1]) > 0
    ]
    if screen_candidates:
        _bucket, best = max(screen_candidates, key=lambda item: (screen_color_priority(item[1]), item[1][0]))
    else:
        _bucket, best = max(buckets.items(), key=lambda item: item[1][0])
    count, r_total, g_total, b_total = best
    color = (
        round(r_total / count),
        round(g_total / count),
        round(b_total / count),
    )
    return color, count / total


def chroma_key_frame(
    image: Image.Image,
    key_rgb: tuple[int, int, int],
    threshold: int,
    softness: int,
    despill_strength: float,
    halo_pixels: int,
) -> Image.Image:
    rgba = image.convert("RGBA")
    output_pixels: list[tuple[int, int, int, int]] = []
    k_r, k_g, k_b = key_rgb
    if softness <= 0:
        max_distance = max(threshold, 1)
    else:
        max_distance = threshold + softness

    for r_value, g_value, b_value, _ in rgba.getdata():
        dist = math.sqrt(
            (r_value - k_r) ** 2
            + (g_value - k_g) ** 2
            + (b_value - k_b) ** 2
        )
        if dist <= threshold:
            alpha = 0
        elif softness <= 0 or dist >= max_distance:
            alpha = 255
        else:
            alpha = int(((dist - threshold) / softness) * 255)

        max_rb = max(r_value, b_value)
        spill = max(0, g_value - max_rb)
        closeness = max(0.0, 1.0 - min(dist / max_distance, 1.0))
        reduction = int(spill * despill_strength * max(closeness, 1.0 - (alpha / 255.0)))
        output_pixels.append(
            (
                r_value,
                max(0, g_value - reduction),
                b_value,
                alpha,
            )
        )

    keyed = Image.new("RGBA", rgba.size)
    keyed.putdata(output_pixels)

    if halo_pixels > 0:
        alpha_channel = keyed.getchannel("A")
        filter_size = (halo_pixels * 2) + 1
        eroded = alpha_channel.filter(ImageFilter.MinFilter(filter_size))
        keyed.putalpha(eroded)

    return keyed


def import_ai_matte_dependencies():
    configure_ai_model_cache()
    try:
        import torch
        from torchvision import transforms
        from transformers import AutoModelForImageSegmentation
    except ModuleNotFoundError as exc:
        missing_name = getattr(exc, "name", "AI matting dependency")
        raise RuntimeError(
            f"{missing_name} is not installed. Run: python -m pip install -r requirements-ai.txt"
        ) from exc
    return torch, transforms, AutoModelForImageSegmentation


def resolve_ai_runtime_device(torch_module, requested_device: str) -> str:
    requested = normalize_ai_device(requested_device)
    cuda_available = bool(torch_module.cuda.is_available())
    if requested == "cuda" and not cuda_available:
        raise RuntimeError("CUDA was requested for BiRefNet, but torch cannot see an NVIDIA GPU.")
    if requested == "cuda":
        return "cuda"
    if requested == "cpu":
        return "cpu"
    return "cuda" if cuda_available else "cpu"


def load_birefnet_model(model_key: str, requested_device: str):
    torch_module, _transforms, auto_model = import_ai_matte_dependencies()
    normalized_model_key = normalize_ai_model_key(model_key)
    repo_id = AI_MATTE_MODEL_REPOS[normalized_model_key]
    device = resolve_ai_runtime_device(torch_module, requested_device)
    cache_key = (repo_id, device)
    if cache_key in _BIREFNET_MODEL_CACHE:
        return _BIREFNET_MODEL_CACHE[cache_key], device, normalized_model_key, repo_id

    if hasattr(torch_module, "set_float32_matmul_precision"):
        try:
            torch_module.set_float32_matmul_precision("high")
        except Exception:
            pass

    cache_dir = configure_ai_model_cache()
    model = auto_model.from_pretrained(repo_id, trust_remote_code=True, cache_dir=str(cache_dir))
    model.to(device)
    model.eval()
    _BIREFNET_MODEL_CACHE[cache_key] = model
    return model, device, normalized_model_key, repo_id


def import_corridorkey_dependencies():
    configure_ai_model_cache()
    root = default_corridorkey_root()
    module_dir = root / "CorridorKeyModule"
    if not module_dir.exists():
        raise RuntimeError(
            f"CorridorKey is not installed at {root}. Run setup_ai_runtime.bat or clone {CORRIDORKEY_REPO_URL}."
        )

    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    os.environ.setdefault("CORRIDORKEY_SKIP_COMPILE", "1")

    try:
        import importlib
        import numpy as np
        import torch
    except ModuleNotFoundError as exc:
        missing_name = getattr(exc, "name", "CorridorKey dependency")
        raise RuntimeError(
            f"{missing_name} is not installed. Run: python -m pip install -r requirements-ai.txt"
        ) from exc

    try:
        corridor_backend = importlib.import_module("CorridorKeyModule.backend")
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"CorridorKey could not be imported from {root}.") from exc

    try:
        corridor_inference = importlib.import_module("CorridorKeyModule.inference_engine")
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"CorridorKey inference engine could not be imported from {root}.") from exc

    patch_corridorkey_gpu_despeckle(corridor_inference, torch)

    checkpoint_dir = module_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    corridor_backend.CHECKPOINT_DIR = str(checkpoint_dir)
    return np, torch, corridor_backend, root


def patch_corridorkey_gpu_despeckle(corridor_inference, torch_module) -> None:
    try:
        color_utils = corridor_inference.cu
        transforms_functional = corridor_inference.TF
    except Exception:
        return
    if getattr(color_utils.clean_matte_torch, "_sprite_video_lab_safe", False):
        return

    original_clean_matte_torch = color_utils.clean_matte_torch
    functional = torch_module.nn.functional

    def safe_clean_matte_torch(alpha, area_threshold: int, dilation: int = 15, blur_size: int = 5):
        _batch, _channels, height, width = alpha.shape
        if (height * width) <= CORRIDORKEY_GPU_DESPECKLE_PIXEL_LIMIT:
            return original_clean_matte_torch(alpha, area_threshold, dilation=dilation, blur_size=blur_size)

        mask = (alpha > 0.25).to(dtype=alpha.dtype)
        if area_threshold > 0:
            opening_radius = max(1, min(4, area_threshold // 100))
            kernel_size = (opening_radius * 2) + 1
            for _ in range(2):
                mask = -functional.max_pool2d(-mask, kernel_size, stride=1, padding=opening_radius)
                mask = functional.max_pool2d(mask, kernel_size, stride=1, padding=opening_radius)
        if dilation > 0:
            repeats = max(1, dilation // 2)
            for _ in range(repeats):
                mask = functional.max_pool2d(mask, 5, stride=1, padding=2)
        if blur_size > 0:
            kernel_size = int(blur_size * 2 + 1)
            mask = transforms_functional.gaussian_blur(mask, [kernel_size, kernel_size])
        return alpha * mask

    safe_clean_matte_torch._sprite_video_lab_safe = True
    color_utils.clean_matte_torch = safe_clean_matte_torch


def load_corridorkey_engine(requested_device: str, screen_color: str):
    _np, torch_module, corridor_backend, root = import_corridorkey_dependencies()
    device = resolve_ai_runtime_device(torch_module, requested_device)
    cache_key = (device, screen_color)
    if cache_key in _CORRIDORKEY_ENGINE_CACHE:
        return _CORRIDORKEY_ENGINE_CACHE[cache_key], device, root

    engine = corridor_backend.create_engine(
        backend="torch",
        device=device,
        img_size=CORRIDORKEY_IMG_SIZE,
        screen_color=screen_color,
    )
    _CORRIDORKEY_ENGINE_CACHE[cache_key] = engine
    return engine, device, root


def linear_to_srgb_array(values):
    import numpy as np

    clipped = np.clip(values, 0.0, None)
    return np.where(clipped <= 0.0031308, clipped * 12.92, 1.055 * np.power(clipped, 1.0 / 2.4) - 0.055)


def corridorkey_processed_to_image(processed) -> Image.Image:
    import numpy as np

    alpha = np.clip(processed[..., 3:4], 0.0, 1.0)
    premul_rgb = np.clip(processed[..., :3], 0.0, None)
    straight_linear = np.zeros_like(premul_rgb)
    np.divide(premul_rgb, np.maximum(alpha, 1e-6), out=straight_linear, where=alpha > 1e-6)
    straight_srgb = linear_to_srgb_array(straight_linear)
    rgba = np.concatenate([straight_srgb, alpha], axis=-1)
    rgba_u8 = (np.clip(rgba, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(rgba_u8, "RGBA")


def corridorkey_auto_despeckle_on_gpu(image: Image.Image) -> bool:
    return True


def corridorkey_postprocess_on_gpu(device: str) -> bool:
    return str(device).startswith("cuda")


def corridorkey_process_arrays(
    engine,
    rgb,
    mask,
    screen_channel: int,
    despill_strength: float,
    post_process_on_gpu: bool,
    auto_despeckle: bool,
):
    result = engine.process_frame(
        rgb,
        mask,
        input_is_linear=False,
        fg_is_straight=True,
        despill_strength=max(0.0, min(1.0, float(despill_strength or 0.0))),
        auto_despeckle=auto_despeckle,
        despeckle_size=400,
        generate_comp=False,
        post_process_on_gpu=post_process_on_gpu,
        screen_channel=screen_channel,
    )
    return result


def corridorkey_alpha_to_image(alpha) -> Image.Image:
    import numpy as np

    alpha_array = np.asarray(alpha)
    if alpha_array.ndim == 3:
        alpha_array = alpha_array[..., 0]
    alpha_u8 = (np.clip(alpha_array, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(alpha_u8, "L")


def corridorkey_refine_frame(
    image: Image.Image,
    alpha_mask: Image.Image,
    requested_device: str,
    screen_color: str,
    despill_strength: float,
) -> tuple[Image.Image, dict]:
    import numpy as np

    engine, device, root = load_corridorkey_engine(requested_device, screen_color)
    screen_channel = 2 if screen_color == "blue" else 1
    post_process_on_gpu = corridorkey_postprocess_on_gpu(device)
    auto_despeckle = not post_process_on_gpu or corridorkey_auto_despeckle_on_gpu(image)
    uses_safe_despeckle = post_process_on_gpu and (image.size[0] * image.size[1]) > CORRIDORKEY_GPU_DESPECKLE_PIXEL_LIMIT
    rgb = np.array(image.convert("RGB"), dtype=np.uint8, copy=True)
    mask = np.array(alpha_mask.convert("L"), dtype=np.uint8, copy=True)
    result = corridorkey_process_arrays(
        engine,
        rgb,
        mask,
        screen_channel,
        despill_strength,
        post_process_on_gpu,
        auto_despeckle,
    )
    alpha = corridorkey_alpha_to_image(result["processed"][..., 3:4])
    refined = apply_alpha_mask(image, alpha)
    refined = despill_alpha_edges(refined, auto_key_color(image), despill_strength)

    info = {
        "corridorkey_enabled": True,
        "corridorkey_color_source": "original",
        "corridorkey_screen_color": screen_color,
        "corridorkey_device": device,
        "corridorkey_resolution": CORRIDORKEY_IMG_SIZE,
        "corridorkey_post_process": "gpu" if post_process_on_gpu else "cpu",
        "corridorkey_auto_despeckle": auto_despeckle,
        "corridorkey_safe_despeckle": uses_safe_despeckle,
        "corridorkey_tiled": False,
        "corridorkey_root": str(root),
    }
    return refined, info


def resize_birefnet_input(image: Image.Image, size: int) -> Image.Image:
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width <= 0 or height <= 0:
        raise ValueError("invalid image size for BiRefNet inference")
    return rgb.resize((size, size), LANCZOS)


def birefnet_mask_score(mask: Image.Image) -> dict:
    alpha = mask.convert("L")
    total = max(1, alpha.size[0] * alpha.size[1])
    histogram = alpha.histogram()
    strong_pixels = sum(histogram[160:256])
    visible_pixels = total - histogram[0]
    max_alpha = max(index for index, count in enumerate(histogram) if count)
    mean_alpha = sum(index * count for index, count in enumerate(histogram)) / total
    return {
        "max_alpha": max_alpha,
        "mean_alpha": mean_alpha,
        "visible_pixels": visible_pixels,
        "strong_pixels": strong_pixels,
        "visible_ratio": visible_pixels / total,
        "strong_ratio": strong_pixels / total,
    }


def is_weak_birefnet_mask(score: dict) -> bool:
    max_alpha = int(score.get("max_alpha") or 0)
    strong_ratio = float(score.get("strong_ratio") or 0.0)
    visible_ratio = float(score.get("visible_ratio") or 0.0)
    return max_alpha < 80 or (max_alpha < 128 and strong_ratio < 0.002 and visible_ratio < 0.08)


def is_low_confidence_birefnet_mask(score: dict) -> bool:
    max_alpha = int(score.get("max_alpha") or 0)
    mean_alpha = float(score.get("mean_alpha") or 0.0)
    strong_ratio = float(score.get("strong_ratio") or 0.0)
    visible_ratio = float(score.get("visible_ratio") or 0.0)
    if max_alpha < 80:
        return True
    if visible_ratio <= 0.15:
        return False
    if strong_ratio >= 0.03:
        return False
    return mean_alpha < 36


def should_use_birefnet_fallback(current_score: dict, fallback_score: dict) -> bool:
    current_max = int(current_score.get("max_alpha") or 0)
    fallback_max = int(fallback_score.get("max_alpha") or 0)
    current_strong = float(current_score.get("strong_ratio") or 0.0)
    fallback_strong = float(fallback_score.get("strong_ratio") or 0.0)
    if fallback_max < 128:
        return False
    if fallback_max >= max(160, current_max * 2) and fallback_strong > current_strong:
        return True
    return current_max < 80 and fallback_strong >= 0.005


def should_use_solid_background_fallback(
    ai_score: dict,
    solid_score: dict,
    border_color_ratio: float,
) -> bool:
    if border_color_ratio < 0.18:
        return False
    if not is_low_confidence_birefnet_mask(ai_score):
        return False
    solid_strong = float(solid_score.get("strong_ratio") or 0.0)
    solid_mean = float(solid_score.get("mean_alpha") or 0.0)
    ai_strong = float(ai_score.get("strong_ratio") or 0.0)
    ai_mean = float(ai_score.get("mean_alpha") or 0.0)
    if solid_strong < 0.03:
        return False
    return solid_strong >= max(ai_strong * 4.0, 0.08) and solid_mean >= max(ai_mean * 2.0, 40.0)


def solid_background_fallback_alpha(
    image: Image.Image,
    ai_score: dict,
    threshold: int,
    softness: int,
) -> tuple[Image.Image, dict] | None:
    key_rgb, border_ratio = dominant_border_key_color(image)
    solid_frame = chroma_key_frame(
        image=image,
        key_rgb=key_rgb,
        threshold=max(0, min(255, int(threshold))),
        softness=max(0, min(255, int(softness))),
        despill_strength=0.0,
        halo_pixels=0,
    )
    alpha = solid_frame.getchannel("A")
    solid_score = birefnet_mask_score(alpha)
    if not should_use_solid_background_fallback(ai_score, solid_score, border_ratio):
        return None
    return alpha, {
        "solid_key_fallback": True,
        "solid_key_color": rgb_to_hex(key_rgb),
        "solid_key_border_ratio": border_ratio,
        "solid_key_score": solid_score,
        "fallback_model_key": "solid-background-key",
        "fallback_reason": "BiRefNet produced a low-confidence alpha; used solid background key fallback",
    }


def run_birefnet_inference(
    image: Image.Image,
    model_key: str,
    requested_device: str,
    resolution: int,
) -> tuple[Image.Image, dict]:
    torch_module, transforms, _auto_model = import_ai_matte_dependencies()
    model, device, normalized_model_key, repo_id = load_birefnet_model(model_key, requested_device)
    fitted_image = resize_birefnet_input(image, resolution)
    transform = transforms.Compose(
        [
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )
    input_tensor = transform(fitted_image).unsqueeze(0).to(device)
    try:
        model_dtype = next(model.parameters()).dtype
    except StopIteration:
        model_dtype = input_tensor.dtype
    if str(device).startswith("cuda") and model_dtype in {torch_module.float16, torch_module.bfloat16}:
        input_tensor = input_tensor.to(dtype=model_dtype)
    with torch_module.no_grad():
        prediction = model(input_tensor)[-1].sigmoid().to("cpu")
    mask = transforms.ToPILImage()(prediction[0].squeeze()).convert("L")
    mask = mask.resize(image.size, LANCZOS)
    return mask, {
        "model_key": normalized_model_key,
        "model_label": AI_MATTE_MODEL_LABELS[normalized_model_key],
        "repo_id": repo_id,
        "device": device,
        "resolution": resolution,
        "preprocess": "square_resize",
    }


def birefnet_alpha_mask(
    image: Image.Image,
    model_key: str,
    requested_device: str,
    inference_resolution: int | str | None,
    solid_fallback_threshold: int = 42,
    solid_fallback_softness: int = 8,
) -> tuple[Image.Image, dict]:
    normalized_model_key = normalize_ai_model_key(model_key)
    resolution = resolve_ai_resolution(inference_resolution, image)
    mask, info = run_birefnet_inference(image, normalized_model_key, requested_device, resolution)
    score = birefnet_mask_score(mask)
    info["mask_score"] = score
    info["requested_model_key"] = normalized_model_key
    info["fallback_model_key"] = ""
    info["fallback_reason"] = ""
    info["solid_key_fallback"] = False
    info["solid_key_color"] = ""

    solid_fallback = solid_background_fallback_alpha(
        image,
        score,
        solid_fallback_threshold,
        solid_fallback_softness,
    )
    if solid_fallback is not None:
        solid_mask, solid_info = solid_fallback
        info.update(solid_info)
        return solid_mask, info

    fallback_model_key = "birefnet-general"
    if normalized_model_key != fallback_model_key and (is_weak_birefnet_mask(score) or is_low_confidence_birefnet_mask(score)):
        fallback_mask, fallback_info = run_birefnet_inference(image, fallback_model_key, requested_device, resolution)
        fallback_score = birefnet_mask_score(fallback_mask)
        if should_use_birefnet_fallback(score, fallback_score):
            fallback_info["mask_score"] = fallback_score
            fallback_info["requested_model_key"] = normalized_model_key
            fallback_info["fallback_model_key"] = fallback_model_key
            fallback_info["fallback_reason"] = "selected BiRefNet model produced a weak alpha mask"
            fallback_info["solid_key_fallback"] = False
            fallback_info["solid_key_color"] = ""
            return fallback_mask, fallback_info

    return mask, info


def update_ai_model_after_fallback(ai_model: str, ai_info: dict | None) -> str:
    if not ai_info:
        return ai_model
    return str(ai_info.get("model_key") or ai_model)


def luminance_alpha_mask(
    image: Image.Image,
    black_point: int,
    white_point: int,
    gamma: float,
    strength: float,
    polarity: str = "bright",
    key_rgb: tuple[int, int, int] | None = None,
    key_suppression: float = 0.95,
) -> Image.Image:
    black = max(0, min(254, int(black_point)))
    white = max(black + 1, min(255, int(white_point)))
    curve_gamma = max(0.05, float(gamma or 1.0))
    curve_strength = max(0.0, min(2.0, float(strength or 1.0)))
    key_strength = max(0.0, min(1.0, float(key_suppression)))
    rgb = image.convert("RGB")
    scale = white - black
    output = Image.new("L", rgb.size)
    output_pixels: list[int] = []
    keep_dark = polarity == "dark"
    for r_value, g_value, b_value in rgb.getdata():
        luma = int((0.2126 * r_value) + (0.7152 * g_value) + (0.0722 * b_value))
        normalized = clamp_float((luma - black) / scale, 0.0, 1.0)
        if keep_dark:
            normalized = 1.0 - normalized
        adjusted = normalized ** curve_gamma
        alpha = clamp_float(adjusted * curve_strength, 0.0, 1.0)
        if key_rgb is not None and key_strength > 0:
            k_r, k_g, k_b = key_rgb
            dist = math.sqrt((r_value - k_r) ** 2 + (g_value - k_g) ** 2 + (b_value - k_b) ** 2)
            closeness = 1.0 - min(dist / 180.0, 1.0)
            alpha *= 1.0 - ((closeness ** 2) * key_strength)
        output_pixels.append(round(alpha * 255))
    output.putdata(output_pixels)
    return output


def normalize_luma_polarity(value: str | None) -> str:
    normalized = str(value or "bright").strip().lower().replace("-", "_")
    aliases = {
        "light": "bright",
        "white": "bright",
        "keep_bright": "bright",
        "dark": "dark",
        "black": "dark",
        "inverse": "dark",
        "invert": "dark",
        "remove_white": "dark",
        "white_bg": "dark",
        "auto": "auto",
    }
    return aliases.get(normalized, "bright")


def luma_value(rgb: tuple[int, int, int]) -> int:
    r_value, g_value, b_value = rgb
    return int((0.2126 * r_value) + (0.7152 * g_value) + (0.0722 * b_value))


def resolve_luma_polarity(polarity: str, key_rgb: tuple[int, int, int]) -> str:
    normalized = normalize_luma_polarity(polarity)
    if normalized != "auto":
        return normalized
    return "dark" if luma_value(key_rgb) >= 128 else "bright"


def apply_alpha_mask(image: Image.Image, alpha_mask: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    mask = alpha_mask.convert("L")
    if mask.size != rgba.size:
        mask = mask.resize(rgba.size, LANCZOS)
    rgba.putalpha(mask)
    return rgba


def despill_alpha_edges(
    image: Image.Image,
    key_rgb: tuple[int, int, int],
    strength: float,
) -> Image.Image:
    normalized_strength = max(0.0, min(2.5, float(strength or 0.0)))
    if normalized_strength <= 0:
        return image

    rgba = image.convert("RGBA")
    k_r, k_g, k_b = key_rgb
    key_channels = (k_r, k_g, k_b)
    spill_channel = max(range(3), key=lambda index: key_channels[index])
    sorted_key_channels = sorted(key_channels, reverse=True)
    if sorted_key_channels[0] - sorted_key_channels[1] < 24:
        return image
    output_pixels: list[tuple[int, int, int, int]] = []
    for r_value, g_value, b_value, alpha in rgba.getdata():
        channels = [r_value, g_value, b_value]
        spill_value = channels[spill_channel]
        other_values = [value for index, value in enumerate(channels) if index != spill_channel]
        spill = max(0, spill_value - max(other_values))
        if spill <= 0:
            output_pixels.append((r_value, g_value, b_value, alpha))
            continue

        dist = math.sqrt((r_value - k_r) ** 2 + (g_value - k_g) ** 2 + (b_value - k_b) ** 2)
        key_closeness = 1.0 - min(dist / 220.0, 1.0)
        edge_factor = 1.0 - (alpha / 255.0)
        cleanup_factor = max(edge_factor, key_closeness * 0.7)
        reduction = int(spill * normalized_strength * cleanup_factor)
        channels[spill_channel] = max(0, spill_value - reduction)
        output_pixels.append((channels[0], channels[1], channels[2], alpha))

    cleaned = Image.new("RGBA", rgba.size)
    cleaned.putdata(output_pixels)
    return cleaned


def apply_matte_pipeline(
    raw_images: list[Image.Image],
    chroma_enabled: bool,
    matte_mode: str,
    key_mode: str,
    manual_key_hex: str,
    threshold: int,
    softness: int,
    despill_strength: float,
    halo_pixels: int,
    ai_model: str,
    ai_device: str,
    ai_resolution: int | str | None,
    luma_black: int,
    luma_white: int,
    luma_gamma: float,
    luma_strength: float,
    luma_polarity: str,
    corridorkey_enabled: bool,
    corridorkey_screen: str,
) -> tuple[list[Image.Image], tuple[int, int, int], dict]:
    if not raw_images:
        raise ValueError("no frames to matte")

    mode = normalize_matte_mode(matte_mode, chroma_enabled)
    key_rgb = auto_key_color(raw_images[0])
    if key_mode == "manual":
        key_rgb = parse_hex_color(manual_key_hex)
    normalized_luma_black = max(0, min(254, int(luma_black)))
    normalized_luma_white = max(normalized_luma_black + 1, min(255, int(luma_white)))
    normalized_luma_polarity = normalize_luma_polarity(luma_polarity)
    resolved_luma_polarity = resolve_luma_polarity(normalized_luma_polarity, key_rgb)
    matte_info = {
        "mode": mode,
        "model_key": "",
        "model_label": "",
        "repo_id": "",
        "device": "",
        "resolution": 0,
        "luma_enabled": mode in {"luma", "birefnet_luma", "birefnet_luma_key", "birefnet_luma_corridorkey"},
        "luma_black": normalized_luma_black,
        "luma_white": normalized_luma_white,
        "luma_gamma": max(0.05, float(luma_gamma or 1.0)),
        "luma_strength": max(0.0, min(2.0, float(luma_strength or 1.0))),
        "luma_polarity": normalized_luma_polarity,
        "luma_resolved_polarity": resolved_luma_polarity,
        "despill_strength": max(0.0, min(2.5, float(despill_strength or 0.0))),
        "halo_pixels": max(0, int(halo_pixels)),
        "corridorkey_enabled": False,
        "corridorkey_screen_color": "",
        "corridorkey_device": "",
        "corridorkey_resolution": 0,
    }
    mode_uses_corridorkey = mode in {
        "corridorkey",
        "birefnet_corridorkey",
        "birefnet_corridorkey_key",
        "birefnet_luma_corridorkey",
    }
    use_corridorkey = bool((corridorkey_enabled or mode_uses_corridorkey) and mode != "none")
    resolved_corridorkey_screen = resolve_corridorkey_screen(corridorkey_screen, key_rgb)

    if mode == "none":
        return raw_images, key_rgb, matte_info

    if mode in {"chroma", "corridorkey"}:
        keyed_frames = []
        corridor_info: dict | None = None
        for raw_image in raw_images:
            chroma_frame = chroma_key_frame(
                image=raw_image,
                key_rgb=key_rgb,
                threshold=threshold,
                softness=softness,
                despill_strength=despill_strength,
                halo_pixels=halo_pixels,
            )
            if use_corridorkey:
                refined_frame, corridor_info = corridorkey_refine_frame(
                    raw_image,
                    chroma_frame.getchannel("A"),
                    ai_device,
                    resolved_corridorkey_screen,
                    matte_info["despill_strength"],
                )
                keyed_frames.append(refined_frame)
            else:
                keyed_frames.append(chroma_frame)
        if corridor_info:
            matte_info.update(corridor_info)
        return keyed_frames, key_rgb, matte_info

    if mode == "luma":
        keyed_frames: list[Image.Image] = []
        for raw_image in raw_images:
            alpha = luminance_alpha_mask(
                raw_image,
                matte_info["luma_black"],
                max(matte_info["luma_black"] + 1, matte_info["luma_white"]),
                matte_info["luma_gamma"],
                matte_info["luma_strength"],
                polarity=matte_info["luma_resolved_polarity"],
                key_rgb=key_rgb,
            )
            if matte_info["halo_pixels"] > 0:
                filter_size = (matte_info["halo_pixels"] * 2) + 1
                alpha = alpha.filter(ImageFilter.MinFilter(filter_size))
            keyed_frame = apply_alpha_mask(raw_image, alpha)
            keyed_frame = despill_alpha_edges(keyed_frame, key_rgb, matte_info["despill_strength"])
            keyed_frames.append(keyed_frame)
        return keyed_frames, key_rgb, matte_info

    keyed_frames: list[Image.Image] = []
    ai_info: dict | None = None
    corridor_info: dict | None = None
    resolved_ai_model = ai_model
    for raw_image in raw_images:
        ai_alpha, ai_info = birefnet_alpha_mask(
            raw_image,
            resolved_ai_model,
            ai_device,
            ai_resolution,
            threshold,
            softness,
        )
        resolved_ai_model = update_ai_model_after_fallback(resolved_ai_model, ai_info)
        if matte_info["halo_pixels"] > 0:
            filter_size = (matte_info["halo_pixels"] * 2) + 1
            ai_alpha = ai_alpha.filter(ImageFilter.MinFilter(filter_size))
        if mode in {"birefnet_luma", "birefnet_luma_key", "birefnet_luma_corridorkey"}:
            luma_alpha = luminance_alpha_mask(
                raw_image,
                matte_info["luma_black"],
                max(matte_info["luma_black"] + 1, matte_info["luma_white"]),
                matte_info["luma_gamma"],
                matte_info["luma_strength"],
                polarity=matte_info["luma_resolved_polarity"],
                key_rgb=key_rgb,
            )
            if mode == "birefnet_luma_key":
                alpha = ImageChops.darker(ai_alpha, luma_alpha)
            else:
                alpha = ImageChops.lighter(ai_alpha, luma_alpha)
        else:
            alpha = ai_alpha
        if use_corridorkey:
            keyed_frame, corridor_info = corridorkey_refine_frame(
                raw_image,
                alpha,
                ai_device,
                resolved_corridorkey_screen,
                matte_info["despill_strength"],
            )
            if mode == "birefnet_corridorkey_key":
                refined_alpha = ImageChops.darker(ai_alpha, keyed_frame.getchannel("A"))
                keyed_frame.putalpha(refined_alpha)
        else:
            keyed_frame = apply_alpha_mask(raw_image, alpha)
            keyed_frame = despill_alpha_edges(keyed_frame, key_rgb, matte_info["despill_strength"])
        keyed_frames.append(keyed_frame)

    if ai_info:
        matte_info.update(ai_info)
    if corridor_info:
        matte_info.update(corridor_info)
    return keyed_frames, key_rgb, matte_info


def stable_resize_frames(
    keyed_frames: list[Image.Image],
    target_size: int,
    reduce_px: int,
    canvas_mode: str = "auto",
    hard_alpha: bool = False,
) -> tuple[list[Image.Image], list[tuple[int, int, int, int] | None], float, tuple[int, int]]:
    bboxes = [frame.getchannel("A").getbbox() for frame in keyed_frames]
    valid_boxes = [box for box in bboxes if box is not None]
    if not valid_boxes:
        raise RuntimeError("all frames became transparent after chroma key")

    stable_box = (
        min(box[0] for box in valid_boxes),
        min(box[1] for box in valid_boxes),
        max(box[2] for box in valid_boxes),
        max(box[3] for box in valid_boxes),
    )
    stable_width = stable_box[2] - stable_box[0]
    stable_height = stable_box[3] - stable_box[1]
    canvas_mode = normalize_canvas_mode(canvas_mode)
    canvas_height = max(8, target_size)
    margin = max(0, min(reduce_px, max(0, (canvas_height - 8) // 2)))

    if canvas_mode == "auto":
        inner_height = max(8, canvas_height - (margin * 2))
        scale = inner_height / max(stable_height, 1)
        resized_stable_size = (
            max(1, round(stable_width * scale)),
            max(1, round(stable_height * scale)),
        )
        canvas_width = max(8, resized_stable_size[0] + (margin * 2))
        paste_x = (canvas_width - resized_stable_size[0]) // 2
        paste_y = (canvas_height - resized_stable_size[1]) // 2
    else:
        inner_size = max(8, canvas_height - (margin * 2))
        scale = min(inner_size / max(stable_width, 1), inner_size / max(stable_height, 1))
        resized_stable_size = (
            max(1, round(stable_width * scale)),
            max(1, round(stable_height * scale)),
        )
        canvas_width = canvas_height
        paste_x = (canvas_width - resized_stable_size[0]) // 2
        if canvas_mode == "square_center":
            paste_y = (canvas_height - resized_stable_size[1]) // 2
        else:
            paste_y = canvas_height - margin - resized_stable_size[1]

    canvas_size = (canvas_width, canvas_height)

    rendered: list[Image.Image] = []
    for frame in keyed_frames:
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        cropped = frame.crop(stable_box)
        resized = resize_rgba_with_premultiplied_alpha(
            cropped,
            resized_stable_size,
        )
        if hard_alpha:
            resized = enforce_hard_alpha(resized)
        canvas.paste(resized, (paste_x, paste_y), resized)
        if hard_alpha:
            canvas = enforce_hard_alpha(canvas)
        rendered.append(canvas)

    return rendered, bboxes, scale, canvas_size


def should_preserve_source_canvas(media_type: str, reduce_px: int, canvas_mode: str) -> bool:
    return media_type in {"image", "image_sequence"} and reduce_px <= 0 and normalize_canvas_mode(canvas_mode) == "auto"


def resize_frames_on_source_canvas(
    keyed_frames: list[Image.Image],
    output_scale: float,
    hard_alpha: bool = False,
) -> tuple[list[Image.Image], list[tuple[int, int, int, int] | None], float, tuple[int, int]]:
    bboxes = [frame.getchannel("A").getbbox() for frame in keyed_frames]
    if not any(box is not None for box in bboxes):
        raise RuntimeError("all frames became transparent after chroma key")

    scale = normalize_output_scale(output_scale)
    rendered: list[Image.Image] = []
    max_width = 0
    max_height = 0
    for frame in keyed_frames:
        target_size = (
            max(1, round(frame.width * scale)),
            max(1, round(frame.height * scale)),
        )
        resized = frame.copy() if target_size == frame.size else resize_rgba_with_premultiplied_alpha(frame, target_size)
        if hard_alpha:
            resized = enforce_hard_alpha(resized)
        rendered.append(resized)
        max_width = max(max_width, resized.width)
        max_height = max(max_height, resized.height)

    return rendered, bboxes, scale, (max_width, max_height)


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def job_manifest_path(job_id: str) -> Path:
    return job_dir(job_id) / "manifest.json"


def save_job_manifest(job_id: str, payload: dict) -> None:
    path = job_manifest_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_job_manifest(job_id: str) -> dict:
    path = job_manifest_path(job_id)
    if not path.exists():
        raise FileNotFoundError(f"job not found: {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def extract_raw_frames(
    source_path: Path,
    raw_dir: Path,
    start_time: float,
    end_time: float,
    keep_every: int,
) -> tuple[list[Path], dict]:
    ffmpeg = resolve_ffmpeg_binary("ffmpeg")
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    def build_args(hwaccel: str | None) -> list[str]:
        args = [ffmpeg, "-y"]
        if hwaccel:
            args += ["-hwaccel", hwaccel]
        args += [
            "-ss",
            f"{start_time:.3f}",
            "-to",
            f"{end_time:.3f}",
            "-i",
            str(source_path),
        ]
        if keep_every > 1:
            args += ["-vf", f"select=not(mod(n\\,{keep_every}))"]
        args += ["-vsync", "0", str(raw_dir / "frame_%05d.png")]
        return args

    accel = run_ffmpeg_with_auto_accel(build_args)
    frames = sorted(raw_dir.glob("frame_*.png"))
    if not frames:
        raise RuntimeError("no frames extracted from the selected segment")
    return frames, accel


def extract_single_frame(source_path: Path, output_path: Path, sample_time: float) -> tuple[Path, dict]:
    ffmpeg = resolve_ffmpeg_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def build_args(hwaccel: str | None) -> list[str]:
        args = [ffmpeg, "-y"]
        if hwaccel:
            args += ["-hwaccel", hwaccel]
        args += [
            "-ss",
            f"{sample_time:.3f}",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            str(output_path),
        ]
        return args

    accel = run_ffmpeg_with_auto_accel(build_args)
    if not output_path.exists():
        raise RuntimeError("failed to extract preview frame")
    return output_path, accel


def copy_sequence_frames(
    upload_id: str,
    raw_dir: Path,
    start_frame: int,
    end_frame: int,
) -> tuple[list[Path], list[dict]]:
    entries = source_frame_entries(upload_id)
    frame_count = len(entries)
    start_index = clamp_int(int(start_frame or 1), 1, frame_count) - 1
    end_index = clamp_int(int(end_frame or frame_count), 1, frame_count) - 1
    if end_index < start_index:
        end_index = start_index

    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_paths: list[Path] = []
    selected_entries: list[dict] = []
    for output_index, entry in enumerate(entries[start_index : end_index + 1]):
        raw_path = raw_dir / f"frame_{output_index + 1:05d}.png"
        with Image.open(entry["path"]) as image:
            image.convert("RGBA").save(raw_path)
        raw_paths.append(raw_path)
        selected_entries.append(entry)
    if not raw_paths:
        raise RuntimeError("no image sequence frames selected")
    return raw_paths, selected_entries


def process_video_to_job(
    upload_id: str,
    start_time: float,
    end_time: float,
    start_frame: int,
    end_frame: int,
    keep_every: int,
    output_scale: float,
    reduce_px: int,
    canvas_mode: str,
    chroma_enabled: bool,
    matte_mode: str,
    key_mode: str,
    manual_key_hex: str,
    threshold: int,
    softness: int,
    despill_strength: float,
    halo_pixels: int,
    ai_model: str,
    ai_device: str,
    ai_resolution: int | str | None,
    luma_black: int,
    luma_white: int,
    luma_gamma: float,
    luma_strength: float,
    luma_polarity: str,
    corridorkey_enabled: bool,
    corridorkey_screen: str,
    batch_green_to_black: bool = False,
    batch_green_desaturate: bool = False,
    batch_semitransparent_to_black: bool = False,
    batch_semitransparent_to_opaque: bool = False,
    production_context: dict | None = None,
) -> dict:
    source_path, media_type = source_media_entry(upload_id)
    info = upload_media_info(upload_id, source_path, media_type)
    start_time = max(0.0, start_time)
    duration = safe_float(info.get("duration"), 0.0)
    if media_type == "video" and duration > 0:
        end_time = min(end_time, duration)
    elif media_type == "image":
        start_time = 0.0
        end_time = 0.0
        start_frame = 1
        end_frame = 1
    elif media_type == "image_sequence":
        frame_count = max(1, safe_int(info.get("frame_count"), len(source_frame_entries(upload_id))))
        start_time = 0.0
        end_time = 0.0
        keep_every = 1
        start_frame = clamp_int(int(start_frame or 1), 1, frame_count)
        end_frame = clamp_int(int(end_frame or frame_count), start_frame, frame_count)
    if media_type == "video" and end_time <= start_time:
        raise ValueError("end time must be greater than start time")
    if media_type == "video":
        requested_start_frame = int(start_frame or 0)
        requested_end_frame = int(end_frame or 0)
        if requested_start_frame > 0 and requested_end_frame >= requested_start_frame:
            start_frame = requested_start_frame
            end_frame = requested_end_frame
        else:
            start_frame = 0
            end_frame = 0

    job_id = timestamped_id()
    root = job_dir(job_id)
    raw_dir = root / "raw"
    processed_dir = root / "processed"
    thumbs_dir = root / "thumbs"
    for directory in (processed_dir, thumbs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if media_type == "image":
        raw_path = raw_dir / "frame_00001.png"
        _, ffmpeg_accel = extract_image_frame(source_path, raw_path)
        raw_paths = [raw_path]
        source_entries = [{"name": source_path.name}]
    elif media_type == "image_sequence":
        raw_paths, source_entries = copy_sequence_frames(upload_id, raw_dir, start_frame, end_frame)
        ffmpeg_accel = image_sequence_payload()
    else:
        raw_paths, ffmpeg_accel = extract_raw_frames(
            source_path,
            raw_dir,
            start_time,
            end_time,
            max(1, keep_every),
        )
        source_entries = []
    raw_images = [open_rgba_image(path) for path in raw_paths]
    output_scale = normalize_output_scale(output_scale)
    target_size = target_size_from_source_height(max(image.height for image in raw_images), output_scale)

    keyed_frames, key_rgb, matte_info = apply_matte_pipeline(
        raw_images=raw_images,
        chroma_enabled=chroma_enabled,
        matte_mode=matte_mode,
        key_mode=key_mode,
        manual_key_hex=manual_key_hex,
        threshold=threshold,
        softness=softness,
        despill_strength=despill_strength,
        halo_pixels=halo_pixels,
        ai_model=ai_model,
        ai_device=ai_device,
        ai_resolution=ai_resolution,
        luma_black=luma_black,
        luma_white=luma_white,
        luma_gamma=luma_gamma,
        luma_strength=luma_strength,
        luma_polarity=luma_polarity,
        corridorkey_enabled=corridorkey_enabled,
        corridorkey_screen=corridorkey_screen,
    )

    hard_alpha = matte_info["mode"] == "chroma" and softness == 0 and not matte_info["corridorkey_enabled"]
    if should_preserve_source_canvas(media_type, reduce_px, canvas_mode):
        rendered_frames, bboxes, scale, canvas_size = resize_frames_on_source_canvas(
            keyed_frames,
            output_scale,
            hard_alpha=hard_alpha,
        )
    else:
        rendered_frames, bboxes, scale, canvas_size = stable_resize_frames(
            keyed_frames,
            target_size,
            reduce_px,
            canvas_mode,
            hard_alpha=hard_alpha,
        )
    frame_entries: list[dict] = []
    postprocess_changed = {
        "green_to_black": 0,
        "green_desaturate": 0,
        "semitransparent_to_black": 0,
        "semitransparent_to_opaque": 0,
    }
    for index, frame in enumerate(rendered_frames):
        frame_name = f"frame_{index + 1:03d}.png"
        thumb_name = f"thumb_{index + 1:03d}.png"
        frame_path = processed_dir / frame_name
        thumb_path = thumbs_dir / thumb_name
        if batch_green_to_black:
            frame, changed = green_to_black_image(frame)
            postprocess_changed["green_to_black"] += changed
        if batch_green_desaturate:
            frame, changed = green_desaturate_image(frame)
            postprocess_changed["green_desaturate"] += changed
        if batch_semitransparent_to_black:
            frame, changed = semitransparent_to_black_image(frame)
            postprocess_changed["semitransparent_to_black"] += changed
        if batch_semitransparent_to_opaque:
            frame, changed = semitransparent_to_opaque_image(frame)
            postprocess_changed["semitransparent_to_opaque"] += changed
        frame.save(frame_path)
        thumb = frame.copy()
        thumb.thumbnail((128, 128))
        thumb.save(thumb_path)
        frame_entries.append(
            {
                "index": index,
                "name": frame_name,
                "original_name": source_entries[index]["name"] if index < len(source_entries) else frame_name,
                "url": f"/work/jobs/{job_id}/processed/{frame_name}",
                "thumb_url": f"/work/jobs/{job_id}/thumbs/{thumb_name}",
                "bbox": list(bboxes[index]) if bboxes[index] else None,
                "width": frame.size[0],
                "height": frame.size[1],
            }
        )

    manifest = {
        "job_id": job_id,
        "upload_id": upload_id,
        "job_dir": str(root),
        "processed_dir": str(processed_dir),
        "raw_dir": str(raw_dir),
        "source_path": str(source_path),
        "source_media_type": media_type,
        "ffmpeg_accel": ffmpeg_accel,
        "video_info": info,
        "options": {
            "start_time": start_time,
            "end_time": end_time,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "keep_every": keep_every,
            "target_size": target_size,
            "output_scale": output_scale,
            "reduce_px": reduce_px,
            "canvas_mode": normalize_canvas_mode(canvas_mode),
            "preserve_source_canvas": should_preserve_source_canvas(media_type, reduce_px, canvas_mode),
            "output_width": canvas_size[0],
            "output_height": canvas_size[1],
            "chroma_enabled": chroma_enabled,
            "matte_mode": matte_info["mode"],
            "matte": matte_info,
            "key_mode": key_mode,
            "key_color": rgb_to_hex(key_rgb),
            "threshold": threshold,
            "softness": softness,
            "despill_strength": despill_strength,
            "halo_pixels": halo_pixels,
            "corridorkey_enabled": matte_info["corridorkey_enabled"],
            "corridorkey_screen": matte_info["corridorkey_screen_color"],
            "batch_green_to_black": bool(batch_green_to_black),
            "batch_green_desaturate": bool(batch_green_desaturate),
            "batch_semitransparent_to_black": bool(batch_semitransparent_to_black),
            "batch_semitransparent_to_opaque": bool(batch_semitransparent_to_opaque),
            "postprocess_changed_pixels": postprocess_changed,
            "scale": scale,
        },
        "frame_count": len(frame_entries),
        "frames": frame_entries,
    }
    if production_context:
        manifest["production_context"] = production_context
    save_job_manifest(job_id, manifest)
    return manifest


def preview_dir(preview_id: str) -> Path:
    return PREVIEWS_DIR / preview_id


def load_preview_manifest(preview_id: str) -> dict:
    preview_id = str(preview_id or "").strip()
    if not preview_id or Path(preview_id).name != preview_id:
        raise ValueError("invalid preview id")
    path = preview_dir(preview_id) / "preview.json"
    if not path.exists():
        raise FileNotFoundError(f"preview not found: {preview_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_preview_manifest(preview_id: str, manifest: dict) -> None:
    root = preview_dir(preview_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "preview.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def is_green_residue_pixel(
    r_value: int,
    g_value: int,
    b_value: int,
    alpha: int,
    threshold: int,
    dominance: int,
    alpha_floor: int,
) -> bool:
    if alpha < alpha_floor:
        return False

    raw_green_excess = g_value - max(r_value, b_value)
    is_raw_green = g_value >= threshold and raw_green_excess >= dominance
    if is_raw_green:
        return True

    if alpha <= 0:
        return False

    alpha_scale = 255.0 / alpha
    scaled_r = min(255, round(r_value * alpha_scale))
    scaled_g = min(255, round(g_value * alpha_scale))
    scaled_b = min(255, round(b_value * alpha_scale))
    scaled_green_excess = scaled_g - max(scaled_r, scaled_b)
    return scaled_g >= threshold and scaled_green_excess >= dominance


def green_to_black_image(
    image: Image.Image,
    threshold: int = 42,
    dominance: int = 24,
    alpha_floor: int = 1,
) -> tuple[Image.Image, int]:
    rgba = image.convert("RGBA")
    output_pixels: list[tuple[int, int, int, int]] = []
    changed = 0
    threshold = max(0, min(255, int(threshold)))
    dominance = max(0, min(255, int(dominance)))
    alpha_floor = max(0, min(255, int(alpha_floor)))

    for r_value, g_value, b_value, alpha in rgba.getdata():
        if is_green_residue_pixel(r_value, g_value, b_value, alpha, threshold, dominance, alpha_floor):
            output_pixels.append((0, 0, 0, alpha))
            changed += 1
        else:
            output_pixels.append((r_value, g_value, b_value, alpha))

    cleaned = Image.new("RGBA", rgba.size)
    cleaned.putdata(output_pixels)
    return cleaned, changed


def green_desaturate_image(
    image: Image.Image,
    threshold: int = 42,
    dominance: int = 24,
    alpha_floor: int = 1,
) -> tuple[Image.Image, int]:
    rgba = image.convert("RGBA")
    output_pixels: list[tuple[int, int, int, int]] = []
    changed = 0
    threshold = max(0, min(255, int(threshold)))
    dominance = max(0, min(255, int(dominance)))
    alpha_floor = max(0, min(255, int(alpha_floor)))

    for r_value, g_value, b_value, alpha in rgba.getdata():
        if is_green_residue_pixel(r_value, g_value, b_value, alpha, threshold, dominance, alpha_floor):
            gray = clamp_int(round(0.299 * r_value + 0.587 * g_value + 0.114 * b_value), 0, 255)
            output_pixels.append((gray, gray, gray, alpha))
            changed += 1
        else:
            output_pixels.append((r_value, g_value, b_value, alpha))

    cleaned = Image.new("RGBA", rgba.size)
    cleaned.putdata(output_pixels)
    return cleaned, changed


def green_to_black_preview(preview_id: str, threshold: int = 42, dominance: int = 24) -> dict:
    preview = load_preview_manifest(preview_id)
    root = preview_dir(preview["preview_id"])
    processed_path = root / "processed.png"
    if not processed_path.exists():
        raise FileNotFoundError(f"processed preview missing: {processed_path}")

    image = open_rgba_image(processed_path)
    cleaned, changed = green_to_black_image(image, threshold=threshold, dominance=dominance)
    image.close()
    cleaned.save(processed_path)
    cleaned.close()

    postprocess = preview.setdefault("postprocess", {})
    green_black = postprocess.setdefault("green_to_black", {})
    green_black["enabled"] = True
    green_black["threshold"] = max(0, min(255, int(threshold)))
    green_black["dominance"] = max(0, min(255, int(dominance)))
    green_black["changed_pixels"] = changed
    green_black["updated_at"] = iso_now()
    preview["processed_url"] = f"/work/previews/{preview['preview_id']}/processed.png?ts={int(time.time() * 1000)}"
    save_preview_manifest(preview["preview_id"], preview)
    return preview


def green_desaturate_preview(preview_id: str, threshold: int = 42, dominance: int = 24) -> dict:
    preview = load_preview_manifest(preview_id)
    root = preview_dir(preview["preview_id"])
    processed_path = root / "processed.png"
    if not processed_path.exists():
        raise FileNotFoundError(f"processed preview missing: {processed_path}")

    image = open_rgba_image(processed_path)
    cleaned, changed = green_desaturate_image(image, threshold=threshold, dominance=dominance)
    image.close()
    cleaned.save(processed_path)
    cleaned.close()

    postprocess = preview.setdefault("postprocess", {})
    green_desaturate = postprocess.setdefault("green_desaturate", {})
    green_desaturate["enabled"] = True
    green_desaturate["threshold"] = max(0, min(255, int(threshold)))
    green_desaturate["dominance"] = max(0, min(255, int(dominance)))
    green_desaturate["changed_pixels"] = changed
    green_desaturate["updated_at"] = iso_now()
    preview["processed_url"] = f"/work/previews/{preview['preview_id']}/processed.png?ts={int(time.time() * 1000)}"
    save_preview_manifest(preview["preview_id"], preview)
    return preview


def semitransparent_to_black_image(
    image: Image.Image,
    alpha_min: int = 1,
    alpha_max: int = 254,
) -> tuple[Image.Image, int]:
    rgba = image.convert("RGBA")
    output_pixels: list[tuple[int, int, int, int]] = []
    changed = 0
    alpha_min = max(0, min(255, int(alpha_min)))
    alpha_max = max(alpha_min, min(255, int(alpha_max)))

    for r_value, g_value, b_value, alpha in rgba.getdata():
        if alpha_min <= alpha <= alpha_max:
            output_pixels.append((0, 0, 0, alpha))
            changed += 1
        else:
            output_pixels.append((r_value, g_value, b_value, alpha))

    cleaned = Image.new("RGBA", rgba.size)
    cleaned.putdata(output_pixels)
    return cleaned, changed


def semitransparent_to_black_preview(preview_id: str, alpha_min: int = 1, alpha_max: int = 254) -> dict:
    preview = load_preview_manifest(preview_id)
    root = preview_dir(preview["preview_id"])
    processed_path = root / "processed.png"
    if not processed_path.exists():
        raise FileNotFoundError(f"processed preview missing: {processed_path}")

    image = open_rgba_image(processed_path)
    cleaned, changed = semitransparent_to_black_image(image, alpha_min=alpha_min, alpha_max=alpha_max)
    image.close()
    cleaned.save(processed_path)
    cleaned.close()

    postprocess = preview.setdefault("postprocess", {})
    semitransparent_black = postprocess.setdefault("semitransparent_to_black", {})
    semitransparent_black["enabled"] = True
    semitransparent_black["alpha_min"] = max(0, min(255, int(alpha_min)))
    semitransparent_black["alpha_max"] = max(0, min(255, int(alpha_max)))
    semitransparent_black["changed_pixels"] = changed
    semitransparent_black["updated_at"] = iso_now()
    preview["processed_url"] = f"/work/previews/{preview['preview_id']}/processed.png?ts={int(time.time() * 1000)}"
    save_preview_manifest(preview["preview_id"], preview)
    return preview


def semitransparent_to_opaque_image(
    image: Image.Image,
    alpha_min: int = 1,
    alpha_max: int = 254,
) -> tuple[Image.Image, int]:
    rgba = image.convert("RGBA")
    output_pixels: list[tuple[int, int, int, int]] = []
    changed = 0
    alpha_min = max(0, min(255, int(alpha_min)))
    alpha_max = max(alpha_min, min(255, int(alpha_max)))

    for r_value, g_value, b_value, alpha in rgba.getdata():
        if alpha_min <= alpha <= alpha_max:
            output_pixels.append((r_value, g_value, b_value, 255))
            changed += 1
        else:
            output_pixels.append((r_value, g_value, b_value, alpha))

    cleaned = Image.new("RGBA", rgba.size)
    cleaned.putdata(output_pixels)
    return cleaned, changed


def semitransparent_to_opaque_preview(preview_id: str, alpha_min: int = 1, alpha_max: int = 254) -> dict:
    preview = load_preview_manifest(preview_id)
    root = preview_dir(preview["preview_id"])
    processed_path = root / "processed.png"
    if not processed_path.exists():
        raise FileNotFoundError(f"processed preview missing: {processed_path}")

    image = open_rgba_image(processed_path)
    cleaned, changed = semitransparent_to_opaque_image(image, alpha_min=alpha_min, alpha_max=alpha_max)
    image.close()
    cleaned.save(processed_path)
    cleaned.close()

    postprocess = preview.setdefault("postprocess", {})
    semitransparent_opaque = postprocess.setdefault("semitransparent_to_opaque", {})
    semitransparent_opaque["enabled"] = True
    semitransparent_opaque["alpha_min"] = max(0, min(255, int(alpha_min)))
    semitransparent_opaque["alpha_max"] = max(0, min(255, int(alpha_max)))
    semitransparent_opaque["changed_pixels"] = changed
    semitransparent_opaque["updated_at"] = iso_now()
    preview["processed_url"] = f"/work/previews/{preview['preview_id']}/processed.png?ts={int(time.time() * 1000)}"
    save_preview_manifest(preview["preview_id"], preview)
    return preview


def preview_frame(
    upload_id: str,
    sample_time: float,
    sample_frame: int,
    output_scale: float,
    reduce_px: int,
    canvas_mode: str,
    chroma_enabled: bool,
    matte_mode: str,
    key_mode: str,
    manual_key_hex: str,
    threshold: int,
    softness: int,
    despill_strength: float,
    halo_pixels: int,
    ai_model: str,
    ai_device: str,
    ai_resolution: int | str | None,
    luma_black: int,
    luma_white: int,
    luma_gamma: float,
    luma_strength: float,
    luma_polarity: str,
    corridorkey_enabled: bool,
    corridorkey_screen: str,
    batch_green_to_black: bool = False,
    batch_green_desaturate: bool = False,
    batch_semitransparent_to_black: bool = False,
    batch_semitransparent_to_opaque: bool = False,
) -> dict:
    source_path, media_type = source_media_entry(upload_id)
    info = upload_media_info(upload_id, source_path, media_type)
    duration = safe_float(info.get("duration"), 0.0)
    if media_type == "video" and duration > 0:
        sample_time = clamp_float(sample_time, 0.0, duration)
    else:
        sample_time = 0.0
    selected_source_name = source_path.name
    selected_sample_frame = 1

    preview_id = timestamped_id()
    root = preview_dir(preview_id)
    raw_path = root / "raw.png"
    source_preview_path = root / "source.png"
    processed_path = root / "processed.png"
    root.mkdir(parents=True, exist_ok=True)

    if media_type == "image":
        _, ffmpeg_accel = extract_image_frame(source_path, raw_path)
    elif media_type == "image_sequence":
        entries = source_frame_entries(upload_id)
        selected_index = clamp_int(int(sample_frame or 1), 1, len(entries)) - 1
        selected_entry = entries[selected_index]
        selected_source_name = selected_entry["name"]
        selected_sample_frame = selected_index + 1
        with Image.open(selected_entry["path"]) as image:
            image.convert("RGBA").save(raw_path)
        ffmpeg_accel = image_sequence_payload()
    else:
        _, ffmpeg_accel = extract_single_frame(source_path, raw_path, sample_time)
    raw_image = open_rgba_image(raw_path)
    output_scale = normalize_output_scale(output_scale)
    target_size = target_size_from_source_height(raw_image.height, output_scale)

    raw_image.save(source_preview_path)

    keyed_frames, key_rgb, matte_info = apply_matte_pipeline(
        raw_images=[raw_image],
        chroma_enabled=chroma_enabled,
        matte_mode=matte_mode,
        key_mode=key_mode,
        manual_key_hex=manual_key_hex,
        threshold=threshold,
        softness=softness,
        despill_strength=despill_strength,
        halo_pixels=halo_pixels,
        ai_model=ai_model,
        ai_device=ai_device,
        ai_resolution=ai_resolution,
        luma_black=luma_black,
        luma_white=luma_white,
        luma_gamma=luma_gamma,
        luma_strength=luma_strength,
        luma_polarity=luma_polarity,
        corridorkey_enabled=corridorkey_enabled,
        corridorkey_screen=corridorkey_screen,
    )
    keyed_image = keyed_frames[0]

    hard_alpha = matte_info["mode"] == "chroma" and softness == 0 and not matte_info["corridorkey_enabled"]
    if should_preserve_source_canvas(media_type, reduce_px, canvas_mode):
        rendered_frames, _, scale, canvas_size = resize_frames_on_source_canvas(
            [keyed_image],
            output_scale,
            hard_alpha=hard_alpha,
        )
    else:
        rendered_frames, _, scale, canvas_size = stable_resize_frames(
            [keyed_image],
            target_size,
            reduce_px,
            canvas_mode,
            hard_alpha=hard_alpha,
        )
    rendered_frame = rendered_frames[0]
    postprocess_changed = {
        "green_to_black": 0,
        "green_desaturate": 0,
        "semitransparent_to_black": 0,
        "semitransparent_to_opaque": 0,
    }
    if batch_green_to_black:
        rendered_frame, changed = green_to_black_image(rendered_frame)
        postprocess_changed["green_to_black"] += changed
    if batch_green_desaturate:
        rendered_frame, changed = green_desaturate_image(rendered_frame)
        postprocess_changed["green_desaturate"] += changed
    if batch_semitransparent_to_black:
        rendered_frame, changed = semitransparent_to_black_image(rendered_frame)
        postprocess_changed["semitransparent_to_black"] += changed
    if batch_semitransparent_to_opaque:
        rendered_frame, changed = semitransparent_to_opaque_image(rendered_frame)
        postprocess_changed["semitransparent_to_opaque"] += changed
    rendered_frame.save(processed_path)

    manifest = {
        "preview_id": preview_id,
        "upload_id": upload_id,
        "sample_time": sample_time,
        "sample_frame": selected_sample_frame,
        "source_name": selected_source_name,
        "source_path": str(source_path),
        "source_media_type": media_type,
        "source_url": f"/work/previews/{preview_id}/source.png",
        "processed_url": f"/work/previews/{preview_id}/processed.png",
        "key_color": rgb_to_hex(key_rgb),
        "matte": matte_info,
        "ffmpeg_accel": ffmpeg_accel,
        "scale": scale,
        "postprocess_changed": postprocess_changed,
        "options": {
            "target_size": target_size,
            "output_scale": output_scale,
            "reduce_px": reduce_px,
            "canvas_mode": normalize_canvas_mode(canvas_mode),
            "preserve_source_canvas": should_preserve_source_canvas(media_type, reduce_px, canvas_mode),
            "output_width": canvas_size[0],
            "output_height": canvas_size[1],
            "chroma_enabled": chroma_enabled,
            "matte_mode": matte_info["mode"],
            "key_mode": key_mode,
            "threshold": threshold,
            "softness": softness,
            "despill_strength": despill_strength,
            "halo_pixels": halo_pixels,
            "corridorkey_enabled": matte_info["corridorkey_enabled"],
            "corridorkey_screen": matte_info["corridorkey_screen_color"],
            "batch_green_to_black": bool(batch_green_to_black),
            "batch_green_desaturate": bool(batch_green_desaturate),
            "batch_semitransparent_to_black": bool(batch_semitransparent_to_black),
            "batch_semitransparent_to_opaque": bool(batch_semitransparent_to_opaque),
            "postprocess_changed_pixels": postprocess_changed,
        },
    }
    (root / "preview.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def save_preview_as_job(preview_id: str) -> dict:
    preview = load_preview_manifest(preview_id)
    if str(preview.get("source_media_type") or "").lower() != "image":
        raise ValueError("direct preview save is only available for image uploads")

    source_preview_path = preview_dir(preview["preview_id"]) / "source.png"
    raw_preview_path = preview_dir(preview["preview_id"]) / "raw.png"
    processed_preview_path = preview_dir(preview["preview_id"]) / "processed.png"
    if not processed_preview_path.exists():
        raise FileNotFoundError(f"processed preview missing: {processed_preview_path}")

    source_path = repair_mojibake_path(Path(preview["source_path"]))
    media_type = str(preview.get("source_media_type") or "image").lower()
    info = media_info(source_path, media_type)
    options = preview.get("options") or {}
    matte_info = preview.get("matte") or {"mode": options.get("matte_mode") or "chroma"}

    job_id = timestamped_id()
    root = job_dir(job_id)
    raw_dir = root / "raw"
    processed_dir = root / "processed"
    thumbs_dir = root / "thumbs"
    for directory in (raw_dir, processed_dir, thumbs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if raw_preview_path.exists():
        shutil.copy2(raw_preview_path, raw_dir / "frame_00001.png")
    elif source_preview_path.exists():
        shutil.copy2(source_preview_path, raw_dir / "frame_00001.png")

    frame_name = "frame_001.png"
    thumb_name = "thumb_001.png"
    frame_path = processed_dir / frame_name
    thumb_path = thumbs_dir / thumb_name
    shutil.copy2(processed_preview_path, frame_path)

    frame = open_rgba_image(frame_path)
    thumb = frame.copy()
    thumb.thumbnail((128, 128))
    thumb.save(thumb_path)
    bbox = frame.getchannel("A").getbbox()
    canvas_size = frame.size

    manifest = {
        "job_id": job_id,
        "upload_id": preview.get("upload_id") or "",
        "job_dir": str(root),
        "processed_dir": str(processed_dir),
        "raw_dir": str(raw_dir),
        "source_path": str(source_path),
        "source_media_type": media_type,
        "ffmpeg_accel": preview.get("ffmpeg_accel") or {},
        "video_info": info,
        "options": {
            "start_time": 0,
            "end_time": 0,
            "keep_every": 1,
            "target_size": options.get("target_size") or canvas_size[1],
            "output_scale": options.get("output_scale") or preview.get("output_scale") or 1,
            "reduce_px": options.get("reduce_px") or 0,
            "canvas_mode": normalize_canvas_mode(str(options.get("canvas_mode") or "auto")),
            "output_width": options.get("output_width") or canvas_size[0],
            "output_height": options.get("output_height") or canvas_size[1],
            "chroma_enabled": bool(options.get("chroma_enabled", True)),
            "matte_mode": matte_info.get("mode") or options.get("matte_mode") or "chroma",
            "matte": matte_info,
            "key_mode": options.get("key_mode") or "auto",
            "key_color": preview.get("key_color") or "#000000",
            "threshold": options.get("threshold") or 0,
            "softness": options.get("softness") or 0,
            "despill_strength": options.get("despill_strength") or 0,
            "halo_pixels": options.get("halo_pixels") or 0,
            "corridorkey_enabled": bool(options.get("corridorkey_enabled", False)),
            "corridorkey_screen": options.get("corridorkey_screen") or "auto",
            "scale": preview.get("scale") or 1,
        },
        "frame_count": 1,
        "frames": [
            {
                "index": 0,
                "name": frame_name,
                "url": f"/work/jobs/{job_id}/processed/{frame_name}",
                "thumb_url": f"/work/jobs/{job_id}/thumbs/{thumb_name}",
                "bbox": list(bbox) if bbox else None,
                "width": canvas_size[0],
                "height": canvas_size[1],
            }
        ],
    }
    save_job_manifest(job_id, manifest)
    return manifest


def natural_sort_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def field_storage_items(form: cgi.FieldStorage, key: str) -> list:
    if key not in form:
        return []
    value = form[key]
    return value if isinstance(value, list) else [value]


def import_animation_frames_to_job(file_items: list) -> dict:
    candidates = []
    for item in file_items:
        raw_filename = str(getattr(item, "filename", "") or "frame")
        display_name = Path(raw_filename.replace("\\", "/")).name or "frame"
        if not getattr(item, "file", None):
            continue
        suffix = Path(display_name).suffix.lower()
        content_type = str(getattr(item, "type", "") or "")
        if suffix not in ANIMATION_FRAME_EXTENSIONS and not content_type.startswith("image/"):
            continue
        candidates.append((raw_filename, display_name, item))

    candidates.sort(key=lambda pair: natural_sort_key(pair[0]))
    if not candidates:
        raise ValueError("no supported image frames found")

    job_id = timestamped_id()
    root = job_dir(job_id)
    raw_dir = root / "raw"
    processed_dir = root / "processed"
    thumbs_dir = root / "thumbs"
    for directory in (raw_dir, processed_dir, thumbs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    frame_entries: list[dict] = []
    max_width = 0
    max_height = 0
    for index, (_, display_name, item) in enumerate(candidates):
        frame_name = f"frame_{index + 1:03d}.png"
        thumb_name = f"thumb_{index + 1:03d}.png"
        raw_path = raw_dir / frame_name
        frame_path = processed_dir / frame_name
        thumb_path = thumbs_dir / thumb_name

        with Image.open(item.file) as source_image:
            image = source_image.convert("RGBA")
            image.save(raw_path)
            image.save(frame_path)
            thumb = image.copy()
            thumb.thumbnail((128, 128))
            thumb.save(thumb_path)
            bbox = image.getchannel("A").getbbox()
            max_width = max(max_width, image.size[0])
            max_height = max(max_height, image.size[1])

            frame_entries.append(
                {
                    "index": index,
                    "name": frame_name,
                    "original_name": display_name,
                    "url": f"/work/jobs/{job_id}/processed/{frame_name}",
                    "thumb_url": f"/work/jobs/{job_id}/thumbs/{thumb_name}",
                    "bbox": list(bbox) if bbox else None,
                    "width": image.size[0],
                    "height": image.size[1],
                }
            )
            image.close()

    manifest = {
        "job_id": job_id,
        "upload_id": "",
        "job_dir": str(root),
        "processed_dir": str(processed_dir),
        "raw_dir": str(raw_dir),
        "source_path": "",
        "source_media_type": "animation",
        "ffmpeg_accel": custom_animation_payload(),
        "video_info": {
            "media_type": "animation",
            "duration": 0,
            "fps": 0,
            "width": max_width,
            "height": max_height,
        },
        "options": {
            "start_time": 0,
            "end_time": 0,
            "keep_every": 1,
            "target_size": max_height,
            "reduce_px": 0,
            "canvas_mode": "custom",
            "output_width": max_width,
            "output_height": max_height,
            "chroma_enabled": False,
            "matte_mode": "none",
            "matte": {"mode": "none", "source": "custom_animation"},
            "key_mode": "none",
            "key_color": "#000000",
            "threshold": 0,
            "softness": 0,
            "despill_strength": 0,
            "halo_pixels": 0,
            "corridorkey_enabled": False,
            "corridorkey_screen": "auto",
            "scale": 1,
            "source_order": "filename",
        },
        "frame_count": len(frame_entries),
        "frames": frame_entries,
    }
    save_job_manifest(job_id, manifest)
    return manifest


def line_cleaner_dir(run_id: str) -> Path:
    return LINE_CLEANER_DIR / run_id


def resolve_realesrgan_binary() -> str | None:
    configured = str(os.environ.get(REAL_ESRGAN_BINARY_ENV, "")).strip().strip("\"'")
    if configured:
        path = Path(configured).expanduser()
        if path.exists() and path.is_file():
            return str(path)
    for name in ("realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        ROOT_DIR / "tools" / "realesrgan-ncnn-vulkan.exe",
        ROOT_DIR / "tools" / "realesrgan-ncnn-vulkan" / "realesrgan-ncnn-vulkan.exe",
        WORK_DIR / "tools" / "realesrgan-ncnn-vulkan.exe",
        WORK_DIR / "tools" / "realesrgan-ncnn-vulkan" / "realesrgan-ncnn-vulkan.exe",
        DEFAULT_WORK_DIR / "tools" / "realesrgan-ncnn-vulkan.exe",
        DEFAULT_WORK_DIR / "tools" / "realesrgan-ncnn-vulkan" / "realesrgan-ncnn-vulkan.exe",
    ):
        if path.exists() and path.is_file():
            return str(path)
    return None


def resolve_realesrgan_model_dir(binary: str | None = None) -> Path | None:
    configured = str(os.environ.get(REAL_ESRGAN_MODEL_DIR_ENV, "")).strip().strip("\"'")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    if binary:
        candidates.append(Path(binary).resolve().parent / "models")
    candidates.extend(
        [
            ROOT_DIR / "tools" / "realesrgan-ncnn-vulkan" / "models",
            WORK_DIR / "tools" / "realesrgan-ncnn-vulkan" / "models",
            DEFAULT_WORK_DIR / "tools" / "realesrgan-ncnn-vulkan" / "models",
        ]
    )
    for path in candidates:
        param_path = path / f"{REAL_ESRGAN_ANIME_MODEL}.param"
        bin_path = path / f"{REAL_ESRGAN_ANIME_MODEL}.bin"
        if param_path.exists() and bin_path.exists():
            return path
    return None


def realesrgan_missing_message() -> str:
    return (
        "Real-ESRGAN anime is not ready. Expected "
        "realesrgan-ncnn-vulkan.exe plus models/realesrgan-x4plus-anime.param and .bin. "
        f"Set {REAL_ESRGAN_BINARY_ENV} and optionally {REAL_ESRGAN_MODEL_DIR_ENV}, "
        "or install the portable package under the configured work/tools/realesrgan-ncnn-vulkan."
    )


def average_visible_rgb(image: Image.Image) -> tuple[int, int, int]:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return (0, 0, 0)
    cropped = rgba.crop(bbox)
    pixels = list(cropped.getdata())
    visible = [pixel for pixel in pixels if pixel[3] > 0]
    if not visible:
        return (0, 0, 0)
    count = len(visible)
    return (
        sum(pixel[0] for pixel in visible) // count,
        sum(pixel[1] for pixel in visible) // count,
        sum(pixel[2] for pixel in visible) // count,
    )


def prepare_realesrgan_rgb_input(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new("RGB", rgba.size, average_visible_rgb(rgba))
    background.paste(rgba.convert("RGB"), mask=rgba.getchannel("A"))
    return background


def apply_alpha_cutoff(image: Image.Image, alpha_cutoff: int) -> Image.Image:
    rgba = image.convert("RGBA")
    if alpha_cutoff <= 0:
        return rgba
    red, green, blue, alpha = rgba.split()
    alpha = alpha.point(lambda value: 0 if value <= alpha_cutoff else value)
    return Image.merge("RGBA", (red, green, blue, alpha))


def quantize_rgba(image: Image.Image, color_count: int) -> Image.Image:
    if color_count >= 256:
        return image.convert("RGBA")
    rgba = image.convert("RGBA")
    try:
        return rgba.quantize(colors=color_count, method=Image.Quantize.FASTOCTREE).convert("RGBA")
    except Exception:
        return rgba


def resize_to_scale(image: Image.Image, source_size: tuple[int, int], scale: float) -> Image.Image:
    rgba = image.convert("RGBA")
    source_width, source_height = source_size
    target_width = max(1, round(source_width * scale))
    target_height = max(1, round(source_height * scale))
    return rgba.resize((target_width, target_height), LANCZOS)


def resize_rgba_premultiplied(
    image: Image.Image,
    target_size: tuple[int, int],
    resample: Image.Resampling = LANCZOS,
) -> Image.Image:
    return image.convert("RGBA").convert("RGBa").resize(target_size, resample).convert("RGBA")


def alpha_coverage(image: Image.Image) -> float:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    total = max(1, rgba.width * rgba.height)
    return sum(alpha.histogram()[1:]) / total


def image_has_visible_alpha(image: Image.Image) -> bool:
    return image.convert("RGBA").getchannel("A").getbbox() is not None


def image_path_has_visible_alpha(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            return image_has_visible_alpha(image)
    except (OSError, FileNotFoundError):
        return False


def magic_output_lost_alpha(source: Image.Image, output: Image.Image) -> bool:
    source_coverage = alpha_coverage(source)
    if source_coverage <= 0:
        return False
    output_coverage = alpha_coverage(output)
    return output_coverage <= 0 or output_coverage < source_coverage * MAGIC_ALPHA_LOSS_FALLBACK_RATIO


def fallback_magic_upscale(image: Image.Image, scale: int = MAGIC_UPSCALE) -> Image.Image:
    rgba = image.convert("RGBA")
    target_size = (max(1, rgba.width * scale), max(1, rgba.height * scale))
    return resize_rgba_premultiplied(rgba, target_size, LANCZOS)


def normalize_magic_resize_mode(value: str | None) -> str:
    mode = str(value or MAGIC_RESIZE_MODE_DEFAULT).strip().lower()
    return mode if mode in MAGIC_RESIZE_MODES else MAGIC_RESIZE_MODE_DEFAULT


def resize_magic_frame(
    image: Image.Image,
    source_size: tuple[int, int],
    scale: float,
    resize_mode: str = MAGIC_RESIZE_MODE_DEFAULT,
) -> Image.Image:
    source_width, source_height = source_size
    target_size = (max(1, round(source_width * scale)), max(1, round(source_height * scale)))
    mode = MAGIC_RESIZE_MODES[normalize_magic_resize_mode(resize_mode)]
    return resize_rgba_premultiplied(image, target_size, mode["resample"])


def expand_bbox(
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
    padding: int,
) -> tuple[int, int, int, int]:
    width, height = image_size
    left, top, right, bottom = bbox
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(width, right + padding),
        min(height, bottom + padding),
    )


def build_magic_upscaled_frame(
    source_rgba: Image.Image,
    ai_input_path: Path,
    ai_output_path: Path,
) -> tuple[Image.Image, tuple[int, int]]:
    source_rgba = source_rgba.convert("RGBA")
    source_size = source_rgba.size
    alpha_bbox = source_rgba.getchannel("A").getbbox()
    if not alpha_bbox:
        return Image.new("RGBA", (max(1, source_size[0] * 4), max(1, source_size[1] * 4)), (0, 0, 0, 0)), source_size

    crop_box = expand_bbox(alpha_bbox, source_size, MAGIC_CROP_PADDING)
    crop_rgba = source_rgba.crop(crop_box)
    crop_rgba.save(ai_input_path)
    run_realesrgan_anime(ai_input_path, ai_output_path)

    with Image.open(ai_output_path) as upscaled_image:
        upscaled_crop = upscaled_image.convert("RGBA")
    if magic_output_lost_alpha(crop_rgba, upscaled_crop):
        upscaled_crop.close()
        upscaled_crop = fallback_magic_upscale(crop_rgba)
        upscaled_crop.save(ai_output_path, optimize=True, compress_level=9)
    scale_x = max(1, round(upscaled_crop.width / crop_rgba.width))
    scale_y = max(1, round(upscaled_crop.height / crop_rgba.height))

    upscaled_full_size = (source_size[0] * scale_x, source_size[1] * scale_y)
    upscaled_full = Image.new("RGBA", upscaled_full_size, (0, 0, 0, 0))
    upscaled_full.alpha_composite(upscaled_crop, (crop_box[0] * scale_x, crop_box[1] * scale_y))
    return upscaled_full, source_size


def process_magic_frame(source_rgba: Image.Image, ai_input_path: Path, ai_output_path: Path) -> Image.Image:
    upscaled_full, source_size = build_magic_upscaled_frame(source_rgba, ai_input_path, ai_output_path)
    return resize_magic_frame(upscaled_full, source_size, 0.5, MAGIC_RESIZE_MODE_DEFAULT)


def run_realesrgan_anime(input_path: Path, output_path: Path, output_scale: int | None = None) -> None:
    binary = resolve_realesrgan_binary()
    model_dir = resolve_realesrgan_model_dir(binary)
    if not binary or not model_dir:
        raise RuntimeError(realesrgan_missing_message())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        binary,
        "-i",
        str(input_path),
        "-o",
        str(output_path),
        "-n",
        REAL_ESRGAN_ANIME_MODEL,
        "-m",
        str(model_dir),
        "-f",
        "png",
    ]
    if output_scale:
        args.extend(["-s", str(output_scale)])
    run_process(args)
    if not output_path.exists():
        raise RuntimeError("Real-ESRGAN anime did not produce an output image")


def resolve_magic_variant_dir(manifest: dict, variant: dict) -> Path:
    source_dir = Path(str(variant.get("frames_dir") or ""))
    if not source_dir.is_absolute():
        source_dir = MAGIC_DIR / f"{manifest['magic_id']}-magic" / str(source_dir)
    return source_dir


def find_cached_magic_frames(
    job_id: str,
    resize_mode: str,
    use_realesrgan: bool = True,
) -> dict[int, dict[str, dict]]:
    cache: dict[int, dict[str, dict]] = {}
    expected_model = REAL_ESRGAN_ANIME_MODEL if use_realesrgan else "none"
    try:
        job_manifest = load_job_manifest(job_id)
        processed_dir = job_dir(job_id) / "processed"
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        job_manifest = {}
        processed_dir = job_dir(job_id) / "processed"
    source_alpha_by_index: dict[int, bool] = {}
    for entry in job_manifest.get("frames") or []:
        source_index = safe_int(entry.get("index"), -1)
        source_path = processed_dir / str(entry.get("name") or "")
        if source_index >= 0 and source_path.exists():
            source_alpha_by_index[source_index] = image_path_has_visible_alpha(source_path)

    manifest_paths = sorted(
        MAGIC_DIR.glob("*-magic/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for manifest_path in manifest_paths:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("job_id") != job_id:
            continue
        manifest_uses_realesrgan = safe_bool(
            manifest.get("use_realesrgan"),
            manifest.get("model") == REAL_ESRGAN_ANIME_MODEL,
        )
        if manifest_uses_realesrgan != use_realesrgan:
            continue
        if manifest.get("model") != expected_model:
            continue
        if normalize_magic_resize_mode(manifest.get("resize_mode")) != resize_mode:
            continue

        variant_sources: dict[str, dict[int, dict]] = {}
        for variant_config in MAGIC_VARIANTS:
            variant = magic_manifest_variant(manifest, str(variant_config["key"]))
            source_dir = resolve_magic_variant_dir(manifest, variant)
            entries: dict[int, dict] = {}
            for entry in variant.get("frames") or []:
                source_index = safe_int(entry.get("source_index"), -1)
                source_path = source_dir / str(entry.get("name") or "")
                if source_index >= 0 and source_path.exists():
                    entries[source_index] = {
                        "entry": entry,
                        "path": source_path,
                    }
            variant_sources[str(variant_config["key"])] = entries

        primary_entries = variant_sources.get(str(MAGIC_VARIANTS[0]["key"]), {})
        for source_index in primary_entries:
            if source_index in cache:
                continue
            cached_variants = {}
            for variant_config in MAGIC_VARIANTS:
                variant_key = str(variant_config["key"])
                cached = variant_sources.get(variant_key, {}).get(source_index)
                if not cached:
                    break
                cached_variants[variant_key] = cached
            else:
                if source_alpha_by_index.get(source_index) and any(
                    not image_path_has_visible_alpha(cached["path"])
                    for cached in cached_variants.values()
                ):
                    continue
                cache[source_index] = cached_variants
    return cache


def process_line_cleaner_frames(
    file_items: list,
    method: str,
    scale: float,
    alpha_cutoff: int,
    sharpen_percent: int,
    color_count: int,
) -> dict:
    method = method if method in LINE_CLEANER_METHODS else "classic"
    candidates = []
    for item in file_items:
        raw_filename = str(getattr(item, "filename", "") or "frame")
        display_name = Path(raw_filename.replace("\\", "/")).name or "frame"
        if not getattr(item, "file", None):
            continue
        suffix = Path(display_name).suffix.lower()
        content_type = str(getattr(item, "type", "") or "")
        if suffix not in ANIMATION_FRAME_EXTENSIONS and not content_type.startswith("image/"):
            continue
        candidates.append((raw_filename, display_name, item))

    candidates.sort(key=lambda pair: natural_sort_key(pair[0]))
    if not candidates:
        raise ValueError("no supported image frames found")

    if method == "realesrgan_anime":
        binary = resolve_realesrgan_binary()
        if not binary or not resolve_realesrgan_model_dir(binary):
            raise RuntimeError(realesrgan_missing_message())

    run_id = timestamped_id()
    root = line_cleaner_dir(run_id)
    raw_dir = root / "raw"
    ai_input_dir = root / "ai-input"
    ai_output_dir = root / "ai-output"
    processed_dir = root / "processed"
    for directory in (raw_dir, ai_input_dir, ai_output_dir, processed_dir):
        directory.mkdir(parents=True, exist_ok=True)

    frames: list[dict] = []
    total_source_bytes = 0
    total_processed_bytes = 0
    max_width = 0
    max_height = 0

    for index, (_, display_name, item) in enumerate(candidates):
        frame_name = f"frame_{index + 1:03d}.png"
        raw_path = raw_dir / frame_name
        processed_path = processed_dir / frame_name

        with Image.open(item.file) as source_image:
            source_rgba = source_image.convert("RGBA")
        source_rgba.save(raw_path, optimize=True, compress_level=9)
        total_source_bytes += raw_path.stat().st_size

        working = source_rgba
        if method == "realesrgan_anime":
            ai_input_path = ai_input_dir / frame_name
            ai_output_path = ai_output_dir / frame_name
            prepare_realesrgan_rgb_input(source_rgba).save(ai_input_path)
            run_realesrgan_anime(ai_input_path, ai_output_path)
            upscaled_rgb = Image.open(ai_output_path).convert("RGB")
            upscaled_alpha = source_rgba.getchannel("A").resize(upscaled_rgb.size, LANCZOS)
            working = Image.merge("RGBA", (*upscaled_rgb.split(), upscaled_alpha))

        resized = resize_to_scale(working, source_rgba.size, scale)
        cleaned = apply_alpha_cutoff(resized, alpha_cutoff)
        if sharpen_percent > 0:
            cleaned = cleaned.filter(ImageFilter.UnsharpMask(radius=1.0, percent=sharpen_percent, threshold=1))
        cleaned = quantize_rgba(cleaned, color_count)
        cleaned.save(processed_path, optimize=True, compress_level=9)

        processed_bytes = processed_path.stat().st_size
        total_processed_bytes += processed_bytes
        max_width = max(max_width, cleaned.width)
        max_height = max(max_height, cleaned.height)
        frames.append(
            {
                "index": index,
                "name": frame_name,
                "original_name": display_name,
                "url": f"/work/line-cleaner/{run_id}/processed/{frame_name}",
                "width": cleaned.width,
                "height": cleaned.height,
                "bytes": processed_bytes,
            }
        )

    manifest = {
        "run_id": run_id,
        "method": method,
        "model": REAL_ESRGAN_ANIME_MODEL if method == "realesrgan_anime" else "",
        "scale": scale,
        "alpha_cutoff": alpha_cutoff,
        "sharpen_percent": sharpen_percent,
        "color_count": color_count,
        "frame_count": len(frames),
        "source_bytes": total_source_bytes,
        "processed_bytes": total_processed_bytes,
        "max_width": max_width,
        "max_height": max_height,
        "frames": frames,
        "created_at": iso_now(),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def write_aligned_rgba_video_frames(
    frame_paths: list[Path],
    frame_sizes: list[tuple[int, int]],
    target_dir: Path,
    cell_width: int,
    cell_height: int,
    render_sizes: list[tuple[int, int]] | None = None,
    resize_resample: Image.Resampling = NEAREST,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for index, frame_path in enumerate(frame_paths, start=1):
        frame = open_rgba_image(frame_path)
        render_size = render_sizes[index - 1] if render_sizes else frame_sizes[index - 1]
        frame_width, frame_height = render_size
        if frame.size != render_size:
            resized = frame.resize(render_size, resize_resample)
            frame.close()
            frame = resized
        canvas = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
        offset_x = (cell_width - frame_width) // 2
        offset_y = (cell_height - frame_height) // 2
        canvas.paste(frame, (offset_x, offset_y), frame)
        frame.close()
        canvas.save(target_dir / f"frame_{index:03d}.png")
        canvas.close()


def save_alpha_mov(
    frame_paths: list[Path],
    frame_sizes: list[tuple[int, int]],
    output_path: Path,
    cell_width: int,
    cell_height: int,
    duration_ms: int,
    render_sizes: list[tuple[int, int]] | None = None,
) -> None:
    if not frame_paths:
        raise ValueError("no frames selected for alpha video export")
    ffmpeg = resolve_ffmpeg_binary("ffmpeg")
    duration_ms = clamp_int(duration_ms, 20, 5000)
    video_frames_dir = output_path.parent / "mov_frames_tmp"
    if video_frames_dir.exists():
        shutil.rmtree(video_frames_dir)
    try:
        write_aligned_rgba_video_frames(
            frame_paths,
            frame_sizes,
            video_frames_dir,
            cell_width,
            cell_height,
            render_sizes=render_sizes,
            resize_resample=NEAREST,
        )
        input_pattern = video_frames_dir / "frame_%03d.png"
        run_process(
            [
                ffmpeg,
                "-y",
                "-framerate",
                f"1000/{duration_ms}",
                "-start_number",
                "1",
                "-i",
                str(input_pattern),
                "-frames:v",
                str(len(frame_paths)),
                "-c:v",
                "qtrle",
                "-pix_fmt",
                "argb",
                "-an",
                str(output_path),
            ]
        )
    finally:
        shutil.rmtree(video_frames_dir, ignore_errors=True)


def save_gif(
    frame_paths: list[Path],
    frame_sizes: list[tuple[int, int]],
    output_path: Path,
    cell_width: int,
    cell_height: int,
    duration_ms: int,
) -> None:
    if not frame_paths:
        raise ValueError("no frames selected for GIF export")
    ffmpeg = resolve_ffmpeg_binary("ffmpeg")
    duration_ms = clamp_int(duration_ms, 20, 5000)
    video_frames_dir = output_path.parent / "gif_frames_tmp"
    palette_path = output_path.parent / f"{output_path.stem}-palette.png"
    if video_frames_dir.exists():
        shutil.rmtree(video_frames_dir)
    if palette_path.exists():
        palette_path.unlink()
    try:
        write_aligned_rgba_video_frames(frame_paths, frame_sizes, video_frames_dir, cell_width, cell_height)
        input_pattern = video_frames_dir / "frame_%03d.png"
        run_process(
            [
                ffmpeg,
                "-y",
                "-framerate",
                f"1000/{duration_ms}",
                "-start_number",
                "1",
                "-i",
                str(input_pattern),
                "-frames:v",
                str(len(frame_paths)),
                "-vf",
                "palettegen=reserve_transparent=1:transparency_color=000000",
                str(palette_path),
            ]
        )
        run_process(
            [
                ffmpeg,
                "-y",
                "-framerate",
                f"1000/{duration_ms}",
                "-start_number",
                "1",
                "-i",
                str(input_pattern),
                "-i",
                str(palette_path),
                "-frames:v",
                str(len(frame_paths)),
                "-lavfi",
                "paletteuse=alpha_threshold=128",
                "-loop",
                "0",
                str(output_path),
            ]
        )
    finally:
        shutil.rmtree(video_frames_dir, ignore_errors=True)
        if palette_path.exists():
            palette_path.unlink()


def save_sprite_sheet(
    frame_paths: list[Path],
    frame_sizes: list[tuple[int, int]],
    sheet_path: Path,
    metadata_path: Path,
    cell_width: int,
    cell_height: int,
    duration_ms: int,
) -> dict:
    if not frame_paths:
        raise ValueError("no frames selected for sprite sheet export")

    frame_count = len(frame_paths)
    columns = max(1, math.ceil(math.sqrt(frame_count)))
    rows = max(1, math.ceil(frame_count / columns))
    sheet_width = columns * cell_width
    sheet_height = rows * cell_height
    sheet = Image.new("RGBA", (sheet_width, sheet_height), (0, 0, 0, 0))
    frames: list[dict] = []

    try:
        for index, frame_path in enumerate(frame_paths):
            frame = open_rgba_image(frame_path)
            frame_width, frame_height = frame_sizes[index]
            column = index % columns
            row = index // columns
            cell_x = column * cell_width
            cell_y = row * cell_height
            offset_x = cell_x + (cell_width - frame_width) // 2
            offset_y = cell_y + (cell_height - frame_height) // 2
            sheet.paste(frame, (offset_x, offset_y), frame)
            frame.close()
            frames.append(
                {
                    "index": index,
                    "name": frame_path.name,
                    "duration_ms": duration_ms,
                    "frame": {
                        "x": cell_x,
                        "y": cell_y,
                        "w": cell_width,
                        "h": cell_height,
                    },
                    "sprite": {
                        "x": offset_x,
                        "y": offset_y,
                        "w": frame_width,
                        "h": frame_height,
                    },
                    "source_size": {
                        "w": frame_width,
                        "h": frame_height,
                    },
                }
            )
        sheet.save(sheet_path, optimize=True, compress_level=9)
    finally:
        sheet.close()

    metadata = {
        "image": sheet_path.name,
        "format": "fixed-grid",
        "frame_count": frame_count,
        "columns": columns,
        "rows": rows,
        "cell_width": cell_width,
        "cell_height": cell_height,
        "width": sheet_width,
        "height": sheet_height,
        "duration_ms": duration_ms,
        "frames": frames,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def magic_preview_job(
    job_id: str,
    selected_indices: list[int],
    resize_mode: str = MAGIC_RESIZE_MODE_DEFAULT,
    use_realesrgan: bool = True,
) -> dict:
    resize_mode = normalize_magic_resize_mode(resize_mode)
    resize_mode_label = str(MAGIC_RESIZE_MODES[resize_mode]["label"])
    use_realesrgan = bool(use_realesrgan)
    model_name = REAL_ESRGAN_ANIME_MODEL if use_realesrgan else "none"
    manifest = load_job_manifest(job_id)
    processed_dir = job_dir(job_id) / "processed"
    frame_map = {entry["index"]: entry for entry in manifest["frames"]}
    seen_indices: set[int] = set()
    indices: list[int] = []
    for index in selected_indices:
        if index in frame_map and index not in seen_indices:
            indices.append(index)
            seen_indices.add(index)
    if not indices:
        raise ValueError("no frames selected for MAGIC")

    if use_realesrgan:
        binary = resolve_realesrgan_binary()
        if not binary or not resolve_realesrgan_model_dir(binary):
            raise RuntimeError(realesrgan_missing_message())

    magic_id = timestamped_id()
    root = MAGIC_DIR / f"{magic_id}-magic"
    ai_input_dir = root / "ai-input"
    ai_output_dir = root / "ai-output"
    variants: dict[str, dict] = {}
    for variant in MAGIC_VARIANTS:
        frames_dir = root / str(variant["dir"])
        variants[str(variant["key"])] = {
            "key": str(variant["key"]),
            "label": str(variant["label"]),
            "scale": float(variant["scale"]),
            "frames_dir": str(frames_dir),
            "frame_count": 0,
            "max_width": 0,
            "max_height": 0,
            "bytes": 0,
            "frames": [],
        }

    for directory in (ai_input_dir, ai_output_dir, *[Path(variant["frames_dir"]) for variant in variants.values()]):
        directory.mkdir(parents=True, exist_ok=True)

    cached_magic = find_cached_magic_frames(job_id, resize_mode, use_realesrgan)
    generated_count = 0
    reused_count = 0
    for output_index, frame_index in enumerate(indices, start=1):
        entry = frame_map[frame_index]
        source_path = processed_dir / entry["name"]
        frame_name = f"frame_{output_index:03d}.png"
        ai_input_path = ai_input_dir / frame_name
        ai_output_path = ai_output_dir / frame_name

        cached_variants = cached_magic.get(frame_index)
        if cached_variants:
            for variant in variants.values():
                variant_key = str(variant["key"])
                cached = cached_variants[variant_key]
                cached_entry = cached["entry"]
                frames_dir = Path(variant["frames_dir"])
                processed_path = frames_dir / frame_name
                shutil.copy2(cached["path"], processed_path)
                processed_bytes = processed_path.stat().st_size
                frame_width = safe_int(cached_entry.get("width"), 0)
                frame_height = safe_int(cached_entry.get("height"), 0)
                if frame_width <= 0 or frame_height <= 0:
                    with Image.open(processed_path) as frame:
                        frame_width, frame_height = frame.size
                variant["bytes"] += processed_bytes
                variant["frame_count"] += 1
                variant["max_width"] = max(int(variant["max_width"]), frame_width)
                variant["max_height"] = max(int(variant["max_height"]), frame_height)
                variant["frames"].append(
                    {
                        "index": output_index - 1,
                        "source_index": frame_index,
                        "name": frame_name,
                        "original_name": entry.get("original_name") or entry.get("name") or frame_name,
                        "url": f"/work/magic/{root.name}/{frames_dir.name}/{frame_name}",
                        "width": frame_width,
                        "height": frame_height,
                        "bytes": processed_bytes,
                        "cached": True,
                    }
                )
            reused_count += 1
            continue

        with Image.open(source_path) as image:
            source_rgba = image.convert("RGBA")
        if use_realesrgan:
            upscaled_frame, source_size = build_magic_upscaled_frame(source_rgba, ai_input_path, ai_output_path)
        else:
            source_size = source_rgba.size
            upscaled_frame = source_rgba.copy()
        generated_count += 1

        for variant in variants.values():
            frames_dir = Path(variant["frames_dir"])
            processed_path = frames_dir / frame_name
            magic_frame = resize_magic_frame(
                upscaled_frame,
                source_size,
                float(variant["scale"]),
                resize_mode,
            )
            magic_frame.save(processed_path, optimize=True, compress_level=9)
            processed_bytes = processed_path.stat().st_size
            variant["bytes"] += processed_bytes
            variant["frame_count"] += 1
            variant["max_width"] = max(int(variant["max_width"]), magic_frame.width)
            variant["max_height"] = max(int(variant["max_height"]), magic_frame.height)
            variant["frames"].append(
                {
                    "index": output_index - 1,
                    "source_index": frame_index,
                    "name": frame_name,
                    "original_name": entry.get("original_name") or entry.get("name") or frame_name,
                    "url": f"/work/magic/{root.name}/{frames_dir.name}/{frame_name}",
                    "width": magic_frame.width,
                    "height": magic_frame.height,
                    "bytes": processed_bytes,
                    "cached": False,
                }
            )
            magic_frame.close()

        source_rgba.close()
        upscaled_frame.close()

    primary = variants["half"]

    result = {
        "magic_id": magic_id,
        "job_id": job_id,
        "model": model_name,
        "use_realesrgan": use_realesrgan,
        "upscale": MAGIC_UPSCALE if use_realesrgan else 1,
        "final_scale": 0.5,
        "resize_mode": resize_mode,
        "resize_mode_label": resize_mode_label,
        "output_dir": str(root),
        "frames_dir": str(primary["frames_dir"]),
        "frame_count": int(primary["frame_count"]),
        "max_width": int(primary["max_width"]),
        "max_height": int(primary["max_height"]),
        "bytes": int(primary["bytes"]),
        "frames": primary["frames"],
        "variants": variants,
        "generated_count": generated_count,
        "reused_count": reused_count,
        "created_at": iso_now(),
    }
    (root / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_magic_manifest(magic_id: str) -> dict:
    magic_id = str(magic_id or "").strip()
    if not magic_id or Path(magic_id).name != magic_id:
        raise ValueError("invalid MAGIC id")
    path = MAGIC_DIR / f"{magic_id}-magic" / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"MAGIC result not found: {magic_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def magic_manifest_variant(manifest: dict, variant_key: str) -> dict:
    variants = manifest.get("variants") or {}
    key = variant_key if variant_key in variants else "half"
    if key in variants:
        return variants[key]
    return {
        "key": "half",
        "label": "1/2",
        "frames_dir": manifest.get("frames_dir") or "",
        "frame_count": manifest.get("frame_count") or 0,
        "max_width": manifest.get("max_width") or 0,
        "max_height": manifest.get("max_height") or 0,
        "frames": manifest.get("frames") or [],
    }


def export_magic_frames(
    magic_id: str,
    variant_key: str = "half",
    video_duration_ms: int = 100,
) -> dict:
    manifest = load_magic_manifest(magic_id)
    job_manifest = load_job_manifest(str(manifest.get("job_id") or ""))
    job_frame_sizes = {
        entry["index"]: (
            safe_int(entry.get("width"), 0),
            safe_int(entry.get("height"), 0),
        )
        for entry in job_manifest.get("frames") or []
    }
    variant = magic_manifest_variant(manifest, variant_key)
    source_dir = resolve_magic_variant_dir(manifest, variant)
    if not source_dir.exists():
        raise FileNotFoundError(f"MAGIC frames not found: {manifest['magic_id']}")

    variant_key = str(variant.get("key") or "half")
    target_dir = configured_exports_dir() / f"{timestamped_id()}-magic-{variant_key}-frames"
    frames_dir = target_dir / "frames"
    sheet_dir = target_dir / "sprite-sheet"
    frames_dir.mkdir(parents=True, exist_ok=True)
    sheet_dir.mkdir(parents=True, exist_ok=True)

    copied_count = 0
    copied_paths: list[Path] = []
    mov_frame_sizes: list[tuple[int, int]] = []
    for output_index, entry in enumerate(variant.get("frames") or [], start=1):
        source_path = source_dir / str(entry.get("name") or "")
        if not source_path.exists():
            continue
        target_path = frames_dir / f"frame_{output_index:03d}.png"
        shutil.copy2(source_path, target_path)
        copied_count += 1
        copied_paths.append(target_path)
        source_size = job_frame_sizes.get(safe_int(entry.get("source_index"), -1))
        if not source_size or source_size[0] <= 0 or source_size[1] <= 0:
            source_size = (
                max(1, round(safe_int(entry.get("width"), 1) / max(float(variant.get("scale") or 1), 0.0001))),
                max(1, round(safe_int(entry.get("height"), 1) / max(float(variant.get("scale") or 1), 0.0001))),
            )
        mov_frame_sizes.append(source_size)

    if copied_count <= 0:
        raise ValueError("no MAGIC frames exported")

    cell_width = 0
    cell_height = 0
    frame_sizes: list[tuple[int, int]] = []
    for frame_path in copied_paths:
        frame = open_rgba_image(frame_path)
        frame_sizes.append(frame.size)
        cell_width = max(cell_width, frame.size[0])
        cell_height = max(cell_height, frame.size[1])
        frame.close()

    video_duration_ms = clamp_int(video_duration_ms, 20, 5000)
    timestamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    mov_name = f"magic-{variant_key}-{timestamp}.mov"
    gif_name = f"magic-{variant_key}-{timestamp}.gif"
    sheet_name = "sheet.png"
    sheet_json_name = "sheet.json"
    mov_cell_width = max(width for width, _height in mov_frame_sizes)
    mov_cell_height = max(height for _width, height in mov_frame_sizes)
    save_alpha_mov(
        copied_paths,
        frame_sizes,
        target_dir / mov_name,
        mov_cell_width,
        mov_cell_height,
        video_duration_ms,
        render_sizes=mov_frame_sizes,
    )
    save_gif(copied_paths, frame_sizes, target_dir / gif_name, cell_width, cell_height, video_duration_ms)
    sheet_metadata = save_sprite_sheet(
        copied_paths,
        frame_sizes,
        sheet_dir / sheet_name,
        sheet_dir / sheet_json_name,
        cell_width,
        cell_height,
        video_duration_ms,
    )

    result = {
        "output_dir": str(target_dir),
        "frames_dir": str(frames_dir),
        "sheet_dir": str(sheet_dir),
        "video_name": mov_name,
        "video_url": export_url(target_dir, mov_name),
        "mov_name": mov_name,
        "mov_url": export_url(target_dir, mov_name),
        "gif_name": gif_name,
        "gif_url": export_url(target_dir, gif_name),
        "sheet_name": sheet_name,
        "sheet_url": export_url(target_dir, f"sprite-sheet/{sheet_name}"),
        "sheet_json_name": sheet_json_name,
        "sheet_json_url": export_url(target_dir, f"sprite-sheet/{sheet_json_name}"),
        "sheet_columns": sheet_metadata["columns"],
        "sheet_rows": sheet_metadata["rows"],
        "sheet_width": sheet_metadata["width"],
        "sheet_height": sheet_metadata["height"],
        "frame_count": copied_count,
        "max_width": mov_cell_width,
        "max_height": mov_cell_height,
        "gif_width": variant.get("max_width") or 0,
        "gif_height": variant.get("max_height") or 0,
        "video_duration_ms": video_duration_ms,
        "source_magic_id": manifest.get("magic_id") or magic_id,
        "variant_key": variant_key,
        "variant_label": variant.get("label") or "1/2",
        "model": manifest.get("model") or REAL_ESRGAN_ANIME_MODEL,
        "use_realesrgan": safe_bool(
            manifest.get("use_realesrgan"),
            manifest.get("model") == REAL_ESRGAN_ANIME_MODEL,
        ),
        "resize_mode": manifest.get("resize_mode") or MAGIC_RESIZE_MODE_DEFAULT,
        "resize_mode_label": manifest.get("resize_mode_label") or MAGIC_RESIZE_MODES[MAGIC_RESIZE_MODE_DEFAULT]["label"],
        "created_at": iso_now(),
    }
    (target_dir / "export.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def export_job(job_id: str, selected_indices: list[int], video_duration_ms: int) -> dict:
    manifest = load_job_manifest(job_id)
    processed_dir = job_dir(job_id) / "processed"
    target_dir = configured_exports_dir() / f"{timestamped_id()}-export"
    frames_dir = target_dir / "frames"
    sheet_dir = target_dir / "sprite-sheet"
    frames_dir.mkdir(parents=True, exist_ok=True)
    sheet_dir.mkdir(parents=True, exist_ok=True)

    frame_map = {entry["index"]: entry for entry in manifest["frames"]}
    seen_indices: set[int] = set()
    indices: list[int] = []
    for index in selected_indices:
        if index in frame_map and index not in seen_indices:
            indices.append(index)
            seen_indices.add(index)
    if not indices:
        raise ValueError("no frames selected for export")

    copied_paths: list[Path] = []
    for output_index, frame_index in enumerate(indices, start=1):
        entry = frame_map[frame_index]
        source_path = processed_dir / entry["name"]
        target_path = frames_dir / f"frame_{output_index:03d}.png"
        shutil.copy2(source_path, target_path)
        copied_paths.append(target_path)

    cell_width = 0
    cell_height = 0
    frame_sizes: list[tuple[int, int]] = []
    for frame_path in copied_paths:
        frame = open_rgba_image(frame_path)
        frame_sizes.append(frame.size)
        cell_width = max(cell_width, frame.size[0])
        cell_height = max(cell_height, frame.size[1])
        frame.close()

    video_duration_ms = clamp_int(video_duration_ms, 20, 5000)
    timestamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    mov_name = f"animation-{timestamp}.mov"
    gif_name = f"animation-{timestamp}.gif"
    sheet_name = "sheet.png"
    sheet_json_name = "sheet.json"
    save_alpha_mov(copied_paths, frame_sizes, target_dir / mov_name, cell_width, cell_height, video_duration_ms)
    save_gif(copied_paths, frame_sizes, target_dir / gif_name, cell_width, cell_height, video_duration_ms)
    sheet_metadata = save_sprite_sheet(
        copied_paths,
        frame_sizes,
        sheet_dir / sheet_name,
        sheet_dir / sheet_json_name,
        cell_width,
        cell_height,
        video_duration_ms,
    )

    return {
        "output_dir": str(target_dir),
        "frames_dir": str(frames_dir),
        "sheet_dir": str(sheet_dir),
        "video_name": mov_name,
        "video_url": export_url(target_dir, mov_name),
        "mov_name": mov_name,
        "mov_url": export_url(target_dir, mov_name),
        "gif_name": gif_name,
        "gif_url": export_url(target_dir, gif_name),
        "sheet_name": sheet_name,
        "sheet_url": export_url(target_dir, f"sprite-sheet/{sheet_name}"),
        "sheet_json_name": sheet_json_name,
        "sheet_json_url": export_url(target_dir, f"sprite-sheet/{sheet_json_name}"),
        "sheet_columns": sheet_metadata["columns"],
        "sheet_rows": sheet_metadata["rows"],
        "sheet_width": sheet_metadata["width"],
        "sheet_height": sheet_metadata["height"],
        "frame_count": len(copied_paths),
        "video_duration_ms": video_duration_ms,
    }


class AppHandler(BaseHTTPRequestHandler):
    server_version = "SpriteVideoLab/0.1"

    def log_message(self, format, *args) -> None:
        return

    def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"ok": False, "error": message}, status=status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/app-version":
            self.send_json(
                {
                    "ok": True,
                    "version": current_app_version(),
                    "poll_ms": APP_VERSION_POLL_MS,
                }
            )
            return
        if parsed.path == "/api/runtime-info":
            self.send_json({"ok": True, "runtime": runtime_info()})
            return
        if parsed.path == "/api/output-path":
            self.send_json({"ok": True, "output_path": output_path_payload()})
            return
        if parsed.path == "/":
            self.serve_app_file(APP_DIR / "index.html", content_type="text/html; charset=utf-8")
            return
        if parsed.path.startswith("/app/"):
            relative = parsed.path.removeprefix("/app/")
            self.serve_app_file(APP_DIR / relative)
            return
        if parsed.path.startswith("/media/upload/"):
            upload_id = parsed.path.removeprefix("/media/upload/")
            self.serve_media_file(source_video_path(upload_id), allow_range=True)
            return
        if parsed.path.startswith("/work/"):
            relative = parsed.path.removeprefix("/work/")
            self.serve_work_file((WORK_DIR / relative).resolve())
            return
        if parsed.path.startswith("/exports/"):
            relative = parsed.path.removeprefix("/exports/")
            self.serve_export_file((configured_exports_dir() / relative).resolve())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/import-path":
                payload = self.read_json_body()
                raw_path = str(payload.get("path") or "").strip().strip("\"'")
                result = register_video_from_path(Path(raw_path))
                self.send_json({"ok": True, "upload": result})
                return
            if parsed.path == "/api/upload":
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={
                        "REQUEST_METHOD": "POST",
                        "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                        "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                    },
                )
                file_items = field_storage_items(form, "video")
                if not file_items:
                    raise ValueError("media file missing")
                result = register_uploaded_media(file_items)
                self.send_json({"ok": True, "upload": result})
                return
            if parsed.path == "/api/import-animation":
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={
                        "REQUEST_METHOD": "POST",
                        "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                        "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                    },
                )
                result = import_animation_frames_to_job(field_storage_items(form, "frames"))
                self.send_json({"ok": True, "job": result})
                return
            if parsed.path == "/api/line-cleaner-process":
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={
                        "REQUEST_METHOD": "POST",
                        "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                        "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                    },
                )
                result = process_line_cleaner_frames(
                    field_storage_items(form, "frames"),
                    method=str(form.getfirst("method", "classic")),
                    scale=clamp_float(safe_float(form.getfirst("scale", form.getfirst("output_scale", 0.5)), 0.5), 0.05, 2.0),
                    alpha_cutoff=clamp_int(safe_int(form.getfirst("alpha_cutoff", 8), 8), 0, 255),
                    sharpen_percent=clamp_int(safe_int(form.getfirst("sharpen_percent", 80), 80), 0, 300),
                    color_count=clamp_int(safe_int(form.getfirst("color_count", 128), 128), 2, 256),
                )
                self.send_json({"ok": True, "result": result})
                return
            if parsed.path == "/api/process":
                payload = self.read_json_body()
                upload_id = str(payload.get("upload_id") or "")
                result = process_video_to_job(
                    upload_id=upload_id,
                    start_time=safe_float(payload.get("start_time"), 0.0),
                    end_time=safe_float(payload.get("end_time"), 0.0),
                    start_frame=safe_int(payload.get("start_frame"), 0),
                    end_frame=safe_int(payload.get("end_frame"), 0),
                    keep_every=max(1, safe_int(payload.get("keep_every"), 1)),
                    output_scale=output_scale_from_upload_payload(upload_id, payload),
                    reduce_px=max(0, safe_int(payload.get("reduce_px"), 0)),
                    canvas_mode=normalize_canvas_mode(str(payload.get("canvas_mode") or "auto")),
                    chroma_enabled=bool(payload.get("chroma_enabled", True)),
                    matte_mode=str(payload.get("matte_mode") or ""),
                    key_mode=str(payload.get("key_mode") or "auto"),
                    manual_key_hex=str(payload.get("manual_key_hex") or "#00FF00"),
                    threshold=max(0, safe_int(payload.get("threshold"), 80)),
                    softness=max(0, safe_int(payload.get("softness"), 32)),
                    despill_strength=max(0.0, safe_float(payload.get("despill_strength"), 0.85)),
                    halo_pixels=max(0, safe_int(payload.get("halo_pixels"), 1)),
                    ai_model=normalize_ai_model_key(str(payload.get("ai_model") or DEFAULT_AI_MATTE_MODEL)),
                    ai_device=normalize_ai_device(str(payload.get("ai_device") or "auto")),
                    ai_resolution=payload.get("ai_resolution"),
                    luma_black=max(0, min(254, safe_int(payload.get("luma_black"), 24))),
                    luma_white=max(1, min(255, safe_int(payload.get("luma_white"), 230))),
                    luma_gamma=max(0.05, safe_float(payload.get("luma_gamma"), 1.0)),
                    luma_strength=max(0.0, min(2.0, safe_float(payload.get("luma_strength"), 1.0))),
                    luma_polarity=normalize_luma_polarity(str(payload.get("luma_polarity") or "auto")),
                    corridorkey_enabled=bool(payload.get("corridorkey_enabled", False)),
                    corridorkey_screen=normalize_corridorkey_screen(str(payload.get("corridorkey_screen") or "auto")),
                    batch_green_to_black=bool(payload.get("batch_green_to_black", False)),
                    batch_green_desaturate=bool(payload.get("batch_green_desaturate", False)),
                    batch_semitransparent_to_black=bool(payload.get("batch_semitransparent_to_black", False)),
                    batch_semitransparent_to_opaque=bool(payload.get("batch_semitransparent_to_opaque", False)),
                    production_context={
                        key: str(payload.get(key)).strip()
                        for key in ("production_id", "scene_id", "shot_id", "shot_version_id")
                        if payload_has_value(payload, key)
                    } or None,
                )
                self.send_json({"ok": True, "job": result})
                return
            if parsed.path == "/api/preview-frame":
                payload = self.read_json_body()
                upload_id = str(payload.get("upload_id") or "")
                result = preview_frame(
                    upload_id=upload_id,
                    sample_time=safe_float(payload.get("sample_time"), 0.0),
                    sample_frame=safe_int(payload.get("sample_frame"), 1),
                    output_scale=output_scale_from_upload_payload(upload_id, payload),
                    reduce_px=max(0, safe_int(payload.get("reduce_px"), 0)),
                    canvas_mode=normalize_canvas_mode(str(payload.get("canvas_mode") or "auto")),
                    chroma_enabled=bool(payload.get("chroma_enabled", True)),
                    matte_mode=str(payload.get("matte_mode") or ""),
                    key_mode=str(payload.get("key_mode") or "auto"),
                    manual_key_hex=str(payload.get("manual_key_hex") or "#00FF00"),
                    threshold=max(0, safe_int(payload.get("threshold"), 80)),
                    softness=max(0, safe_int(payload.get("softness"), 32)),
                    despill_strength=max(0.0, safe_float(payload.get("despill_strength"), 0.85)),
                    halo_pixels=max(0, safe_int(payload.get("halo_pixels"), 1)),
                    ai_model=normalize_ai_model_key(str(payload.get("ai_model") or DEFAULT_AI_MATTE_MODEL)),
                    ai_device=normalize_ai_device(str(payload.get("ai_device") or "auto")),
                    ai_resolution=payload.get("ai_resolution"),
                    luma_black=max(0, min(254, safe_int(payload.get("luma_black"), 24))),
                    luma_white=max(1, min(255, safe_int(payload.get("luma_white"), 230))),
                    luma_gamma=max(0.05, safe_float(payload.get("luma_gamma"), 1.0)),
                    luma_strength=max(0.0, min(2.0, safe_float(payload.get("luma_strength"), 1.0))),
                    luma_polarity=normalize_luma_polarity(str(payload.get("luma_polarity") or "auto")),
                    corridorkey_enabled=bool(payload.get("corridorkey_enabled", False)),
                    corridorkey_screen=normalize_corridorkey_screen(str(payload.get("corridorkey_screen") or "auto")),
                    batch_green_to_black=bool(payload.get("batch_green_to_black", False)),
                    batch_green_desaturate=bool(payload.get("batch_green_desaturate", False)),
                    batch_semitransparent_to_black=bool(payload.get("batch_semitransparent_to_black", False)),
                    batch_semitransparent_to_opaque=bool(payload.get("batch_semitransparent_to_opaque", False)),
                )
                self.send_json({"ok": True, "preview": result})
                return
            if parsed.path == "/api/save-preview":
                payload = self.read_json_body()
                result = save_preview_as_job(str(payload.get("preview_id") or ""))
                self.send_json({"ok": True, "job": result})
                return
            if parsed.path == "/api/preview-green-to-black":
                payload = self.read_json_body()
                result = green_to_black_preview(
                    str(payload.get("preview_id") or ""),
                    threshold=max(0, min(255, safe_int(payload.get("threshold"), 42))),
                    dominance=max(0, min(255, safe_int(payload.get("dominance"), 24))),
                )
                self.send_json({"ok": True, "preview": result})
                return
            if parsed.path == "/api/preview-green-desaturate":
                payload = self.read_json_body()
                result = green_desaturate_preview(
                    str(payload.get("preview_id") or ""),
                    threshold=max(0, min(255, safe_int(payload.get("threshold"), 42))),
                    dominance=max(0, min(255, safe_int(payload.get("dominance"), 24))),
                )
                self.send_json({"ok": True, "preview": result})
                return
            if parsed.path == "/api/preview-semitransparent-to-black":
                payload = self.read_json_body()
                result = semitransparent_to_black_preview(
                    str(payload.get("preview_id") or ""),
                    alpha_min=max(0, min(255, safe_int(payload.get("alpha_min"), 1))),
                    alpha_max=max(0, min(255, safe_int(payload.get("alpha_max"), 254))),
                )
                self.send_json({"ok": True, "preview": result})
                return
            if parsed.path == "/api/preview-semitransparent-to-opaque":
                payload = self.read_json_body()
                result = semitransparent_to_opaque_preview(
                    str(payload.get("preview_id") or ""),
                    alpha_min=max(0, min(255, safe_int(payload.get("alpha_min"), 1))),
                    alpha_max=max(0, min(255, safe_int(payload.get("alpha_max"), 254))),
                )
                self.send_json({"ok": True, "preview": result})
                return
            if parsed.path == "/api/export":
                payload = self.read_json_body()
                result = export_job(
                    job_id=str(payload.get("job_id") or ""),
                    selected_indices=[safe_int(value, -1) for value in (payload.get("selected_indices") or [])],
                    video_duration_ms=safe_int(payload.get("video_duration_ms"), 100),
                )
                self.send_json({"ok": True, "export": result})
                return
            if parsed.path == "/api/magic-preview":
                if not MAGIC_PREVIEW_LOCK.acquire(blocking=False):
                    self.send_error_json("MAGIC 正在处理，请等当前任务结束后再点。", HTTPStatus.CONFLICT)
                    return
                payload = self.read_json_body()
                try:
                    result = magic_preview_job(
                        job_id=str(payload.get("job_id") or ""),
                        selected_indices=[safe_int(value, -1) for value in (payload.get("selected_indices") or [])],
                        resize_mode=str(payload.get("resize_mode") or MAGIC_RESIZE_MODE_DEFAULT),
                        use_realesrgan=safe_bool(payload.get("use_realesrgan"), True),
                    )
                    self.send_json({"ok": True, "magic": result})
                finally:
                    MAGIC_PREVIEW_LOCK.release()
                return
            if parsed.path == "/api/export-magic-frames":
                payload = self.read_json_body()
                result = export_magic_frames(
                    str(payload.get("magic_id") or ""),
                    variant_key=str(payload.get("variant_key") or "half"),
                    video_duration_ms=safe_int(payload.get("video_duration_ms"), 100),
                )
                self.send_json({"ok": True, "export": result})
                return
            if parsed.path == "/api/output-path":
                payload = self.read_json_body()
                result = set_output_path(str(payload.get("path") or ""))
                self.send_json({"ok": True, "output_path": result})
                return
            if parsed.path == "/api/select-output-path":
                result = choose_output_path()
                self.send_json(
                    {
                        "ok": True,
                        "output_path": result,
                        "cancelled": bool(result.get("cancelled")),
                    }
                )
                return
            if parsed.path == "/api/open-path":
                payload = self.read_json_body()
                target = Path(str(payload.get("path") or "").strip()).expanduser().resolve()
                if not target.exists():
                    raise FileNotFoundError(target)
                open_path_in_file_browser(target)
                self.send_json({"ok": True})
                return
        except FileNotFoundError as exc:
            self.send_error_json(str(exc), status=HTTPStatus.NOT_FOUND)
            return
        except Exception as exc:
            self.send_error_json(str(exc), status=HTTPStatus.BAD_REQUEST)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def serve_app_file(self, path: Path, content_type: str | None = None, allow_range: bool = False) -> None:
        if not is_within_root(path, APP_DIR):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.serve_file(path, content_type=content_type, allow_range=allow_range, cache_control="no-store")

    def serve_work_file(self, path: Path, content_type: str | None = None, allow_range: bool = False) -> None:
        if not is_within_root(path, WORK_DIR):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.serve_file(path, content_type=content_type, allow_range=allow_range)

    def serve_export_file(self, path: Path, content_type: str | None = None, allow_range: bool = False) -> None:
        export_root = configured_exports_dir()
        if not is_within_root(path, export_root):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.serve_file(path, content_type=content_type, allow_range=allow_range)

    def serve_media_file(self, path: Path, content_type: str | None = None, allow_range: bool = False) -> None:
        self.serve_file(path, content_type=content_type, allow_range=allow_range)

    def serve_file(
        self,
        path: Path,
        content_type: str | None = None,
        allow_range: bool = False,
        cache_control: str | None = None,
    ) -> None:
        path = path.resolve()
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        guessed_type = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        file_size = path.stat().st_size
        range_header = self.headers.get("Range") if allow_range else None

        if range_header and range_header.startswith("bytes="):
            start_text, _, end_text = range_header.removeprefix("bytes=").partition("-")
            start = int(start_text or "0")
            end = int(end_text or file_size - 1)
            end = min(end, file_size - 1)
            if start > end:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            length = (end - start) + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", guessed_type)
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                self.wfile.write(handle.read(length))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guessed_type)
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(file_size))
        if allow_range:
            self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)


def serve_once(host: str, port: int) -> None:
    ensure_runtime_dirs()
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Sprite Video Lab running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def stop_child_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_with_reloader(host: str, port: int) -> None:
    ensure_runtime_dirs()
    watch_state = watch_snapshot()
    child: subprocess.Popen | None = None
    print(f"Sprite Video Lab reloader watching {len(watch_state)} files.")
    try:
        while True:
            if child is None or child.poll() is not None:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT_DIR / "server.py"),
                        "--serve",
                        "--host",
                        host,
                        "--port",
                        str(port),
                    ],
                    cwd=str(ROOT_DIR),
                )
            time.sleep(0.8)
            next_snapshot = watch_snapshot()
            if next_snapshot != watch_state:
                print("Changes detected. Reloading Sprite Video Lab...")
                watch_state = next_snapshot
                stop_child_process(child)
                child = None
    except KeyboardInterrupt:
        pass
    finally:
        stop_child_process(child)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sprite Video Lab.")
    parser.add_argument("--serve", action="store_true", help="Run the HTTP server once without file watching.")
    parser.add_argument("--host", default=None, help=f"Host to bind. Defaults to ${HOST_ENV} or {DEFAULT_HOST}.")
    parser.add_argument("--port", type=int, default=None, help=f"Port to bind. Defaults to ${PORT_ENV} or {DEFAULT_PORT}.")
    args = parser.parse_args()
    host = configured_host(args.host)
    port = configured_port(args.port)
    if args.serve:
        serve_once(host, port)
        return
    run_with_reloader(host, port)


if __name__ == "__main__":
    main()
