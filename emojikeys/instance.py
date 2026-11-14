"""One running app: a PID file held with flock. The UI holds it while it runs; monitor checks it."""

import fcntl
import os
import sys
import tempfile
from pathlib import Path


def path():
    if sys.platform == "darwin":
        # gettempdir() follows $TMPDIR
        return Path(tempfile.gettempdir()) / "emojikeys.pid"
    return Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "emojikeys.pid"


def acquire():
    """Lock the PID file and write our PID; return the open file to keep, or None when another process holds it."""
    lock = open(path(), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    lock.seek(0)
    lock.truncate()
    lock.write(f"{os.getpid()}\n")
    lock.flush()
    return lock


def owner():
    """Return the PID of the process holding the lock, or None."""
    try:
        return int(path().read_text())
    except (OSError, ValueError):
        return None
