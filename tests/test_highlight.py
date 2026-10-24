from __future__ import annotations

import io
import os
import time
import unittest
from unittest.mock import patch

from minimalcode.highlight import MarkdownHighlighter, strip_ansi

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

RENDERED_SAMPLE = """# MinimalCode README

Use bold, italic, old text and inline code.

- ☑ completed item
> quoted text

| Feature | State |
|---|---|
| highlight | ready |

```python
print("hello")  # a comment
```
"""


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def _render(text: str, stream: io.StringIO, *, color: bool | None = None) -> str:
    highlighter = MarkdownHighlighter(stream, color=color)
    for index in range(0, len(text), 7):
        highlighter.feed(text[index : index + 7])
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
        sample = "```python\nprint('ok')\n```\n"
        for delta in ("`", "`", "`python\nprint('ok')\n", "`", "`", "`\n"):
            highlighter.feed(delta)
        highlighter.finish()
        self.assertEqual(strip_ansi(stream.getvalue()), sample)

    def test_non_tty_output_stays_plain(self) -> None:
        rendered = _render(SAMPLE, io.StringIO())
        self.assertEqual(rendered, SAMPLE)
        self.assertNotIn("\x1b[", rendered)

    def test_deltas_are_visible_before_stream_finishes(self) -> None:
        stream = io.StringIO()
        highlighter = MarkdownHighlighter(stream, color=True)
        visible = ""
        for delta in ("# ", "README", "\n\n", "Hello", " ", "world"):
            highlighter.feed(delta)
            visible += delta
            self.assertEqual(strip_ansi(stream.getvalue()), visible)
        self.assertIn("\x1b[", stream.getvalue())

    def test_rendered_output_is_independent_of_chunk_size(self) -> None:
        sample = (
            "snake_case_name, items[0], 2 * 3, "
            "**bold**, `code`, [docs](https://example.com)\n"
        )
        expected = (
            "snake_case_name, items[0], 2 * 3, "
            "bold, code, docs (https://example.com)\n"
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
        self.assertLess(elapsed, 3.0)

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
        self.assertEqual(strip_ansi(stream.getvalue()), " " * indent + "indented text\n")
        self.assertLess(elapsed, 5.0)

    def test_crlf_and_unclosed_fence_are_preserved(self) -> None:
        sample = "# Title\r\n\r\n```text\r\nplain\r\n"
        rendered = _render(sample, io.StringIO(), color=True)
        self.assertEqual(strip_ansi(rendered), sample)


if __name__ == "__main__":
    unittest.main()
