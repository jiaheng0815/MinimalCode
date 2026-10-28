from __future__ import annotations

import io
import os
import time
import unittest
from unittest.mock import patch

from minimalcode.highlight import (
    MarkdownHighlighter,
    sanitize_terminal_text,
    strip_ansi,
)

SAMPLE = """# MinimalCode README

Use **bold**, *italic*, ~~old~~ text and `inline code`.

- [x] completed item
> quoted text

| Feature | State |
|---|---|
| highlight | ready |

```python
print("hello")  # a comment
```
"""

RENDERED_SAMPLE = """MinimalCode README

Use bold, italic, old text and inline code.

- ☑ completed item
> quoted text

| Feature | State |
| highlight | ready |

print("hello")  # a comment
"""


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def _render(
    text: str,
    stream: io.StringIO,
    *,
    color: bool | None = None,
    size: int = 7,
) -> str:
    highlighter = MarkdownHighlighter(stream, color=color)
    for index in range(0, len(text), size):
        highlighter.feed(text[index : index + size])
    highlighter.finish()
    return stream.getvalue()


class HighlightTests(unittest.TestCase):
    def test_renders_markdown_without_inline_delimiters(self) -> None:
        rendered = _render(SAMPLE, io.StringIO(), color=True)
        self.assertIn("\x1b[", rendered)
        self.assertEqual(strip_ansi(rendered), RENDERED_SAMPLE)

    def test_unterminated_bold_renders_immediately(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        highlighter.feed("This is **incomplete bold")
        self.assertEqual(
            strip_ansi(stream.getvalue()),
            "This is incomplete bold",
        )
        self.assertIn("\x1b[1;", stream.getvalue())

    def test_plain_text_metacharacters_are_preserved(self) -> None:
        samples = (
            "snake_case_name here",
            "call items[0] then done",
            "2 * 3 * 4 is twelve",
            "see [the docs] for more",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                rendered = _render(sample, io.StringIO(), color=True)
                self.assertEqual(strip_ansi(rendered), sample)

    def test_brackets_are_preserved_across_deltas(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        for delta in ("call items[", "0", "]", " then done"):
            highlighter.feed(delta)
        highlighter.finish()
        self.assertEqual(
            strip_ansi(stream.getvalue()),
            "call items[0] then done",
        )

    def test_complete_link_renders_label_and_url(self) -> None:
        rendered = _render(
            "see [the docs](https://example.com) now",
            io.StringIO(),
            color=True,
        )
        self.assertEqual(strip_ansi(rendered), "see the docs (https://example.com) now")

    def test_link_url_is_rendered_across_deltas(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        for delta in ("[docs](", "https://exa", "mple.com", ")", "!"):
            highlighter.feed(delta)
        highlighter.finish()
        self.assertEqual(
            strip_ansi(stream.getvalue()),
            "docs (https://example.com)!",
        )

    def test_bold_markers_can_be_split_across_deltas(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        visible: list[str] = []
        for delta in ("*", "*bold*", "*"):
            highlighter.feed(delta)
            visible.append(strip_ansi(stream.getvalue()))
        self.assertEqual(visible, ["", "bold", "bold"])

    def test_fence_markers_can_be_split_across_deltas(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        sample = "print('ok')\n"
        for delta in ("`", "`", "`python\nprint('ok')\n", "`", "`", "`\n"):
            highlighter.feed(delta)
        highlighter.finish()
        self.assertEqual(strip_ansi(stream.getvalue()), sample)

    def test_terminal_control_sequences_are_removed(self) -> None:
        text = "safe\x1b[2J\x1b]0;title\x07 still\x1b[31mred\x08\nnext"
        self.assertEqual(sanitize_terminal_text(text), "safe stillred\nnext")

    def test_split_escape_sequences_do_not_leak(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        for delta in ("safe\x1b", "[2", "Jtext\n"):
            highlighter.feed(delta)
        highlighter.finish()
        rendered = strip_ansi(stream.getvalue())
        self.assertEqual(rendered, "safetext\n")

    def test_trailing_lone_carriage_return_is_dropped(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        highlighter.feed("line\r")
        highlighter.feed("\nnext")
        highlighter.finish()
        self.assertEqual(strip_ansi(stream.getvalue()), "line\r\nnext")

    def test_non_tty_output_stays_plain(self) -> None:
        rendered = _render(SAMPLE, io.StringIO())
        self.assertEqual(rendered, SAMPLE)
        self.assertNotIn("\x1b[", rendered)

    def test_deltas_are_visible_before_stream_finishes(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        visible: list[str] = []
        for delta in ("# ", "README", "\n\n", "Hello", " ", "world"):
            highlighter.feed(delta)
            visible.append(strip_ansi(stream.getvalue()))
        # The heading marker is consumed, so nothing is printed until the
        # heading text itself arrives; later deltas render without waiting.
        self.assertEqual(
            visible,
            [
                "",
                "README",
                "README\n\n",
                "README\n\nHello",
                "README\n\nHello ",
                "README\n\nHello world",
            ],
        )
        self.assertIn("\x1b[", stream.getvalue())

    def test_rendered_output_is_independent_of_chunk_size(self) -> None:
        sample = (
            "snake_case_name, items[0], 2 * 3, "
            "**bold**, `code`, [docs](https://example.com)\n"
        )
        expected = (
            "snake_case_name, items[0], 2 * 3, bold, code, docs (https://example.com)\n"
        )
        outputs = []
        for size in (1, 2, 3, 7, len(sample)):
            stream = io.StringIO()
            highlighter = MarkdownHighlighter(stream, color=True)
            for index in range(0, len(sample), size):
                highlighter.feed(sample[index : index + size])
            highlighter.finish()
            outputs.append(strip_ansi(stream.getvalue()))
        self.assertEqual(outputs, [expected] * len(outputs))

    def test_large_single_delta_stays_linear_enough(self) -> None:
        sample = "word " * 90_000
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        started = time.perf_counter()
        highlighter.feed(sample)
        highlighter.finish()
        elapsed = time.perf_counter() - started
        self.assertEqual(strip_ansi(stream.getvalue()), sample)
        # A quadratic feed of this size takes minutes, so the bound only has to
        # clear a slow shared runner: the Intel macOS image needed 3.9s here.
        self.assertLess(elapsed, 15.0)

    def test_no_color_disables_tty_styles(self) -> None:
        with patch.dict(os.environ, {"NO_COLOR": ""}):
            rendered = _render(SAMPLE, TtyBuffer())
        self.assertEqual(rendered, SAMPLE)

    def test_deeply_indented_line_stays_linear(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        indent = 40_000
        started = time.perf_counter()
        for _ in range(indent):
            highlighter.feed(" ")
        highlighter.feed("indented text\n")
        highlighter.finish()
        elapsed = time.perf_counter() - started
        self.assertEqual(
            strip_ansi(stream.getvalue()), " " * indent + "indented text\n"
        )
        self.assertLess(elapsed, 5.0)

    def test_crlf_and_unclosed_fence_are_preserved(self) -> None:
        sample = "# Title\r\n\r\n```text\r\nplain\r\n"
        rendered = _render(sample, io.StringIO(), color=True)
        self.assertEqual(strip_ansi(rendered), "Title\r\n\r\nplain\r\n")

    def test_trailing_backslash_is_preserved(self) -> None:
        for size in (1, 2, 3, len("path\\")):
            with self.subTest(size=size):
                sample = "path\\"
                stream = io.StringIO()
                highlighter = MarkdownHighlighter(stream, color=True)
                for index in range(0, len(sample), size):
                    highlighter.feed(sample[index : index + size])
                highlighter.finish()
                self.assertEqual(strip_ansi(stream.getvalue()), sample)

    def test_fence_lines_are_hidden(self) -> None:
        sample = "before\n\n```python\nprint(1)\nprint(2)\n```\n\nafter\n"
        expected = "before\n\nprint(1)\nprint(2)\n\nafter\n"
        for size in (1, 2, 3, 7, len(sample)):
            with self.subTest(size=size):
                rendered = _render(sample, io.StringIO(), color=True, size=size)
                self.assertEqual(strip_ansi(rendered), expected)
                self.assertNotIn("```", rendered)

    def test_tilde_fence_lines_are_hidden(self) -> None:
        sample = "~~~js\nlet a = 1;\n~~~\n"
        for size in (1, 2, 3, len(sample)):
            with self.subTest(size=size):
                rendered = _render(sample, io.StringIO(), color=True, size=size)
                self.assertEqual(strip_ansi(rendered), "let a = 1;\n")
                self.assertNotIn("~~~", rendered)

    def test_table_separator_rows_are_filtered(self) -> None:
        cases = {
            "| Feature | State |\n| --- | --- |\n| a | b |\n": (
                "| Feature | State |\n| a | b |\n"
            ),
            "| a | b |\n|---|---|\n| 1 | 2 |\n": "| a | b |\n| 1 | 2 |\n",
            "| a | b |\n|:--|--:|\n| 1 | 2 |\n": "| a | b |\n| 1 | 2 |\n",
            "| a | b |\n--- | ---\n| 1 | 2 |\n": "| a | b |\n| 1 | 2 |\n",
            "  | a | b |\n  | --- | --- |\n  | 1 | 2 |\n": (
                "  | a | b |\n  | 1 | 2 |\n"
            ),
        }
        for sample, expected in cases.items():
            for size in (1, 2, 3, 7, len(sample)):
                with self.subTest(sample=sample, size=size):
                    rendered = _render(sample, io.StringIO(), color=True, size=size)
                    self.assertEqual(strip_ansi(rendered), expected)
                    self.assertNotIn("---", rendered)

    def test_table_rows_with_dashes_in_cells_are_preserved(self) -> None:
        sample = "| a | b |\n| --- | --- |\n| x-y | --- done --- |\n"
        expected = "| a | b |\n| x-y | --- done --- |\n"
        for size in (1, 3, 7, len(sample)):
            with self.subTest(size=size):
                rendered = _render(sample, io.StringIO(), color=True, size=size)
                self.assertEqual(strip_ansi(rendered), expected)

    def test_indented_fence_closes_so_later_headings_render_as_markdown(self) -> None:
        from minimalcode.highlight.palette import _PALETTES

        palette = _PALETTES["dark"]
        for indent in (0, 1, 2, 3, 4, 6):
            pad = " " * indent
            sample = (
                f"1. step\n\n{pad}```python\n{pad}print(1)\n{pad}```\n\n## 二级标题\n"
            )
            expected = f"1. step\n\n{pad}print(1)\n\n二级标题\n"
            for size in (1, 2, 3, 7, len(sample)):
                with self.subTest(indent=indent, size=size):
                    rendered = _render(sample, io.StringIO(), color=True, size=size)
                    self.assertEqual(strip_ansi(rendered), expected)
                    self.assertIn(palette["h2"], rendered)

    def test_indented_marker_with_trailing_text_does_not_close_a_fence(self) -> None:
        from minimalcode.highlight.palette import _PALETTES

        sample = "```text\n    ```not a closing fence\n```\n## After\n"
        rendered = _render(sample, io.StringIO(), color=True)
        self.assertEqual(strip_ansi(rendered), "    ```not a closing fence\nAfter\n")
        # The fence opened at the first line still ends at the bare marker, so
        # the heading after it is styled instead of staying code.
        self.assertIn(_PALETTES["dark"]["h2"], rendered)

    def test_thematic_breaks_are_preserved(self) -> None:
        for sample in ("---\n", "***\n", "___\n", "- - -\n"):
            with self.subTest(sample=sample):
                for size in (1, 2, len(sample)):
                    stream = io.StringIO()
                    highlighter = MarkdownHighlighter(stream, color=True)
                    for index in range(0, len(sample), size):
                        highlighter.feed(sample[index : index + size])
                    highlighter.finish()
                    self.assertEqual(strip_ansi(stream.getvalue()), sample)

    def test_bullet_lists_are_not_treated_as_rules(self) -> None:
        sample = "- first\n- second\n"
        rendered = _render(sample, io.StringIO(), color=True)
        self.assertEqual(strip_ansi(rendered), sample)

    def test_heading_marker_is_not_printed(self) -> None:
        from minimalcode.highlight.palette import _PALETTES

        palette = _PALETTES["dark"]
        for level in (1, 2, 3, 4, 5, 6):
            sample = f"{'#' * level} 二级标题\nnext line\n"
            for size in range(1, len(sample) + 1):
                with self.subTest(level=level, size=size):
                    rendered = _render(sample, io.StringIO(), color=True, size=size)
                    # The `#` run and its separating space are consumed, so the
                    # heading reads as plain styled text like `**bold**` does.
                    self.assertEqual(strip_ansi(rendered), "二级标题\nnext line\n")
                    self.assertIn(palette[f"h{level}"], rendered)
                    self.assertNotIn("#", rendered)

    def test_heading_marker_alone_at_end_of_stream_is_preserved(self) -> None:
        for sample in ("#", "##", "######"):
            for size in range(1, len(sample) + 1):
                with self.subTest(sample=sample, size=size):
                    stream = io.StringIO()
                    highlighter = MarkdownHighlighter(stream, color=True)
                    for index in range(0, len(sample), size):
                        highlighter.feed(sample[index : index + size])
                    highlighter.finish()
                    self.assertEqual(strip_ansi(stream.getvalue()), sample)

    def test_heading_level_does_not_depend_on_delta_size(self) -> None:
        from minimalcode.highlight.palette import _PALETTES

        palette = _PALETTES["dark"]
        for level in (1, 2, 3, 6):
            sample = f"{'#' * level} Heading\n"
            expected_style = palette[f"h{level}"]
            for size in (1, 2, 3, level, len(sample)):
                with self.subTest(level=level, size=size):
                    rendered = _render(sample, io.StringIO(), color=True, size=size)
                    self.assertEqual(strip_ansi(rendered), "Heading\n")
                    self.assertIn(expected_style, rendered)
                    other_styles = {
                        style
                        for key, style in palette.items()
                        if key.startswith("h") and style != expected_style
                    }
                    self.assertFalse(
                        any(style in rendered for style in other_styles),
                        rendered,
                    )


if __name__ == "__main__":
    unittest.main()
