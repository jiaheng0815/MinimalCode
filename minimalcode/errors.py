class MinimalCodeError(RuntimeError):
    """Base exception for expected MinimalCode failures."""


class ConfigError(MinimalCodeError):
    """Raised when MinimalCode configuration is invalid."""


class ShellError(MinimalCodeError):
    """Raised when no supported shell can be started."""


class ProviderError(MinimalCodeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ToolError(MinimalCodeError):
    """Raised for invalid or unsafe tool input."""


class ClipboardError(MinimalCodeError):
    """Raised when an image cannot be read from the system clipboard."""
