"""Safe prompt wrappers with Escape key and Ctrl+C/Ctrl+D support.

Wraps Rich's ``Prompt`` and ``Confirm`` to detect Escape (via raw terminal)
and convert ``KeyboardInterrupt``/``EOFError`` into ``PromptCancelled``.

``PromptCancelled`` inherits from ``BaseException`` so it passes through the
~9 ``except Exception`` broad catches in the setup wizard without being
swallowed.
"""

import os
import sys

from rich.prompt import Confirm, Prompt

try:
    import select
    import termios
    import tty

    _HAS_TERMIOS = True
except ImportError:
    # Windows or other non-POSIX — fall back to input()
    select = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]
    _HAS_TERMIOS = False


class PromptCancelled(BaseException):
    """Raised when the user presses Escape, Ctrl+C, or Ctrl+D at a prompt.

    Inherits from ``BaseException`` (not ``Exception``) so it propagates
    through ``except Exception`` blocks in wizard steps.
    """


_ESCAPE_TIMEOUT = 0.05  # 50ms to distinguish bare Escape from escape sequences


def _read_utf8_char(fd: int, lead: int) -> tuple[str, bytes]:
    """Read a complete UTF-8 character given its lead byte.

    Determines the expected continuation byte count from the lead byte
    pattern, reads that many bytes via ``_read_with_timeout``, and decodes.

    Returns ``(char, leftover)`` where *char* is the decoded character
    (empty string on failure) and *leftover* is a non-continuation byte
    that was consumed but could not be used — the caller must re-process it.
    """
    if 0xC0 <= lead <= 0xDF:
        n = 1
    elif 0xE0 <= lead <= 0xEF:
        n = 2
    elif 0xF0 <= lead <= 0xF7:
        n = 3
    else:
        return ("", b"")

    raw = bytes([lead])
    for _ in range(n):
        b = _read_with_timeout(fd, _ESCAPE_TIMEOUT)
        if not b:
            return ("", b"")
        if not (0x80 <= b[0] <= 0xBF):
            return ("", b)  # Return consumed byte for re-processing
        raw += b

    decoded = raw.decode("utf-8", errors="replace")
    if "\ufffd" in decoded:
        return ("", b"")
    return (decoded, b"")


def _read_with_timeout(fd: int, timeout: float) -> bytes:
    """Read a single byte from *fd* with a timeout via ``select``."""
    ready, _, _ = select.select([fd], [], [], timeout)
    if ready:
        return os.read(fd, 1)
    return b""


def _safe_readline(password: bool = False) -> str:
    """Read a line from stdin with Escape/Ctrl+C/Ctrl+D detection.

    Uses raw terminal mode on POSIX systems. Falls back to ``input()``
    when termios is unavailable (Windows, non-TTY, piped input).
    """
    if not _HAS_TERMIOS or not sys.stdin.isatty():
        try:
            return input()
        except (KeyboardInterrupt, EOFError) as exc:
            raise PromptCancelled(str(exc)) from exc

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    buf: list[str] = []

    try:
        tty.setraw(fd)
        pending = b""

        while True:
            if pending:
                ch = pending
                pending = b""
            else:
                ready, _, _ = select.select([fd], [], [])
                if not ready:
                    continue
                ch = os.read(fd, 1)

            if not ch:
                raise PromptCancelled("EOF")

            byte = ch[0]

            if byte == 0x1B:  # Escape
                # Check if more bytes follow (escape sequence vs bare Escape)
                follow = _read_with_timeout(fd, _ESCAPE_TIMEOUT)
                if not follow:
                    # Bare Escape — user pressed Esc
                    raise PromptCancelled("Escape")
                # Escape sequence — consume and ignore
                if follow == b"[":
                    # CSI sequence (\x1b[...) — consume until alpha terminator
                    while True:
                        seq_byte = _read_with_timeout(fd, _ESCAPE_TIMEOUT)
                        if not seq_byte or (0x40 <= seq_byte[0] <= 0x7E):
                            break
                elif follow == b"O":
                    # SS3 sequence (\x1bO..., e.g., F1–F4) — consume terminator
                    _read_with_timeout(fd, _ESCAPE_TIMEOUT)
                continue

            if byte == 0x03:  # Ctrl+C
                raise PromptCancelled("Ctrl+C")

            if byte == 0x04:  # Ctrl+D
                raise PromptCancelled("Ctrl+D")

            if byte in (0x0D, 0x0A):  # Enter
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(buf)

            if byte in (0x7F, 0x08):  # Backspace / Delete
                if buf:
                    buf.pop()
                    if not password:
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                continue

            if byte >= 0x80:  # High byte (UTF-8 lead or stray continuation)
                char, leftover = _read_utf8_char(fd, byte)
                if char:
                    buf.append(char)
                    if not password:
                        sys.stdout.write(char)
                        sys.stdout.flush()
                if leftover:
                    pending = leftover
            elif byte >= 0x20:  # Printable ASCII character
                buf.append(chr(byte))
                if not password:
                    sys.stdout.write(chr(byte))
                    sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


class SafePrompt(Prompt):
    """Rich Prompt subclass with Escape key and interrupt handling."""

    @classmethod
    def get_input(
        cls,
        console,  # noqa: ANN001
        prompt,  # noqa: ANN001
        password: bool = False,
        stream=None,  # noqa: ANN001
    ) -> str:
        if stream is not None:
            try:
                return super().get_input(console, prompt, password=password, stream=stream)
            except (KeyboardInterrupt, EOFError) as exc:
                raise PromptCancelled(str(exc)) from exc

        console.print(prompt, end="")
        try:
            return _safe_readline(password=password)
        except PromptCancelled:
            console.print()  # Newline after the prompt
            raise


class SafeConfirm(Confirm):
    """Rich Confirm subclass with Escape key and interrupt handling."""

    @classmethod
    def get_input(
        cls,
        console,  # noqa: ANN001
        prompt,  # noqa: ANN001
        password: bool = False,
        stream=None,  # noqa: ANN001
    ) -> str:
        if stream is not None:
            try:
                return super().get_input(console, prompt, password=password, stream=stream)
            except (KeyboardInterrupt, EOFError) as exc:
                raise PromptCancelled(str(exc)) from exc

        console.print(prompt, end="")
        try:
            return _safe_readline(password=password)
        except PromptCancelled:
            console.print()  # Newline after the prompt
            raise
