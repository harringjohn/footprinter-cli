"""Tests for access control documentation content.

Verifies that reference/permission-policies-and-access-control.md contains required sections
explaining the security posture, fail-open baseline, and how to
switch to deny-by-default.
"""

from pathlib import Path

import pytest

REFERENCE_DIR = Path(__file__).parent.parent / "reference"
ACCESS_CONTROL_DOC = REFERENCE_DIR / "permission-policies-and-access-control.md"


@pytest.fixture
def security_posture_section():
    """Extract the Security Posture section from the access control doc."""
    content = ACCESS_CONTROL_DOC.read_text()
    lines = content.split("\n")
    in_section = False
    section_lines = []
    for line in lines:
        if line.strip().startswith("#") and "security posture" in line.lower():
            in_section = True
            section_lines.append(line)
            continue
        if in_section:
            # Stop at next heading of same or higher level
            if line.strip().startswith("#") and not line.strip().startswith("####"):
                break
            section_lines.append(line)
    return "\n".join(section_lines)


class TestSecurityPostureSection:
    """reference/permission-policies-and-access-control.md should document the security posture."""

    def test_has_security_posture_section(self):
        """A Security Posture heading should exist."""
        content = ACCESS_CONTROL_DOC.read_text()
        headings = [line.strip() for line in content.split("\n") if line.strip().startswith("#")]
        posture_headings = [h for h in headings if "security posture" in h.lower()]
        assert posture_headings, (
            "Expected a '## Security Posture' or '### Security Posture' heading in "
            "reference/permission-policies-and-access-control.md"
        )

    def test_explains_fail_open_design(self, security_posture_section):
        """Section should explain the fail-open design."""
        assert security_posture_section, "Security Posture section not found"
        text = security_posture_section.lower()
        assert "fail-open" in text, "Security Posture section should contain 'fail-open' language"

    def test_distinguishes_seeded_from_baseline(self, security_posture_section):
        """Section should distinguish seeded policies from hardcoded baselines."""
        assert security_posture_section, "Security Posture section not found"
        text = security_posture_section.lower()
        assert "seed" in text, "Section should mention seeded policies"
        assert "baseline" in text, "Section should mention hardcoded baselines"

    def test_shows_deny_by_default_switch(self, security_posture_section):
        """Section should show how to switch to deny-by-default."""
        assert security_posture_section, "Security Posture section not found"
        assert "fp permission set global --access deny" in security_posture_section, (
            "Section should contain 'fp permission set global --access deny' command"
        )


class TestVisitScopeDocumentation:
    """reference/permission-policies-and-access-control.md should document the visit-scope asymmetry."""

    @pytest.fixture
    def doc_content(self):
        return ACCESS_CONTROL_DOC.read_text()

    def test_visits_checkable_but_not_settable(self, doc_content):
        """Doc should state that visits can be checked but not individually set."""
        lines = doc_content.lower().split("\n")
        has_asymmetry = any(
            "visit" in line
            and ("check" in line or "checkable" in line)
            and ("not" in line or "no " in line or "cannot" in line)
            for line in lines
        )
        assert has_asymmetry, (
            "Doc should have a line stating visits are checkable but not "
            "individually settable (e.g., 'visits can be checked but not set')"
        )

    def test_visit_inheritance_path(self, doc_content):
        """Doc should name the visit inheritance path: source:browser -> global -> baseline."""
        assert "source:browser" in doc_content, (
            "Doc should reference source:browser in the visit inheritance path"
        )
        lines = doc_content.split("\n")
        has_chain = any(
            "source:browser" in line and "global" in line and "baseline" in line
            for line in lines
        )
        assert has_chain, (
            "Doc should name the full visit inheritance chain "
            "(source:browser → global → baseline) on a single line or passage"
        )
