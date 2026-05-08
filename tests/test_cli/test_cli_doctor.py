"""Tests for ``fp doctor`` — post-install health check command."""

import json
import platform

import pytest
from conftest import run_fp


# ---------------------------------------------------------------------------
# 1. Subcommand registration
# ---------------------------------------------------------------------------


class TestDoctorRegistration:
    def test_doctor_help(self):
        stdout, stderr, code = run_fp("doctor", "--help")
        output = stdout + stderr
        assert code == 0
        assert "doctor" in output.lower()

    def test_doctor_bare_invocation_runs_checks(self):
        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "python" in output.lower()


# ---------------------------------------------------------------------------
# 2. Healthy install — all checks pass
# ---------------------------------------------------------------------------


class TestDoctorHealthyInstall:
    def test_healthy_install_exits_zero(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        db = home / "footprinter.db"
        _create_minimal_db(db)
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert code == 0
        assert "FAIL" not in output

    def test_healthy_install_shows_ok_for_each_check(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        db = home / "footprinter.db"
        _create_minimal_db(db)
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        ok_count = output.count("OK")
        assert ok_count >= 3


# ---------------------------------------------------------------------------
# 3. Individual check failures
# ---------------------------------------------------------------------------


class TestDoctorPythonVersion:
    def test_python_version_fail(self, tmp_path, monkeypatch):
        from footprinter.cli import doctor

        monkeypatch.setattr(doctor, "_get_python_version", lambda: (3, 10, 0))

        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "FAIL" in output
        assert "3.11" in output


class TestDoctorConfig:
    def test_missing_config_warns(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))
        monkeypatch.delenv("FOOTPRINTER_CONFIG", raising=False)

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "WARN" in output
        assert "setup" in output.lower()

    def test_unparseable_config_fails(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text(": : : invalid yaml [[[")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))
        monkeypatch.delenv("FOOTPRINTER_CONFIG", raising=False)

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "FAIL" in output


class TestDoctorDatabase:
    def test_missing_database_warns(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "WARN" in output
        assert "ingest" in output.lower()


class TestDoctorFDA:
    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_fda_not_readable_warns(self, tmp_path, monkeypatch):
        from footprinter.cli import doctor

        monkeypatch.setattr(doctor, "_probe_fda", lambda: False)

        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "WARN" in output
        assert "Full Disk Access" in output


class TestDoctorSemanticDeps:
    def test_semantic_deps_missing_warns(self, tmp_path, monkeypatch):
        from footprinter.cli import doctor

        monkeypatch.setattr(doctor, "_find_spec", lambda name: None)

        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "WARN" in output
        assert "semantic" in output.lower() or "pipx install" in output.lower()

    def test_semantic_deps_checks_onnxruntime_not_sentence_transformers(self, monkeypatch):
        from footprinter.cli import doctor

        recorded = []

        def record(name):
            recorded.append(name)
            return None

        monkeypatch.setattr(doctor, "_find_spec", record)
        doctor._check_semantic_deps()

        assert "onnxruntime" in recorded
        assert "sentence_transformers" not in recorded

    def test_semantic_deps_ok_message_mentions_onnxruntime(self, monkeypatch):
        from footprinter.cli import doctor

        monkeypatch.setattr(doctor, "_find_spec", lambda name: object())
        result = doctor._check_semantic_deps()

        assert result.status == "OK"
        assert "onnxruntime" in result.message
        assert "sentence_transformers" not in result.message

    def test_find_spec_valueerror_returns_none(self, monkeypatch):
        """find_spec raises ValueError when a package's __spec__ is None."""
        from footprinter.cli.doctor import _find_spec

        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: (_ for _ in ()).throw(ValueError("__spec__ is None")),
        )
        assert _find_spec("chromadb") is None

    def test_find_spec_module_not_found_returns_none(self, monkeypatch):
        """find_spec raises ModuleNotFoundError for missing submodule parents."""
        from footprinter.cli.doctor import _find_spec

        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)),
        )
        assert _find_spec("nonexistent.sub") is None


class TestDoctorParseDeps:
    def test_parse_deps_checks_pypdf_not_pdfplumber(self, monkeypatch):
        from footprinter.cli import doctor

        recorded = []

        def record(name):
            recorded.append(name)
            return None

        monkeypatch.setattr(doctor, "_find_spec", record)
        doctor._check_parse_deps()

        assert "pypdf" in recorded
        assert "pdfplumber" not in recorded


class TestDoctorWarnMessageRendering:
    """Rich must not swallow the [full] markup in install hints."""

    def _force_missing(self, monkeypatch, *names):
        from footprinter.cli import doctor

        targets = set(names)
        real = doctor._find_spec

        def fake(name):
            if name in targets:
                return None
            return real(name)

        monkeypatch.setattr(doctor, "_find_spec", fake)

    def _setup_home(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

    def test_full_extra_renders_in_semantic_warn_hint(self, tmp_path, monkeypatch):
        self._force_missing(
            monkeypatch, "chromadb", "onnxruntime", "sentence_transformers"
        )
        self._setup_home(tmp_path, monkeypatch)

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "footprinter-cli[full]" in output

    def test_full_extra_renders_in_parse_warn_hint(self, tmp_path, monkeypatch):
        self._force_missing(monkeypatch, "docx", "pypdf", "pdfplumber")
        self._setup_home(tmp_path, monkeypatch)

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        assert "footprinter-cli[full]" in output

    def test_json_warn_message_contains_unescaped_full_extra(
        self, tmp_path, monkeypatch
    ):
        self._force_missing(
            monkeypatch, "chromadb", "onnxruntime", "sentence_transformers"
        )
        self._setup_home(tmp_path, monkeypatch)

        stdout, stderr, code = run_fp("doctor", "--json")
        data = json.loads(stdout)

        semantic_check = next(c for c in data if c["name"] == "semantic_deps")
        assert semantic_check["status"] == "WARN"
        assert "footprinter-cli[full]" in semantic_check["message"]
        assert "\\[full]" not in semantic_check["message"]


# ---------------------------------------------------------------------------
# 4. Exit codes
# ---------------------------------------------------------------------------


class TestDoctorExitCodes:
    def test_exit_nonzero_on_fail(self, tmp_path, monkeypatch):
        from footprinter.cli import doctor

        monkeypatch.setattr(doctor, "_get_python_version", lambda: (3, 10, 0))

        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        assert code == 1

    def test_exit_zero_when_only_warns(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        assert code == 0


# ---------------------------------------------------------------------------
# 5. JSON output
# ---------------------------------------------------------------------------


class TestDoctorJsonOutput:
    def test_json_flag_produces_valid_json(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        db = home / "footprinter.db"
        _create_minimal_db(db)
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor", "--json")
        data = json.loads(stdout)
        assert isinstance(data, list)
        assert len(data) >= 3
        for check in data:
            assert "name" in check
            assert "status" in check
            assert check["status"] in ("OK", "WARN", "FAIL")


# ---------------------------------------------------------------------------
# 6. Output format consistency
# ---------------------------------------------------------------------------


class TestDoctorOutputFormat:
    def test_consistent_format(self, tmp_path, monkeypatch):
        home = tmp_path / ".footprinter"
        home.mkdir()
        (home / "config.yaml").write_text("directories:\n  - ~/Work\n")
        db = home / "footprinter.db"
        _create_minimal_db(db)
        monkeypatch.setenv("FOOTPRINTER_HOME", str(home))

        stdout, stderr, code = run_fp("doctor")
        output = stdout + stderr
        lines_with_status = [
            line for line in output.splitlines()
            if any(s in line for s in ("OK", "WARN", "FAIL"))
        ]
        assert len(lines_with_status) >= 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_minimal_db(path):
    """Create a minimal SQLite DB that passes the doctor check."""
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY)")
    conn.close()
