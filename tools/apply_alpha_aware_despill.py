from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import server  # noqa: E402


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def median_channel(colors: list[tuple[int, int, int]], channel: int) -> int:
    values = sorted(color[channel] for color in colors)
    return values[len(values) // 2]


def median_color(colors: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not colors:
        raise ValueError("cannot calculate a median colour from an empty sample")
    return tuple(median_channel(colors, channel) for channel in range(3))


def estimate_hidden_screen_key(image: Image.Image) -> tuple[int, int, int] | None:
    """Estimate a green/blue plate from RGB hidden behind transparent pixels."""
    rgba = image.convert("RGBA")
    pixel_count = rgba.width * rgba.height
    step = max(1, round(math.sqrt(pixel_count / 30000)))
    green_samples: list[tuple[int, int, int]] = []
    blue_samples: list[tuple[int, int, int]] = []
    for index, (red, green, blue, alpha) in enumerate(rgba.getdata()):
        if index % step != 0 or alpha > 8:
            continue
        if green >= 40 and green - max(red, blue) >= 12:
            green_samples.append((red, green, blue))
        elif blue >= 40 and blue - max(red, green) >= 12:
            blue_samples.append((red, green, blue))
    samples = green_samples if len(green_samples) >= len(blue_samples) else blue_samples
    if len(samples) < 24:
        return None
    return median_color(samples)


def edge_green_excess(pixels: list[tuple[int, int, int, int]]) -> float:
    values = [
        max(0, green - max(red, blue))
        for red, green, blue, alpha in pixels
        if 0 < alpha < 254
    ]
    return (sum(values) / len(values)) if values else 0.0


def process_tree(
    source_root: Path,
    backup_root: Path | None,
    report_path: Path | None,
    dry_run: bool,
) -> dict:
    source_root = source_root.resolve()
    frame_paths = sorted(path for path in source_root.rglob("*.png") if path.is_file())
    if not frame_paths:
        raise ValueError(f"no PNG frames found under {source_root}")
    if not dry_run and backup_root is None:
        raise ValueError("--backup-dir is required unless --dry-run is used")
    if backup_root is not None:
        backup_root = backup_root.resolve()
        if backup_root == source_root or source_root in backup_root.parents:
            raise ValueError("backup directory must be outside the source frame tree")

    detected_keys: dict[Path, tuple[int, int, int]] = {}
    for frame_path in frame_paths:
        with Image.open(frame_path) as image:
            key = estimate_hidden_screen_key(image)
        if key is not None:
            detected_keys[frame_path] = key
    if not detected_keys:
        raise RuntimeError("no recoverable green/blue screen colour found in transparent RGB")
    fallback_key = median_color(list(detected_keys.values()))

    records: list[dict] = []
    aggregate = defaultdict(int)
    before_green_sum = 0.0
    after_green_sum = 0.0
    for frame_path in frame_paths:
        relative = frame_path.relative_to(source_root)
        key_rgb = detected_keys.get(frame_path, fallback_key)
        before_hash = file_sha256(frame_path)
        with Image.open(frame_path) as opened:
            source = opened.convert("RGBA")
        before_pixels = list(source.getdata())
        alpha_before = bytes(source.getchannel("A").getdata())
        cleaned = server.alpha_aware_despill_frame(source, source, key_rgb)
        after_pixels = list(cleaned.getdata())
        alpha_after = bytes(cleaned.getchannel("A").getdata())
        if alpha_before != alpha_after:
            raise RuntimeError(f"alpha changed for {frame_path}")

        changed_visible = 0
        changed_opaque = 0
        for before, after in zip(before_pixels, after_pixels):
            if before[:3] == after[:3]:
                continue
            if before[3] > 0:
                changed_visible += 1
            if before[3] >= 254:
                changed_opaque += 1
        if changed_opaque:
            raise RuntimeError(f"opaque pixels changed for {frame_path}: {changed_opaque}")

        before_green = edge_green_excess(before_pixels)
        after_green = edge_green_excess(after_pixels)
        before_green_sum += before_green
        after_green_sum += after_green
        if not dry_run:
            assert backup_root is not None
            backup_path = backup_root / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if not backup_path.exists():
                shutil.copy2(frame_path, backup_path)
            elif file_sha256(backup_path) != before_hash:
                raise RuntimeError(f"existing backup does not match source: {backup_path}")

            temporary_path = frame_path.with_name(f"{frame_path.stem}.alpha-aware.tmp.png")
            cleaned.save(temporary_path, optimize=True, compress_level=9)
            os.replace(temporary_path, frame_path)
            after_hash = file_sha256(frame_path)
        else:
            after_hash = ""

        record = {
            "path": relative.as_posix(),
            "key_rgb": list(key_rgb),
            "key_source": "frame" if frame_path in detected_keys else "root_fallback",
            "width": source.width,
            "height": source.height,
            "alpha_sha256": hashlib.sha256(alpha_before).hexdigest(),
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "changed_visible_pixels": changed_visible,
            "changed_opaque_pixels": changed_opaque,
            "edge_green_excess_before": round(before_green, 4),
            "edge_green_excess_after": round(after_green, 4),
        }
        records.append(record)
        aggregate[relative.parts[0] if len(relative.parts) > 1 else "."] += 1

    result = {
        "source_root": str(source_root),
        "backup_root": str(backup_root) if backup_root is not None else "",
        "dry_run": dry_run,
        "frame_count": len(frame_paths),
        "frame_key_count": len(detected_keys),
        "fallback_key_rgb": list(fallback_key),
        "animations": dict(sorted(aggregate.items())),
        "edge_green_excess_before_mean": round(before_green_sum / len(records), 4),
        "edge_green_excess_after_mean": round(after_green_sum / len(records), 4),
        "records": records,
    }
    if report_path is not None:
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply Sprite Video Lab's automatic alpha-aware despill to transparent PNG frames."
    )
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = process_tree(args.source_root, args.backup_dir, args.report, args.dry_run)
    print(json.dumps({key: value for key, value in result.items() if key != "records"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
