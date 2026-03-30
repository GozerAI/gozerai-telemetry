"""Distributed tracing correlation for cross-service request tracking.

Provides a ``CorrelationContext`` that propagates a correlation ID through
async call chains using ``contextvars``.  Includes helpers for HTTP header
injection and extraction.

Zero dependencies — pure stdlib.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any, Callable, Dict, Optional, Tuple

# ── Context variable ─────────────────────────────────────────────────────────

_correlation_id: ContextVar[Optional[str]] = ContextVar("_correlation_id", default=None)

HEADER_NAME = "X-Correlation-ID"


class CorrelationContext:
    """Scoped correlation ID context manager.

    Usage::

        with CorrelationContext("req-123"):
            assert get_correlation_id() == "req-123"
        # ID is restored to previous value after the block
    """

    def __init__(self, correlation_id: Optional[str] = None) -> None:
        self.correlation_id = correlation_id or uuid.uuid4().hex
        self._token: Any = None

    def __enter__(self) -> "CorrelationContext":
        self._token = _correlation_id.set(self.correlation_id)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._token is not None:
            _correlation_id.reset(self._token)


# ── Public API ───────────────────────────────────────────────────────────────

def set_correlation_id(cid: str) -> None:
    """Set the current correlation ID in the context."""
    _correlation_id.set(cid)


def get_correlation_id() -> Optional[str]:
    """Get the current correlation ID, or ``None`` if not set."""
    return _correlation_id.get()


def new_correlation_id() -> str:
    """Generate a new correlation ID, set it in the context, and return it."""
    cid = uuid.uuid4().hex
    _correlation_id.set(cid)
    return cid


def inject_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Add the correlation ID to *headers* (mutates and returns the dict).

    If no correlation ID is set in the current context, a new one is generated.
    """
    cid = get_correlation_id()
    if cid is None:
        cid = new_correlation_id()
    headers[HEADER_NAME] = cid
    return headers


def extract_correlation_id(headers: Dict[str, str]) -> Optional[str]:
    """Extract the correlation ID from incoming request headers.

    Performs a case-insensitive lookup.
    """
    for key, value in headers.items():
        if key.lower() == HEADER_NAME.lower():
            return value
    return None


def correlation_middleware(
    headers: Dict[str, str],
) -> str:
    """Extract or generate a correlation ID from request headers.

    - If the incoming *headers* contain ``X-Correlation-ID``, that value is
      used and set in the current context.
    - Otherwise a new ID is generated.

    Returns the active correlation ID.
    """
    cid = extract_correlation_id(headers)
    if cid is None:
        cid = uuid.uuid4().hex
    set_correlation_id(cid)
    return cid
