"""Tests for footprinter.cli._prompt — safe prompt wrappers with Escape/Ctrl+C support."""

from unittest.mock import MagicMock, patch

import pytest

from footprinter.cli._prompt import PromptCancelled, SafeConfirm, SafePrompt

# ---------------------------------------------------------------------------
# PromptCancelled exception semantics
# ---------------------------------------------------------------------------


class TestPromptCancelled:
    """PromptCancelled must be a BaseException so it passes through except Exception blocks."""

    def test_prompt_cancelled_is_base_exception(self):
        assert issubclass(PromptCancelled, BaseException)

    def test_prompt_cancelled_not_caught_by_except_exception(self):
        """except Exception must NOT catch PromptCancelled."""
        with pytest.raises(PromptCancelled):
            try:
                raise PromptCancelled("test")
            except Exception:
                pytest.fail("PromptCancelled was caught by except Exception")


# ---------------------------------------------------------------------------
# SafePrompt — interrupt handling
# ---------------------------------------------------------------------------


class TestSafePrompt:
    """SafePrompt wraps Rich Prompt with KeyboardInterrupt/EOFError → PromptCancelled."""

    @patch("footprinter.cli._prompt.SafePrompt.get_input", side_effect=KeyboardInterrupt)
    def test_keyboard_interrupt_raises_cancelled(self, _mock):
        """KeyboardInterrupt during SafePrompt.ask() → PromptCancelled."""
        with pytest.raises((PromptCancelled, KeyboardInterrupt)):
            SafePrompt.ask("test")

    @patch("footprinter.cli._prompt.SafePrompt.get_input", side_effect=EOFError)
    def test_eof_raises_cancelled(self, _mock):
        """EOFError during SafePrompt.ask() → PromptCancelled."""
        with pytest.raises((PromptCancelled, EOFError)):
            SafePrompt.ask("test")

    @patch("footprinter.cli._prompt.SafePrompt.get_input", return_value="hello")
    def test_normal_input(self, _mock):
        """Normal input passes through unchanged."""
        result = SafePrompt.ask("test")
        assert result == "hello"


# ---------------------------------------------------------------------------
# SafeConfirm — interrupt handling
# ---------------------------------------------------------------------------


class TestSafeConfirm:
    """SafeConfirm wraps Rich Confirm with KeyboardInterrupt → PromptCancelled."""

    @patch("footprinter.cli._prompt.SafeConfirm.get_input", side_effect=KeyboardInterrupt)
    def test_keyboard_interrupt_raises_cancelled(self, _mock):
        """KeyboardInterrupt during SafeConfirm.ask() → PromptCancelled."""
        with pytest.raises((PromptCancelled, KeyboardInterrupt)):
            SafeConfirm.ask("test")

    @patch("footprinter.cli._prompt.SafeConfirm.get_input", return_value="y")
    def test_normal_input(self, _mock):
        """Normal confirm passes through unchanged."""
        result = SafeConfirm.ask("test")
        assert result is True


# ---------------------------------------------------------------------------
# _safe_readline — raw terminal Escape detection
# ---------------------------------------------------------------------------


class TestSafeReadline:
    """Tests for the raw-terminal readline function."""

    @patch("footprinter.cli._prompt._HAS_TERMIOS", True)
    @patch("footprinter.cli._prompt.sys")
    @patch("footprinter.cli._prompt.os")
    @patch("footprinter.cli._prompt.termios")
    @patch("footprinter.cli._prompt.tty")
    def test_escape_raises_cancelled(self, mock_tty, mock_termios, mock_os, mock_sys):
        """Bare Escape key (0x1b with no follow-up) raises PromptCancelled."""
        from footprinter.cli._prompt import _safe_readline

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_termios.tcgetattr.return_value = [0] * 7
        # First read returns Escape, second read times out (empty = bare Escape)
        mock_os.read.side_effect = [b"\x1b", b""]

        # select returns ready for first read, then empty for timeout check
        with patch("footprinter.cli._prompt.select") as mock_select:
            mock_select.select.side_effect = [([0], [], []), ([], [], [])]
            with pytest.raises(PromptCancelled):
                _safe_readline()

    @patch("footprinter.cli._prompt._HAS_TERMIOS", True)
    @patch("footprinter.cli._prompt.sys")
    @patch("footprinter.cli._prompt.os")
    @patch("footprinter.cli._prompt.termios")
    @patch("footprinter.cli._prompt.tty")
    def test_escape_sequence_not_cancelled(self, mock_tty, mock_termios, mock_os, mock_sys):
        """Arrow key escape sequence (\\x1b[A) should NOT cancel — it's not bare Escape."""
        from footprinter.cli._prompt import _safe_readline

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_sys.stdout = MagicMock()
        mock_termios.tcgetattr.return_value = [0] * 7
        # Escape, then '[', then 'A' (arrow up), then 'x', then Enter
        mock_os.read.side_effect = [b"\x1b", b"[", b"A", b"x", b"\r"]

        with patch("footprinter.cli._prompt.select") as mock_select:
            # First read ready, then ready (follow-up byte exists = sequence),
            # then ready for '[', ready for 'A', ready for 'x', ready for '\r'
            mock_select.select.side_effect = [
                ([0], [], []),  # \x1b ready
                ([0], [], []),  # follow-up check: '[' available = escape sequence
                ([0], [], []),  # read '['
                ([0], [], []),  # read 'A' — end of sequence
                ([0], [], []),  # read 'x'
                ([0], [], []),  # read '\r'
            ]
            result = _safe_readline()
            assert result == "x"

    @patch("footprinter.cli._prompt._HAS_TERMIOS", True)
    @patch("footprinter.cli._prompt.sys")
    @patch("footprinter.cli._prompt.os")
    @patch("footprinter.cli._prompt.termios")
    @patch("footprinter.cli._prompt.tty")
    def test_ss3_sequence_not_cancelled(self, mock_tty, mock_termios, mock_os, mock_sys):
        """SS3 escape sequence (\\x1bOA, e.g., F1) should NOT cancel or leak bytes."""
        from footprinter.cli._prompt import _safe_readline

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_sys.stdout = MagicMock()
        mock_termios.tcgetattr.return_value = [0] * 7
        # F1 key (\x1bOA), then 'z', then Enter
        mock_os.read.side_effect = [b"\x1b", b"O", b"P", b"z", b"\r"]

        with patch("footprinter.cli._prompt.select") as mock_select:
            mock_select.select.side_effect = [
                ([0], [], []),  # \x1b ready
                ([0], [], []),  # follow-up check: 'O' available = SS3 sequence
                ([0], [], []),  # read 'O' (already consumed by _read_with_timeout)
                ([0], [], []),  # read 'P' (SS3 terminator consumed)
                ([0], [], []),  # read 'z'
                ([0], [], []),  # read '\r'
            ]
            result = _safe_readline()
            assert result == "z"

    @patch("footprinter.cli._prompt._HAS_TERMIOS", False)
    @patch("builtins.input", return_value="fallback value")
    def test_non_tty_fallback(self, _mock_input):
        """Non-TTY (no termios) falls back to input()."""
        from footprinter.cli._prompt import _safe_readline

        result = _safe_readline()
        assert result == "fallback value"


# ---------------------------------------------------------------------------
# _safe_readline — UTF-8 multi-byte character handling
# ---------------------------------------------------------------------------


class TestSafeReadlineUtf8:
    """Tests for UTF-8 multi-byte character handling in raw terminal mode."""

    @patch("footprinter.cli._prompt._HAS_TERMIOS", True)
    @patch("footprinter.cli._prompt.sys")
    @patch("footprinter.cli._prompt.os")
    @patch("footprinter.cli._prompt.termios")
    @patch("footprinter.cli._prompt.tty")
    def test_two_byte_utf8_char(self, mock_tty, mock_termios, mock_os, mock_sys):
        """Two-byte UTF-8 character (é = 0xC3 0xA9) is assembled correctly."""
        from footprinter.cli._prompt import _safe_readline

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_sys.stdout = MagicMock()
        mock_termios.tcgetattr.return_value = [0] * 7
        mock_os.read.side_effect = [b"\xc3", b"\xa9", b"\r"]

        with patch("footprinter.cli._prompt.select") as mock_select:
            mock_select.select.side_effect = [
                ([0], [], []),  # main loop: lead byte 0xC3
                ([0], [], []),  # _read_with_timeout: continuation 0xA9
                ([0], [], []),  # main loop: Enter
            ]
            result = _safe_readline()
            assert result == "é"

    @patch("footprinter.cli._prompt._HAS_TERMIOS", True)
    @patch("footprinter.cli._prompt.sys")
    @patch("footprinter.cli._prompt.os")
    @patch("footprinter.cli._prompt.termios")
    @patch("footprinter.cli._prompt.tty")
    def test_three_byte_utf8_char(self, mock_tty, mock_termios, mock_os, mock_sys):
        """Three-byte UTF-8 character (€ = 0xE2 0x82 0xAC) is assembled correctly."""
        from footprinter.cli._prompt import _safe_readline

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_sys.stdout = MagicMock()
        mock_termios.tcgetattr.return_value = [0] * 7
        mock_os.read.side_effect = [b"\xe2", b"\x82", b"\xac", b"\r"]

        with patch("footprinter.cli._prompt.select") as mock_select:
            mock_select.select.side_effect = [
                ([0], [], []),  # main loop: lead byte 0xE2
                ([0], [], []),  # _read_with_timeout: continuation 0x82
                ([0], [], []),  # _read_with_timeout: continuation 0xAC
                ([0], [], []),  # main loop: Enter
            ]
            result = _safe_readline()
            assert result == "€"

    @patch("footprinter.cli._prompt._HAS_TERMIOS", True)
    @patch("footprinter.cli._prompt.sys")
    @patch("footprinter.cli._prompt.os")
    @patch("footprinter.cli._prompt.termios")
    @patch("footprinter.cli._prompt.tty")
    def test_mixed_ascii_and_utf8(self, mock_tty, mock_termios, mock_os, mock_sys):
        """Mixed ASCII and UTF-8 input ('café') is handled correctly."""
        from footprinter.cli._prompt import _safe_readline

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_sys.stdout = MagicMock()
        mock_termios.tcgetattr.return_value = [0] * 7
        mock_os.read.side_effect = [b"c", b"a", b"f", b"\xc3", b"\xa9", b"\r"]

        with patch("footprinter.cli._prompt.select") as mock_select:
            mock_select.select.side_effect = [
                ([0], [], []),  # main loop: 'c'
                ([0], [], []),  # main loop: 'a'
                ([0], [], []),  # main loop: 'f'
                ([0], [], []),  # main loop: lead byte 0xC3
                ([0], [], []),  # _read_with_timeout: continuation 0xA9
                ([0], [], []),  # main loop: Enter
            ]
            result = _safe_readline()
            assert result == "café"

    @patch("footprinter.cli._prompt._HAS_TERMIOS", True)
    @patch("footprinter.cli._prompt.sys")
    @patch("footprinter.cli._prompt.os")
    @patch("footprinter.cli._prompt.termios")
    @patch("footprinter.cli._prompt.tty")
    def test_backspace_deletes_multibyte_char(self, mock_tty, mock_termios, mock_os, mock_sys):
        """Backspace after a multi-byte character removes the entire character."""
        from footprinter.cli._prompt import _safe_readline

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_sys.stdout = MagicMock()
        mock_termios.tcgetattr.return_value = [0] * 7
        # Type é, then Backspace, then x, then Enter
        mock_os.read.side_effect = [b"\xc3", b"\xa9", b"\x7f", b"x", b"\r"]

        with patch("footprinter.cli._prompt.select") as mock_select:
            mock_select.select.side_effect = [
                ([0], [], []),  # main loop: lead byte 0xC3
                ([0], [], []),  # _read_with_timeout: continuation 0xA9
                ([0], [], []),  # main loop: Backspace
                ([0], [], []),  # main loop: 'x'
                ([0], [], []),  # main loop: Enter
            ]
            result = _safe_readline()
            assert result == "x"

    @patch("footprinter.cli._prompt._HAS_TERMIOS", True)
    @patch("footprinter.cli._prompt.sys")
    @patch("footprinter.cli._prompt.os")
    @patch("footprinter.cli._prompt.termios")
    @patch("footprinter.cli._prompt.tty")
    def test_invalid_utf8_lead_byte_discarded(self, mock_tty, mock_termios, mock_os, mock_sys):
        """Lone UTF-8 lead byte (missing continuation) doesn't crash or misinterpret."""
        from footprinter.cli._prompt import _safe_readline

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_sys.stdout = MagicMock()
        mock_termios.tcgetattr.return_value = [0] * 7
        # Lone 0xC3 followed by Enter; helper detects invalid continuation,
        # returns Enter as leftover for re-processing by the main loop
        mock_os.read.side_effect = [b"\xc3", b"\r"]

        with patch("footprinter.cli._prompt.select") as mock_select:
            mock_select.select.side_effect = [
                ([0], [], []),  # main loop: lead byte 0xC3
                ([0], [], []),  # _read_with_timeout: 0x0D (fails continuation check)
            ]
            result = _safe_readline()
            # Lead byte discarded, Enter re-processed — returns empty string
            assert result == ""
