"""Errors raised by platform adapters.

Like API errors, they carry a machine-readable ``code`` (translated by the UI as
``errors.<code>``) and optional ``params``, never user-facing text.
"""

from typing import Any


class PublishError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.params = params

    def __repr__(self) -> str:
        return f"PublishError({self.code!r}, retryable={self.retryable}, params={self.params!r})"
