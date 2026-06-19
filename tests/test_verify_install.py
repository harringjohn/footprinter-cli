"""Tests for scripts/release/verify_install.sh — bundled-config fail and pass paths."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "release" / "verify_install.sh"

INSTALL_COMMON_STUB = """\
#!/usr/bin/env bash
ensure_python_3_11() {{ PYTHON_BIN="{python3_stub}"; }}
"""

PYTHON3_STUB = """\
#!/usr/bin/env bash
if [[ "$1" == "-m" ]]; then
    case "$2" in
        venv)
            target="$3"
            mkdir -p "$target/bin"
            cp "$0" "$target/bin/python3"
            chmod +x "$target/bin/python3"
            for ep in fp fp-mcp fp-api; do
                printf '#!/usr/bin/env bash\\necho "footprinter 9.9.9"\\n' > "$target/bin/$ep"
                chmod +x "$target/bin/$ep"
            done
            exit 0
            ;;
        pip)
            exit 0
            ;;
        pytest)
            exit 0
            ;;
    esac
elif [[ "$1" == "-c" ]]; then
    code="$2"
    if [[ "$code" == *"import footprinter.fixture"* ]]; then
        exit 1
    fi
    if [[ "$code" == *"importlib.resources"* ]]; then
        if [[ -n "${STUB_CONFIG_MISSING:-}" ]]; then
            echo "/nonexistent/config.example.yaml"
        else
            echo "${STUB_CONFIG_PATH}"
        fi
        exit 0
    fi
    exit 0
fi
exit 0
"""


@pytest.fixture()
def verify_harness(tmp_path):
    """Minimal directory tree with stubs for verify_install.sh."""
    scripts_dir = tmp_path / "scripts" / "release"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(VERIFY_SCRIPT, scripts_dir / "verify_install.sh")

    # Neutralise the Rosetta re-exec guard so this harness's tests do not depend
    # on host arch behaviour. The exec lives inside the loop-guarded `if` block
    # (8-space indent); assert the substitution lands so a future re-indentation
    # cannot silently turn this back into a no-op.
    script = scripts_dir / "verify_install.sh"
    original = script.read_text()
    neutralised = original.replace(
        '        exec arch -arm64 "$0" "$@"\n',
        "        true\n",
    )
    assert neutralised != original, (
        "verify_install.sh re-exec guard not neutralised — the exec line "
        "no longer matches the expected text; update this fixture."
    )
    script.write_text(neutralised)

    stubs_dir = tmp_path / "stubs"
    stubs_dir.mkdir()
    python3 = stubs_dir / "python3"
    python3.write_text(PYTHON3_STUB)
    python3.chmod(python3.stat().st_mode | stat.S_IEXEC)

    (scripts_dir / "_install_common.sh").write_text(
        INSTALL_COMMON_STUB.format(python3_stub=python3)
    )

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "footprinter_cli-9.9.9-py3-none-any.whl").write_text("")

    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("")

    # Bundled config file for happy-path tests (STUB_CONFIG_PATH points here)
    installed_dir = tmp_path / "installed_config"
    installed_dir.mkdir()
    (installed_dir / "config.example.yaml").write_text("# bundled config stub")

    return tmp_path


def _run_verify(
    harness_dir: Path,
    version: str = "9.9.9",
    with_pytest: bool = False,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = ["bash", str(harness_dir / "scripts" / "release" / "verify_install.sh"), version]
    if with_pytest:
        cmd.append("--with-pytest")
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=harness_dir, env=env)


class TestVerifyInstall:
    def test_missing_bundled_config_fails_loudly(self, verify_harness):
        """Missing bundled config path logs FAIL and exits non-zero after all phases."""
        result = _run_verify(
            verify_harness, with_pytest=True, env_overrides={"STUB_CONFIG_MISSING": "1"}
        )
        assert result.returncode != 0
        assert "FAIL: could not locate bundled config.example.yaml" in result.stderr

    def test_bundled_config_present_passes(self, verify_harness):
        """Script exits zero when bundled config is found and copied into the workspace."""
        config_path = verify_harness / "installed_config" / "config.example.yaml"
        result = _run_verify(
            verify_harness,
            with_pytest=True,
            env_overrides={"STUB_CONFIG_PATH": str(config_path)},
        )
        assert result.returncode == 0
        assert "FAIL" not in result.stderr
        assert "OK: bundled config.example.yaml in place for conftest" in result.stdout


# ── Rosetta re-exec loop-guard regression ─────────────────────────────────
#
# Under a single-arch x86_64 bash (Intel Homebrew under Rosetta), the
# top-of-file guard re-execs `arch -arm64 "$0" "$@"`, which resolves to the
# same x86_64-only bash, so `arch` never reports arm64 and the guard re-execs
# forever. The fix sentinel-gates the re-exec so it fires at most once.

ARCH_STUB = """\
#!/usr/bin/env bash
# Stub `arch`: always reports a non-arm64 arch. When asked to run a command
# `arch -arm64 <cmd...>`, it re-execs that command (mimicking the real macOS
# re-exec to native arm64) WITHOUT changing the reported arch — exactly the
# situation a single-arch x86_64 bash creates. A safety cap keeps the test
# itself from hanging if the guard is unpatched (the loop is buggy).
COUNTER_FILE="${ARCH_REEXEC_COUNTER:?ARCH_REEXEC_COUNTER must be set}"
SAFETY_CAP="${ARCH_REEXEC_CAP:-8}"

if [[ "$1" == "-arm64" ]]; then
    shift
    count="$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)"
    count=$((count + 1))
    echo "$count" > "$COUNTER_FILE"
    if [[ "$count" -ge "$SAFETY_CAP" ]]; then
        echo "arch-stub: safety cap reached ($count); aborting re-exec loop" >&2
        exit 1
    fi
    exec "$@"
fi
# Plain `arch` query: report a non-arm64 value, as Rosetta does.
echo "i386"
"""

UNAME_STUB = """\
#!/usr/bin/env bash
echo "Darwin"
"""

SYSCTL_STUB = """\
#!/usr/bin/env bash
# `sysctl -n hw.optional.arm64` → 1 (Apple Silicon present)
echo "1"
"""


@pytest.fixture()
def loopguard_harness(tmp_path):
    """Harness that KEEPS the Rosetta re-exec guard intact, with PATH stubs that
    drive the guard's preconditions true on any host (Darwin + arm64 hw + a
    non-arm64 `arch`), so the real guard can be exercised."""
    scripts_dir = tmp_path / "scripts" / "release"
    scripts_dir.mkdir(parents=True)
    # Copy the script UNMODIFIED — the guard stays in place (unlike verify_harness).
    shutil.copy2(VERIFY_SCRIPT, scripts_dir / "verify_install.sh")

    stubs_dir = tmp_path / "stubs"
    stubs_dir.mkdir()
    for name, body in (
        ("arch", ARCH_STUB),
        ("uname", UNAME_STUB),
        ("sysctl", SYSCTL_STUB),
        ("python3", PYTHON3_STUB),
    ):
        stub = stubs_dir / name
        stub.write_text(body)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    (scripts_dir / "_install_common.sh").write_text(
        INSTALL_COMMON_STUB.format(python3_stub=stubs_dir / "python3")
    )

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "footprinter_cli-9.9.9-py3-none-any.whl").write_text("")

    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("")

    installed_dir = tmp_path / "installed_config"
    installed_dir.mkdir()
    (installed_dir / "config.example.yaml").write_text("# bundled config stub")

    return tmp_path


class TestArchReexecLoopGuard:
    def test_arch_reexec_guards_against_infinite_loop(self, loopguard_harness):
        """The guard must re-exec at most once. With a stub `arch` that always
        reports non-arm64 (as a single-arch x86_64 bash does under Rosetta), an
        unguarded re-exec loops forever; the sentinel-gated guard fires once,
        then proceeds under the current interpreter to completion."""
        counter_file = loopguard_harness / "arch_reexec_count"
        config_path = loopguard_harness / "installed_config" / "config.example.yaml"

        stubs_dir = loopguard_harness / "stubs"
        env = os.environ.copy()
        env["PATH"] = f"{stubs_dir}{os.pathsep}{env['PATH']}"
        env["ARCH_REEXEC_COUNTER"] = str(counter_file)
        env["STUB_CONFIG_PATH"] = str(config_path)

        script = loopguard_harness / "scripts" / "release" / "verify_install.sh"
        result = subprocess.run(
            ["bash", str(script), "9.9.9", "--with-pytest"],
            capture_output=True,
            text=True,
            cwd=loopguard_harness,
            env=env,
            timeout=30,
        )

        reexec_count = int(counter_file.read_text().strip()) if counter_file.exists() else 0
        # Loop-guard holds: the re-exec fires at most once.
        assert reexec_count <= 1, (
            f"re-exec fired {reexec_count} times (expected <= 1) — loop guard failed\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # And the script runs through to completion rather than spinning.
        assert result.returncode == 0, (
            f"script did not exit 0 (got {result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_second_pass_warns_about_x86_only_bash(self, loopguard_harness):
        """When the guard cannot obtain a native arm64 bash, it warns to stderr
        naming the cause (x86_64-only bash) before continuing."""
        counter_file = loopguard_harness / "arch_reexec_count"
        config_path = loopguard_harness / "installed_config" / "config.example.yaml"

        stubs_dir = loopguard_harness / "stubs"
        env = os.environ.copy()
        env["PATH"] = f"{stubs_dir}{os.pathsep}{env['PATH']}"
        env["ARCH_REEXEC_COUNTER"] = str(counter_file)
        env["STUB_CONFIG_PATH"] = str(config_path)

        script = loopguard_harness / "scripts" / "release" / "verify_install.sh"
        result = subprocess.run(
            ["bash", str(script), "9.9.9", "--with-pytest"],
            capture_output=True,
            text=True,
            cwd=loopguard_harness,
            env=env,
            timeout=30,
        )

        assert "x86_64-only" in result.stderr, (
            f"expected a second-pass warning naming x86_64-only bash\n"
            f"stderr:\n{result.stderr}"
        )
