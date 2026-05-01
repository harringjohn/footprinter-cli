"""Tests for footprinter.services.roles — Role enum."""

from footprinter.services.roles import Role


def test_role_has_admin_and_viewer():
    assert Role.ADMIN.value == "admin"
    assert Role.VIEWER.value == "viewer"


def test_admin_can_write():
    assert Role.ADMIN.can_write is True


def test_viewer_cannot_write():
    assert Role.VIEWER.can_write is False


def test_admin_sees_all():
    assert Role.ADMIN.sees_all is True


def test_viewer_does_not_see_all():
    assert Role.VIEWER.sees_all is False


def test_role_from_string():
    assert Role("admin") is Role.ADMIN
    assert Role("viewer") is Role.VIEWER


def test_role_members_exhaustive():
    assert len(list(Role)) == 2
