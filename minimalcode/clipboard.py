"""Read an image from the system clipboard without requiring a file on disk."""

from __future__ import annotations

import base64
import binascii
import re
import shutil
import subprocess
import sys

from .errors import ClipboardError
from .vision import SUPPORTED_IMAGE_FORMATS, image_media_type

_TIMEOUT_SECONDS = 10.0
_MAX_IMAGE_BYTES = 64 * 1024 * 1024

# Windows PowerShell has no direct "write bytes to stdout" primitive that
# survives the console encoding, so the PNG is emitted as base64.
_WINDOWS_SCRIPT = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$image = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $image) { exit 3 }
$stream = New-Object System.IO.MemoryStream
try {
    $image.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    [Convert]::ToBase64String($stream.ToArray())
} finally {
    $stream.Dispose()
    $image.Dispose()
}
"""

# AppleScript renders pasted raw data as «data PNGf<hex>».
_MACOS_SCRIPT = "get the clipboard as «class PNGf»"


def _platform() -> str:
    return sys.platform


def clipboard_image() -> tuple[str, bytes] | None:
    """Return ``(media_type, data)`` for the clipboard image, or None.

    The caller never has to save a file first. A clipboard without an image is
    not an error; a clipboard read failure raises :class:`ClipboardError`.
    """
    if _platform() == "win32":
        data = _windows_image()
    elif _platform() == "darwin":
        data = _macos_image()
    else:
        data = _linux_image()
    if not data:
        return None
    if len(data) > _MAX_IMAGE_BYTES:
        raise ClipboardError(
            f"clipboard image is too large: {len(data)} bytes exceeds "
            f"{_MAX_IMAGE_BYTES}"
        )
    media_type = image_media_type(data)
    if media_type is None:
        raise ClipboardError(f"clipboard image must be {SUPPORTED_IMAGE_FORMATS}")
    return media_type, data


def _run(command: list[str], *, label: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ClipboardError(
            f"{label} clipboard reader is not available: {command[0]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClipboardError(f"{label} clipboard read timed out") from exc
    except OSError as exc:
        raise ClipboardError(f"{label} clipboard read failed: {exc}") from exc


def _first_error_line(completed: subprocess.CompletedProcess[bytes]) -> str:
    text = completed.stderr.decode("utf-8", errors="replace").strip()
    if not text:
        return f"exit code {completed.returncode}"
    return text.splitlines()[0][:200]


def _windows_image() -> bytes:
    executable = None
    for name in ("powershell", "pwsh"):
        executable = shutil.which(name)
        if executable:
            break
    if executable is None:
        raise ClipboardError("clipboard image paste needs PowerShell on Windows")
    completed = _run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-STA",
            "-NonInteractive",
            "-Command",
            _WINDOWS_SCRIPT,
        ],
        label="Windows",
    )
    if completed.returncode == 3:
        return b""
    if completed.returncode != 0:
        raise ClipboardError(
            f"Windows clipboard read failed: {_first_error_line(completed)}"
        )
    return _decode_base64(completed.stdout)


def _decode_base64(data: bytes) -> bytes:
    compact = b"".join(data.split())
    if not compact:
        return b""
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ClipboardError("clipboard reader returned invalid image data") from exc


def _macos_image() -> bytes:
    completed = _run(["osascript", "-e", _MACOS_SCRIPT], label="macOS")
    if completed.returncode != 0:
        return b""
    return _parse_applescript_data(completed.stdout)


def _parse_applescript_data(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="replace")
    match = re.search(r"PNGf([0-9A-Fa-f]+)", text)
    if match is None:
        return b""
    try:
        return bytes.fromhex(match.group(1))
    except ValueError as exc:
        raise ClipboardError("macOS clipboard returned invalid image data") from exc


def _linux_image() -> bytes:
    candidates = (
        ("wl-paste", ["--type", "image/png"]),
        ("xclip", ["-selection", "clipboard", "-t", "image/png", "-o"]),
    )
    found = False
    for name, arguments in candidates:
        executable = shutil.which(name)
        if executable is None:
            continue
        found = True
        try:
            completed = _run([executable, *arguments], label=name)
        except ClipboardError:
            # A missing Wayland session is common on X11; try the next reader.
            continue
        if completed.returncode == 0 and completed.stdout:
            return completed.stdout
    if not found:
        raise ClipboardError(
            "clipboard image paste needs wl-paste (Wayland) or xclip (X11)"
        )
    return b""


__all__ = ["clipboard_image"]
