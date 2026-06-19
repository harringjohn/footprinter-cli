"""Tests for db/search.py and db/sql_utils.py query helpers."""

from footprinter.db.search import (
    chat_fts5_fallback,
    enrich_chat_visibility,
    enrich_file_metadata,
    file_fts5_fallback,
    search_browser_keyword,
    search_chats_keyword,
    search_emails_keyword,
    search_files_keyword,
)
from footprinter.db.sql_utils import (
    build_fts5_query,
    build_term_conditions,
    split_query_terms,
)


class TestQueryHelpers:
    """Query-building utilities moved from search_service to db/sql_utils."""

    def test_split_query_terms_basic(self):
        assert split_query_terms("hello world") == ["hello", "world"]

    def test_split_query_terms_drops_short(self):
        assert split_query_terms("a go hi") == ["go", "hi"]

    def test_split_query_terms_empty(self):
        assert split_query_terms("") == []

    def test_build_fts5_query_and_prefix(self):
        result = build_fts5_query(["hello", "world"])
        assert '"hello"*' in result
        assert '"world"*' in result

    def test_build_fts5_query_strips_quotes(self):
        result = build_fts5_query(['he"llo'])
        assert '"' not in result.replace('"hello"*', "")

    def test_build_fts5_query_empty(self):
        assert build_fts5_query([]) == ""

    def test_build_term_conditions_single_column(self):
        cond, params = build_term_conditions(["title"], ["foo"])
        assert "title LIKE ?" in cond
        assert params == ["%foo%"]

    def test_build_term_conditions_multi_column(self):
        cond, params = build_term_conditions(["url", "title"], ["bar"])
        assert "url LIKE ?" in cond
        assert "title LIKE ?" in cond
        assert params == ["%bar%", "%bar%"]

    def test_build_term_conditions_multi_term(self):
        cond, params = build_term_conditions(["title"], ["foo", "bar"])
        assert " AND " in cond
        assert len(params) == 2


class TestSearchFilesKeyword:
    """Keyword search for files via db/search.py."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def test_returns_list_of_dicts(self, db_conn):
        results = search_files_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert "id" in results[0]
        assert "name" in results[0]
        assert "path" in results[0]

    def test_excerpt_uses_content_preview_when_populated(self, db_conn):
        """Keyword files resolve to the content_preview rung when it is populated.

        File 1 (readme.md) carries content_preview='This is a readme' with
        access='allow', so the file-excerpt precedence resolves to the
        content_preview rung (excerpt_source='content_preview'), not the
        name/path title fallback.
        """
        results = search_files_keyword(db_conn, terms=["readme"], has_query=True, limit=10)
        match = [r for r in results if r["name"] == "readme.md"][0]
        assert match["excerpt_source"] == "content_preview"
        assert "This is a readme" in match["excerpt"]
        assert "snippet" not in match
        assert match["chars_returned"] == len(match["excerpt"])
        assert match["chars_available"] == len("This is a readme")
        assert match["has_more"] is False

    def test_excerpt_shows_content_when_populated(self, db_conn):
        """A file hit with a populated content_preview returns it as the excerpt."""
        results = search_files_keyword(db_conn, terms=["report"], has_query=True, limit=10)
        match = [r for r in results if r["id"] == 3][0]
        assert match["excerpt_source"] == "content_preview"
        assert "Report content" in match["excerpt"]
        assert "snippet" not in match

    def test_excerpt_falls_back_to_metadata_when_no_content(self, db_conn):
        """Excerpt uses name — path (source='title') when content_preview is NULL.

        Guards the bottom rung — the no-opt-in / NULL content_preview case.
        """
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (110, 'local', 'notes.txt', '/Users/u/docs/notes.txt', "
            "'listed', 'text', 1000, '2026-01-15', 'full', 'allow', NULL)"
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = search_files_keyword(db_conn, terms=["notes"], has_query=True, limit=10)
        match = [r for r in results if r["id"] == 110][0]
        assert match["excerpt_source"] == "title"
        assert "notes.txt" in match["excerpt"]
        assert "/Users/u/docs/notes.txt" in match["excerpt"]

    def test_excerpt_respects_500_char_budget(self, db_conn):
        """content_preview excerpts are sliced to the flat 500-char ceiling."""
        long_preview = "report " + ("z" * 1000)
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (111, 'local', 'long.md', '/Users/u/docs/long.md', "
            "'listed', 'markdown', 9000, '2026-01-15', 'full', 'allow', ?)",
            (long_preview,),
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = search_files_keyword(db_conn, terms=["report"], has_query=True, limit=10)
        match = [r for r in results if r["id"] == 111][0]
        assert match["chars_returned"] == 500
        assert match["chars_available"] == len(long_preview)
        assert match["has_more"] is True

    def test_denied_file_no_content_leak(self, db_conn):
        """A file with access='deny' must not leak content_preview in any field.

        The access gate must hold on the keyword path too: the excerpt falls
        back to the name/path title rung.
        """
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (112, 'local', 'classified.docx', '/Users/u/docs/classified.docx', "
            "'listed', 'document', 3000, '2026-01-15', 'full', 'deny', "
            "'CONFIDENTIAL DATA that must not leak')"
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = search_files_keyword(db_conn, terms=["classified"], has_query=True, limit=10)
        match = [r for r in results if r["id"] == 112][0]
        assert match["excerpt_source"] == "title"
        for key, value in match.items():
            if isinstance(value, str):
                assert "CONFIDENTIAL DATA" not in value, f"content_preview leaked via field '{key}'"

    def test_null_access_fails_closed_no_content_leak(self, db_conn):
        """A file with a NULL access must not leak content_preview.

        The gate fails closed on a missing access value: the excerpt falls
        back to the name/path title rung rather than surfacing content.
        """
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (113, 'local', 'orphan.txt', '/Users/u/docs/orphan.txt', "
            "'listed', 'text', 2000, '2026-01-15', 'full', NULL, "
            "'SECRET payload that must not leak')"
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = search_files_keyword(db_conn, terms=["orphan"], has_query=True, limit=10)
        match = [r for r in results if r["id"] == 113][0]
        assert match["excerpt_source"] == "title"
        for key, value in match.items():
            if isinstance(value, str):
                assert "SECRET payload" not in value, f"content_preview leaked via field '{key}'"

    def test_no_snippet_key(self, db_conn):
        results = search_files_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert all("snippet" not in r for r in results)

    def test_exclude_hidden(self, db_conn):
        results = search_files_keyword(
            db_conn,
            terms=[],
            has_query=False,
            limit=10,
            exclude_hidden=True,
        )
        names = [r["name"] for r in results]
        assert "secret.py" not in names

    def test_include_hidden_when_not_excluded(self, db_conn):
        results = search_files_keyword(
            db_conn,
            terms=[],
            has_query=False,
            limit=10,
            exclude_hidden=False,
        )
        names = [r["name"] for r in results]
        assert "secret.py" in names

    def test_project_filter(self, db_conn):
        results = search_files_keyword(
            db_conn,
            terms=[],
            has_query=False,
            project="Alpha",
            limit=10,
        )
        assert all(r["project"] == "Alpha" for r in results)

    def test_date_range_filter(self, db_conn):
        results = search_files_keyword(
            db_conn,
            terms=[],
            has_query=False,
            date_from="2026-01-11",
            date_to="2026-01-12",
            limit=10,
        )
        assert all(r["modified_at"] >= "2026-01-11" for r in results)

    def test_account_filter(self, db_conn):
        results = search_files_keyword(
            db_conn,
            terms=[],
            has_query=False,
            account="work",
            limit=10,
        )
        assert all(r["account"] == "work" for r in results)


class TestSearchEmailsKeyword:
    """Keyword search for emails via db/search.py."""

    def test_excludes_removed_emails(self, db_conn):
        db_conn.execute(
            """INSERT INTO emails (message_id, thread_id, account, from_address, from_name,
                                   to_addresses, subject, body_preview, received_at,
                                   labels, status, visibility, access)
               VALUES ('msg-rm', 'thr-rm', 'work', 'removed@example.com', 'Removed',
                       'alice@example.com', 'Removed Email', 'Gone', '2026-01-15T09:00:00',
                       'inbox', 'removed', 'full', 'allow')"""
        )
        db_conn.commit()
        results = search_emails_keyword(db_conn, terms=[], has_query=False, limit=50)
        subjects = [r["subject"] for r in results]
        assert "Removed Email" not in subjects

    def test_returns_list_of_dicts(self, db_conn):
        results = search_emails_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert "id" in results[0]
        assert "subject" in results[0]

    def test_excerpt_from_body_preview(self, db_conn):
        """Emails carry an excerpt sourced from body_preview with provenance."""
        results = search_emails_keyword(db_conn, terms=["update"], has_query=True, limit=10)
        match = [r for r in results if r["subject"] == "Project Update"][0]
        assert match["excerpt"] == "Here is the update..."
        assert match["excerpt_source"] == "body_preview"
        assert match["chars_returned"] == len("Here is the update...")
        assert match["chars_available"] == len("Here is the update...")
        assert match["has_more"] is False

    def test_no_snippet_key(self, db_conn):
        results = search_emails_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert all("snippet" not in r for r in results)

    def test_exclude_hidden(self, db_conn):
        results = search_emails_keyword(
            db_conn,
            terms=[],
            has_query=False,
            limit=10,
            exclude_hidden=True,
        )
        subjects = [r["subject"] for r in results]
        assert "Hidden Email" not in subjects

    def test_account_filter(self, db_conn):
        results = search_emails_keyword(
            db_conn,
            terms=[],
            has_query=False,
            account="personal",
            limit=10,
        )
        assert all(r["account"] == "personal" for r in results)

    def test_sender_filter(self, db_conn):
        results = search_emails_keyword(
            db_conn,
            terms=[],
            has_query=False,
            sender="alice",
            limit=10,
        )
        assert len(results) >= 1
        assert all("alice" in r["from_address"].lower() for r in results)


class TestSearchChatsKeyword:
    """Keyword search for chats via db/search.py."""

    def test_excludes_removed_chats(self, db_conn):
        db_conn.execute(
            """INSERT INTO chats (external_id, account, title, message_count,
                                  created_at, visibility, access, status)
               VALUES ('conv-rm', 'claude', 'Removed Chat', 0,
                       '2026-01-10', 'full', 'allow', 'removed')"""
        )
        db_conn.commit()
        results = search_chats_keyword(db_conn, terms=[], has_query=False, limit=50)
        titles = [r["title"] for r in results]
        assert "Removed Chat" not in titles

    def test_returns_list_of_dicts(self, db_conn):
        results = search_chats_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert "id" in results[0]
        assert "title" in results[0]

    def test_excerpt_from_title(self, db_conn):
        """Keyword chats carry a title excerpt with excerpt_source='title'.

        The message-derived excerpt is the job of a later issue; here the
        title fallback applies.
        """
        results = search_chats_keyword(db_conn, terms=["Visible"], has_query=True, limit=10)
        match = [r for r in results if r["title"] == "Visible Chat"][0]
        assert match["excerpt"] == "Visible Chat"
        assert match["excerpt_source"] == "title"
        assert match["chars_returned"] == len("Visible Chat")
        assert match["has_more"] is False

    def test_no_snippet_key(self, db_conn):
        results = search_chats_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert all("snippet" not in r for r in results)

    def test_exclude_hidden(self, db_conn):
        results = search_chats_keyword(
            db_conn,
            terms=[],
            has_query=False,
            limit=10,
            exclude_hidden=True,
        )
        titles = [r["title"] for r in results]
        assert "Hidden Chat" not in titles

    def test_keyword_match(self, db_conn):
        results = search_chats_keyword(
            db_conn,
            terms=["Visible"],
            has_query=True,
            limit=10,
        )
        assert len(results) == 1
        assert results[0]["title"] == "Visible Chat"


class TestSearchBrowserKeyword:
    """Keyword search for browser visits via db/search.py."""

    def test_excludes_removed_visits(self, db_conn):
        db_conn.execute(
            """INSERT INTO visits (url, title, visit_time, browser, status, visibility, access)
               VALUES ('https://removed.example.com', 'Removed Visit',
                       '2026-01-15 09:00:00', 'safari', 'removed', 'full', 'allow')"""
        )
        db_conn.commit()
        results = search_browser_keyword(db_conn, terms=[], has_query=False, limit=50)
        titles = [r["title"] for r in results]
        assert "Removed Visit" not in titles

    def test_returns_list_of_dicts(self, db_conn):
        results = search_browser_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert "id" in results[0]
        assert "url" in results[0]

    def test_date_filter(self, db_conn):
        results = search_browser_keyword(
            db_conn,
            terms=[],
            has_query=False,
            date_from="2026-01-15 11:00:00",
            limit=10,
        )
        assert all(r["visit_time"] >= "2026-01-15 11:00:00" for r in results)

    def test_keyword_match(self, db_conn):
        results = search_browser_keyword(
            db_conn,
            terms=["Example"],
            has_query=True,
            limit=10,
        )
        assert len(results) >= 1
        assert all("example" in r["url"].lower() or "Example" in r["title"] for r in results)


class TestChatFts5Fallback:
    """FTS5 fallback for chat search via db/search.py."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.commit()

    def test_returns_list_of_dicts(self, db_conn):
        self._rebuild_fts(db_conn)
        results = chat_fts5_fallback(db_conn, "Visible Chat", 10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert "chat_id" in results[0]
        assert "chat_title" in results[0]
        assert "relevance_score" in results[0]

    def test_excludes_removed(self, db_conn):
        db_conn.execute("UPDATE chats SET status = 'removed' WHERE id = 1")
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = chat_fts5_fallback(db_conn, "Visible Chat", 10)
        chat_ids = [r["chat_id"] for r in results]
        assert 1 not in chat_ids


class TestFileFts5Fallback:
    """FTS5 fallback for file search via db/search.py."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def test_returns_list_of_dicts(self, db_conn):
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "readme", 10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert "id" in results[0]
        assert "name" in results[0]
        assert "relevance_score" in results[0]

    def test_excerpt_shows_content_when_populated(self, db_conn):
        """Excerpt uses content_preview (source='content_preview') when access allows it."""
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (100, 'local', 'report.pdf', '/Users/u/docs/report.pdf', "
            "'listed', 'pdf', 5000, '2026-01-15', 'full', 'allow', "
            "'This is the report content preview text')"
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "report", 10)
        assert len(results) >= 1
        match = [r for r in results if r["id"] == 100][0]
        assert "report content preview" in match["excerpt"]
        assert match["excerpt_source"] == "content_preview"
        assert "snippet" not in match

    def test_excerpt_respects_500_char_budget(self, db_conn):
        """content_preview excerpts are sliced to the flat 500-char ceiling, not 200."""
        long_preview = "report " + ("z" * 1000)
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (103, 'local', 'long.md', '/Users/u/docs/long.md', "
            "'listed', 'markdown', 9000, '2026-01-15', 'full', 'allow', ?)",
            (long_preview,),
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "report", 10)
        match = [r for r in results if r["id"] == 103][0]
        assert match["chars_returned"] == 500
        assert match["chars_available"] == len(long_preview)
        assert match["has_more"] is True

    def test_excerpt_falls_back_to_metadata_when_no_content(self, db_conn):
        """Excerpt uses name — path (source='title') when content_preview is NULL."""
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (102, 'local', 'notes.txt', '/Users/u/docs/notes.txt', "
            "'listed', 'text', 1000, '2026-01-15', 'full', 'allow', NULL)"
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "notes", 10)
        assert len(results) >= 1
        match = [r for r in results if r["id"] == 102][0]
        assert match["excerpt_source"] == "title"
        assert "notes.txt" in match["excerpt"]
        assert "/Users/u/docs/notes.txt" in match["excerpt"]

    def test_denied_file_no_content_leak(self, db_conn):
        """File with access='deny' must not have content_preview in any field."""
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (101, 'local', 'classified.docx', '/Users/u/docs/classified.docx', "
            "'listed', 'document', 3000, '2026-01-15', 'full', 'deny', "
            "'CONFIDENTIAL DATA that must not leak')"
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "classified", 10)
        assert len(results) >= 1
        match = [r for r in results if r["id"] == 101][0]
        # No field should contain the content_preview text
        for key, value in match.items():
            if isinstance(value, str):
                assert "CONFIDENTIAL DATA" not in value, f"content_preview leaked via field '{key}'"

    def test_short_query_returns_empty(self, db_conn):
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "a", 10)
        assert results == []


class TestEnrichChatVisibility:
    """Enrich chat results with visibility from DB."""

    def test_returns_lookup_dict(self, db_conn):
        lookup = enrich_chat_visibility(db_conn, [1, 2, 3])
        assert isinstance(lookup, dict)
        assert 1 in lookup
        assert lookup[1]["visibility"] == "full"
        assert lookup[2]["visibility"] == "hidden"

    def test_empty_ids_returns_empty(self, db_conn):
        lookup = enrich_chat_visibility(db_conn, [])
        assert lookup == {}

    def test_missing_ids_excluded(self, db_conn):
        lookup = enrich_chat_visibility(db_conn, [999])
        assert 999 not in lookup

    def test_excludes_removed(self, db_conn):
        db_conn.execute("UPDATE chats SET status = 'removed' WHERE id = 2")
        db_conn.commit()
        lookup = enrich_chat_visibility(db_conn, [1, 2])
        assert 1 in lookup
        assert 2 not in lookup


class TestEnrichFileMetadata:
    """Enrich file results with metadata from DB."""

    def test_returns_lookup_dict(self, db_conn):
        lookup = enrich_file_metadata(db_conn, [1, 2])
        assert isinstance(lookup, dict)
        assert 1 in lookup
        assert "name" in lookup[1]
        assert "visibility" in lookup[1]

    def test_excludes_removed(self, db_conn):
        db_conn.execute("UPDATE files SET status = 'removed' WHERE id = 1")
        db_conn.commit()
        lookup = enrich_file_metadata(db_conn, [1])
        assert 1 not in lookup

    def test_empty_ids_returns_empty(self, db_conn):
        lookup = enrich_file_metadata(db_conn, [])
        assert lookup == {}


class TestWhereClauseRemoved:
    """Verify dead _where_clause helper has been removed."""

    def test_where_clause_removed(self):
        import footprinter.db.search as search_mod

        assert not hasattr(search_mod, "_where_clause")


# ---------------------------------------------------------------------------
# Status kwarg matrix for keyword search + FTS5 + enrichment.
# ADMIN-only widening flows from MCP tool → service layer → these functions.
# ---------------------------------------------------------------------------


_TABLES_WITH_STATUS_REASON = frozenset({"files", "folders", "clients", "projects"})


def _seed_mixed_status(conn, table: str) -> None:
    """Mark id=1 listed, id=2 unlisted, id=3 removed in *table*.

    Sets ``status_reason`` only on tables that have the column.
    """
    if table in _TABLES_WITH_STATUS_REASON:
        conn.executemany(
            f"UPDATE {table} SET status = ?, status_reason = ? WHERE id = ?",
            [
                ("listed", None, 1),
                ("unlisted", "user_hidden", 2),
                ("removed", "deleted_by_user", 3),
            ],
        )
    else:
        conn.executemany(
            f"UPDATE {table} SET status = ? WHERE id = ?",
            [("listed", 1), ("unlisted", 2), ("removed", 3)],
        )
    conn.commit()


class TestSearchFilesKeywordStatusMatrix:
    """search_files_keyword respects the status kwarg + surfaces status fields.

    Tests pass ``exclude_hidden=False`` so the visibility filter on fixture
    rows doesn't interfere with status-only assertions.
    """

    def test_default_returns_only_listed(self, db_conn):
        _seed_mixed_status(db_conn, "files")
        results = search_files_keyword(
            db_conn, terms=[], has_query=False, limit=50, exclude_hidden=False
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1]

    def test_status_all_returns_everything(self, db_conn):
        _seed_mixed_status(db_conn, "files")
        results = search_files_keyword(
            db_conn, terms=[], has_query=False, limit=50, status="all",
            exclude_hidden=False,
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1, 2, 3]

    def test_status_listed_unlisted(self, db_conn):
        _seed_mixed_status(db_conn, "files")
        results = search_files_keyword(
            db_conn,
            terms=[],
            has_query=False,
            limit=50,
            status=["listed", "unlisted"],
            exclude_hidden=False,
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1, 2]

    def test_status_listed_removed(self, db_conn):
        _seed_mixed_status(db_conn, "files")
        results = search_files_keyword(
            db_conn,
            terms=[],
            has_query=False,
            limit=50,
            status=["listed", "removed"],
            exclude_hidden=False,
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1, 3]

    def test_results_include_status_fields(self, db_conn):
        _seed_mixed_status(db_conn, "files")
        results = search_files_keyword(
            db_conn, terms=[], has_query=False, limit=50, status="all",
            exclude_hidden=False,
        )
        by_id = {r["id"]: r for r in results}
        assert by_id[2]["status"] == "unlisted"
        assert by_id[2]["status_reason"] == "user_hidden"
        assert by_id[3]["status"] == "removed"
        assert by_id[3]["status_reason"] == "deleted_by_user"


class TestSearchEmailsKeywordStatusMatrix:
    """search_emails_keyword respects the status kwarg + surfaces status fields."""

    def test_default_returns_only_listed(self, db_conn):
        _seed_mixed_status(db_conn, "emails")
        results = search_emails_keyword(
            db_conn, terms=[], has_query=False, limit=50, exclude_hidden=False
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1]

    def test_status_all_returns_everything(self, db_conn):
        _seed_mixed_status(db_conn, "emails")
        results = search_emails_keyword(
            db_conn, terms=[], has_query=False, limit=50, status="all",
            exclude_hidden=False,
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1, 2, 3]

    def test_results_include_status_field(self, db_conn):
        _seed_mixed_status(db_conn, "emails")
        results = search_emails_keyword(
            db_conn,
            terms=[],
            has_query=False,
            limit=50,
            status=["listed", "removed"],
            exclude_hidden=False,
        )
        by_id = {r["id"]: r for r in results}
        assert by_id[3]["status"] == "removed"


class TestSearchChatsKeywordStatusMatrix:
    """search_chats_keyword respects the status kwarg + surfaces status fields."""

    def test_default_returns_only_listed(self, db_conn):
        _seed_mixed_status(db_conn, "chats")
        results = search_chats_keyword(
            db_conn, terms=[], has_query=False, limit=50, exclude_hidden=False
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1]

    def test_status_all_returns_everything(self, db_conn):
        _seed_mixed_status(db_conn, "chats")
        results = search_chats_keyword(
            db_conn, terms=[], has_query=False, limit=50, status="all",
            exclude_hidden=False,
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1, 2, 3]

    def test_results_include_status_field(self, db_conn):
        _seed_mixed_status(db_conn, "chats")
        results = search_chats_keyword(
            db_conn,
            terms=[],
            has_query=False,
            limit=50,
            status=["listed", "unlisted"],
            exclude_hidden=False,
        )
        by_id = {r["id"]: r for r in results}
        assert by_id[2]["status"] == "unlisted"


class TestSearchBrowserKeywordStatusMatrix:
    """search_browser_keyword respects the status kwarg + surfaces status fields."""

    def test_default_returns_only_listed(self, db_conn):
        _seed_mixed_status(db_conn, "visits")
        results = search_browser_keyword(
            db_conn, terms=[], has_query=False, limit=50, exclude_hidden=False
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1]

    def test_status_all_returns_everything(self, db_conn):
        _seed_mixed_status(db_conn, "visits")
        results = search_browser_keyword(
            db_conn, terms=[], has_query=False, limit=50, status="all",
            exclude_hidden=False,
        )
        ids = sorted(r["id"] for r in results)
        assert ids == [1, 2, 3]

    def test_results_include_status_field(self, db_conn):
        _seed_mixed_status(db_conn, "visits")
        results = search_browser_keyword(
            db_conn, terms=[], has_query=False, limit=50, status="all",
            exclude_hidden=False,
        )
        by_id = {r["id"]: r for r in results}
        assert by_id[3]["status"] == "removed"


class TestChatFts5FallbackStatusMatrix:
    """chat_fts5_fallback respects the status kwarg."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.commit()

    def test_default_excludes_unlisted_and_removed(self, db_conn):
        # Mark chat 1 unlisted so default filter (listed only) drops it.
        db_conn.execute("UPDATE chats SET status = 'unlisted' WHERE id = 1")
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = chat_fts5_fallback(db_conn, "Visible Chat", 10)
        chat_ids = [r["chat_id"] for r in results]
        assert 1 not in chat_ids

    def test_widened_includes_unlisted(self, db_conn):
        db_conn.execute("UPDATE chats SET status = 'unlisted' WHERE id = 1")
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = chat_fts5_fallback(
            db_conn, "Visible Chat", 10, status=["listed", "unlisted"]
        )
        chat_ids = [r["chat_id"] for r in results]
        assert 1 in chat_ids


class TestFileFts5FallbackStatusMatrix:
    """file_fts5_fallback respects the status kwarg."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def test_default_excludes_unlisted(self, db_conn):
        db_conn.execute("UPDATE files SET status = 'unlisted' WHERE id = 1")
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "readme", 10)
        ids = [r["id"] for r in results]
        assert 1 not in ids

    def test_widened_includes_unlisted(self, db_conn):
        db_conn.execute("UPDATE files SET status = 'unlisted' WHERE id = 1")
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(
            db_conn, "readme", 10, status=["listed", "unlisted"]
        )
        ids = [r["id"] for r in results]
        assert 1 in ids


class TestEnrichChatVisibilityStatusMatrix:
    """enrich_chat_visibility respects the status kwarg."""

    def test_default_excludes_removed(self, db_conn):
        db_conn.execute("UPDATE chats SET status = 'removed' WHERE id = 1")
        db_conn.commit()
        lookup = enrich_chat_visibility(db_conn, [1, 2])
        assert 1 not in lookup

    def test_widened_includes_removed(self, db_conn):
        db_conn.execute("UPDATE chats SET status = 'removed' WHERE id = 1")
        db_conn.commit()
        lookup = enrich_chat_visibility(db_conn, [1, 2], status="all")
        assert 1 in lookup


class TestEnrichFileMetadataStatusMatrix:
    """enrich_file_metadata respects the status kwarg."""

    def test_default_excludes_removed(self, db_conn):
        db_conn.execute("UPDATE files SET status = 'removed' WHERE id = 1")
        db_conn.commit()
        lookup = enrich_file_metadata(db_conn, [1, 2])
        assert 1 not in lookup

    def test_widened_includes_removed(self, db_conn):
        db_conn.execute("UPDATE files SET status = 'removed' WHERE id = 1")
        db_conn.commit()
        lookup = enrich_file_metadata(db_conn, [1, 2], status="all")
        assert 1 in lookup
