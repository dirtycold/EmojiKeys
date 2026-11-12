"""Prepare mapped actions once and fire them on key presses."""

import logging
import subprocess

from . import inject
from .hidpp import Reprog

log = logging.getLogger(__name__)


class Type:
    """Type text; {combo} presses keys, {{ and }} are literal braces. Parsed once.

    Without input simulation (Linux, or macOS without Accessibility) the text is copied
    to the clipboard with copy(text) for the user to paste, and combos are skipped.
    The UI passes clipboard.Clipboard; without copy nothing is copied.
    """

    def __init__(self, text, copy=None):
        self.copy = copy
        # str for text, Combo for keys (None where input simulation is unsupported)
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
                self.steps.append(inject.Combo(text[i + 1 : end]) if inject.SUPPORTED else None)
                i = end + 1
            elif text[i] == "}":
                raise ValueError(f"unmatched }} in {text!r}; write }}}} for a literal brace")
            else:
                buf += text[i]
                i += 1
        if buf:
            self.steps.append(buf)
        self.text = "".join(step for step in self.steps if isinstance(step, str))
        self.combos = any(not isinstance(step, str) for step in self.steps)

    def __call__(self):
        # checked on each press: Accessibility may be granted while we run
        if inject.trusted():
            for step in self.steps:
                if isinstance(step, str):
                    inject.write(step)
                else:
                    step.press()
        elif self.text and self.copy:
            self.copy(self.text)
            log.info("no input simulation: copied the text to the clipboard%s", ", skipped key combos" if self.combos else "")
        elif self.text:
            log.warning("no input simulation and no clipboard; text not typed")
        else:
            log.warning("key combos need input simulation (Accessibility on macOS); skipped")


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
    """Prepared actions by (control_id, fn); set() swaps in a new mapping with one assignment."""

    def __init__(self, config, copy=None):
        """copy(text) puts text on the clipboard where it can't be typed (the UI passes clipboard.Clipboard)."""
        self.kinds = {"type": lambda text: Type(text, copy), "run": Run}
        self.actions = {}
        self.set(config)

    def set(self, config):
        """Prepare every action of config; raises on a bad one and keeps the old mapping."""
        self.actions = {key: self.kinds[action.type](action.value) for key, action in config.keys.items()}

    @property
    def control_ids(self):
        """Control IDs that have a mapping, i.e. the ones to divert."""
        return tuple(sorted({control_id for control_id, fn in self.actions}))

    def press(self, control_id, fn):
        # F keys only ever report their task, so they are mapped as Fn+key whatever Fn shows
        fire = self.actions.get((control_id, fn or control_id in Reprog.FKEYS))
        if fire is None:
            return
        try:
            fire()
        except Exception as e:
            log.warning("action for %s failed: %s", Reprog.NAMES.get(control_id, f"{control_id:#06x}"), e)
