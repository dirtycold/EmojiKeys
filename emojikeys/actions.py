"""Prepare mapped actions once and fire them on key presses."""

import logging
import subprocess

from . import inject
from .hidpp import Reprog

log = logging.getLogger(__name__)


class Type:
    """Type text; {combo} presses keys, {{ and }} are literal braces. Parsed once."""

    def __init__(self, text):
        if not inject.SUPPORTED:
            raise NotImplementedError("type actions are not supported yet on this OS")
        self.steps = []
        buf, i = "", 0
        while i < len(text):
            if text.startswith(("{{", "}}"), i):
                buf += text[i]
                i += 2
            elif text[i] == "{":
                end = text.find("}", i)
                if end < 0:
                    raise ValueError(f"unclosed {{ in {text!r}")
                if buf:
                    self.steps.append(buf)
                    buf = ""
                self.steps.append(inject.Combo(text[i + 1 : end]))
                i = end + 1
            elif text[i] == "}":
                raise ValueError(f"unmatched }} in {text!r}; write }}}} for a literal brace")
            else:
                buf += text[i]
                i += 1
        if buf:
            self.steps.append(buf)

    def __call__(self):
        for step in self.steps:
            if isinstance(step, str):
                inject.write(step)
            else:
                step.press()


class Run:
    """Run a shell command without waiting; it outlives us (own session)."""

    def __init__(self, command):
        self.command = command

    def __call__(self):
        subprocess.Popen(
            self.command,
            shell=True,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class Keymap:
    """Prepared actions by (cid, fn); set() swaps in a new mapping with one assignment."""

    KINDS = {"type": Type, "run": Run}

    def __init__(self, config):
        self._actions = {}
        self.set(config)

    def set(self, config):
        """Prepare every action of config; raises on a bad one and keeps the old mapping."""
        self._actions = {key: self.KINDS[action.type](action.value) for key, action in config.keys.items()}

    @property
    def cids(self):
        """Control IDs that have a mapping, i.e. the ones to divert."""
        return tuple(sorted({cid for cid, _fn in self._actions}))

    def press(self, cid, fn):
        # F keys only ever report their task, so they are mapped as Fn+key whatever Fn shows
        fire = self._actions.get((cid, fn or cid in Reprog.FKEYS))
        if fire is None:
            return
        try:
            fire()
        except Exception as e:
            log.warning("action for %s failed: %s", Reprog.NAMES.get(cid, f"{cid:#06x}"), e)
