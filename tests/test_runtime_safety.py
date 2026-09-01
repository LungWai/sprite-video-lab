import hashlib
import io
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import server


class InMemoryDownloadResponse(io.BytesIO):
    def __init__(self, payload: bytes, *, advertised_length: int | None = None):
        super().__init__(payload)
        self.headers = {}
        if advertised_length is not None:
            self.headers["Content-Length"] = str(advertised_length)
        self.read_sizes = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


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


class RealEsrganIntegrityTests(unittest.TestCase):
    def test_verified_download_writes_matching_bytes_in_one_mib_chunks(self):
        payload = (b"v" * (1024 * 1024)) + b"erified"
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "package.zip"
            with InMemoryDownloadResponse(payload) as response:
                written = self.copy_verified_download(
                    response,
                    destination,
                    expected_sha256,
                    len(payload),
                )

            self.assertEqual(written, len(payload))
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(response.read_sizes, [1024 * 1024] * 3)

    def test_verified_download_rejects_altered_bytes_without_partial_artifact(self):
        expected_sha256 = hashlib.sha256(b"expected archive").hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "package.zip"
            with InMemoryDownloadResponse(b"altered archive") as response:
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    self.copy_verified_download(
                        response,
                        destination,
                        expected_sha256,
                        1024,
                    )

            self.assertFalse(destination.exists())

    def test_verified_download_rejects_oversized_stream_without_partial_artifact(self):
        payload = b"oversized"
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "package.zip"
            with InMemoryDownloadResponse(payload) as response:
                with self.assertRaisesRegex(RuntimeError, "allowed size"):
                    self.copy_verified_download(
                        response,
                        destination,
                        expected_sha256,
                        len(payload) - 1,
                    )

            self.assertFalse(destination.exists())

    def test_download_rejects_checksum_mismatch_without_partial_artifact(self):
        payload = b"altered archive"
        expected_sha256 = hashlib.sha256(b"expected archive").hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "package.zip"
            response = InMemoryDownloadResponse(payload, advertised_length=len(payload))
            with (
                mock.patch.object(server, "urlopen", return_value=response),
                mock.patch.object(server, "REAL_ESRGAN_WINDOWS_PACKAGE_SHA256", expected_sha256),
                self.assertRaisesRegex(RuntimeError, "checksum"),
            ):
                server.download_realesrgan_windows_package(destination)

            self.assertFalse(destination.exists())

    def test_download_rejects_advertised_oversize_before_streaming(self):
        payload = b"must not be read"
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "package.zip"
            response = InMemoryDownloadResponse(payload, advertised_length=(64 * 1024 * 1024) + 1)
            with (
                mock.patch.object(server, "urlopen", return_value=response),
                self.assertRaisesRegex(RuntimeError, "allowed size"),
            ):
                server.download_realesrgan_windows_package(destination)

            self.assertEqual(response.read_sizes, [])
            self.assertFalse(destination.exists())

    def test_download_enforces_streamed_size_despite_smaller_advertised_length(self):
        payload = b"12345"
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "package.zip"
            response = InMemoryDownloadResponse(payload, advertised_length=4)
            with (
                mock.patch.object(server, "urlopen", return_value=response),
                mock.patch.object(server, "REAL_ESRGAN_WINDOWS_PACKAGE_MAX_BYTES", 4),
                mock.patch.object(server, "REAL_ESRGAN_WINDOWS_PACKAGE_SHA256", expected_sha256),
                self.assertRaisesRegex(RuntimeError, "allowed size"),
            ):
                server.download_realesrgan_windows_package(destination)

            self.assertFalse(destination.exists())

    def test_invalid_archive_install_leaves_fresh_target_absent(self):
        payload = b"not a ZIP archive"
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            target_dir = work_dir / "tools" / "realesrgan-ncnn-vulkan"

            with self.invalid_archive_install_context(work_dir, payload):
                with self.assertRaises(zipfile.BadZipFile):
                    server.install_realesrgan_runtime(True)

            self.assertFalse(target_dir.exists())

    def test_invalid_archive_install_preserves_preexisting_partial_target(self):
        payload = b"not a ZIP archive"
        sentinel_bytes = b"pre-existing partial installation\x00\xff"
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            target_dir = work_dir / "tools" / "realesrgan-ncnn-vulkan"
            nested_dir = target_dir / "models"
            nested_dir.mkdir(parents=True)
            sentinel = nested_dir / "sentinel.bin"
            sentinel.write_bytes(sentinel_bytes)
            before = self.snapshot_tree(target_dir)

            with self.invalid_archive_install_context(work_dir, payload):
                with self.assertRaises(zipfile.BadZipFile):
                    server.install_realesrgan_runtime(True)

            self.assertEqual(self.snapshot_tree(target_dir), before)
            self.assertEqual(sentinel.read_bytes(), sentinel_bytes)

    def copy_verified_download(self, response, destination, expected_sha256, max_bytes):
        copy_download = getattr(server, "copy_verified_download", None)
        self.assertIsNotNone(copy_download, "copy_verified_download is missing")
        return copy_download(response, destination, expected_sha256, max_bytes)

    @staticmethod
    @contextmanager
    def invalid_archive_install_context(work_dir: Path, payload: bytes):
        response = InMemoryDownloadResponse(payload, advertised_length=len(payload))
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        with (
            mock.patch.object(server, "WORK_DIR", work_dir),
            mock.patch.object(server, "resolve_realesrgan_binary", return_value=None),
            mock.patch.object(server, "resolve_realesrgan_model_dir", return_value=None),
            mock.patch.object(server, "urlopen", return_value=response),
            mock.patch.object(server, "REAL_ESRGAN_WINDOWS_PACKAGE_SHA256", expected_sha256),
        ):
            yield

    @staticmethod
    def snapshot_tree(root: Path) -> dict[str, tuple[str, bytes]]:
        snapshot = {}
        for path in sorted(root.rglob("*")):
            relative_path = path.relative_to(root).as_posix()
            snapshot[relative_path] = ("directory", b"") if path.is_dir() else ("file", path.read_bytes())
        return snapshot


class RealEsrganArchiveSafetyTests(unittest.TestCase):
    def test_realesrgan_archive_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_path = root / "package.zip"
            extract_dir = root / "extracted"
            extract_dir.mkdir()
            outside_path = root / "outside.txt"
            with zipfile.ZipFile(package_path, "w") as archive:
                archive.writestr("../outside.txt", b"must stay contained")

            with self.assertRaises(RuntimeError):
                server.extract_realesrgan_package(package_path, extract_dir)

            self.assertFalse(outside_path.exists())
