"""Key mapping: Action and Config dataclasses. JSON is touched only in load and save."""

import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path

from .hidpp import Reprog

TYPES = ("type", "run")


@dataclasses.dataclass(frozen=True)
class Action:
    """What a key does: type text with {combo} keys in it, or run a shell command."""

    type: str
    value: str

    def __post_init__(self):
        if self.type not in TYPES:
            raise ValueError(f"unknown action type {self.type!r}, expected one of: {', '.join(TYPES)}")


@dataclasses.dataclass
class Config:
    """Mapping of (control ID, Fn held) to Action; unmapped keys are left alone.

    F keys only ever report their task (media function), so they are mapped as Fn+F4 .. Fn+F12 only.
    """

    keys: dict = dataclasses.field(default_factory=dict)

    @staticmethod
    def path():
        if sys.platform == "darwin":
            return Path.home() / "Library/Application Support/EmojiKeys/config.json"
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "emojikeys/config.json"

    @staticmethod
    def names():
        """Return {name: (cid, fn)} of every mappable key, in keyboard order."""
        names = {}
        for cid in Reprog.MAPPABLE:
            for fn in (True,) if cid in Reprog.FKEYS else (False, True):
                names[("Fn+" if fn else "") + Reprog.NAMES[cid]] = (cid, fn)
        return names

    @classmethod
    def default(cls):
        # emoji after Solaar's key names, in control ID order; copied to the clipboard where they can't be typed
        keys = {
            (0x0104, False): Action("type", "😍"),
            (0x0105, False): Action("type", "😢"),
            (0x0106, False): Action("type", "😊"),
            (0x0107, False): Action("type", "😂"),
        }
        if sys.platform == "darwin":
            keys[(0x0108, False)] = Action("type", "{ctrl+cmd+space}")
            keys[(0x0103, False)] = Action("run", "open -a Screenshot")
            return cls(keys)
        # emoji picker and screenshot tool: the first one installed (KDE, then GNOME), else unmapped
        for cid, commands in (
            (0x0108, ("plasma-emojier", "gnome-characters")),
            (0x0103, ("spectacle", "gnome-screenshot --interactive")),
        ):
            command = next((c for c in commands if shutil.which(c.split()[0])), None)
            if command:
                keys[(cid, False)] = Action("run", command)
        return cls(keys)

    @classmethod
    def load(cls, path=None):
        path = Path(path or cls.path())
        data = json.loads(path.read_text(encoding="utf-8"))
        names = cls.names()
        keys = {}
        for name, action in data.get("keys", {}).items():
            if "Fn+" + name in names and name not in names:
                raise ValueError(f"{path}: F keys are mapped as {'Fn+' + name!r}, not {name!r}")
            if name not in names:
                raise ValueError(f"{path}: unknown key {name!r}, expected one of: {', '.join(names)}")
            try:
                keys[names[name]] = Action(action["type"], action["value"])
            except KeyError as e:
                raise ValueError(f"{path}: key {name!r}: missing {e}") from None
            except (TypeError, ValueError) as e:
                raise ValueError(f"{path}: key {name!r}: {e}") from None
        return cls(keys)

    def save(self, path=None):
        path = Path(path or self.path())
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"keys": {name: dataclasses.asdict(self.keys[key]) for name, key in self.names().items() if key in self.keys}}
        # write then rename, so a crash never leaves a half-written file
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
