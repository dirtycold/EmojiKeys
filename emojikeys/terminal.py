"""Keep typing done during a test off the terminal."""

import contextlib
import sys
import termios


@contextlib.contextmanager
def quiet():
    """Don't echo typing, and discard it afterwards so the shell never runs it."""
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    silent = termios.tcgetattr(fd)
    silent[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, silent)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)
        termios.tcflush(fd, termios.TCIFLUSH)
