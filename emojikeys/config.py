"""Key mapping: Action and Config dataclasses. JSON is touched only in load and save."""

import dataclasses
import json
import os
import sys
from pathlib import Path

from .hidpp import Reprog

TYPES = ("text", "keys", "app", "run")


@dataclasses.dataclass(frozen=True)
class Action:
    """What a key does: type text, press keys, open an app or run a command."""

    type: str
    value: str

    def __post_init__(self):
        if self.type not in TYPES:
            raise ValueError(f"unknown action type {self.type!r}, expected one of: {', '.join(TYPES)}")


@dataclasses.dataclass
class Config:
    """Mapping of control ID to Action; unmapped keys are left alone."""

    keys: dict = dataclasses.field(default_factory=dict)

    @staticmethod
    def path():
        if sys.platform == "darwin":
            return Path.home() / "Library/Application Support/EmojiKeys/config.json"
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "emojikeys/config.json"

    @classmethod
    def default(cls):
        # emoji after Solaar's key names, in control ID order
        return cls({
            0x0104: Action("text", "😍"),
            0x0105: Action("text", "😢"),
            0x0106: Action("text", "😊"),
            0x0107: Action("text", "😂"),
            0x0108: Action("keys", "ctrl+cmd+space"),
        })

    @classmethod
    def load(cls, path=None):
        path = Path(path or cls.path())
        data = json.loads(path.read_text(encoding="utf-8"))
        cids = {Reprog.NAMES[cid]: cid for cid in Reprog.MAPPABLE}
        keys = {}
        for name, action in data.get("keys", {}).items():
            if name not in cids:
                raise ValueError(f"{path}: unknown key {name!r}, expected one of: {', '.join(cids)}")
            try:
                keys[cids[name]] = Action(action["type"], action["value"])
            except KeyError as e:
                raise ValueError(f"{path}: key {name!r}: missing {e}") from None
            except (TypeError, ValueError) as e:
                raise ValueError(f"{path}: key {name!r}: {e}") from None
        return cls(keys)

    def save(self, path=None):
        path = Path(path or self.path())
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "keys": {
                Reprog.NAMES[cid]: dataclasses.asdict(self.keys[cid]) for cid in Reprog.MAPPABLE if cid in self.keys
            }
        }
        # write then rename, so a crash never leaves a half-written file
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
