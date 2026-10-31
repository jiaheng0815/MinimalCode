from __future__ import annotations

import base64
import subprocess
import unittest
from unittest.mock import patch

from minimalcode.clipboard import (
    _decode_base64,
    _linux_image,
    _macos_image,
    _parse_applescript_data,
    _windows_image,
    clipboard_image,
)
from minimalcode.errors import ClipboardError

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _completed(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class ClipboardTests(unittest.TestCase):
    def test_clipboard_image_returns_media_type_and_bytes(self) -> None:
        with (
            patch("minimalcode.clipboard._platform", return_value="win32"),
            patch(
                "minimalcode.clipboard._windows_image",
                return_value=PNG_BYTES,
            ),
        ):
            image = clipboard_image()

        self.assertEqual(image, ("image/png", PNG_BYTES))

    def test_clipboard_image_returns_none_for_an_empty_clipboard(self) -> None:
        with (
            patch("minimalcode.clipboard._platform", return_value="linux"),
            patch("minimalcode.clipboard._linux_image", return_value=b""),
        ):
            self.assertIsNone(clipboard_image())

    def test_clipboard_image_rejects_unsupported_bytes(self) -> None:
        with (
            patch("minimalcode.clipboard._platform", return_value="darwin"),
            patch("minimalcode.clipboard._macos_image", return_value=b"not an image"),
            self.assertRaises(ClipboardError),
        ):
            clipboard_image()

    def test_windows_reader_decodes_the_png_as_base64(self) -> None:
        encoded = base64.b64encode(PNG_BYTES)
        with (
            patch("minimalcode.clipboard.shutil.which", return_value="powershell"),
            patch(
                "minimalcode.clipboard.subprocess.run",
                return_value=_completed(stdout=encoded + b"\r\n"),
            ) as run,
        ):
            data = _windows_image()

        self.assertEqual(data, PNG_BYTES)
        command = run.call_args.args[0]
        self.assertIn("-STA", command)
        self.assertIn("-NonInteractive", command)

    def test_windows_reader_treats_exit_three_as_no_image(self) -> None:
        with (
            patch("minimalcode.clipboard.shutil.which", return_value="powershell"),
            patch(
                "minimalcode.clipboard.subprocess.run",
                return_value=_completed(returncode=3),
            ),
        ):
            self.assertEqual(_windows_image(), b"")

    def test_windows_reader_reports_failures(self) -> None:
        with (
            patch("minimalcode.clipboard.shutil.which", return_value="powershell"),
            patch(
                "minimalcode.clipboard.subprocess.run",
                return_value=_completed(stderr=b"clipboard is busy", returncode=1),
            ),
            self.assertRaisesRegex(ClipboardError, "clipboard is busy"),
        ):
            _windows_image()

    def test_windows_reader_requires_powershell(self) -> None:
        with (
            patch("minimalcode.clipboard.shutil.which", return_value=None),
            self.assertRaisesRegex(ClipboardError, "PowerShell"),
        ):
            _windows_image()

    def test_decode_base64_rejects_garbage(self) -> None:
        with self.assertRaisesRegex(ClipboardError, "invalid image data"):
            _decode_base64(b"not base64!")

    def test_macos_reader_parses_applescript_data(self) -> None:
        raw = f"«data PNGf{PNG_BYTES.hex().upper()}»\n".encode()
        self.assertEqual(_parse_applescript_data(raw), PNG_BYTES)

    def test_macos_reader_returns_empty_when_no_png_is_available(self) -> None:
        with patch(
            "minimalcode.clipboard.subprocess.run",
            return_value=_completed(stderr=b"NO_PNG", returncode=1),
        ):
            self.assertEqual(_macos_image(), b"")

    def test_linux_reader_uses_wl_paste_first(self) -> None:
        with (
            patch(
                "minimalcode.clipboard.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            patch(
                "minimalcode.clipboard.subprocess.run",
                return_value=_completed(stdout=PNG_BYTES),
            ) as run,
        ):
            data = _linux_image()

        self.assertEqual(data, PNG_BYTES)
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/wl-paste")

    def test_linux_reader_requires_a_clipboard_tool(self) -> None:
        with (
            patch("minimalcode.clipboard.shutil.which", return_value=None),
            self.assertRaisesRegex(ClipboardError, "wl-paste"),
        ):
            _linux_image()


if __name__ == "__main__":
    unittest.main()
