"""Optional machine-readable status on an otherwise unchanged tool observation."""


class ToolOutput(str):
    """A string-compatible result; plain strings continue to mean successful output."""

    status: str
    error_code: str | None

    def __new__(cls, text: str, *, status: str = "ok", error_code: str | None = None):
        if status not in {"ok", "error", "timeout", "denied", "invalid"}:
            raise ValueError("invalid tool output status")
        instance = super().__new__(cls, text)
        instance.status = status
        instance.error_code = error_code
        return instance
