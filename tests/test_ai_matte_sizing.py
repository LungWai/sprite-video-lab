import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

import server


class AiMatteSizingTests(unittest.TestCase):
    def test_watermark_removal_detects_each_corner_and_keeps_canvas_size(self):
        source = Image.new("RGBA", (100, 80), (0, 255, 0, 255))
        for y in range(3, 10):
            for x in range(6, 19):
                source.putpixel((x, y), (150, 255, 150, 255))
        processed = Image.new("RGBA", source.size, (220, 80, 40, 255))

        cleaned_frames, info = server.remove_detected_watermarks([source], [processed])

        self.assertEqual(info["locations"], ["top_left"])
        self.assertEqual(info["removed_frames"], 1)
        self.assertEqual(cleaned_frames[0].size, source.size)
        self.assertEqual(cleaned_frames[0].getpixel((8, 5)), (0, 0, 0, 0))
        self.assertEqual(cleaned_frames[0].getpixel((50, 40)), (220, 80, 40, 255))

        source = Image.new("RGBA", (100, 80), (0, 255, 0, 255))
        for y in range(72, 79):
            for x in range(86, 99):
                source.putpixel((x, y), (150, 255, 150, 255))
        location, _scores = server.detect_watermark_corner(source)
        self.assertEqual(location, "bottom_right")

    def test_watermark_removal_skips_frames_without_corner_contrast(self):
        source = Image.new("RGBA", (100, 80), (0, 255, 0, 255))
        self.assertEqual(server.detect_watermark_corner(source)[0], None)

    def test_corridorkey_mode_only_requires_corridorkey(self):
        self.assertEqual(server.normalize_matte_mode("corridorkey", True), "corridorkey")
        self.assertEqual(server.normalize_matte_mode("corridorkey_green", True), "corridorkey")
        self.assertEqual(server.normalize_matte_mode("corridorkey_blue", True), "corridorkey")
        self.assertEqual(server.ai_components_for_matte_mode("corridorkey"), ["corridorkey"])

    def test_corridorkey_with_birefnet_coarse_mask_requires_both_models(self):
        self.assertEqual(
            server.ai_components_for_matte_mode("corridorkey", "birefnet"),
            ["birefnet", "corridorkey"],
        )
        self.assertEqual(server.normalize_corridorkey_coarse_mask("unknown"), "chroma")

    def test_removed_advanced_modes_migrate_to_the_nearest_kept_mode(self):
        self.assertEqual(
            server.normalize_matte_mode("birefnet_corridorkey_key", True),
            "corridorkey",
        )
        self.assertEqual(server.normalize_matte_mode("chroma_birefnet", True), "chroma")
        self.assertEqual(server.normalize_matte_mode("birefnet_luma_key", True), "luma")
        self.assertEqual(
            server.normalize_matte_mode("birefnet_luma_corridorkey", True),
            "luma",
        )

    def test_corridorkey_options_use_ez_defaults_and_clamp_values(self):
        self.assertEqual(server.normalize_corridorkey_options(), server.CORRIDORKEY_DEFAULTS)
        self.assertEqual(
            server.normalize_corridorkey_options(
                {
                    "color_space": "bad",
                    "despill_strength": 8,
                    "refiner_scale": -2,
                    "despeckle_enabled": False,
                    "despeckle_size": 2000000,
                    "garbage_matte_enabled": True,
                    "garbage_matte_px": 0,
                }
            ),
            {
                "color_space": "srgb",
                "despill_strength": 1.0,
                "refiner_scale": 0.0,
                "despeckle_enabled": False,
                "despeckle_size": 999999,
                "garbage_matte_enabled": True,
                "garbage_matte_px": 1,
            },
        )

    def test_corridorkey_process_arrays_routes_every_ez_parameter(self):
        captured = {}

        class FakeEngine:
            def process_frame(self, rgb, mask, **kwargs):
                captured["rgb"] = rgb
                captured["mask"] = mask
                captured.update(kwargs)
                return {"processed": "ok"}

        options = server.normalize_corridorkey_options(
            {
                "color_space": "linear",
                "despill_strength": 0.35,
                "refiner_scale": 0.8,
                "despeckle_enabled": False,
                "despeckle_size": 123,
                "garbage_matte_enabled": True,
                "garbage_matte_px": 30,
            }
        )
        result = server.corridorkey_process_arrays(FakeEngine(), "rgb", "mask", "green", options)

        self.assertEqual(result, {"processed": "ok"})
        self.assertEqual(captured["rgb"], "rgb")
        self.assertEqual(captured["mask"], "mask")
        self.assertTrue(captured["input_is_linear"])
        self.assertEqual(captured["screen_color"], "green")
        self.assertEqual(captured["despill_strength"], 0.35)
        self.assertEqual(captured["refiner_scale"], 0.8)
        self.assertFalse(captured["auto_despeckle"])
        self.assertEqual(captured["despeckle_size"], 123)
        self.assertEqual(captured["garbage_matte_px"], 30)

    def test_corridorkey_preserves_source_rgb_only_in_opaque_interior(self):
        source = Image.new("RGB", (15, 15), (230, 140, 40))
        refined = Image.new("RGBA", (15, 15), (120, 90, 160, 128))
        for y in range(2, 13):
            for x in range(2, 13):
                refined.putpixel((x, y), (110, 80, 150, 255))

        result = server.preserve_corridorkey_opaque_source_rgb(source, refined)

        self.assertEqual(result.getpixel((7, 7)), (230, 140, 40, 255))
        self.assertEqual(result.getpixel((0, 0)), (120, 90, 160, 128))
        self.assertEqual(result.getchannel("A").tobytes(), refined.getchannel("A").tobytes())

    def test_manual_key_colors_keep_unique_valid_samples(self):
        colors = server.normalize_manual_key_colors(
            "#00FF00",
            ["#10C850", "#10c850", "bad", "#14B85C"],
        )

        self.assertEqual(colors, [(16, 200, 80), (20, 184, 92)])

    def test_explicit_empty_manual_key_colors_do_not_fall_back_to_green(self):
        self.assertEqual(server.normalize_manual_key_colors("#00FF00", []), [])

    def test_missing_manual_key_colors_keep_legacy_single_color_fallback(self):
        self.assertEqual(server.normalize_manual_key_colors("#12B84C", None), [(18, 184, 76)])

    def test_chroma_key_uses_nearest_of_multiple_background_samples(self):
        image = Image.new("RGBA", (3, 1))
        image.putdata(
            [
                (16, 200, 80, 255),
                (20, 184, 92, 255),
                (230, 140, 40, 255),
            ]
        )

        result = server.chroma_key_frame(
            image=image,
            key_rgb=(16, 200, 80),
            key_rgbs=[(16, 200, 80), (20, 184, 92)],
            threshold=4,
            softness=0,
            despill_strength=0,
            halo_pixels=0,
        )

        self.assertEqual(list(result.getchannel("A").getdata()), [0, 0, 255])

    def test_chroma_key_only_reduces_existing_transparent_input_alpha(self):
        image = Image.new("RGBA", (4, 1))
        image.putdata(
            [
                (0, 0, 0, 0),
                (255, 0, 0, 128),
                (0, 0, 255, 255),
                (255, 0, 0, 255),
            ]
        )

        result = server.chroma_key_frame(
            image=image,
            key_rgb=(0, 0, 255),
            threshold=10,
            softness=0,
            despill_strength=0,
            halo_pixels=0,
        )

        self.assertEqual(list(result.getchannel("A").getdata()), [0, 128, 0, 255])

    def test_standalone_corridorkey_uses_ez_foreground_without_birefnet(self):
        raw = Image.new("RGBA", (2, 1))
        raw.putdata([(0, 200, 40, 255), (215, 45, 30, 255)])
        chroma_alpha = Image.new("L", raw.size)
        chroma_alpha.putdata([0, 255])
        corridor_alpha = Image.new("L", raw.size)
        corridor_alpha.putdata([12, 180])
        corridor_result = Image.new("RGBA", raw.size)
        corridor_result.putdata([(5, 6, 7, 12), (101, 102, 103, 180)])
        observed = {}

        def fake_corridor(_image, _alpha, _device, _screen, options):
            observed["options"] = options
            return corridor_result, {
                "corridorkey_enabled": True,
                "corridorkey_screen_color": "green",
            }

        with (
            mock.patch.object(
                server,
                "chroma_key_frame",
                return_value=server.apply_alpha_mask(raw, chroma_alpha),
            ),
            mock.patch.object(
                server,
                "corridorkey_refine_frame",
                side_effect=fake_corridor,
            ),
            mock.patch.object(server, "birefnet_alpha_mask") as birefnet,
        ):
            frames, _key, info = server.apply_matte_pipeline(
                raw_images=[raw],
                chroma_enabled=True,
                matte_mode="corridorkey",
                key_mode="manual",
                manual_key_hex="#00C828",
                threshold=12,
                softness=16,
                despill_strength=0.6,
                halo_pixels=0,
                ai_model="birefnet-hr-matting",
                ai_device="cpu",
                ai_resolution=1024,
                luma_black=0,
                luma_white=85,
                luma_gamma=0.55,
                luma_strength=1.7,
                luma_polarity="auto",
                corridorkey_enabled=True,
                corridorkey_screen="green",
                corridorkey_options={"despill_strength": 0.25, "refiner_scale": 1.5},
            )

        birefnet.assert_not_called()
        self.assertEqual(list(frames[0].getchannel("A").getdata()), [12, 180])
        self.assertEqual(frames[0].getpixel((1, 0))[:3], (101, 102, 103))
        self.assertEqual(observed["options"]["despill_strength"], 0.25)
        self.assertEqual(observed["options"]["refiner_scale"], 1.5)
        self.assertTrue(info["corridorkey_enabled"])

    def test_corridorkey_chroma_coarse_mask_uses_all_manual_colors_and_threshold(self):
        raw = Image.new("RGBA", (3, 1))
        raw.putdata(
            [
                (16, 200, 80, 255),
                (20, 184, 92, 255),
                (230, 140, 40, 255),
            ]
        )
        observed = {}

        def fake_corridor(image, alpha, _device, _screen, _options):
            observed["alpha"] = list(alpha.getdata())
            return server.apply_alpha_mask(image, alpha), {
                "corridorkey_enabled": True,
                "corridorkey_screen_color": "green",
            }

        with (
            mock.patch.object(server, "chroma_key_frame", wraps=server.chroma_key_frame) as chroma,
            mock.patch.object(server, "corridorkey_refine_frame", side_effect=fake_corridor),
        ):
            server.apply_matte_pipeline(
                raw_images=[raw],
                chroma_enabled=True,
                matte_mode="corridorkey",
                key_mode="manual",
                manual_key_hex="#10C850",
                manual_key_colors=["#10C850", "#14B85C"],
                threshold=4,
                softness=0,
                despill_strength=0.6,
                halo_pixels=0,
                ai_model="birefnet-hr-matting",
                ai_device="cpu",
                ai_resolution=1024,
                luma_black=0,
                luma_white=85,
                luma_gamma=0.55,
                luma_strength=1.7,
                luma_polarity="auto",
                corridorkey_enabled=True,
                corridorkey_screen="green",
                corridorkey_coarse_mask="chroma",
            )

        self.assertEqual(observed["alpha"], [0, 0, 255])
        self.assertEqual(chroma.call_args.kwargs["key_rgbs"], [(16, 200, 80), (20, 184, 92)])
        self.assertEqual(chroma.call_args.kwargs["threshold"], 4)

    def test_corridorkey_can_use_birefnet_as_its_coarse_mask(self):
        raw = Image.new("RGBA", (2, 1), (20, 180, 60, 255))
        biref_alpha = Image.new("L", raw.size)
        biref_alpha.putdata([24, 220])
        corridor_result = Image.new("RGBA", raw.size, (90, 100, 110, 180))
        observed = {}

        def fake_corridor(_image, alpha, _device, _screen, _options):
            observed["alpha"] = list(alpha.getdata())
            return corridor_result, {
                "corridorkey_enabled": True,
                "corridorkey_screen_color": "green",
            }

        with (
            mock.patch.object(
                server,
                "birefnet_alpha_mask",
                return_value=(
                    biref_alpha,
                    {
                        "model_key": "birefnet-hr-matting",
                        "model_label": "BiRefNet HR-matting",
                    },
                ),
            ) as birefnet,
            mock.patch.object(server, "chroma_key_frame") as chroma,
            mock.patch.object(server, "corridorkey_refine_frame", side_effect=fake_corridor),
        ):
            frames, _key, info = server.apply_matte_pipeline(
                raw_images=[raw],
                chroma_enabled=True,
                matte_mode="corridorkey",
                key_mode="auto",
                manual_key_hex="#00FF00",
                threshold=35,
                softness=16,
                despill_strength=0.6,
                halo_pixels=1,
                ai_model="birefnet-hr-matting",
                ai_device="auto",
                ai_resolution="auto",
                luma_black=0,
                luma_white=85,
                luma_gamma=0.55,
                luma_strength=1.7,
                luma_polarity="auto",
                corridorkey_enabled=True,
                corridorkey_screen="green",
                corridorkey_coarse_mask="birefnet",
            )

        self.assertFalse(birefnet.call_args.kwargs["allow_solid_fallback"])
        chroma.assert_not_called()
        self.assertEqual(observed["alpha"], [24, 220])
        self.assertEqual(frames[0].getchannel("A").getpixel((0, 0)), 180)
        self.assertEqual(info["corridorkey_coarse_mask"], "birefnet")
        self.assertEqual(info["model_key"], "birefnet-hr-matting")

    def test_alpha_aware_despill_recovers_edge_color_without_changing_alpha(self):
        key_rgb = (14, 129, 64)
        foreground_rgb = (236, 170, 80)
        alpha = 64
        normalized_alpha = alpha / 255.0
        observed_rgb = tuple(
            server.linear_to_srgb_byte(
                (server._SRGB_TO_LINEAR_LUT[foreground_rgb[index]] * normalized_alpha)
                + (server._SRGB_TO_LINEAR_LUT[key_rgb[index]] * (1.0 - normalized_alpha))
            )
            for index in range(3)
        )
        source = Image.new("RGBA", (3, 1))
        source.putdata(
            [
                (*key_rgb, 255),
                (*observed_rgb, 255),
                (*foreground_rgb, 255),
            ]
        )
        matte = source.copy()
        alpha_channel = Image.new("L", (3, 1))
        alpha_channel.putdata([0, alpha, 255])
        matte.putalpha(alpha_channel)

        cleaned = server.alpha_aware_despill_frame(source, matte, key_rgb)
        cleaned_pixels = list(cleaned.getdata())

        self.assertEqual(list(cleaned.getchannel("A").getdata()), [0, alpha, 255])
        self.assertEqual(cleaned_pixels[0], (0, 0, 0, 0))
        self.assertEqual(cleaned_pixels[2], (*foreground_rgb, 255))
        before_distance = sum(abs(observed_rgb[index] - foreground_rgb[index]) for index in range(3))
        after_distance = sum(abs(cleaned_pixels[1][index] - foreground_rgb[index]) for index in range(3))
        self.assertLess(after_distance, before_distance)
        self.assertLessEqual(abs(cleaned_pixels[1][0] - foreground_rgb[0]), 10)
        self.assertLessEqual(abs(cleaned_pixels[1][1] - foreground_rgb[1]), 10)
        self.assertLessEqual(abs(cleaned_pixels[1][2] - foreground_rgb[2]), 10)

    def test_matte_pipeline_applies_alpha_aware_despill_automatically(self):
        raw = Image.new("RGBA", (3, 1))
        raw.putdata([(0, 180, 70, 255), (120, 155, 70, 255), (235, 170, 80, 255)])
        with mock.patch.object(
            server,
            "alpha_aware_despill_frame",
            wraps=server.alpha_aware_despill_frame,
        ) as alpha_aware:
            frames, _key_rgb, info = server.apply_matte_pipeline(
                raw_images=[raw],
                chroma_enabled=True,
                matte_mode="chroma",
                key_mode="manual",
                manual_key_hex="#00B446",
                threshold=12,
                softness=150,
                despill_strength=0.0,
                halo_pixels=0,
                ai_model="birefnet-hr-matting",
                ai_device="auto",
                ai_resolution="auto",
                luma_black=0,
                luma_white=85,
                luma_gamma=0.55,
                luma_strength=1.7,
                luma_polarity="auto",
                corridorkey_enabled=False,
                corridorkey_screen="auto",
            )

        self.assertEqual(len(frames), 1)
        self.assertEqual(alpha_aware.call_count, 1)
        self.assertTrue(info["alpha_aware_despill"])
        self.assertEqual(info["alpha_aware_despill_method"], "linear_unmix")

    def test_no_matte_skips_alpha_aware_despill(self):
        raw = Image.new("RGBA", (1, 1), (30, 170, 80, 123))
        with mock.patch.object(server, "alpha_aware_despill_frame") as alpha_aware:
            frames, _key_rgb, info = server.apply_matte_pipeline(
                raw_images=[raw],
                chroma_enabled=False,
                matte_mode="none",
                key_mode="auto",
                manual_key_hex="#00FF00",
                threshold=80,
                softness=16,
                despill_strength=0.6,
                halo_pixels=1,
                ai_model="birefnet-hr-matting",
                ai_device="auto",
                ai_resolution="auto",
                luma_black=0,
                luma_white=85,
                luma_gamma=0.55,
                luma_strength=1.7,
                luma_polarity="auto",
                corridorkey_enabled=False,
                corridorkey_screen="auto",
            )

        alpha_aware.assert_not_called()
        self.assertFalse(info["alpha_aware_despill"])
        self.assertEqual(info["alpha_aware_despill_method"], "")
        self.assertEqual(frames[0].getpixel((0, 0)), raw.getpixel((0, 0)))

    def test_realesrgan_install_requires_explicit_confirmation(self):
        with mock.patch.object(server, "download_realesrgan_windows_package") as download_package:
            with self.assertRaisesRegex(ValueError, "确认"):
                server.install_realesrgan_runtime(False)

        download_package.assert_not_called()

    def test_realesrgan_install_skips_download_when_runtime_is_ready(self):
        ready_status = {"installed": True, "binary": "ready.exe", "model_dir": "models"}
        with (
            mock.patch.object(server, "realesrgan_install_status", return_value=ready_status),
            mock.patch.object(server, "download_realesrgan_windows_package") as download_package,
        ):
            result = server.install_realesrgan_runtime(True)

        self.assertFalse(result["downloaded"])
        self.assertEqual(result["status"], ready_status)
        download_package.assert_not_called()

    def test_realesrgan_install_downloads_portable_runtime_into_work_tools(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            work_dir = temp_root / "work"
            package_path = temp_root / "fake-realesrgan.zip"
            package_root = "realesrgan-ncnn-vulkan-20220424-windows"
            with zipfile.ZipFile(package_path, "w") as archive:
                archive.writestr(f"{package_root}/realesrgan-ncnn-vulkan.exe", b"fake-exe")
                archive.writestr(
                    f"{package_root}/models/{server.REAL_ESRGAN_ANIME_MODEL}.param",
                    b"fake-param",
                )
                archive.writestr(
                    f"{package_root}/models/{server.REAL_ESRGAN_ANIME_MODEL}.bin",
                    b"fake-bin",
                )

            def fake_download(destination):
                destination.write_bytes(package_path.read_bytes())

            with (
                mock.patch.object(server, "WORK_DIR", work_dir),
                mock.patch.object(server, "DEFAULT_WORK_DIR", work_dir),
                mock.patch.object(server.shutil, "which", return_value=None),
                mock.patch.dict(
                    server.os.environ,
                    {
                        server.REAL_ESRGAN_BINARY_ENV: "",
                        server.REAL_ESRGAN_MODEL_DIR_ENV: "",
                    },
                ),
                mock.patch.object(
                    server,
                    "download_realesrgan_windows_package",
                    side_effect=fake_download,
                ) as download_package,
            ):
                result = server.install_realesrgan_runtime(True)

            target_dir = work_dir / "tools" / "realesrgan-ncnn-vulkan"
            self.assertTrue(result["downloaded"])
            self.assertTrue(result["status"]["installed"])
            self.assertTrue((target_dir / "realesrgan-ncnn-vulkan.exe").is_file())
            self.assertTrue((target_dir / "models" / f"{server.REAL_ESRGAN_ANIME_MODEL}.param").is_file())
            self.assertTrue((target_dir / "models" / f"{server.REAL_ESRGAN_ANIME_MODEL}.bin").is_file())
            download_package.assert_called_once()

    def test_ai_model_install_requires_explicit_confirmation(self):
        with mock.patch.object(server, "require_ai_runtime_for_components") as require_runtime:
            with self.assertRaisesRegex(ValueError, "确认"):
                server.install_ai_models_for_matte_mode(False, "birefnet")

        require_runtime.assert_not_called()

    def test_ai_model_install_installs_selected_corridorkey_screen(self):
        completed_status = {"installed": True}
        with (
            mock.patch.object(server, "require_ai_runtime_for_components") as require_runtime,
            mock.patch.object(server, "download_birefnet_model") as download_birefnet,
            mock.patch.object(server, "download_corridorkey_checkpoint") as download_corridorkey,
            mock.patch.object(server, "ai_model_install_status", return_value=completed_status),
        ):
            result = server.install_ai_models_for_matte_mode(
                True,
                "corridorkey",
                "birefnet-hr-matting",
                "chroma",
                "blue",
            )

        require_runtime.assert_called_once_with(["corridorkey"])
        download_birefnet.assert_not_called()
        self.assertEqual(
            download_corridorkey.call_args_list,
            [mock.call("blue")],
        )
        self.assertEqual(
            result["installed_models"],
            ["corridorkey-blue"],
        )

    def test_ai_model_status_checks_selected_corridorkey_screen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            corridor_root = Path(temp_dir)
            (corridor_root / "CorridorKeyModule").mkdir()
            with (
                mock.patch.object(server, "default_corridorkey_root", return_value=corridor_root),
                mock.patch.object(server, "missing_ai_dependency_names", return_value=[]),
                mock.patch.object(server, "corridorkey_checkpoint_is_cached", return_value=True) as cached,
            ):
                status = server.ai_model_install_status(
                    "corridorkey",
                    "birefnet-hr-matting",
                    "chroma",
                    "blue",
                )

        cached.assert_called_once_with("blue")
        self.assertTrue(status["installed"])
        self.assertEqual(status["models"]["corridorkey-blue"], True)
        self.assertNotIn("corridorkey-green", status["models"])

    def test_ai_model_install_adds_hr_matting_for_corridorkey_birefnet_coarse_mask(self):
        completed_status = {"installed": True}
        with (
            mock.patch.object(server, "require_ai_runtime_for_components") as require_runtime,
            mock.patch.object(server, "download_birefnet_model") as download_birefnet,
            mock.patch.object(server, "download_corridorkey_checkpoint") as download_corridorkey,
            mock.patch.object(server, "ai_model_install_status", return_value=completed_status),
        ):
            result = server.install_ai_models_for_matte_mode(
                True,
                "corridorkey",
                "birefnet-hr-matting",
                "birefnet",
            )

        require_runtime.assert_called_once_with(["birefnet", "corridorkey"])
        download_birefnet.assert_called_once_with("birefnet-hr-matting")
        download_corridorkey.assert_called_once_with("green")
        self.assertEqual(result["installed_models"], ["birefnet-hr-matting", "corridorkey-green"])

    def test_ai_model_install_only_downloads_hr_matting_for_birefnet(self):
        completed_status = {"installed": True}
        with (
            mock.patch.object(server, "require_ai_runtime_for_components") as require_runtime,
            mock.patch.object(server, "download_birefnet_model") as download_birefnet,
            mock.patch.object(server, "download_corridorkey_checkpoint") as download_corridorkey,
            mock.patch.object(server, "ai_model_install_status", return_value=completed_status),
        ):
            result = server.install_ai_models_for_matte_mode(
                True,
                "birefnet",
                "birefnet-general",
            )

        require_runtime.assert_called_once_with(["birefnet"])
        download_birefnet.assert_called_once_with("birefnet-hr-matting")
        download_corridorkey.assert_not_called()
        self.assertEqual(result["installed_models"], ["birefnet-hr-matting"])

    def test_birefnet_download_limits_snapshot_to_required_hr_files(self):
        with (
            mock.patch.object(server, "configure_ai_model_cache", return_value=Path("model-cache")),
            mock.patch("huggingface_hub.snapshot_download") as snapshot_download,
        ):
            result = server.download_birefnet_model("birefnet-general")

        self.assertEqual(result, "birefnet-hr-matting")
        snapshot_download.assert_called_once_with(
            repo_id="ZhengPeng7/BiRefNet_HR-matting",
            revision=server.BIREFNET_HR_MATTING_REVISION,
            cache_dir="model-cache",
            allow_patterns=list(server.BIREFNET_REQUIRED_FILES),
        )

    def test_corridorkey_download_fetches_pinned_selected_checkpoint(self):
        with (
            mock.patch.object(server, "default_corridorkey_root", return_value=Path("corridor-root")),
            mock.patch("huggingface_hub.hf_hub_download") as hf_hub_download,
        ):
            result = server.download_corridorkey_checkpoint("blue")

        repo_id, filename, revision = server.CORRIDORKEY_TORCH_CHECKPOINTS["blue"]
        self.assertEqual(result, "blue")
        hf_hub_download.assert_called_once_with(
            repo_id=repo_id,
            filename=filename,
            revision=revision,
            local_dir=str(Path("corridor-root") / "CorridorKeyModule" / "checkpoints"),
        )

    def test_model_and_corridor_color_values_normalize_to_supported_models(self):
        self.assertEqual(server.normalize_ai_model_key("general"), "birefnet-hr-matting")
        self.assertEqual(server.normalize_ai_model_key("lite-2k"), "birefnet-hr-matting")
        self.assertEqual(server.normalize_corridorkey_screen("auto"), "green")
        self.assertEqual(server.normalize_corridorkey_screen("blue"), "blue")

    def test_non_ai_matte_mode_never_starts_installation(self):
        with mock.patch.object(server, "require_ai_runtime_for_components") as require_runtime:
            with self.assertRaisesRegex(ValueError, "不需要"):
                server.install_ai_models_for_matte_mode(True, "chroma")

        require_runtime.assert_not_called()

    def test_birefnet_processing_uses_cached_files_only(self):
        captured = {}

        class FakeModel:
            def to(self, _device):
                return self

            def eval(self):
                return self

        class FakeAutoModel:
            @staticmethod
            def from_pretrained(_repo_id, **kwargs):
                captured.update(kwargs)
                return FakeModel()

        fake_torch = mock.Mock()
        fake_torch.cuda.is_available.return_value = False
        with (
            mock.patch.object(server, "_BIREFNET_MODEL_CACHE", {}),
            mock.patch.object(
                server,
                "import_ai_matte_dependencies",
                return_value=(fake_torch, mock.Mock(), FakeAutoModel),
            ),
            mock.patch.object(server, "configure_ai_model_cache", return_value=Path("model-cache")),
        ):
            server.load_birefnet_model("birefnet-hr-matting", "cpu")

        self.assertTrue(captured["local_files_only"])
        self.assertEqual(captured["revision"], server.BIREFNET_HR_MATTING_REVISION)

    def test_clear_runtime_files_requires_confirmation_and_stays_inside_managed_dirs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            managed_dirs = tuple(work_dir / name for name in ("uploads", "jobs", "exports"))
            for directory in managed_dirs:
                directory.mkdir(parents=True)
                (directory / "generated.bin").write_bytes(b"generated")
            settings_path = work_dir / "settings.json"
            settings_path.write_text('{"keep": true}', encoding="utf-8")
            unmanaged_path = work_dir / "manual-output"
            unmanaged_path.mkdir()
            (unmanaged_path / "keep.bin").write_bytes(b"keep")
            external_export_root = Path(temp_dir) / "external-exports"
            external_export_root.mkdir()
            generated_export = external_export_root / "20260721-120000-abcd-export"
            generated_export.mkdir()
            (generated_export / "generated.mov").write_bytes(b"generated")
            downloaded_copy = external_export_root / "downloaded-copy.mov"
            downloaded_copy.write_bytes(b"keep")

            with (
                mock.patch.object(server, "WORK_DIR", work_dir),
                mock.patch.object(server, "EXPORTS_DIR", managed_dirs[2]),
                mock.patch.object(server, "MANAGED_RUNTIME_DIRS", managed_dirs),
                mock.patch.object(server, "configured_exports_dir", return_value=external_export_root),
            ):
                with self.assertRaises(ValueError):
                    server.clear_managed_runtime_files(False)
                self.assertTrue((managed_dirs[0] / "generated.bin").exists())

                result = server.clear_managed_runtime_files(True)

            self.assertEqual(result["cleared"], ["uploads", "jobs", "exports"])
            self.assertEqual(result["cleared_export_directories"], [generated_export.name])
            self.assertTrue(all(directory.is_dir() and not any(directory.iterdir()) for directory in managed_dirs))
            self.assertTrue(settings_path.exists())
            self.assertTrue((unmanaged_path / "keep.bin").exists())
            self.assertFalse(generated_export.exists())
            self.assertTrue(downloaded_copy.exists())

    def test_auto_ai_resolution_uses_area_for_wide_images(self):
        image = Image.new("RGBA", (2048, 768))

        self.assertEqual(server.auto_ai_resolution_for_image(image), 1248)

    def test_auto_ai_resolution_preserves_small_image_floor(self):
        image = Image.new("RGBA", (320, 180))

        self.assertEqual(server.auto_ai_resolution_for_image(image), 1024)

    def test_auto_ai_resolution_caps_large_images(self):
        image = Image.new("RGBA", (4096, 4096))

        self.assertEqual(server.auto_ai_resolution_for_image(image), 2560)

    def test_birefnet_input_resize_does_not_letterbox_wide_images(self):
        image = Image.new("RGB", (2048, 768), (210, 20, 30))

        resized = server.resize_birefnet_input(image, 128)

        self.assertEqual(resized.size, (128, 128))
        self.assertEqual(resized.getpixel((0, 0)), (210, 20, 30))
        self.assertEqual(resized.getpixel((127, 0)), (210, 20, 30))
        self.assertEqual(resized.getpixel((0, 127)), (210, 20, 30))
        self.assertEqual(resized.getpixel((127, 127)), (210, 20, 30))

    def test_birefnet_alpha_mask_keeps_hr_without_downloading_a_fallback_model(self):
        image = Image.new("RGBA", (10, 10), (40, 160, 80, 255))
        hr_mask = Image.new("L", (10, 10), 0)
        hr_score = {
            "max_alpha": 255,
            "mean_alpha": 19.58,
            "visible_ratio": 0.235,
            "strong_ratio": 0.0759,
        }

        with (
            mock.patch.object(
                server,
                "run_birefnet_inference",
                return_value=(hr_mask, {"model_key": "birefnet-hr-matting"}),
            ) as run_inference,
            mock.patch.object(server, "birefnet_mask_score", return_value=hr_score),
            mock.patch.object(server, "solid_background_fallback_alpha", return_value=None),
        ):
            selected_mask, info = server.birefnet_alpha_mask(
                image,
                "birefnet-hr-matting",
                "cpu",
                1024,
            )

        self.assertIs(selected_mask, hr_mask)
        self.assertEqual(info["model_key"], "birefnet-hr-matting")
        self.assertEqual(info["fallback_model_key"], "")
        self.assertEqual(run_inference.call_count, 1)

    def test_auto_key_color_uses_dominant_border_color_not_corner_average(self):
        image = Image.new("RGBA", (128, 64), (255, 255, 255, 255))
        for y in range(40, 64):
            for x in range(128):
                image.putpixel((x, y), (35, 40, 45, 255))

        self.assertEqual(server.auto_key_color(image), (255, 255, 255))

    def test_auto_key_color_prefers_large_green_screen_inside_dark_frame(self):
        image = Image.new("RGBA", (128, 64), (1, 1, 1, 255))
        for y in range(12, 56):
            for x in range(20, 118):
                image.putpixel((x, y), (0, 255, 0, 255))

        self.assertEqual(server.auto_key_color(image), (0, 255, 0))

    def test_batch_background_residue_to_black_uses_magenta_key(self):
        image = Image.new("RGBA", (5, 1), (0, 0, 0, 0))
        image.putpixel((0, 0), (220, 20, 200, 255))
        image.putpixel((2, 0), (64, 0, 64, 64))
        image.putpixel((3, 0), (0, 220, 0, 255))
        image.putpixel((4, 0), (220, 20, 20, 255))

        cleaned, changed = server.background_to_black_image(image, (255, 0, 255))

        self.assertEqual(changed, 1)
        self.assertEqual(cleaned.getpixel((0, 0)), (220, 20, 200, 255))
        self.assertEqual(cleaned.getpixel((2, 0)), (0, 0, 0, 64))
        self.assertEqual(cleaned.getpixel((3, 0)), (0, 220, 0, 255))
        self.assertEqual(cleaned.getpixel((4, 0)), (220, 20, 20, 255))

    def test_background_residue_desaturate_uses_selected_key_hue(self):
        image = Image.new("RGBA", (3, 1), (0, 0, 0, 0))
        image.putpixel((1, 0), (230, 20, 210, 200))
        image.putpixel((2, 0), (20, 210, 20, 200))

        cleaned, changed = server.background_desaturate_image(image, (255, 0, 255))

        self.assertEqual(changed, 1)
        red, green, blue, alpha = cleaned.getpixel((1, 0))
        self.assertEqual(red, green)
        self.assertEqual(green, blue)
        self.assertEqual(alpha, 200)
        self.assertEqual(cleaned.getpixel((2, 0)), (20, 210, 20, 200))

    def test_background_residue_desaturate_matches_any_sampled_key(self):
        image = Image.new("RGBA", (4, 1), (0, 0, 0, 0))
        image.putpixel((1, 0), (20, 200, 80, 160))
        image.putpixel((2, 0), (35, 165, 115, 160))

        cleaned, changed = server.background_desaturate_image(
            image,
            (20, 200, 80),
            key_rgbs=[(20, 200, 80), (35, 165, 115)],
        )

        self.assertEqual(changed, 2)
        self.assertEqual(len({*cleaned.getpixel((1, 0))[:3]}), 1)
        self.assertEqual(len({*cleaned.getpixel((2, 0))[:3]}), 1)

    def test_background_desaturate_never_changes_opaque_subject_color(self):
        image = Image.new("RGBA", (7, 1), (200, 20, 20, 255))
        image.putpixel((0, 0), (0, 0, 0, 0))
        image.putpixel((1, 0), (20, 220, 20, 160))
        image.putpixel((2, 0), (20, 220, 20, 255))
        image.putpixel((5, 0), (20, 220, 20, 160))

        cleaned, changed = server.background_desaturate_image(image, (0, 255, 0))

        self.assertEqual(changed, 1)
        red, green, blue, alpha = cleaned.getpixel((1, 0))
        self.assertEqual(red, green)
        self.assertEqual(green, blue)
        self.assertEqual(alpha, 160)
        self.assertEqual(cleaned.getpixel((2, 0)), (20, 220, 20, 255))
        self.assertEqual(cleaned.getpixel((5, 0)), (20, 220, 20, 160))

    def test_esr_smoothing_upscales_then_restores_source_size_and_alpha(self):
        image = Image.new("RGBA", (3, 2), (30, 70, 110, 255))
        image.putpixel((0, 0), (200, 40, 90, 64))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured = {}

            def fake_realesrgan(input_path, output_path, output_scale=None):
                captured["output_scale"] = output_scale
                with Image.open(input_path) as source:
                    self.assertEqual(source.mode, "RGB")
                    source.resize((source.width * 4, source.height * 4), Image.Resampling.NEAREST).save(output_path)

            with mock.patch.object(server, "run_realesrgan_anime", side_effect=fake_realesrgan):
                smoothed = server.smooth_source_frame_with_realesrgan(
                    image,
                    root / "input.png",
                    root / "output.png",
                    root / "restored.png",
                )

            self.assertEqual(captured["output_scale"], 4)
            self.assertEqual(smoothed.size, image.size)
            self.assertEqual(smoothed.getchannel("A").tobytes(), image.getchannel("A").tobytes())
            self.assertTrue((root / "restored.png").is_file())

    def test_preview_esr_smoothing_happens_before_matte(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            previews_dir = root / "previews"
            source_path = root / "source.png"
            Image.new("RGBA", (2, 1), (10, 20, 30, 255)).save(source_path)
            observed = {}

            def fake_extract(_source_path, raw_path):
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(source_path) as source:
                    source.convert("RGBA").save(raw_path)
                return raw_path, {"mode": "test"}

            def fake_smoothing(frames, smoothing_root):
                observed["smoothing_root"] = smoothing_root
                return [Image.new("RGBA", frames[0].size, (210, 40, 80, 255))], {
                    "enabled": True,
                    "model": server.REAL_ESRGAN_ANIME_MODEL,
                    "upscale": 4,
                    "restored_to_source_size": True,
                    "frame_count": 1,
                }

            def fake_matte(**kwargs):
                observed["matte_input"] = kwargs["raw_images"][0].getpixel((0, 0))
                return [kwargs["raw_images"][0].copy()], (255, 0, 255), {
                    "mode": "chroma",
                    "corridorkey_enabled": False,
                    "corridorkey_screen_color": "auto",
                }

            def fake_stable_resize(frames, *_args, **_kwargs):
                return [frames[0].copy()], [None], 1.0, frames[0].size

            with (
                mock.patch.object(server, "PREVIEWS_DIR", previews_dir),
                mock.patch.object(server, "source_media_entry", return_value=(source_path, "image")),
                mock.patch.object(server, "upload_media_info", return_value={"media_type": "image", "width": 2, "height": 1}),
                mock.patch.object(server, "extract_image_frame", side_effect=fake_extract),
                mock.patch.object(server, "require_realesrgan_smoothing_ready"),
                mock.patch.object(server, "preprocess_frames_with_realesrgan_smoothing", side_effect=fake_smoothing),
                mock.patch.object(server, "apply_matte_pipeline", side_effect=fake_matte),
                mock.patch.object(server, "should_preserve_source_canvas", return_value=False),
                mock.patch.object(server, "stable_resize_frames", side_effect=fake_stable_resize),
            ):
                result = server.preview_frame(
                    upload_id="upload-1",
                    sample_time=0,
                    sample_frame=1,
                    output_scale=1.0,
                    reduce_px=0,
                    canvas_mode="auto",
                    chroma_enabled=True,
                    matte_mode="chroma",
                    key_mode="manual",
                    manual_key_hex="#FF00FF",
                    threshold=80,
                    softness=16,
                    despill_strength=0.6,
                    halo_pixels=1,
                    ai_model="birefnet-hr-matting",
                    ai_device="auto",
                    ai_resolution="auto",
                    luma_black=0,
                    luma_white=85,
                    luma_gamma=0.55,
                    luma_strength=1.7,
                    luma_polarity="auto",
                    corridorkey_enabled=False,
                    corridorkey_screen="auto",
                    preprocess_esr_smoothing=True,
                )

            self.assertEqual(observed["matte_input"], (210, 40, 80, 255))
            self.assertEqual(observed["smoothing_root"].name, "esr-smoothing")
            self.assertTrue(result["options"]["preprocess_esr_smoothing"])
            self.assertTrue(result["options"]["preprocess_esr"]["restored_to_source_size"])

    def test_batch_esr_smoothing_happens_before_matte(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "jobs"
            source_path = root / "source.png"
            Image.new("RGBA", (2, 1), (10, 20, 30, 255)).save(source_path)
            observed = {}

            def fake_extract(_source_path, raw_path):
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(source_path) as source:
                    source.convert("RGBA").save(raw_path)
                return raw_path, {"mode": "test"}

            def fake_smoothing(frames, smoothing_root):
                observed["smoothing_root"] = smoothing_root
                return [Image.new("RGBA", frames[0].size, (70, 120, 180, 255))], {
                    "enabled": True,
                    "model": server.REAL_ESRGAN_ANIME_MODEL,
                    "upscale": 4,
                    "restored_to_source_size": True,
                    "frame_count": 1,
                }

            def fake_matte(**kwargs):
                observed["matte_input"] = kwargs["raw_images"][0].getpixel((0, 0))
                return [kwargs["raw_images"][0].copy()], (0, 255, 0), {
                    "mode": "chroma",
                    "corridorkey_enabled": False,
                    "corridorkey_screen_color": "auto",
                }

            def fake_stable_resize(frames, *_args, **_kwargs):
                return [frames[0].copy()], [None], 1.0, frames[0].size

            with (
                mock.patch.object(server, "JOBS_DIR", jobs_dir),
                mock.patch.object(server, "source_media_entry", return_value=(source_path, "image")),
                mock.patch.object(server, "upload_media_info", return_value={"media_type": "image", "width": 2, "height": 1}),
                mock.patch.object(server, "extract_image_frame", side_effect=fake_extract),
                mock.patch.object(server, "require_realesrgan_smoothing_ready"),
                mock.patch.object(server, "preprocess_frames_with_realesrgan_smoothing", side_effect=fake_smoothing),
                mock.patch.object(server, "apply_matte_pipeline", side_effect=fake_matte),
                mock.patch.object(server, "should_preserve_source_canvas", return_value=False),
                mock.patch.object(server, "stable_resize_frames", side_effect=fake_stable_resize),
            ):
                result = server.process_video_to_job(
                    upload_id="upload-1",
                    start_time=0,
                    end_time=0,
                    start_frame=1,
                    end_frame=1,
                    keep_every=1,
                    output_scale=1.0,
                    reduce_px=0,
                    canvas_mode="auto",
                    chroma_enabled=True,
                    matte_mode="chroma",
                    key_mode="auto",
                    manual_key_hex="#00FF00",
                    threshold=80,
                    softness=16,
                    despill_strength=0.6,
                    halo_pixels=1,
                    ai_model="birefnet-hr-matting",
                    ai_device="auto",
                    ai_resolution="auto",
                    luma_black=0,
                    luma_white=85,
                    luma_gamma=0.55,
                    luma_strength=1.7,
                    luma_polarity="auto",
                    corridorkey_enabled=False,
                    corridorkey_screen="auto",
                    preprocess_esr_smoothing=True,
                )

            self.assertEqual(observed["matte_input"], (70, 120, 180, 255))
            self.assertEqual(observed["smoothing_root"].name, "esr-smoothing")
            self.assertTrue(result["options"]["preprocess_esr_smoothing"])
            self.assertTrue(result["options"]["preprocess_esr"]["restored_to_source_size"])

    def test_single_preview_background_cleanup_reads_manifest_key_color(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            previews_dir = Path(temp_dir) / "previews"
            preview_id = "magenta-preview"
            root = previews_dir / preview_id
            root.mkdir(parents=True)
            image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
            image.putpixel((1, 0), (220, 20, 200, 160))
            image.save(root / "processed.png")
            (root / "preview.json").write_text(
                json.dumps(
                    {
                        "preview_id": preview_id,
                        "key_color": "#FF00FF",
                        "processed_url": f"/work/previews/{preview_id}/processed.png",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(server, "PREVIEWS_DIR", previews_dir):
                result = server.background_to_black_preview(preview_id)

            with Image.open(root / "processed.png") as cleaned:
                self.assertEqual(cleaned.convert("RGBA").getpixel((1, 0)), (0, 0, 0, 160))
            self.assertEqual(result["postprocess"]["background_to_black"]["key_color"], "#FF00FF")
            self.assertEqual(result["postprocess"]["background_to_black"]["changed_pixels"], 1)

    def test_solid_background_fallback_accepts_low_confidence_ai_mask(self):
        image = Image.new("RGBA", (128, 64), (255, 255, 255, 255))
        for y in range(24, 64):
            for x in range(128):
                image.putpixel((x, y), (35, 40, 45, 255))
        ai_score = {
            "max_alpha": 245,
            "mean_alpha": 20.0,
            "visible_ratio": 0.7,
            "strong_ratio": 0.008,
        }

        fallback = server.solid_background_fallback_alpha(image, ai_score, 42, 8)

        self.assertIsNotNone(fallback)
        alpha, info = fallback
        self.assertEqual(info["solid_key_color"], "#FFFFFF")
        self.assertEqual(alpha.getpixel((64, 0)), 0)
        self.assertEqual(alpha.getpixel((64, 63)), 255)

    def test_luma_auto_direction_uses_white_background_as_transparent(self):
        image = Image.new("RGBA", (3, 1), (255, 255, 255, 255))
        image.putpixel((1, 0), (220, 0, 20, 255))
        image.putpixel((2, 0), (0, 0, 0, 255))

        polarity = server.resolve_luma_polarity("auto", (255, 255, 255))
        alpha = server.luminance_alpha_mask(image, 0, 85, 0.55, 1.7, polarity=polarity)

        self.assertEqual(polarity, "dark")
        self.assertEqual(alpha.getpixel((0, 0)), 0)
        self.assertGreater(alpha.getpixel((1, 0)), 200)
        self.assertEqual(alpha.getpixel((2, 0)), 255)

    def test_luma_auto_direction_uses_black_background_as_transparent(self):
        self.assertEqual(server.resolve_luma_polarity("auto", (0, 0, 0)), "bright")

    def test_auto_video_preserves_source_canvas_by_default(self):
        self.assertTrue(server.should_preserve_source_canvas("video", 0, "auto"))
        self.assertTrue(server.should_preserve_source_canvas("video", 1, "auto"))
        self.assertTrue(server.should_preserve_source_canvas("video", 0, "square_bottom"))
        self.assertEqual(server.effective_canvas_settings("video", 24, "square_center"), (0, "auto"))

    def test_source_canvas_resize_keeps_video_frame_dimensions(self):
        first = Image.new("RGBA", (96, 54), (0, 0, 0, 0))
        second = Image.new("RGBA", (96, 54), (0, 0, 0, 0))
        for y in range(10, 40):
            for x in range(20, 42):
                first.putpixel((x, y), (255, 255, 255, 255))
        for y in range(12, 44):
            for x in range(48, 76):
                second.putpixel((x, y), (255, 255, 255, 255))

        rendered, bboxes, scale, canvas_size = server.resize_frames_on_source_canvas(
            [first, second],
            1.0,
        )

        self.assertEqual(scale, 1.0)
        self.assertEqual(canvas_size, (96, 54))
        self.assertEqual([frame.size for frame in rendered], [(96, 54), (96, 54)])
        self.assertEqual(bboxes, [(20, 10, 42, 40), (48, 12, 76, 44)])

    def test_source_canvas_resize_scales_whole_canvas_not_subject_bbox(self):
        frame = Image.new("RGBA", (100, 60), (0, 0, 0, 0))
        for y in range(20, 40):
            for x in range(70, 90):
                frame.putpixel((x, y), (255, 255, 255, 255))

        rendered, _bboxes, scale, canvas_size = server.resize_frames_on_source_canvas(
            [frame],
            0.5,
        )

        self.assertEqual(scale, 0.5)
        self.assertEqual(canvas_size, (50, 30))
        self.assertEqual(rendered[0].size, (50, 30))

    def test_frames_export_only_copies_frames_and_writes_per_frame_durations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "jobs"
            exports_dir = root / "exports"
            job_id = "export-frames"
            processed_dir = jobs_dir / job_id / "processed"
            processed_dir.mkdir(parents=True)
            exports_dir.mkdir()
            for index, color in enumerate(((255, 0, 0, 255), (0, 0, 255, 255)), start=1):
                Image.new("RGBA", (8, 6), color).save(processed_dir / f"source_{index:03d}.png")

            with (
                mock.patch.object(server, "JOBS_DIR", jobs_dir),
                mock.patch.object(server, "configured_exports_dir", return_value=exports_dir),
            ):
                server.save_job_manifest(
                    job_id,
                    {
                        "frames": [
                            {"index": 0, "name": "source_001.png"},
                            {"index": 1, "name": "source_002.png"},
                        ]
                    },
                )
                result = server.export_job(job_id, [1, 0], 75, "frames")

            output_dir = Path(result["output_dir"])
            frames_dir = Path(result["frames_dir"])
            metadata = json.loads((frames_dir / "frames.json").read_text(encoding="utf-8"))
            self.assertEqual(result["export_format"], "frames")
            self.assertEqual(metadata["frame_duration_ms"], 75)
            self.assertEqual(metadata["total_duration_ms"], 150)
            self.assertEqual(
                metadata["frames"],
                [
                    {"index": 0, "source_index": 1, "file": "frame_001.png", "duration_ms": 75},
                    {"index": 1, "source_index": 0, "file": "frame_002.png", "duration_ms": 75},
                ],
            )
            self.assertEqual(sorted(path.name for path in frames_dir.iterdir()), ["frame_001.png", "frame_002.png", "frames.json"])
            self.assertFalse((output_dir / "sprite-sheet").exists())
            self.assertEqual(list(output_dir.glob("*.mov")), [])
            self.assertEqual(list(output_dir.glob("*.gif")), [])

    def test_each_non_frame_export_only_runs_its_selected_generator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "jobs"
            exports_dir = root / "exports"
            job_id = "export-formats"
            processed_dir = jobs_dir / job_id / "processed"
            processed_dir.mkdir(parents=True)
            exports_dir.mkdir()
            Image.new("RGBA", (8, 6), (255, 255, 255, 255)).save(processed_dir / "source.png")

            def fake_mov(_paths, _sizes, output_path, _width, _height, _duration):
                output_path.write_bytes(b"mov")

            def fake_gif(_paths, _sizes, output_path, _width, _height, _duration):
                output_path.write_bytes(b"gif")

            def fake_sheet(_paths, _sizes, sheet_path, metadata_path, width, height, duration):
                sheet_path.write_bytes(b"sheet")
                metadata_path.write_text("{}", encoding="utf-8")
                return {"columns": 1, "rows": 1, "width": width, "height": height, "duration_ms": duration}

            with (
                mock.patch.object(server, "JOBS_DIR", jobs_dir),
                mock.patch.object(server, "configured_exports_dir", return_value=exports_dir),
                mock.patch.object(server, "save_alpha_mov", side_effect=fake_mov) as mov_mock,
                mock.patch.object(server, "save_gif", side_effect=fake_gif) as gif_mock,
                mock.patch.object(server, "save_sprite_sheet", side_effect=fake_sheet) as sheet_mock,
            ):
                server.save_job_manifest(job_id, {"frames": [{"index": 0, "name": "source.png"}]})
                mov_result = server.export_job(job_id, [0], 100, "mov")
                gif_result = server.export_job(job_id, [0], 100, "gif")
                sheet_result = server.export_job(job_id, [0], 100, "sprite_sheet")

            self.assertEqual((mov_mock.call_count, gif_mock.call_count, sheet_mock.call_count), (1, 1, 1))
            self.assertEqual(set(path.suffix for path in Path(mov_result["output_dir"]).iterdir()), {".mov"})
            self.assertEqual(set(path.suffix for path in Path(gif_result["output_dir"]).iterdir()), {".gif"})
            self.assertEqual(
                sorted(path.name for path in Path(sheet_result["sheet_dir"]).iterdir()),
                ["sheet.json", "sheet.png"],
            )
            for result in (mov_result, gif_result, sheet_result):
                self.assertFalse((Path(result["output_dir"]) / "frames").exists())

    def test_export_rejects_unknown_format_before_creating_output(self):
        with self.assertRaisesRegex(ValueError, "unsupported export format"):
            server.normalize_export_format("everything")

    def test_magic_upscale_falls_back_when_realesrgan_drops_alpha(self):
        source = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
        for y in range(2, 8):
            for x in range(3, 18):
                source.putpixel((x, y), (20, 220, 140, 255))

        original_runner = server.run_realesrgan_anime

        def fake_runner(input_path: Path, output_path: Path, output_scale=None) -> None:
            with Image.open(input_path) as image:
                Image.new("RGBA", (image.width * 4, image.height * 4), (0, 0, 0, 0)).save(output_path)

        server.run_realesrgan_anime = fake_runner
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                upscaled, source_size = server.build_magic_upscaled_frame(
                    source,
                    temp_path / "input.png",
                    temp_path / "output.png",
                )

                self.assertEqual(source_size, source.size)
                self.assertIsNotNone(upscaled.getchannel("A").getbbox())
                with Image.open(temp_path / "output.png") as saved_output:
                    self.assertIsNotNone(saved_output.convert("RGBA").getchannel("A").getbbox())
                upscaled.close()
        finally:
            server.run_realesrgan_anime = original_runner

    def test_magic_cache_skips_blank_frame_when_source_has_alpha(self):
        old_jobs_dir = server.JOBS_DIR
        old_magic_dir = server.MAGIC_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                server.JOBS_DIR = root / "jobs"
                server.MAGIC_DIR = root / "magic"

                job_id = "job-1"
                processed_dir = server.job_dir(job_id) / "processed"
                processed_dir.mkdir(parents=True)
                source = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
                for y in range(3, 9):
                    for x in range(3, 9):
                        source.putpixel((x, y), (255, 255, 255, 255))
                source.save(processed_dir / "frame_001.png")
                server.save_job_manifest(
                    job_id,
                    {
                        "frame_count": 1,
                        "frames": [{"index": 0, "name": "frame_001.png"}],
                    },
                )

                magic_root = server.MAGIC_DIR / "run-1-magic"
                variants = {}
                for config in server.MAGIC_VARIANTS:
                    frames_dir = magic_root / str(config["dir"])
                    frames_dir.mkdir(parents=True)
                    Image.new("RGBA", (6, 6), (0, 0, 0, 0)).save(frames_dir / "frame_001.png")
                    variants[str(config["key"])] = {
                        "key": str(config["key"]),
                        "frames_dir": str(frames_dir),
                        "frames": [
                            {
                                "index": 0,
                                "source_index": 0,
                                "name": "frame_001.png",
                                "width": 6,
                                "height": 6,
                            }
                        ],
                    }
                (magic_root / "manifest.json").write_text(
                    json.dumps(
                        {
                            "magic_id": "run-1",
                            "job_id": job_id,
                            "model": server.REAL_ESRGAN_ANIME_MODEL,
                            "resize_mode": "soft",
                            "variants": variants,
                        }
                    ),
                    encoding="utf-8",
                )

                self.assertEqual(server.find_cached_magic_frames(job_id, "soft"), {})
        finally:
            server.JOBS_DIR = old_jobs_dir
            server.MAGIC_DIR = old_magic_dir

    def test_magic_preview_can_skip_realesrgan(self):
        old_jobs_dir = server.JOBS_DIR
        old_magic_dir = server.MAGIC_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                server.JOBS_DIR = root / "jobs"
                server.MAGIC_DIR = root / "magic"

                job_id = "job-2"
                processed_dir = server.job_dir(job_id) / "processed"
                processed_dir.mkdir(parents=True)
                source = Image.new("RGBA", (24, 16), (0, 0, 0, 0))
                for y in range(4, 12):
                    for x in range(5, 20):
                        source.putpixel((x, y), (40, 180, 255, 255))
                source.save(processed_dir / "frame_001.png")
                server.save_job_manifest(
                    job_id,
                    {
                        "frame_count": 1,
                        "frames": [
                            {
                                "index": 0,
                                "name": "frame_001.png",
                                "width": 24,
                                "height": 16,
                            }
                        ],
                    },
                )

                result = server.magic_preview_job(job_id, [0], "soft", use_realesrgan=False)

                self.assertFalse(result["use_realesrgan"])
                self.assertEqual(result["model"], "none")
                self.assertEqual(result["upscale"], 1)
                self.assertEqual(result["generated_count"], 1)
                self.assertEqual(result["reused_count"], 0)
                self.assertEqual(result["frame_count"], 1)
                for variant in result["variants"].values():
                    frame_path = Path(variant["frames_dir"]) / "frame_001.png"
                    with Image.open(frame_path) as image:
                        self.assertIsNotNone(image.convert("RGBA").getchannel("A").getbbox())
        finally:
            server.JOBS_DIR = old_jobs_dir
            server.MAGIC_DIR = old_magic_dir

    def test_scale_processing_reuses_unchanged_frames_and_variants(self):
        old_jobs_dir = server.JOBS_DIR
        old_magic_dir = server.MAGIC_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                server.JOBS_DIR = root / "jobs"
                server.MAGIC_DIR = root / "magic"

                job_id = "job-scale-delta"
                processed_dir = server.job_dir(job_id) / "processed"
                processed_dir.mkdir(parents=True)
                frames = []
                for index, color in enumerate(((40, 180, 255, 255), (255, 120, 40, 255))):
                    name = f"frame_{index + 1:03d}.png"
                    Image.new("RGBA", (24, 16), color).save(processed_dir / name)
                    frames.append({"index": index, "name": name, "width": 24, "height": 16})
                server.save_job_manifest(job_id, {"frame_count": 2, "frames": frames})

                first = server.magic_preview_job(
                    job_id,
                    [0],
                    "soft",
                    use_realesrgan=False,
                    variant_keys=["half", "quarter"],
                )
                second = server.magic_preview_job(
                    job_id,
                    [0, 1],
                    "soft",
                    use_realesrgan=False,
                    variant_keys=["half", "quarter"],
                )

                self.assertEqual(first["variant_keys"], ["half", "quarter"])
                self.assertEqual(first["variants"]["half"]["frames"][0]["width"], 12)
                self.assertEqual(first["variants"]["half"]["frames"][0]["height"], 8)
                self.assertEqual(first["variants"]["quarter"]["frames"][0]["width"], 6)
                self.assertEqual(first["variants"]["quarter"]["frames"][0]["height"], 4)
                self.assertEqual(second["generated_count"], 1)
                self.assertEqual(second["reused_count"], 1)
                self.assertEqual(second["generated_variant_count"], 2)
                self.assertEqual(second["reused_variant_count"], 2)
                self.assertTrue(second["variants"]["half"]["frames"][0]["cached"])
                self.assertFalse(second["variants"]["half"]["frames"][1]["cached"])
        finally:
            server.JOBS_DIR = old_jobs_dir
            server.MAGIC_DIR = old_magic_dir

    def test_scale_processing_esr_restores_100_percent_to_original_dimensions(self):
        old_jobs_dir = server.JOBS_DIR
        old_magic_dir = server.MAGIC_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                server.JOBS_DIR = root / "jobs"
                server.MAGIC_DIR = root / "magic"
                job_id = "job-esr-full"
                processed_dir = server.job_dir(job_id) / "processed"
                processed_dir.mkdir(parents=True)
                source_path = processed_dir / "frame_001.png"
                Image.new("RGBA", (24, 16), (40, 180, 255, 255)).save(source_path)
                server.save_job_manifest(
                    job_id,
                    {"frame_count": 1, "frames": [{"index": 0, "name": source_path.name, "width": 24, "height": 16}]},
                )

                def fake_upscale(image, _input_path, _output_path):
                    return image.resize((image.width * 4, image.height * 4)), image.size

                with (
                    mock.patch.object(server, "resolve_realesrgan_binary", return_value=Path("fake-esr.exe")),
                    mock.patch.object(server, "resolve_realesrgan_model_dir", return_value=root / "models"),
                    mock.patch.object(server, "build_magic_upscaled_frame", side_effect=fake_upscale) as upscale_mock,
                ):
                    result = server.magic_preview_job(
                        job_id,
                        [0],
                        "soft",
                        use_realesrgan=True,
                        variant_keys=["full"],
                    )
                    added_size = server.magic_preview_job(
                        job_id,
                        [0],
                        "soft",
                        use_realesrgan=True,
                        variant_keys=["quarter"],
                    )

                output = result["variants"]["full"]["frames"][0]
                self.assertTrue(result["use_realesrgan"])
                self.assertEqual(result["upscale"], 4)
                self.assertEqual(upscale_mock.call_count, 1)
                self.assertEqual(added_size["esr_generated_count"], 0)
                self.assertEqual(added_size["esr_reused_count"], 1)
                self.assertEqual(added_size["variants"]["quarter"]["frames"][0]["width"], 6)
                self.assertEqual((output["width"], output["height"]), (24, 16))
                with Image.open(Path(result["variants"]["full"]["frames_dir"]) / output["name"]) as image:
                    self.assertEqual(image.size, (24, 16))
        finally:
            server.JOBS_DIR = old_jobs_dir
            server.MAGIC_DIR = old_magic_dir

    def test_scale_variant_export_only_generates_the_requested_format(self):
        old_jobs_dir = server.JOBS_DIR
        old_magic_dir = server.MAGIC_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                server.JOBS_DIR = root / "jobs"
                server.MAGIC_DIR = root / "magic"
                exports_dir = root / "exports"
                exports_dir.mkdir()
                job_id = "job-scale-export"
                server.save_job_manifest(
                    job_id,
                    {"frame_count": 1, "frames": [{"index": 0, "name": "frame_001.png", "width": 24, "height": 16}]},
                )
                scale_root = server.MAGIC_DIR / "scale-export-magic"
                frames_dir = scale_root / "frames"
                frames_dir.mkdir(parents=True)
                Image.new("RGBA", (12, 8), (40, 180, 255, 255)).save(frames_dir / "frame_001.png")
                (scale_root / "manifest.json").write_text(
                    json.dumps(
                        {
                            "magic_id": "scale-export",
                            "job_id": job_id,
                            "use_realesrgan": True,
                            "variants": {
                                "half": {
                                    "key": "half",
                                    "label": "1/2",
                                    "scale": 0.5,
                                    "frames_dir": str(frames_dir),
                                    "frame_count": 1,
                                    "frames": [{"source_index": 0, "name": "frame_001.png", "width": 12, "height": 8}],
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )

                def fake_mov(_paths, _sizes, output_path, _width, _height, _duration, **_kwargs):
                    output_path.write_bytes(b"mov")

                def fake_gif(_paths, _sizes, output_path, _width, _height, _duration):
                    output_path.write_bytes(b"gif")

                def fake_sheet(_paths, _sizes, sheet_path, metadata_path, width, height, duration):
                    sheet_path.write_bytes(b"sheet")
                    metadata_path.write_text("{}", encoding="utf-8")
                    return {"columns": 1, "rows": 1, "width": width, "height": height, "duration_ms": duration}

                with (
                    mock.patch.object(server, "configured_exports_dir", return_value=exports_dir),
                    mock.patch.object(server, "save_alpha_mov", side_effect=fake_mov) as mov_mock,
                    mock.patch.object(server, "save_gif", side_effect=fake_gif) as gif_mock,
                    mock.patch.object(server, "save_sprite_sheet", side_effect=fake_sheet) as sheet_mock,
                ):
                    frames_result = server.export_magic_frames("scale-export", "half", 80, "frames")
                    mov_result = server.export_magic_frames("scale-export", "half", 80, "mov")
                    gif_result = server.export_magic_frames("scale-export", "half", 80, "gif")
                    sheet_result = server.export_magic_frames("scale-export", "half", 80, "sprite_sheet")

                self.assertEqual((mov_mock.call_count, gif_mock.call_count, sheet_mock.call_count), (1, 1, 1))
                self.assertEqual(mov_mock.call_args.args[3:5], (12, 8))
                self.assertNotIn("render_sizes", mov_mock.call_args.kwargs)
                self.assertTrue(Path(frames_result["frames_dir"], "frames.json").is_file())
                self.assertEqual(set(path.suffix for path in Path(mov_result["output_dir"]).iterdir()), {".mov", ".json"})
                self.assertEqual(set(path.suffix for path in Path(gif_result["output_dir"]).iterdir()), {".gif", ".json"})
                self.assertEqual(sorted(path.name for path in Path(sheet_result["sheet_dir"]).iterdir()), ["sheet.json", "sheet.png"])
                self.assertFalse((Path(mov_result["output_dir"]) / "frames").exists())
                self.assertFalse((Path(gif_result["output_dir"]) / "sprite-sheet").exists())
        finally:
            server.JOBS_DIR = old_jobs_dir
            server.MAGIC_DIR = old_magic_dir


if __name__ == "__main__":
    unittest.main()
