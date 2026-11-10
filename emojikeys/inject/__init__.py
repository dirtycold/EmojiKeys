"""Simulated input: type text and press key combos. macOS only; Linux is not supported yet."""

import sys

if sys.platform == "darwin":
    from .macos import Combo, trusted, write
else:

    class Combo:
        def __init__(self, text):
            raise NotImplementedError("input simulation is not supported yet on Linux")

    def trusted(prompt=False):
        return False

    def write(text):
        raise NotImplementedError("input simulation is not supported yet on Linux")


__all__ = ["Combo", "trusted", "write"]
