"""`with_session_retry` — plan §3 / Must-10.

Wraps every instagrapi call category. On `LoginRequired` /
`PleaseWaitFewMinutes` it tries `relogin()` once from persisted settings;
on a second failure it re-raises so the caller can short-circuit the run.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def with_session_retry(
    relogin: Callable[[], None] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator factory.

    `relogin` is called once if the first attempt raises a recoverable
    auth error. Pass `None` (default) to skip relogin and just retry once.
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return fn(*args, **kwargs)
            except Exception as first_err:
                if not _is_recoverable(first_err):
                    raise
                if relogin is not None:
                    relogin()
                return fn(*args, **kwargs)

        return inner

    return decorator


def _is_recoverable(err: BaseException) -> bool:
    # We import lazily so the module is importable in test environments
    # that have not installed instagrapi.
    try:
        from instagrapi.exceptions import (
            LoginRequired,
            PleaseWaitFewMinutes,
        )
    except Exception:  # pragma: no cover - import-time fallback
        return False
    return isinstance(err, (LoginRequired, PleaseWaitFewMinutes))


__all__ = ["with_session_retry"]
