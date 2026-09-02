"""Static contract tests for the Windows portable bundle and launcher scripts.

These tests run on any platform. They parse the PowerShell and batch scripts
into structure (variable tables, command sequences, brace-delimited blocks) and
assert the contracts the scripts must satisfy so that the portable ZIP that
``build_portable_bundle.ps1`` produces is actually launchable by
``start_sprite_video_lab_portable.bat`` and both launchers wait for the server
to be ready before opening a browser.

Nothing here executes cmd.exe or Windows PowerShell. Where ``pwsh`` is
available the readiness probe is exercised against a local HTTP server; that
test is skipped otherwise. All other evidence is static.
"""

import http.server
import re
import shutil
import subprocess
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILDER_PATH = REPO_ROOT / "build_portable_bundle.ps1"
READINESS_PATH = REPO_ROOT / "wait_for_server.ps1"
STANDARD_LAUNCHER_PATH = REPO_ROOT / "start_sprite_video_lab.bat"
PORTABLE_LAUNCHER_PATH = REPO_ROOT / "start_sprite_video_lab_portable.bat"

READINESS_ENDPOINT = "/api/app-version"
SERVER_START_TITLE = '"Sprite Video Lab Server"'


# --------------------------------------------------------------------------
# Script parsing helpers
# --------------------------------------------------------------------------


def read_script(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"required script is missing: {path.relative_to(REPO_ROOT)}")
    return path.read_text(encoding="utf-8-sig")


def powershell_code_lines(text: str) -> list[str]:
    """Return script lines with comment-only lines removed (positions preserved as '')."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        lines.append("" if stripped.startswith("#") else line)
    return lines


def batch_logical_commands(text: str) -> list[str]:
    """Join ``^`` continuations and drop comments so each entry is one cmd.exe command."""
    commands = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if buffer:
            buffer += " " + line.strip()
        else:
            buffer = line.strip()
        if buffer.endswith("^"):
            buffer = buffer[:-1].rstrip()
            continue
        if buffer and not buffer.lower().startswith(("rem ", "::")):
            commands.append(buffer)
        buffer = ""
    if buffer:
        commands.append(buffer)
    return commands


def parse_batch_set_table(commands: list[str]) -> dict[str, str]:
    """Collect ``set "NAME=VALUE"`` assignments (first assignment wins, including
    the ``if "%NAME%"=="" set ...`` default idiom)."""
    table: dict[str, str] = {}
    pattern = re.compile(r'set\s+"([A-Za-z_][A-Za-z0-9_]*)=([^"]*)"', re.IGNORECASE)
    for command in commands:
        for match in pattern.finditer(command):
            name, value = match.group(1).upper(), match.group(2)
            table.setdefault(name, value)
    return table


def expand_batch_value(value: str, table: dict[str, str], roots: dict[str, str]) -> str:
    """Expand ``%VAR%`` references using the script's own set table.

    ``roots`` maps variables that stand for the extracted bundle directory to a
    relative-path prefix so that the result is a bundle-relative path.
    """
    previous = None
    while previous != value:
        previous = value

        def replace(match: re.Match) -> str:
            name = match.group(1).upper()
            if name in roots:
                return roots[name]
            if name in table:
                return table[name]
            return match.group(0)

        value = re.sub(r"%([A-Za-z_~][A-Za-z0-9_]*)%", replace, value)
    return value


def parse_powershell_join_path_table(lines: list[str]) -> dict[str, tuple[str, str]]:
    """Collect ``$var = Join-Path $parent "leaf"`` assignments as var -> (parent, leaf)."""
    table: dict[str, tuple[str, str]] = {}
    pattern = re.compile(r'^\s*\$(\w+)\s*=\s*Join-Path\s+\$(\w+)\s+"([^"]+)"\s*$')
    for line in lines:
        match = pattern.match(line)
        if match:
            table[match.group(1)] = (match.group(2), match.group(3))
    return table


def resolve_powershell_path(expression: str, table: dict[str, tuple[str, str]], root_var: str) -> str:
    """Resolve ``$var`` or ``(Join-Path $var "leaf")`` to a path relative to ``$root_var``.

    Raises ``AssertionError`` when the expression cannot be traced back to the
    root variable through the script's own Join-Path assignments.
    """
    expression = expression.strip()
    join = re.fullmatch(r'\(?\s*Join-Path\s+\$(\w+)\s+"([^"]+)"\s*\)?', expression)
    if join:
        prefix = resolve_powershell_path("$" + join.group(1), table, root_var)
        return join.group(2) if prefix == "" else prefix + "\\" + join.group(2)
    var = re.fullmatch(r"\$(\w+)", expression)
    if not var:
        raise AssertionError(f"unsupported path expression: {expression!r}")
    name = var.group(1)
    if name == root_var:
        return ""
    if name not in table:
        raise AssertionError(f"${name} is not derived from ${root_var} via Join-Path")
    parent, leaf = table[name]
    prefix = resolve_powershell_path("$" + parent, table, root_var)
    return leaf if prefix == "" else prefix + "\\" + leaf


def resolve_powershell_string(expression: str, lines: list[str]) -> str:
    """Resolve ``$var`` to the double-quoted literal it was assigned (``$var = "..."``),
    or return the literal itself when ``expression`` is already a quoted string."""
    expression = expression.strip()
    literal = re.fullmatch(r'"([^"]*)"', expression)
    if literal:
        return literal.group(1)
    var = re.fullmatch(r"\$(\w+)", expression)
    if not var:
        raise AssertionError(f"unsupported string expression: {expression!r}")
    assignments = [
        match.group(1)
        for line in lines
        for match in [re.match(rf'^\s*\${var.group(1)}\s*=\s*"([^"]*)"\s*$', line)]
        if match
    ]
    if len(assignments) != 1:
        raise AssertionError(f"expected exactly one string assignment to ${var.group(1)}, found {len(assignments)}")
    return assignments[0]


def find_line(lines: list[str], predicate, *, description: str, start: int = 0) -> int:
    for index in range(start, len(lines)):
        if predicate(lines[index]):
            return index
    raise AssertionError(f"could not find {description}")


def find_all_lines(lines: list[str], predicate) -> list[int]:
    return [index for index, line in enumerate(lines) if predicate(line)]


def block_end(lines: list[str], open_index: int) -> int:
    """Return the index of the line that closes the ``{`` block opened on ``open_index``."""
    depth = 0
    for index in range(open_index, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if depth == 0:
            return index
        if depth < 0:
            break
    raise AssertionError(f"unbalanced braces starting at line {open_index + 1}")


def parse_powershell_array(lines: list[str], variable: str) -> list[str]:
    """Return the string entries of ``$variable = @( "a", "b", ... )``."""
    start = find_line(
        lines,
        lambda line: re.match(rf"^\s*\${variable}\s*=\s*@\(\s*$", line) is not None,
        description=f"the ${variable} array",
    )
    entries = []
    for line in lines[start + 1 :]:
        if line.strip() == ")":
            return entries
        match = re.fullmatch(r'\s*"([^"]+)",?\s*', line)
        if not match:
            raise AssertionError(f"unexpected ${variable} entry: {line!r}")
        entries.append(match.group(1))
    raise AssertionError(f"${variable} array is not closed")


# --------------------------------------------------------------------------
# Builder: bundle layout and fail-fast validation
# --------------------------------------------------------------------------


class BuilderLayoutContractTests(unittest.TestCase):
    """The bundle written by build_portable_bundle.ps1 must match the layout the
    portable launcher executes from."""

    def setUp(self):
        self.builder = read_script(BUILDER_PATH)
        self.lines = powershell_code_lines(self.builder)
        self.paths = parse_powershell_join_path_table(self.lines)

    def _copy_tree_contents_function(self) -> list[str]:
        start = find_line(
            self.lines,
            lambda line: re.match(r"^\s*function\s+Copy-TreeContents\b", line) is not None,
            description="the Copy-TreeContents function",
        )
        return self.lines[start : block_end(self.lines, start) + 1]

    def test_copy_tree_contents_copies_children_not_the_container(self):
        """Contract: Copy-TreeContents places the *contents* of Source directly under
        Destination (per-child Copy-Item), so the Python home does not end up nested
        one level deeper than the launcher expects."""
        body = "\n".join(self._copy_tree_contents_function())
        self.assertRegex(body, r"Ensure-Directory\s+-PathValue\s+\$Destination")
        self.assertRegex(body, r"Get-ChildItem\s+-LiteralPath\s+\$Source\s+-Force")
        self.assertRegex(
            body, r"Copy-Item\s+-LiteralPath\s+\$_\.FullName\s+-Destination\s+\$Destination\s+-Recurse\s+-Force"
        )
        self.assertNotRegex(
            body,
            r"Copy-Item\s+-LiteralPath\s+\$Source\b",
            "Copy-TreeContents must not copy the Source directory itself as a container",
        )

    def test_python_runtime_is_copied_into_the_python_runtime_folder(self):
        """Contract: the resolved Python home is copied with Copy-TreeContents into
        $pythonRuntimeRoot (runtime\\python), not into $runtimeRoot as a container
        (which would produce runtime\\Python312 and a missing runtime\\python\\python.exe)."""
        copy_lines = find_all_lines(self.lines, lambda line: "$pythonHomeResolved" in line and "Copy-" in line)
        self.assertEqual(len(copy_lines), 1, "expected exactly one copy of the Python home")
        copy_line = self.lines[copy_lines[0]]
        match = re.match(r"^\s*Copy-TreeContents\s+-Source\s+\$pythonHomeResolved\s+-Destination\s+(\$\w+)\s*$", copy_line)
        self.assertIsNotNone(match, f"Python home must be copied via Copy-TreeContents, found: {copy_line.strip()!r}")
        destination = resolve_powershell_path(match.group(1), self.paths, "bundleRoot")
        self.assertEqual(destination, "runtime\\python")

    def test_copy_tree_is_retained_for_the_corridorkey_container(self):
        """Contract: the CorridorKey directory is still copied as a container so the
        bundle keeps runtime\\models\\portable-models\\EZ-CorridorKey\\..."""
        self.assertRegex(
            self.builder,
            r'Copy-Tree\s+-Source\s+\(Join-Path\s+\$modelRootResolved\s+"EZ-CorridorKey"\)\s+-Destination\s+\$portableModelRoot',
        )

    def test_project_files_include_launcher_server_and_readiness_probe(self):
        """Contract: every file the portable launcher executes or delegates to is
        shipped in the bundle."""
        entries = parse_powershell_array(self.lines, "projectFiles")
        for required in ("app", "server.py", "start_sprite_video_lab_portable.bat", "wait_for_server.ps1"):
            with self.subTest(entry=required):
                self.assertIn(required, entries)


class BuilderValidationContractTests(unittest.TestCase):
    """The builder must refuse to produce a ZIP whose contents cannot launch."""

    REQUIRED_LEAVES = {
        "runtime\\python\\python.exe",
        "runtime\\ffmpeg\\ffmpeg.exe",
        "runtime\\ffmpeg\\ffprobe.exe",
        "server.py",
        "start_sprite_video_lab_portable.bat",
    }

    def setUp(self):
        self.builder = read_script(BUILDER_PATH)
        self.lines = powershell_code_lines(self.builder)
        self.paths = parse_powershell_join_path_table(self.lines)
        compress_lines = find_all_lines(self.lines, lambda line: re.match(r"^\s*Compress-Archive\b", line) is not None)
        self.assertEqual(len(compress_lines), 1, "expected exactly one Compress-Archive call")
        self.compress_index = compress_lines[0]

    PATH_EXPRESSION = r"(\(Join-Path\s+\$\w+\s+\"[^\"]+\"\)|\$\w+)"

    def _assert_required_path_helper_checks_leaf_and_throws(self) -> None:
        """Assert-RequiredPath must test its -PathValue with -PathType Leaf and throw otherwise."""
        start = find_line(
            self.lines,
            lambda line: re.match(r"^\s*function\s+Assert-RequiredPath\b", line) is not None,
            description="the Assert-RequiredPath function",
        )
        body = self.lines[start : block_end(self.lines, start) + 1]
        guard_index = find_line(
            body,
            lambda line: re.search(r"if\s*\(\s*-not\s*\(\s*Test-Path\s+-LiteralPath\s+\$PathValue\s+-PathType\s+Leaf\s*\)\s*\)\s*\{", line)
            is not None,
            description="the -PathType Leaf guard inside Assert-RequiredPath",
        )
        throw_index = find_line(body, lambda line: re.match(r"^\s*throw\b", line) is not None, description="throw in Assert-RequiredPath", start=guard_index)
        self.assertLessEqual(throw_index, block_end(body, guard_index))

    def _leaf_validations(self) -> dict[str, int]:
        """Map each bundle-relative path validated with Test-Path -PathType Leaf to its line index.

        Accepts both inline ``Test-Path ... -PathType Leaf`` calls and calls to the
        ``Assert-RequiredPath`` helper, whose body is verified to perform that check.
        """
        validations: dict[str, int] = {}
        inline = re.compile(rf"Test-Path\s+-LiteralPath\s+{self.PATH_EXPRESSION}\s+-PathType\s+Leaf")
        helper = re.compile(rf"^\s*Assert-RequiredPath\s+-PathValue\s+{self.PATH_EXPRESSION}\s+-Description\s+\"[^\"]+\"\s*$")
        helper_verified = False
        for index, line in enumerate(self.lines):
            for match in inline.finditer(line):
                if match.group(1) != "$PathValue":
                    validations[resolve_powershell_path(match.group(1), self.paths, "bundleRoot")] = index
            match = helper.match(line)
            if match:
                if not helper_verified:
                    self._assert_required_path_helper_checks_leaf_and_throws()
                    helper_verified = True
                validations[resolve_powershell_path(match.group(1), self.paths, "bundleRoot")] = index
        return validations

    def test_required_bundle_files_are_validated_as_leaves_before_archiving(self):
        """Contract: python.exe, ffmpeg.exe, ffprobe.exe, server.py and the portable
        launcher are each checked with Test-Path -PathType Leaf at their exact bundle
        path, and every such check runs before Compress-Archive."""
        validations = self._leaf_validations()
        missing = self.REQUIRED_LEAVES - set(validations)
        self.assertFalse(missing, f"bundle paths not validated as leaves: {sorted(missing)}")
        for path in self.REQUIRED_LEAVES:
            with self.subTest(path=path):
                self.assertLess(validations[path], self.compress_index, f"{path} is validated after Compress-Archive")

    def test_bundled_python_import_check_runs_before_archiving(self):
        """Contract: the bundled runtime\\python\\python.exe must import PIL and
        python_multipart, and a non-zero exit throws before any ZIP is created."""
        import_index = find_line(
            self.lines,
            lambda line: re.search(r'&\s+\(Join-Path\s+\$pythonRuntimeRoot\s+"python\.exe"\)\s+-c\s+"import PIL, python_multipart"', line)
            is not None,
            description="the bundled Python import check",
        )
        self.assertEqual(resolve_powershell_path("$pythonRuntimeRoot", self.paths, "bundleRoot"), "runtime\\python")
        exit_code_index = find_line(
            self.lines,
            lambda line: re.search(r"if\s*\(\s*\$LASTEXITCODE\s+-ne\s+0\s*\)\s*\{", line) is not None,
            description="the $LASTEXITCODE check after the import",
            start=import_index,
        )
        throw_index = find_line(
            self.lines,
            lambda line: re.match(r"^\s*throw\b", line) is not None,
            description="a throw inside the $LASTEXITCODE check",
            start=exit_code_index,
        )
        self.assertLessEqual(throw_index, block_end(self.lines, exit_code_index))
        self.assertLess(import_index, self.compress_index)
        self.assertLess(throw_index, self.compress_index)

    def test_failed_validation_cannot_reach_compress_archive(self):
        """Contract: validation failures terminate the script. $ErrorActionPreference is
        Stop, every throw precedes Compress-Archive, and no try/catch between the first
        validation and Compress-Archive could swallow a failure."""
        self.assertIsNotNone(re.search(r'^\$ErrorActionPreference\s*=\s*"Stop"\s*$', self.builder, re.MULTILINE))
        throw_lines = find_all_lines(self.lines, lambda line: re.match(r"^\s*throw\b", line) is not None)
        self.assertTrue(throw_lines, "expected fail-fast throw statements")
        self.assertTrue(all(index < self.compress_index for index in throw_lines))
        first_validation = min(self._leaf_validations().values())
        span = self.lines[first_validation : self.compress_index]
        self.assertFalse(
            any(re.search(r"\b(try|catch|trap)\b\s*\{", line) for line in span),
            "validation must not be wrapped in try/catch/trap before Compress-Archive",
        )


# --------------------------------------------------------------------------
# Cross-file alignment: builder layout vs portable launcher expectations
# --------------------------------------------------------------------------


class PortableLayoutAlignmentTests(unittest.TestCase):
    """Paths the portable launcher executes must be exactly the paths the builder
    writes and validates, derived from each script's own variable tables."""

    def setUp(self):
        builder_lines = powershell_code_lines(read_script(BUILDER_PATH))
        self.builder_paths = parse_powershell_join_path_table(builder_lines)
        self.commands = batch_logical_commands(read_script(PORTABLE_LAUNCHER_PATH))
        self.launcher_table = parse_batch_set_table(self.commands)
        # %APP_ROOT% is "%~dp0" (extracted bundle dir, trailing backslash) -> bundle-relative "".
        self.roots = {"APP_ROOT": "", "~DP0": ""}

    def _launcher_path(self, expression: str) -> str:
        return expand_batch_value(expression, self.launcher_table, self.roots)

    def test_launcher_python_exe_matches_builder_python_runtime(self):
        """Contract: %PYTHON_EXE% in the portable launcher resolves to the same
        bundle-relative path as the builder's $pythonRuntimeRoot\\python.exe."""
        launcher_python = self._launcher_path("%PYTHON_EXE%")
        builder_python = resolve_powershell_path("$pythonRuntimeRoot", self.builder_paths, "bundleRoot") + "\\python.exe"
        self.assertEqual(launcher_python, builder_python)
        self.assertEqual(launcher_python, "runtime\\python\\python.exe")

    def test_launcher_ffmpeg_dir_matches_builder_ffmpeg_runtime(self):
        """Contract: the launcher's default ffmpeg directory is the builder's
        $ffmpegRuntimeRoot, and the launcher checks ffmpeg.exe inside it."""
        launcher_ffmpeg_dir = self._launcher_path("%SPRITE_VIDEO_LAB_FFMPEG_DIR%")
        builder_ffmpeg_dir = resolve_powershell_path("$ffmpegRuntimeRoot", self.builder_paths, "bundleRoot")
        self.assertEqual(launcher_ffmpeg_dir, builder_ffmpeg_dir)
        checks = [command for command in self.commands if re.match(r'if not exist "%SPRITE_VIDEO_LAB_FFMPEG_DIR%\\ffmpeg\.exe"', command)]
        self.assertEqual(len(checks), 1)

    def test_launcher_python_exe_guard_matches_variable(self):
        """Contract: the portable launcher refuses to start when the bundled
        python.exe (the same path the builder validates) is missing."""
        checks = [command for command in self.commands if re.match(r'if not exist "%PYTHON_EXE%"', command)]
        self.assertEqual(len(checks), 1)


# --------------------------------------------------------------------------
# Readiness probe
# --------------------------------------------------------------------------


class ReadinessProbeContractTests(unittest.TestCase):
    """wait_for_server.ps1 must poll /api/app-version with a bounded deadline and
    open the browser only once the server has answered 200."""

    def setUp(self):
        self.script = read_script(READINESS_PATH)
        self.lines = powershell_code_lines(self.script)

    def _param_block(self) -> str:
        start = find_line(self.lines, lambda line: re.match(r"^\s*param\s*\(", line) is not None, description="param block")
        depth = 0
        for index in range(start, len(self.lines)):
            depth += self.lines[index].count("(") - self.lines[index].count(")")
            if depth == 0:
                return "\n".join(self.lines[start : index + 1])
        raise AssertionError("param block is not closed")

    def _success_branch(self) -> tuple[int, int]:
        start = find_line(
            self.lines,
            lambda line: re.search(r"if\s*\(\s*\$response\.StatusCode\s+-eq\s+200\s*\)\s*\{", line) is not None,
            description="the HTTP-200 branch",
        )
        return start, block_end(self.lines, start)

    def test_parameters_match_launcher_interface(self):
        """Contract: the launchers call -HostName, -Port, -TimeoutSeconds and -OpenBrowser;
        HostName and Port are mandatory, TimeoutSeconds defaults to 30, OpenBrowser is a switch."""
        params = self._param_block()
        self.assertRegex(params, r"\[Parameter\(Mandatory\s*=\s*\$true\)\]\s*\[string\]\s*\$HostName")
        self.assertRegex(params, r"\[Parameter\(Mandatory\s*=\s*\$true\)\]\s*\[int\]\s*\$Port")
        self.assertRegex(params, r"\[int\]\s*\$TimeoutSeconds\s*=\s*30")
        self.assertRegex(params, r"\[switch\]\s*\$OpenBrowser")

    def test_probe_targets_app_version_endpoint(self):
        """Contract: readiness is defined as an HTTP 200 from GET /api/app-version."""
        uri_index = find_line(self.lines, lambda line: re.match(r"^\s*\$uri\s*=", line) is not None, description="$uri assignment")
        self.assertIn(READINESS_ENDPOINT, self.lines[uri_index])
        self.assertRegex(self.script, r"Invoke-WebRequest\s+-Uri\s+\$uri\b.*-TimeoutSec\s+1\b")

    def test_polling_is_bounded_by_timeout_seconds_at_250ms(self):
        """Contract: the loop sleeps 250 ms per attempt and stops at a deadline derived
        from -TimeoutSeconds, so a launcher never hangs on a server that never starts."""
        self.assertRegex(self.script, r"\$deadline\s*=\s*\[DateTime\]::UtcNow\.AddSeconds\(\$TimeoutSeconds\)")
        self.assertRegex(self.script, r"\}\s*while\s*\(\s*\[DateTime\]::UtcNow\s+-lt\s+\$deadline\s*\)")
        sleeps = re.findall(r"Start-Sleep\s+-Milliseconds\s+(\d+)", self.script)
        self.assertEqual(sleeps, ["250"])
        self.assertNotRegex(self.script, r"while\s*\(\s*\$true\s*\)")

    def test_wildcard_bind_addresses_are_normalized_to_loopback(self):
        """Contract: a server bound to 0.0.0.0 or :: is probed on 127.0.0.1 / ::1, and
        IPv6 hosts are bracketed in the URL."""
        switch_index = find_line(
            self.lines, lambda line: re.search(r"\$probeHost\s*=\s*switch\s*\(\s*\$normalizedHost\s*\)\s*\{", line) is not None,
            description="the host normalization switch",
        )
        switch_body = "\n".join(self.lines[switch_index : block_end(self.lines, switch_index) + 1])
        mapping = dict(re.findall(r'"([^"]+)"\s*\{\s*"([^"]+)"\s*\}', switch_body))
        self.assertEqual(mapping, {"0.0.0.0": "127.0.0.1", "::": "::1"})
        self.assertRegex(switch_body, r"default\s*\{\s*\$normalizedHost\s*\}")
        self.assertRegex(self.script, r'\$hostForUrl\s*=\s*if\s*\(\s*\$probeHost\.Contains\(":"\)\s*\)\s*\{\s*"\[\$probeHost\]"\s*\}')

    def test_browser_opens_only_inside_http_200_branch(self):
        """Contract: Start-Process (browser launch) exists exactly once, is guarded by
        -OpenBrowser, and sits inside the StatusCode -eq 200 branch."""
        start, end = self._success_branch()
        process_lines = find_all_lines(self.lines, lambda line: "Start-Process" in line)
        self.assertEqual(len(process_lines), 1)
        self.assertTrue(start < process_lines[0] < end, "Start-Process must be inside the HTTP-200 branch")
        guard_index = find_line(
            self.lines, lambda line: re.search(r"if\s*\(\s*\$OpenBrowser\s*\)\s*\{", line) is not None,
            description="the -OpenBrowser guard", start=start,
        )
        self.assertTrue(guard_index < process_lines[0] <= block_end(self.lines, guard_index))

    def test_browser_opens_app_root_not_probe_endpoint(self):
        """Contract: the URL handed to Start-Process resolves to the app root
        (http://host:port/), the same page the launchers opened before readiness
        gating, while Invoke-WebRequest keeps probing /api/app-version."""
        process_index = find_line(self.lines, lambda line: "Start-Process" in line, description="Start-Process")
        process_arg = re.search(r"Start-Process\s+-FilePath\s+(\$\w+|\"[^\"]*\")", self.lines[process_index])
        self.assertIsNotNone(process_arg, "Start-Process must pass -FilePath")
        browser_url = resolve_powershell_string(process_arg.group(1), self.lines)
        self.assertRegex(browser_url, r"^http://\$\{hostForUrl\}:\$Port/$")
        self.assertNotIn(READINESS_ENDPOINT, browser_url)
        probe_index = find_line(self.lines, lambda line: "Invoke-WebRequest" in line, description="Invoke-WebRequest")
        probe_arg = re.search(r"Invoke-WebRequest\s+-Uri\s+(\$\w+|\"[^\"]*\")", self.lines[probe_index])
        probe_url = resolve_powershell_string(probe_arg.group(1), self.lines)
        self.assertEqual(probe_url, "http://${hostForUrl}:$Port" + READINESS_ENDPOINT)
        self.assertNotEqual(browser_url, probe_url)

    def test_exit_codes_distinguish_ready_from_timeout_and_browser_failure(self):
        """Contract: exit 0 happens only inside the HTTP-200 branch; the timeout path
        after the loop and the browser-launch failure path both exit 1."""
        start, end = self._success_branch()
        exit_zero = find_all_lines(self.lines, lambda line: re.match(r"^\s*exit\s+0\s*$", line) is not None)
        self.assertEqual(len(exit_zero), 1)
        self.assertTrue(start < exit_zero[0] < end, "exit 0 must be inside the HTTP-200 branch")
        exit_one = find_all_lines(self.lines, lambda line: re.match(r"^\s*exit\s+1\s*$", line) is not None)
        self.assertEqual(len(exit_one), 2, "expected exit 1 for browser failure and for timeout")
        self.assertTrue(any(start < index < end for index in exit_one), "browser failure must exit 1")
        non_empty = [index for index, line in enumerate(self.lines) if line.strip()]
        self.assertEqual(non_empty[-1], max(exit_one), "the script must end on the timeout exit 1")
        self.assertRegex(self.lines[non_empty[-2]], r"did not become ready")


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed; readiness probe cannot be executed here")
class ReadinessProbeExecutionTests(unittest.TestCase):
    """Executes wait_for_server.ps1 against a local HTTP server when pwsh exists."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == READINESS_ENDPOINT:
                body = b'{"version":"test"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    def _run(self, host: str, port: int, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                shutil.which("pwsh"), "-NoProfile", "-File", str(READINESS_PATH),
                "-HostName", host, "-Port", str(port), "-TimeoutSeconds", str(timeout),
            ],
            capture_output=True, text=True, timeout=timeout + 15,
        )

    def test_exits_zero_once_server_answers_200(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), self._Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self._run("0.0.0.0", server.server_address[1], 10)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exits_one_when_nothing_listens(self):
        probe = http.server.HTTPServer(("127.0.0.1", 0), self._Handler)
        port = probe.server_address[1]
        probe.server_close()
        result = self._run("127.0.0.1", port, 2)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("did not become ready", result.stdout + result.stderr)


# --------------------------------------------------------------------------
# Launchers
# --------------------------------------------------------------------------


class LauncherStartupContractTests(unittest.TestCase):
    """Both launchers must start the server, wait for readiness via the shared probe,
    and abort (never open a browser) when the probe fails."""

    LAUNCHERS = {
        "standard": STANDARD_LAUNCHER_PATH,
        "portable": PORTABLE_LAUNCHER_PATH,
    }
    # The portable launcher's pre-existing error paths pause so a double-click user can
    # read the message; the standard launcher's do not. The readiness failure mirrors each.
    PAUSES_ON_ERROR = {"standard": False, "portable": True}

    def _commands(self, path: Path) -> list[str]:
        return batch_logical_commands(read_script(path))

    def _post_start_sequence(self, commands: list[str]) -> list[str]:
        starts = [index for index, command in enumerate(commands) if re.match(rf"start\s+{re.escape(SERVER_START_TITLE)}", command)]
        self.assertEqual(len(starts), 1, "expected exactly one server console start command")
        server_start = commands[starts[0]]
        self.assertRegex(server_start, r'"%PYTHON_EXE%"\s+"%~dp0server\.py"\s+--serve\s+--host\s+"%SPRITE_VIDEO_LAB_HOST%"\s+--port\s+"%SPRITE_VIDEO_LAB_PORT%"')
        return commands[starts[0] + 1 :]

    def test_no_fixed_sleep_remains(self):
        """Contract: startup no longer relies on a fixed `timeout /t` sleep."""
        for name, path in self.LAUNCHERS.items():
            with self.subTest(launcher=name):
                for command in self._commands(path):
                    self.assertNotRegex(command.lower(), r"^timeout\s+/t\b", f"{name}: fixed sleep remains: {command!r}")

    def test_readiness_probe_follows_server_start_and_gates_exit(self):
        """Contract: immediately after the server console is started the launcher runs
        wait_for_server.ps1 with the same host/port it passed to the server, a 30 s
        timeout and -OpenBrowser, and the very next command tests errorlevel 1."""
        for name, path in self.LAUNCHERS.items():
            with self.subTest(launcher=name):
                sequence = self._post_start_sequence(self._commands(path))
                self.assertGreaterEqual(len(sequence), 2)
                probe = sequence[0]
                self.assertRegex(probe, r'^powershell\s+-NoProfile\s+-ExecutionPolicy\s+Bypass\s+-File\s+"%~dp0wait_for_server\.ps1"')
                self.assertRegex(probe, r'-HostName\s+"%SPRITE_VIDEO_LAB_HOST%"')
                self.assertRegex(probe, r'-Port\s+"%SPRITE_VIDEO_LAB_PORT%"')
                self.assertRegex(probe, r"-TimeoutSeconds\s+30\b")
                self.assertRegex(probe, r"-OpenBrowser\b")
                self.assertRegex(sequence[1].lower(), r"^if errorlevel 1\s*\(")
                failure_block = sequence[1 : sequence.index(")") + 1] if ")" in sequence else sequence[1:]
                self.assertTrue(any(re.match(r"exit\s+/b\s+1", command) for command in failure_block), f"{name}: readiness failure must exit /b 1")
                self.assertTrue(any(command.lower().startswith("echo ") and "failed to start" in command.lower() for command in failure_block))
                pause_positions = [index for index, command in enumerate(failure_block) if command.lower() == "pause"]
                exit_position = next(index for index, command in enumerate(failure_block) if re.match(r"exit\s+/b\s+1", command))
                if self.PAUSES_ON_ERROR[name]:
                    self.assertEqual(len(pause_positions), 1, f"{name}: readiness failure must pause before exiting")
                    self.assertLess(pause_positions[0], exit_position)
                else:
                    self.assertEqual(pause_positions, [], f"{name}: error paths in this launcher do not pause")

    def test_browser_opening_is_delegated_not_started_directly(self):
        """Contract: the launcher opens no browser itself; the only `start` command is
        the server console, and browser opening is delegated via -OpenBrowser."""
        for name, path in self.LAUNCHERS.items():
            with self.subTest(launcher=name):
                commands = self._commands(path)
                start_commands = [command for command in commands if re.match(r"start\s", command, re.IGNORECASE)]
                self.assertEqual(len(start_commands), 1, f"{name}: only the server console may be started: {start_commands}")
                self.assertTrue(start_commands[0].startswith(f"start {SERVER_START_TITLE}"))
                self.assertFalse(
                    any(re.search(r"http://", command) for command in commands),
                    f"{name}: no direct http:// browser launch may remain",
                )
                self.assertEqual(sum("-OpenBrowser" in command for command in commands), 1)

    def test_stale_process_cleanup_and_environment_selection_retained(self):
        """Contract: hardening does not remove the pre-start stale server cleanup or
        the launcher's interpreter/environment selection."""
        standard = self._commands(STANDARD_LAUNCHER_PATH)
        portable = self._commands(PORTABLE_LAUNCHER_PATH)
        for name, commands in (("standard", standard), ("portable", portable)):
            with self.subTest(launcher=name):
                cleanup = [index for index, command in enumerate(commands) if "Get-CimInstance Win32_Process" in command and "Stop-Process" in command]
                self.assertEqual(len(cleanup), 1)
                start_index = next(index for index, command in enumerate(commands) if command.startswith(f"start {SERVER_START_TITLE}"))
                self.assertLess(cleanup[0], start_index, "stale cleanup must precede the server start")
        self.assertTrue(any(command == ":python_ready" for command in standard))
        self.assertTrue(any("where python" in command for command in standard))
        self.assertTrue(any("Get-NetTCPConnection -LocalPort $port" in command for command in standard))
        portable_table = parse_batch_set_table(portable)
        self.assertEqual(portable_table["PATH"], "%PYTHON_ROOT%;%PYTHON_ROOT%\\Scripts;%FFMPEG_ROOT%;%PATH%")
        self.assertEqual(portable_table["SPRITE_VIDEO_LAB_HOST"], "127.0.0.1")
        self.assertEqual(portable_table["SPRITE_VIDEO_LAB_PORT"], "8894")


if __name__ == "__main__":
    unittest.main()
