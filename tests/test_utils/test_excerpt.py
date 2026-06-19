"""Tests for footprinter.utils.text.build_excerpt — the uniform excerpt contract."""

from footprinter.utils.text import EXCERPT_BUDGET, build_excerpt


class TestExcerptBudget:
    """The flat ceiling shared across every content-bearing source."""

    def test_budget_is_500(self):
        assert EXCERPT_BUDGET == 500


class TestBuildExcerptUnderBudget:
    """Text shorter than the budget is returned whole, with has_more False."""

    def test_returns_full_text(self):
        result = build_excerpt("short body", source="body_preview")
        assert result["excerpt"] == "short body"

    def test_no_more_content(self):
        result = build_excerpt("short body", source="body_preview")
        assert result["has_more"] is False

    def test_chars_match_length(self):
        text = "short body"
        result = build_excerpt(text, source="body_preview")
        assert result["chars_returned"] == len(text)
        assert result["chars_available"] == len(text)

    def test_source_passed_through(self):
        result = build_excerpt("short body", source="body_preview")
        assert result["excerpt_source"] == "body_preview"


class TestBuildExcerptOverBudget:
    """Text longer than the budget is sliced, with has_more True."""

    def test_sliced_to_budget(self):
        text = "x" * 1200
        result = build_excerpt(text, source="content_preview")
        assert result["excerpt"] == "x" * EXCERPT_BUDGET
        assert result["chars_returned"] == EXCERPT_BUDGET

    def test_has_more_true(self):
        text = "x" * 1200
        result = build_excerpt(text, source="content_preview")
        assert result["has_more"] is True

    def test_chars_available_is_full_length(self):
        text = "x" * 1200
        result = build_excerpt(text, source="content_preview")
        assert result["chars_available"] == 1200


class TestBuildExcerptExplicitAvailable:
    """A windowed excerpt reports availability against the full content, not the window."""

    def test_has_more_reflects_full_length(self):
        window = "...matched window..."
        result = build_excerpt(window, source="chunk", chars_available=5000)
        assert result["chars_available"] == 5000
        assert result["has_more"] is True
        assert result["chars_returned"] == len(window)

    def test_no_more_when_window_equals_available(self):
        window = "whole chunk"
        result = build_excerpt(window, source="chunk", chars_available=len(window))
        assert result["has_more"] is False


class TestBuildExcerptCustomBudget:
    """An explicit budget overrides the default ceiling."""

    def test_respects_smaller_budget(self):
        text = "abcdefghij"
        result = build_excerpt(text, source="title", budget=4)
        assert result["excerpt"] == "abcd"
        assert result["chars_returned"] == 4
        assert result["has_more"] is True
