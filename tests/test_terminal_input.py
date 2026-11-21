"""Regression tests for the multiline terminal prompt."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import nullcontext, redirect_stdout
from unittest.mock import patch

from minimalcode.terminal_input import InputBuffer, _Keys, layout, read_message


class InputBufferTests(unittest.TestCase):
    def test_backspace_joins_the_previous_logical_line(self) -> None:
        buffer = InputBuffer("first line\n", cursor=len("first line\n"))

        self.assertTrue(buffer.backspace())

        self.assertEqual(buffer.text, "first line")
        self.assertEqual(buffer.cursor, len("first line"))

    def test_backspace_crosses_a_visual_wrap(self) -> None:
        buffer = InputBuffer("abcdefgh", cursor=len("abcdefgh"))

        for _ in range(4):
            self.assertTrue(buffer.backspace())

        self.assertEqual(buffer.text, "abcd")
        positioned = layout(
            buffer,
            5,
            prompt="",
            continuation="",
        )
        self.assertEqual(positioned.rows, ["abcd"])
        self.assertEqual((positioned.cursor_row, positioned.cursor_column), (0, 4))

    def test_home_and_end_stay_on_the_cursor_line(self) -> None:
        buffer = InputBuffer("one\ntwo", cursor=len("one\ntw"))

        buffer.move_home()
        self.assertEqual(buffer.cursor, len("one\n"))

        buffer.move_end()
        self.assertEqual(buffer.cursor, len("one\ntwo"))


class LayoutTests(unittest.TestCase):
    def test_wide_characters_wrap_without_splitting_a_cell(self) -> None:
        buffer = InputBuffer("你好世界", cursor=len("你好世界"))

        positioned = layout(
            buffer,
            5,
            prompt="",
            continuation="",
        )

        self.assertEqual(positioned.rows, ["你好", "世界"])
        self.assertEqual((positioned.cursor_row, positioned.cursor_column), (1, 4))

    def test_cursor_after_a_logical_newline_uses_the_continuation_prompt(self) -> None:
        buffer = InputBuffer("first\n", cursor=len("first\n"))

        positioned = layout(
            buffer,
            80,
            prompt="> ",
            continuation="... ",
        )

        self.assertEqual(positioned.rows, ["> first", "... "])
        self.assertEqual((positioned.cursor_row, positioned.cursor_column), (1, 4))


class KeyReaderTests(unittest.TestCase):
    def test_line_feed_from_ctrl_enter_stays_a_newline(self) -> None:
        keys = _Keys()
        source = iter("\n")

        with patch.object(keys, "_read_char", side_effect=lambda: next(source)):
            kind, value = keys.read()

        self.assertEqual((kind, value), ("newline", ""))

    def test_modified_enter_sequences_insert_a_newline(self) -> None:
        for sequence in ("\x1b[13;5u", "\x1b[13;2u", "\x1b[27;5;13~"):
            with self.subTest(sequence=sequence):
                keys = _Keys()
                source = iter(sequence)
                with patch.object(
                    keys,
                    "_read_char",
                    side_effect=lambda source=source: next(source),
                ):
                    kind, value = keys.read()
                self.assertEqual((kind, value), ("newline", ""))

    def test_windows_enter_uses_the_held_modifier(self) -> None:
        modifiers = {0x11: True, 0x10: False}
        with (
            patch("minimalcode.terminal_input.os.name", "nt"),
            patch.object(
                _Keys,
                "_read_char",
                return_value="\r",
            ),
            patch(
                "minimalcode.terminal_input._windows_modifier_down",
                side_effect=lambda key: modifiers[key],
            ),
        ):
            kind, value = _Keys().read()

        self.assertEqual((kind, value), ("newline", ""))

    def test_enter_with_queued_paste_text_becomes_a_newline(self) -> None:
        keys = _Keys()
        source = iter("\r\nx")
        with (
            patch.object(keys, "_read_char", side_effect=lambda: next(source)),
            patch.object(keys, "input_ready", return_value=True),
            patch(
                "minimalcode.terminal_input._windows_modifier_down",
                return_value=False,
            ),
        ):
            kind, value = keys.read()
            following = keys.read()

        self.assertEqual((kind, value), ("newline", ""))
        self.assertEqual(following, ("text", "x"))

    def test_enter_without_queued_input_submits(self) -> None:
        keys = _Keys()
        with (
            patch.object(keys, "_read_char", return_value="\r"),
            patch.object(keys, "input_ready", return_value=False),
            patch(
                "minimalcode.terminal_input._windows_modifier_down",
                return_value=False,
            ),
        ):
            kind, value = keys.read()

        self.assertEqual((kind, value), ("enter", ""))

    def test_bracketed_paste_removes_both_markers(self) -> None:
        keys = _Keys()
        source = iter("\x1b[200~first\nsecond\x1b[201~")

        with patch.object(keys, "_read_char", side_effect=lambda: next(source)):
            kind, value = keys.read()

        self.assertEqual(kind, "paste")
        self.assertEqual(value, "first\nsecond")


class EditorLoopTests(unittest.TestCase):
    def test_ctrl_enter_and_plain_paste_keep_multiple_lines(self) -> None:
        events = [
            *[("text", char) for char in "first"],
            ("newline", ""),
            *[("text", char) for char in "second"],
            ("enter", ""),
        ]
        with (
            patch(
                "minimalcode.terminal_input.os.get_terminal_size",
                return_value=os.terminal_size((80, 10)),
            ),
            patch(
                "minimalcode.terminal_input._raw_terminal",
                return_value=nullcontext(),
            ),
            patch(
                "minimalcode.terminal_input._Keys.read",
                side_effect=events,
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(read_message(), "first\nsecond")

    def test_backspace_walks_back_across_a_visual_wrap(self) -> None:
        events = [
            *[("text", char) for char in "abcdefgh"],
            *[("backspace", "") for _ in range(4)],
            ("enter", ""),
        ]
        with (
            patch(
                "minimalcode.terminal_input.os.get_terminal_size",
                return_value=os.terminal_size((5, 10)),
            ),
            patch(
                "minimalcode.terminal_input._raw_terminal",
                return_value=nullcontext(),
            ),
            patch(
                "minimalcode.terminal_input._Keys.read",
                side_effect=events,
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(read_message(), "abcd")


if __name__ == "__main__":
    unittest.main()
