"""Tests for removed `fp ingest --preview` flag.

The --preview flag was removed. Ingest is incremental and safe
to run directly; preview added ceremony with no value for a single-user tool.
"""

from conftest import run_fp


class TestPreviewFlagRemoved:
    def test_preview_flag_rejected(self):
        """--preview should be rejected as an unrecognized argument."""
        _stdout, _stderr, code = run_fp("ingest", "--preview")
        assert code != 0, "fp ingest --preview should fail (removed)"

    def test_help_omits_preview(self):
        stdout, stderr, code = run_fp("ingest", "--help")
        assert code == 0
        assert "--preview" not in stdout + stderr
