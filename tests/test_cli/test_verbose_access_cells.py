"""Tests for verbose_access_cells() — 5-column verbose table output."""

from footprinter.cli._common import verbose_access_cells


def _row(**kwargs):
    """Build a minimal row dict with defaults for all six access fields."""
    defaults = {
        "mcp_view": "inherit",
        "mcp_read": "inherit",
        "visibility": "visible",
        "access": "allow",
        "access_source": "global",
        "visibility_source": "global",
    }
    defaults.update(kwargs)
    return defaults


class TestVerboseAccessCellsCount:
    def test_returns_six_cells(self):
        result = verbose_access_cells(_row())
        assert len(result) == 6


class TestVerboseAccessCellsOrder:
    def test_cell_order_matches_column_spec(self):
        row = _row(
            mcp_view="opaque",
            mcp_read="deny",
            visibility="hidden",
            access="deny",
            access_source="project:3",
        )
        cells = verbose_access_cells(row)
        assert "opaque" in cells[0]
        assert "deny" in cells[1]
        assert "hidden" in cells[2]
        assert "deny" in cells[3]
        assert "project:3" in cells[4]


class TestVerboseAccessCellsMcpViewMarkup:
    def test_inherit_is_dim(self):
        cells = verbose_access_cells(_row(mcp_view="inherit"))
        assert cells[0] == "[dim]inherit[/dim]"

    def test_visible_is_green(self):
        cells = verbose_access_cells(_row(mcp_view="visible"))
        assert cells[0] == "[green]visible[/green]"

    def test_opaque_is_yellow(self):
        cells = verbose_access_cells(_row(mcp_view="opaque"))
        assert cells[0] == "[yellow]opaque[/yellow]"

    def test_hidden_is_red(self):
        cells = verbose_access_cells(_row(mcp_view="hidden"))
        assert cells[0] == "[red]hidden[/red]"

    def test_missing_key_is_dim_dash(self):
        row = _row()
        del row["mcp_view"]
        cells = verbose_access_cells(row)
        assert cells[0] == "[dim]—[/dim]"


class TestVerboseAccessCellsMcpReadMarkup:
    def test_inherit_is_dim(self):
        cells = verbose_access_cells(_row(mcp_read="inherit"))
        assert cells[1] == "[dim]inherit[/dim]"

    def test_allow_is_green(self):
        cells = verbose_access_cells(_row(mcp_read="allow"))
        assert cells[1] == "[green]allow[/green]"

    def test_deny_is_red(self):
        cells = verbose_access_cells(_row(mcp_read="deny"))
        assert cells[1] == "[red]deny[/red]"

    def test_missing_key_is_dim_dash(self):
        row = _row()
        del row["mcp_read"]
        cells = verbose_access_cells(row)
        assert cells[1] == "[dim]—[/dim]"


class TestVerboseAccessCellsSourceMarkup:
    def test_source_global(self):
        cells = verbose_access_cells(_row(access_source="global"))
        assert cells[4] == "global"

    def test_source_folder_path(self):
        cells = verbose_access_cells(_row(access_source="folder:~/Work"))
        assert cells[4] == "folder:~/Work"

    def test_source_dash_is_dim(self):
        cells = verbose_access_cells(_row(access_source="—"))
        assert cells[4] == "[dim]—[/dim]"

    def test_source_missing_key_is_dim_dash(self):
        row = _row()
        del row["access_source"]
        cells = verbose_access_cells(row)
        assert cells[4] == "[dim]—[/dim]"


class TestVerboseAccessCellsVisSourceMarkup:
    def test_same_as_access_shows_equiv(self):
        cells = verbose_access_cells(_row(visibility_source="global", access_source="global"))
        assert cells[5] == "[dim]≡[/dim]"

    def test_different_from_access_shows_value(self):
        cells = verbose_access_cells(_row(visibility_source="folder:~/Work", access_source="global"))
        assert cells[5] == "folder:~/Work"

    def test_dash_is_dim(self):
        cells = verbose_access_cells(_row(visibility_source="—"))
        assert cells[5] == "[dim]—[/dim]"

    def test_missing_key_is_dim_dash(self):
        row = _row()
        del row["visibility_source"]
        cells = verbose_access_cells(row)
        assert cells[5] == "[dim]—[/dim]"


class TestVerboseAccessCellsIntegration:
    def test_inherit_row(self):
        cells = verbose_access_cells(_row())
        assert cells == [
            "[dim]inherit[/dim]",
            "[dim]inherit[/dim]",
            "[green]visible[/green]",
            "[green]allow[/green]",
            "global",
            "[dim]≡[/dim]",
        ]

    def test_explicit_cached_row(self):
        row = _row(mcp_view="visible", mcp_read="allow", access_source="cached", visibility_source="cached")
        cells = verbose_access_cells(row)
        assert cells == [
            "[green]visible[/green]",
            "[green]allow[/green]",
            "[green]visible[/green]",
            "[green]allow[/green]",
            "cached",
            "[dim]≡[/dim]",
        ]

    def test_folder_row_without_access(self):
        row = {"visibility": "opaque", "access": "—", "access_source": "—", "visibility_source": "—"}
        cells = verbose_access_cells(row)
        assert cells[0] == "[dim]—[/dim]"
        assert cells[1] == "[dim]—[/dim]"
        assert cells[2] == "[yellow]opaque[/yellow]"
        assert cells[3] == "[dim]—[/dim]"
        assert cells[4] == "[dim]—[/dim]"
        assert cells[5] == "[dim]—[/dim]"

    def test_explicit_folder_source_row(self):
        row = _row(
            mcp_view="opaque",
            mcp_read="inherit",
            visibility="opaque",
            access="allow",
            access_source="folder:~/Work",
            visibility_source="folder:~/Work",
        )
        cells = verbose_access_cells(row)
        assert cells == [
            "[yellow]opaque[/yellow]",
            "[dim]inherit[/dim]",
            "[yellow]opaque[/yellow]",
            "[green]allow[/green]",
            "folder:~/Work",
            "[dim]≡[/dim]",
        ]

    def test_split_source_row(self):
        row = _row(
            mcp_view="opaque",
            mcp_read="inherit",
            visibility="opaque",
            access="allow",
            access_source="global",
            visibility_source="folder:~/Work",
        )
        cells = verbose_access_cells(row)
        assert cells == [
            "[yellow]opaque[/yellow]",
            "[dim]inherit[/dim]",
            "[yellow]opaque[/yellow]",
            "[green]allow[/green]",
            "global",
            "folder:~/Work",
        ]
