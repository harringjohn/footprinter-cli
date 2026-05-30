"""Tests for access control documentation content.

Verifies that reference/mcp-access-control.md contains required sections
explaining the security posture, fail-open baseline, and how to
switch to deny-by-default.
"""

from pathlib import Path

import pytest

REFERENCE_DIR = Path(__file__).parent.parent / "reference"
ACCESS_CONTROL_DOC = REFERENCE_DIR / "mcp-access-control.md"


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
    """reference/mcp-access-control.md should document the security posture."""

    def test_has_security_posture_section(self):
        """A Security Posture heading should exist."""
        content = ACCESS_CONTROL_DOC.read_text()
        headings = [line.strip() for line in content.split("\n") if line.strip().startswith("#")]
        posture_headings = [h for h in headings if "security posture" in h.lower()]
        assert posture_headings, (
            "Expected a '## Security Posture' or '### Security Posture' heading in reference/mcp-access-control.md"
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
