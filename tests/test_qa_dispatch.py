"""Tests for scripts/qa.sh ``all`` target — continue-on-failure and summary."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
QA_SCRIPT = REPO_ROOT / "scripts" / "qa.sh"

PYTHON3_STUB = """\
#!/usr/bin/env bash
# Stub that simulates pytest pass/fail via env var.
if [[ " $* " == *" -m pytest "* ]]; then
    if [[ -n "${TIER1_FAIL:-}" ]]; then echo "STUBBED pytest FAIL"; exit 1; fi
    echo "STUBBED pytest PASS"; exit 0
fi
"""

SMOKE_STUB = """\
#!/usr/bin/env bash
# Stub that simulates smoke pass/fail via env var.
if [[ -n "${TIER2_FAIL:-}" ]]; then echo "STUBBED smoke FAIL"; exit 1; fi
echo "STUBBED smoke PASS"; exit 0
"""


@pytest.fixture()
def qa_harness(tmp_path):
    """Minimal directory tree with stubbed tier commands for qa.sh."""
    scripts_dir = tmp_path / "scripts" / "snapshot-qa"
    scripts_dir.mkdir(parents=True)
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)

    shutil.copy2(QA_SCRIPT, tmp_path / "scripts" / "qa.sh")

    python3 = venv_bin / "python3"
    python3.write_text(PYTHON3_STUB)
    python3.chmod(python3.stat().st_mode | stat.S_IEXEC)

    smoke = scripts_dir / "smoke.sh"
    smoke.write_text(SMOKE_STUB)
    smoke.chmod(smoke.stat().st_mode | stat.S_IEXEC)

    return tmp_path


def _run_qa_all(
    harness_dir: Path, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(harness_dir / "scripts" / "qa.sh"), "all"],
        capture_output=True,
        text=True,
        env=env,
        cwd=harness_dir,
    )


class TestQaAll:
    def test_all_continues_after_tier_failure(self, qa_harness):
        """Smoke (Tier 2) runs even when pytest (Tier 1) fails."""
        result = _run_qa_all(qa_harness, {"TIER1_FAIL": "1"})
        assert "=== Tier 1: pytest ===" in result.stdout
        assert "=== Tier 2: smoke ===" in result.stdout

    def test_all_prints_summary(self, qa_harness):
        """A per-tier PASS/FAIL summary is printed."""
        result = _run_qa_all(qa_harness, {"TIER1_FAIL": "1"})
        summary = result.stdout.split("QA Summary")[1]
        assert "pytest:" in summary
        assert "FAIL" in summary
        assert "smoke:" in summary
        assert "PASS" in summary

    def test_all_exits_nonzero_when_tier1_fails(self, qa_harness):
        """Exit code is non-zero when pytest (Tier 1) fails."""
        result = _run_qa_all(qa_harness, {"TIER1_FAIL": "1"})
        assert result.returncode != 0

    def test_all_exits_nonzero_when_tier2_fails(self, qa_harness):
        """Exit code is non-zero when smoke (Tier 2) fails."""
        result = _run_qa_all(qa_harness, {"TIER2_FAIL": "1"})
        assert result.returncode != 0
        assert "=== Tier 1: pytest ===" in result.stdout
        assert "=== Tier 2: smoke ===" in result.stdout

    def test_all_exits_zero_on_success(self, qa_harness):
        """Exit code is 0 and summary shows all tiers passed."""
        result = _run_qa_all(qa_harness)
        assert result.returncode == 0
        summary = result.stdout.split("QA Summary")[1]
        assert "pytest:" in summary and "PASS" in summary
        assert "smoke:" in summary

    def test_all_picks_up_new_arg_free_tier(self, qa_harness):
        """A tier added to ARG_FREE_TIERS is automatically run by `all`."""
        lint_stub = qa_harness / "scripts" / "lint.sh"
        lint_stub.write_text("#!/usr/bin/env bash\necho 'STUBBED lint PASS'; exit 0\n")
        lint_stub.chmod(lint_stub.stat().st_mode | stat.S_IEXEC)

        qa_script = qa_harness / "scripts" / "qa.sh"
        text = qa_script.read_text()
        text = text.replace(
            "ARG_FREE_TIERS=(pytest smoke)",
            "ARG_FREE_TIERS=(pytest smoke lint)",
        )
        text = text.replace(
            "    *)\n",
            "    lint)\n        exec bash \"$SCRIPT_DIR/lint.sh\"\n        ;;\n    *)\n",
            1,
        )
        qa_script.write_text(text)

        result = _run_qa_all(qa_harness)
        assert result.returncode == 0
        assert "=== Tier 3: lint ===" in result.stdout
        summary = result.stdout.split("QA Summary")[1]
        assert "lint:" in summary and "PASS" in summary

    def test_list_shows_all_tiers(self, qa_harness):
        """--list output mentions every tier known to the dispatcher."""
        result = subprocess.run(
            ["bash", str(qa_harness / "scripts" / "qa.sh"), "--list"],
            capture_output=True,
            text=True,
            cwd=qa_harness,
        )
        assert result.returncode == 0
        for tier in ("pytest", "smoke", "cli-verify", "verify-upgrade", "verify-install"):
            assert tier in result.stdout, f"--list missing tier: {tier}"
