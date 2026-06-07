"""Tests for scripts/release/verify_install.sh — missing-config fail path."""

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
        echo ""
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

    return tmp_path


def _run_verify(
    harness_dir: Path,
    version: str = "9.9.9",
    with_pytest: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["bash", str(harness_dir / "scripts" / "release" / "verify_install.sh"), version]
    if with_pytest:
        cmd.append("--with-pytest")
    return subprocess.run(cmd, capture_output=True, text=True, cwd=harness_dir)


class TestVerifyInstall:
    def test_missing_bundled_config_fails_loudly(self, verify_harness):
        """When bundled config can't be located, the script emits FAIL and exits non-zero."""
        result = _run_verify(verify_harness, with_pytest=True)
        assert result.returncode != 0
        assert "FAIL: could not locate bundled config.example.yaml" in result.stderr
