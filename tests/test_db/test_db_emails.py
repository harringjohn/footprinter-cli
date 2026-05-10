"""Tests for footprinter.db.emails listing behavior.

Pins the standardized ``default_exclude=["removed"]`` filter pattern,
and the convention that single-record getters return regardless of status.
"""

from footprinter.db.emails import get_email, list_emails


def _insert_emails(conn):
    conn.execute(
        """
        INSERT INTO emails (id, message_id, thread_id, account, from_address,
                            subject, received_at, status)
        VALUES
            (1, 'msg-listed',   'thr-1', 'work', 'a@example.com', 'Listed mail',
             '2026-01-15T10:00:00', 'listed'),
            (2, 'msg-unlisted', 'thr-2', 'work', 'b@example.com', 'Unlisted mail',
             '2026-01-15T11:00:00', 'unlisted'),
            (3, 'msg-removed',  'thr-3', 'work', 'c@example.com', 'Removed mail',
             '2026-01-15T12:00:00', 'removed')
        """
    )
    conn.commit()


class TestListEmailsDefaultExclude:
    """Default filter excludes ``removed`` only — unlisted is visible."""

    def test_default_returns_listed_and_unlisted(self, tool_db):
        _insert_emails(tool_db)
        result = list_emails(tool_db)
        subjects = {e["subject"] for e in result["emails"]}
        assert subjects == {"Listed mail", "Unlisted mail"}

    def test_default_excludes_removed(self, tool_db):
        _insert_emails(tool_db)
        result = list_emails(tool_db)
        subjects = {e["subject"] for e in result["emails"]}
        assert "Removed mail" not in subjects

    def test_status_all_returns_everything(self, tool_db):
        _insert_emails(tool_db)
        result = list_emails(tool_db, status="all")
        subjects = {e["subject"] for e in result["emails"]}
        assert subjects == {"Listed mail", "Unlisted mail", "Removed mail"}

    def test_explicit_status_filter(self, tool_db):
        _insert_emails(tool_db)
        result = list_emails(tool_db, status="removed")
        subjects = [e["subject"] for e in result["emails"]]
        assert subjects == ["Removed mail"]


class TestGetEmailNoStatusFilter:
    """Single-record getter returns regardless of status (matches get_visit/get_chat)."""

    def test_get_email_returns_unlisted(self, tool_db):
        _insert_emails(tool_db)
        email = get_email(tool_db, 2)
        assert email is not None
        assert email["subject"] == "Unlisted mail"

    def test_get_email_returns_removed(self, tool_db):
        _insert_emails(tool_db)
        email = get_email(tool_db, 3)
        assert email is not None
        assert email["subject"] == "Removed mail"
