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

    def test_runtime_directory_helpers_reject_path_syntax_before_joining(self):
        for directory, label in (
            (server.upload_dir, "upload"),
            (server.job_dir, "job"),
            (server.preview_dir, "preview"),
            (server.line_cleaner_dir, "line-cleaner"),
            (server.load_magic_manifest, "scale-processing"),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                directory("../outside")

    def test_cleanup_removes_all_generated_exports_and_preserves_unrelated_content(self):
        generated_names = [
            "20260902-120000-abcd-export",
            "20260902-120001-abcd-magic-half-frames",
            *[
                f"20260902-120002-abcd-scale-{variant}-{format_name}"
                for variant in ("full", "half", "quarter", "eighth")
                for format_name in ("frames", "sprite_sheet", "mov", "gif")
            ],
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            work_dir = root / "work"
            managed_dirs = tuple(work_dir / name for name in ("uploads", "jobs", "exports", "previews", "line-cleaner", "magic"))
            for directory in managed_dirs:
                directory.mkdir(parents=True)
                (directory / "generated.bin").write_bytes(b"generated")
            settings_path = work_dir / "settings.json"
            settings_path.write_text('{"keep": true}', encoding="utf-8")
            external_export_root = root / "external-exports"
            external_export_root.mkdir()
            for name in generated_names:
                generated = external_export_root / name
                generated.mkdir()
                (generated / "generated.mov").write_bytes(b"generated")
            downloaded_copy = external_export_root / "downloaded-copy.mov"
            downloaded_copy.write_bytes(b"keep")
            manual_output = external_export_root / "manual-output"
            manual_output.mkdir()

            configured_root = self.lexical_alias(external_export_root)
            with (
                mock.patch.object(server, "WORK_DIR", work_dir),
                mock.patch.object(server, "EXPORTS_DIR", managed_dirs[2]),
                mock.patch.object(server, "MANAGED_RUNTIME_DIRS", managed_dirs),
                mock.patch.object(server, "configured_exports_dir", return_value=configured_root),
            ):
                result = server.clear_managed_runtime_files(True)

            self.assertEqual(result["cleared"], ["uploads", "jobs", "exports", "previews", "line-cleaner", "magic"])
            self.assertCountEqual(result["cleared_export_directories"], generated_names)
            self.assertTrue(all(directory.is_dir() and not any(directory.iterdir()) for directory in managed_dirs))
            self.assertTrue(settings_path.exists())
            self.assertTrue(downloaded_copy.exists())
            self.assertTrue(manual_output.is_dir())
            self.assertTrue(all(not (external_export_root / name).exists() for name in generated_names))

    def test_cleanup_rejects_generated_looking_export_symlink_that_escapes_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            work_dir = root / "work"
            managed_dirs = tuple(work_dir / name for name in ("uploads", "jobs", "exports", "previews", "line-cleaner", "magic"))
            for directory in managed_dirs:
                directory.mkdir(parents=True)
            external_export_root = root / "external-exports"
            external_export_root.mkdir()
            outside = root / "outside-generated"
            outside.mkdir()
            escape = external_export_root / "20260902-120000-abcd-export"
            try:
                escape.symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")

            with (
                mock.patch.object(server, "WORK_DIR", work_dir),
                mock.patch.object(server, "EXPORTS_DIR", managed_dirs[2]),
                mock.patch.object(server, "MANAGED_RUNTIME_DIRS", managed_dirs),
                mock.patch.object(server, "configured_exports_dir", return_value=self.lexical_alias(external_export_root)),
            ):
                with self.assertRaises(ValueError):
                    server.clear_managed_runtime_files(True)

            self.assertTrue(outside.is_dir())
            self.assertTrue(escape.is_symlink())

    def test_openable_directory_is_limited_to_work_and_configured_export_roots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            work_dir = root / "work"
            work_dir.mkdir()
            external_export_root = root / "external-exports"
            external_export_root.mkdir()
            ordinary_external_dir = root / "ordinary"
            ordinary_external_dir.mkdir()
            external_file = root / "outside.mov"
            external_file.write_bytes(b"not a directory")

            with (
                mock.patch.object(server, "WORK_DIR", work_dir),
                mock.patch.object(server, "configured_exports_dir", return_value=self.lexical_alias(external_export_root)),
            ):
                self.assertTrue(server.is_openable_directory(work_dir))
                self.assertTrue(server.is_openable_directory(external_export_root))
                self.assertFalse(server.is_openable_directory(ordinary_external_dir))
                self.assertFalse(server.is_openable_directory(external_file))

    @staticmethod
    def lexical_alias(path: Path) -> Path:
        resolved = path.resolve()
        private_var_prefix = "/private/var/"
        if str(resolved).startswith(private_var_prefix):
            alias = Path("/var") / resolved.relative_to("/private/var")
            if alias.exists() and alias.resolve() == resolved:
                return alias
        return resolved
