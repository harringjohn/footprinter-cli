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

    def test_returns_list_of_dicts(self, db_conn):
        results = search_files_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert "id" in results[0]
        assert "name" in results[0]
        assert "path" in results[0]

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
                                   labels, status, mcp_view, mcp_read)
               VALUES ('msg-rm', 'thr-rm', 'work', 'removed@example.com', 'Removed',
                       'alice@example.com', 'Removed Email', 'Gone', '2026-01-15T09:00:00',
                       'inbox', 'removed', 'visible', 'allow')"""
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
            """INSERT INTO chats (external_id, account, title, summary, message_count,
                                  created_at, mcp_view, mcp_read, status)
               VALUES ('conv-rm', 'claude', 'Removed Chat', 'Gone', 0,
                       '2026-01-10', 'visible', 'allow', 'removed')"""
        )
        db_conn.commit()
        results = search_chats_keyword(db_conn, terms=[], has_query=False, limit=50)
        titles = [r["title"] for r in results]
        assert "Removed Chat" not in titles

    def test_excludes_merged_chats(self, db_conn):
        db_conn.execute(
            """INSERT INTO chats (external_id, account, title, summary, message_count,
                                  created_at, mcp_view, mcp_read, status)
               VALUES ('conv-mg', 'claude', 'Merged Chat', 'Merged away', 0,
                       '2026-01-10', 'visible', 'allow', 'merged')"""
        )
        db_conn.commit()
        results = search_chats_keyword(db_conn, terms=[], has_query=False, limit=50)
        titles = [r["title"] for r in results]
        assert "Merged Chat" not in titles

    def test_returns_list_of_dicts(self, db_conn):
        results = search_chats_keyword(db_conn, terms=[], has_query=False, limit=10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert "id" in results[0]
        assert "title" in results[0]

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
            """INSERT INTO visits (url, title, visit_time, browser, status, mcp_view, mcp_read)
               VALUES ('https://removed.example.com', 'Removed Visit',
                       '2026-01-15 09:00:00', 'safari', 'removed', 'visible', 'allow')"""
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

    def test_snippet_shows_content_when_populated(self, db_conn):
        """Snippet uses content_preview when populated and mcp_read allows it."""
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, mcp_view, mcp_read, content_preview) "
            "VALUES (100, 'local', 'report.pdf', '/Users/u/docs/report.pdf', "
            "'active', 'pdf', 5000, '2026-01-15', 'visible', 'allow', "
            "'This is the report content preview text')"
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "report", 10)
        assert len(results) >= 1
        match = [r for r in results if r["id"] == 100][0]
        assert "report content preview" in match["snippet"]

    def test_snippet_falls_back_to_metadata_when_no_content(self, db_conn):
        """Snippet uses name — path when content_preview is NULL."""
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, mcp_view, mcp_read, content_preview) "
            "VALUES (102, 'local', 'notes.txt', '/Users/u/docs/notes.txt', "
            "'active', 'text', 1000, '2026-01-15', 'visible', 'allow', NULL)"
        )
        db_conn.commit()
        self._rebuild_fts(db_conn)
        results = file_fts5_fallback(db_conn, "notes", 10)
        assert len(results) >= 1
        match = [r for r in results if r["id"] == 102][0]
        assert "notes.txt" in match["snippet"]
        assert "/Users/u/docs/notes.txt" in match["snippet"]

    def test_denied_file_no_content_leak(self, db_conn):
        """File with mcp_read='deny' must not have content_preview in any field."""
        db_conn.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, mcp_view, mcp_read, content_preview) "
            "VALUES (101, 'local', 'classified.docx', '/Users/u/docs/classified.docx', "
            "'active', 'document', 3000, '2026-01-15', 'visible', 'deny', "
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
        assert lookup[1]["mcp_view"] == "visible"
        assert lookup[2]["mcp_view"] == "hidden"

    def test_empty_ids_returns_empty(self, db_conn):
        lookup = enrich_chat_visibility(db_conn, [])
        assert lookup == {}

    def test_missing_ids_excluded(self, db_conn):
        lookup = enrich_chat_visibility(db_conn, [999])
        assert 999 not in lookup


class TestEnrichFileMetadata:
    """Enrich file results with metadata from DB."""

    def test_returns_lookup_dict(self, db_conn):
        lookup = enrich_file_metadata(db_conn, [1, 2])
        assert isinstance(lookup, dict)
        assert 1 in lookup
        assert "name" in lookup[1]
        assert "mcp_view" in lookup[1]

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
