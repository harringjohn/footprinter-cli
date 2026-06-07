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

    # Neutralise the Rosetta re-exec guard — it hangs when /usr/local/bin/bash
    # is x86-only (Homebrew) because arch -arm64 can't re-exec an x86 binary.
    script = scripts_dir / "verify_install.sh"
    script.write_text(
        script.read_text().replace(
            '    exec arch -arm64 "$0" "$@"\n',
            "    true\n",
        )
    )

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
