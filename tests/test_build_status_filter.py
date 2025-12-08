"""Unit tests for build_status_filter() helper in sql_utils.

Tests cover all branching paths: None→default, "all"→bypass,
str→exact match, list→IN clause, plus edge cases.
"""

from footprinter.db.sql_utils import build_status_filter


class TestDefaultExclude:
    """status=None with default_exclude produces NOT IN clause."""

    def test_single_value(self):
        conds, params = build_status_filter(
            None,
            column="file.status",
            default_exclude=["removed"],
        )
        assert len(conds) == 1
        assert "NOT IN" in conds[0]
        assert "file.status" in conds[0]
        assert params == ["removed"]

    def test_multi_value(self):
        conds, params = build_status_filter(
            None,
            column="chat.status",
            default_exclude=["merged", "removed"],
        )
        assert len(conds) == 1
        assert "NOT IN" in conds[0]
        assert "?,?" in conds[0]
        assert params == ["merged", "removed"]


class TestDefaultInclude:
    """status=None with default_include produces IN clause."""

    def test_single_value(self):
        conds, params = build_status_filter(
            None,
            column="client.status",
            default_include=["active"],
        )
        assert len(conds) == 1
        assert "IN" in conds[0]
        assert "NOT" not in conds[0]
        assert "client.status" in conds[0]
        assert params == ["active"]


class TestNoDefaults:
    """status=None with no defaults produces no filter."""

    def test_empty_result(self):
        conds, params = build_status_filter(
            None,
            column="project.status",
        )
        assert conds == []
        assert params == []


class TestAllBypass:
    """status="all" bypasses all filtering regardless of defaults."""

    def test_with_default_exclude(self):
        conds, params = build_status_filter(
            "all",
            column="file.status",
            default_exclude=["removed"],
        )
        assert conds == []
        assert params == []

    def test_with_default_include(self):
        conds, params = build_status_filter(
            "all",
            column="client.status",
            default_include=["active"],
        )
        assert conds == []
        assert params == []

    def test_with_no_defaults(self):
        conds, params = build_status_filter(
            "all",
            column="project.status",
        )
        assert conds == []
        assert params == []


class TestExactMatch:
    """Single string status produces = ? clause."""

    def test_single_string(self):
        conds, params = build_status_filter(
            "hidden",
            column="file.status",
        )
        assert len(conds) == 1
        assert "file.status = ?" in conds[0]
        assert params == ["hidden"]


class TestListInClause:
    """List of strings produces IN clause."""

    def test_multiple_values(self):
        conds, params = build_status_filter(
            ["active", "hidden"],
            column="file.status",
        )
        assert len(conds) == 1
        assert "IN" in conds[0]
        assert "?,?" in conds[0]
        assert params == ["active", "hidden"]

    def test_empty_list_no_filter(self):
        conds, params = build_status_filter(
            [],
            column="file.status",
        )
        assert conds == []
        assert params == []


class TestBothDefaultsProvided:
    """default_exclude takes precedence when both are provided."""

    def test_exclude_wins(self):
        conds, params = build_status_filter(
            None,
            column="x.status",
            default_exclude=["removed"],
            default_include=["active"],
        )
        assert len(conds) == 1
        assert "NOT IN" in conds[0]
        assert params == ["removed"]

    def test_include_ignored(self):
        """default_include is silently ignored when default_exclude is set."""
        conds, params = build_status_filter(
            None,
            column="x.status",
            default_exclude=["a", "b"],
            default_include=["c"],
        )
        assert params == ["a", "b"]
        assert "c" not in params


class TestColumnName:
    """Column parameter appears in all generated conditions."""

    def test_column_in_default_exclude(self):
        conds, _ = build_status_filter(
            None,
            column="x.status",
            default_exclude=["removed"],
        )
        assert "x.status" in conds[0]

    def test_column_in_exact_match(self):
        conds, _ = build_status_filter(
            "active",
            column="y.status",
        )
        assert "y.status" in conds[0]

    def test_column_in_list(self):
        conds, _ = build_status_filter(
            ["a", "b"],
            column="z.status",
        )
        assert "z.status" in conds[0]
