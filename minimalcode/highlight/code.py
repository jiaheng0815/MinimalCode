"""Fenced-code token highlighting for the Markdown renderer."""

from __future__ import annotations

import re

_NUMBER_RE = re.compile(
    r"(?<![\w.])-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?!\w)"
)
# Kebab-case names (CSS properties, HTML attributes) and the shell ``$VAR`` form
# share a style because the tokenizer has no per-language grammar to tell them
# apart; identifiers with a single dash would otherwise read as names.
_VARIABLE_RE = re.compile(r"\$(?:\w+:)?\w+|\b[A-Za-z_]\w*-\w+\b")
# One merged set for every language the agent prints. Detecting the language per
# fence would add grammars and a dependency; a shared set only miscolors a word
# that means something else in another language, which is cosmetic.
_KEYWORDS = {
    "and",
    "as",
    "async",
    "await",
    "begin",
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "def",
    "do",
    "else",
    "elseif",
    "end",
    "enum",
    "except",
    "export",
    "False",
    "false",
    "finally",
    "for",
    "foreach",
    "from",
    "function",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "let",
    "match",
    "new",
    "None",
    "not",
    "null",
    "or",
    "param",
    "pass",
    "process",
    "raise",
    "return",
    "switch",
    "throw",
    "True",
    "true",
    "try",
    "undefined",
    "using",
    "var",
    "while",
    "with",
    "yield",
}
_KEYWORD_RE = re.compile(r"\b(?:" + "|".join(sorted(_KEYWORDS)) + r")\b")


class CodeHighlightMixin:
    """Line-oriented code tokenizer mixed into the Markdown renderer.

    The mixin expects the host to provide ``palette``, ``_pending``,
    ``_cursor``, ``_advance``, ``_append``, ``_emit``, ``_end_line``, and the
    ``_code_*`` state initialized by ``MarkdownHighlighter``.
    """

    palette: dict[str, str]
    _pending: str
    _cursor: int
    _code_comment: bool
    _code_quote: str | None
    _code_escape: bool
    _code_word: list[str]
    _code_last: str
    _line_kind: str
    _at_line_start: bool
    _in_fence: bool
    _fence_char: str
    _fence_length: int

    def _advance(self, count: int) -> None:
        raise NotImplementedError

    def _append(
        self,
        segments: list[tuple[str | None, list[str]]],
        text: str,
        style: str | None,
    ) -> None:
        raise NotImplementedError

    def _emit(
        self,
        segments: list[tuple[str | None, list[str]]],
        end: int,
        style: str | None,
    ) -> None:
        raise NotImplementedError

    def _end_line(self) -> None:
        raise NotImplementedError

    def _consume_code_line_start(self, *, final: bool) -> bool:
        """Decide whether the current line closes the fence or starts code."""
        text = self._pending
        start = self._cursor
        line_end = text.find("\n", start)
        line = text[start:] if line_end < 0 else text[start:line_end]
        # The opening side accepts any indentation (models nest fences inside
        # list items), so the closing side must accept it too. Otherwise an
        # indented fence never closes and every later heading, list, or table
        # is rendered as code.
        marker = re.match(rf"^[ \t]*({re.escape(self._fence_char)}+)(.*)$", line)
        if marker:
            marker_text = marker.group(1)
            remainder = marker.group(2).strip()
            if (
                len(marker_text) >= self._fence_length
                and not remainder
                and (line_end >= 0 or final)
            ):
                consumed = len(text) if line_end < 0 else line_end + 1
                # Hide the closing fence too, so no stray ``` follows the code.
                self._advance(consumed - self._cursor)
                self._in_fence = False
                self._end_line()
                return True
            if line_end < 0 and not remainder and not final:
                return False
        elif line_end < 0 and not final and not line.strip(" \t"):
            # Only indentation has arrived so far; the line may still become a
            # closing fence, so wait for the next non-space byte instead of
            # committing the indentation as code content.
            return False

        self._line_kind = "code"
        self._at_line_start = False
        self._reset_code_line()
        return True

    def _consume_code(
        self,
        segments: list[tuple[str | None, list[str]]],
    ) -> None:
        """Consume one character of a fenced code line."""
        text = self._pending
        start = self._cursor
        char = text[start]
        if text.startswith("\r\n", start):
            self._flush_code_word(segments)
            self._emit(segments, start + 2, None)
            self._end_line()
            return
        if char == "\n":
            self._flush_code_word(segments)
            self._emit(segments, start + 1, None)
            self._end_line()
            return

        if self._code_comment:
            self._append(segments, char, self.palette["code_comment"])
            self._advance(1)
            self._code_last = char
            return

        if self._code_quote is not None:
            self._append(segments, char, self.palette["code_string"])
            self._advance(1)
            if self._code_escape:
                self._code_escape = False
            elif char == "\\":
                self._code_escape = True
            elif char == self._code_quote:
                self._code_quote = None
            self._code_last = char
            return

        # '#' starts a comment only at a token boundary; '#include' after
        # whitespace qualifies, but a URL fragment should not swallow the line.
        comment_start = (
            char == "#" and (not self._code_last or self._code_last.isspace())
        ) or text.startswith("//", start)
        if comment_start:
            self._flush_code_word(segments)
            token = "#" if char == "#" else "//"
            self._append(segments, token, self.palette["code_comment"])
            self._advance(len(token))
            self._code_comment = True
            self._code_last = token[-1]
            return

        if char in "\"'`":
            self._flush_code_word(segments)
            self._append(segments, char, self.palette["code_string"])
            self._advance(1)
            self._code_quote = char
            self._code_escape = False
            self._code_last = char
            return

        if char.isalnum() or char in "_$:.-":
            # Buffer identifiers so the whole token can be classified at once.
            # The punctuation is part of the token as well: keeping `a.b`,
            # `mod:Class`, and `x-2` together stops them being split into name and
            # number fragments that get two different colors mid-identifier.
            self._code_word.append(char)
            self._advance(1)
            self._code_last = char
            return

        self._flush_code_word(segments)
        self._append(segments, char, None)
        self._advance(1)
        self._code_last = char

    def _flush_code_word(
        self,
        segments: list[tuple[str | None, list[str]]],
    ) -> None:
        """Classify and emit the buffered identifier or number token."""
        word = "".join(self._code_word)
        self._code_word.clear()
        if not word:
            return
        # `fullmatch` rather than a substring test: the buffer already holds one
        # complete token, so a partial match would style `1.2.3` or `a-2` as a
        # number or variable and leave the rest of the token uncolored.
        if _KEYWORD_RE.fullmatch(word):
            style = self.palette["code_keyword"]
        elif _NUMBER_RE.fullmatch(word):
            style = self.palette["code_number"]
        elif _VARIABLE_RE.fullmatch(word):
            style = self.palette["code_variable"]
        else:
            style = self.palette["code_name"]
        self._append(segments, word, style)

    def _reset_code_line(self) -> None:
        """Reset per-line string, comment, and token state."""
        self._code_comment = False
        self._code_quote = None
        self._code_escape = False
        self._code_last = ""
        self._code_word.clear()
