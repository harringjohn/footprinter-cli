"""Guard tests: the package is retired, so no surface may advertise installing it.

Every published version of this package has been yanked from the index, so a
bare ``pip install`` of it no longer resolves.  Two invariants follow, and both
are easy to break by accident:

1. The architecture docs under ``reference/`` describe the package as it
   shipped.  They must not carry a live install command, because following one
   now fails.

2. The release and QA scripts under ``scripts/`` are frozen historical record.
   Their install commands are deliberately left intact -- but each such script
   must carry a banner saying it is retired, so a reader does not act on them.

The second invariant is the one that already slipped: the original retirement
pass found its files by grepping for a literal install command, which cannot
see a script that calls the shared installer helper instead of writing the
command inline.  These tests detect both spellings.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REFERENCE_DIR = PROJECT_ROOT / "reference"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# A live install instruction: `pip install foo`, `pipx install "foo[extra]"`,
# and the prose form `pip installs foo` that appears in header comments.
INSTALL_COMMAND = re.compile(r"(?:pip|pipx)\s+install\w*\b[^\n]*footprinter-cli")

# The indirect spelling: a script that defers to the shared installer helper
# never names a pip command itself, so the regex above cannot see it.
INSTALL_HELPER = re.compile(r"pip_install_footprinter")

# Any one of these phrases marks a script as retired.  Kept loose on purpose --
# the banners are prose, and pinning exact wording would make them brittle.
RETIREMENT_BANNER = re.compile(
    r"yanked|no longer published|historical reference", re.IGNORECASE
)

# Installs from a local wheel rather than the index, so the yank does not
# affect it and it needs no banner.
BANNER_EXEMPT = {"verify_install.sh"}


def _scripts_that_install_the_package() -> list[Path]:
    """Shell scripts that install this package by name, directly or via the helper."""
    matches = []
    for path in sorted(SCRIPTS_DIR.rglob("*.sh")):
        if path.name in BANNER_EXEMPT:
            continue
        text = path.read_text()
        if INSTALL_COMMAND.search(text) or INSTALL_HELPER.search(text):
            matches.append(path)
    return matches


class TestNoLiveInstallInstructionsInDocs:
    """The reference docs must not tell a reader to install the retired package."""

    def test_reference_docs_have_no_install_commands(self):
        violations = []
        for path in sorted(REFERENCE_DIR.rglob("*.md")):
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if INSTALL_COMMAND.search(line):
                    rel = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{rel}:{i}: {line.strip()}")

        assert violations == [], (
            f"Found {len(violations)} live install instruction(s) in reference/ "
            f"for a package that no longer resolves. Describe the install in the "
            f"past tense, or drop the command:\n" + "\n".join(violations)
        )


class TestRetiredScriptsCarryBanner:
    """Frozen scripts keep their commands, but must warn that they are retired."""

    def test_every_installing_script_is_bannered(self):
        unbannered = [
            str(path.relative_to(PROJECT_ROOT))
            for path in _scripts_that_install_the_package()
            if not RETIREMENT_BANNER.search(path.read_text())
        ]

        assert unbannered == [], (
            f"{len(unbannered)} script(s) install this package by name but carry "
            f"no retirement banner. Their commands may stay as historical record, "
            f"but a reader must be told not to run them:\n" + "\n".join(unbannered)
        )

    def test_detection_covers_the_indirect_spelling(self):
        """The helper-call spelling must be detected, not just literal pip commands.

        This is the gap that let two curl-piped installers ship unbannered.  If
        the detection above regresses to matching only literal pip commands,
        this fails.
        """
        detected = {p.name for p in _scripts_that_install_the_package()}

        assert "install.sh" in detected and "install-full.sh" in detected, (
            "The curl-piped installers were not detected as installing the "
            "package. They call the shared helper rather than naming pip "
            f"directly -- detection must cover that. Detected: {sorted(detected)}"
        )
