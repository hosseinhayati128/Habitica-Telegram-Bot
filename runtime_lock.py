"""Small cross-process lock used by the synchronous WSGI entrypoint.

PythonAnywhere serves paid web apps from multiple worker processes.  The bot's
pickle persistence is a single local file, so a process-local ``asyncio.Lock``
or ``threading.Lock`` cannot protect its read/modify/write transaction.
"""

from __future__ import annotations

import errno
import fcntl
import math
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_LOCK_TIMEOUT_SECONDS = 2.0
MAX_LOCK_TIMEOUT_SECONDS = 30.0
LOCK_POLL_INTERVAL_SECONDS = 0.05


class RuntimeLockUnavailable(TimeoutError):
    """Raised when another worker owns the persistence transaction lock."""


def get_bot_data_path() -> Path:
    """Return the configured persistence path while preserving legacy CWD use.

    Historically ``PicklePersistence(filepath="botdata.pkl")`` resolved the
    file relative to the process working directory.  Keep that default for
    compatibility; deployments should set ``BOT_DATA_PATH`` to an absolute
    path to remove any WSGI working-directory ambiguity.
    """

    configured = Path(os.environ.get("BOT_DATA_PATH", "botdata.pkl")).expanduser()
    if configured.is_absolute():
        return configured.resolve()
    return (Path.cwd() / configured).resolve()


def get_runtime_lock_path() -> Path:
    """Return the lock-file path paired with the configured pickle file."""

    return Path(f"{get_bot_data_path()}.lock")


def _configured_timeout() -> float:
    raw_value = os.environ.get("RUNTIME_LOCK_TIMEOUT_SECONDS")
    if raw_value is None:
        return DEFAULT_LOCK_TIMEOUT_SECONDS

    try:
        value = float(raw_value)
    except ValueError:
        return DEFAULT_LOCK_TIMEOUT_SECONDS

    if not math.isfinite(value) or value < 0:
        return DEFAULT_LOCK_TIMEOUT_SECONDS
    return min(value, MAX_LOCK_TIMEOUT_SECONDS)


@contextmanager
def acquire_runtime_lock(
    *,
    lock_path: Path | None = None,
    timeout: float | None = None,
) -> Iterator[None]:
    """Acquire the Linux advisory lock, waiting for no longer than ``timeout``.

    The caller must keep this context around the complete persistence
    transaction: application construction, initialization, update/tick
    handling, and application shutdown.
    """

    resolved_path = lock_path or get_runtime_lock_path()
    if timeout is None:
        wait_seconds = _configured_timeout()
    elif math.isfinite(timeout):
        wait_seconds = min(max(0.0, timeout), MAX_LOCK_TIMEOUT_SECONDS)
    else:
        wait_seconds = DEFAULT_LOCK_TIMEOUT_SECONDS
    deadline = time.monotonic() + wait_seconds

    file_descriptor = os.open(resolved_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        os.fchmod(file_descriptor, 0o600)
        while True:
            try:
                fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeLockUnavailable from None
                time.sleep(min(LOCK_POLL_INTERVAL_SECONDS, remaining))

        yield
    finally:
        if acquired:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        os.close(file_descriptor)
