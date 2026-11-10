"""CoreGraphics via ctypes: type text and press key combos. Needs Accessibility."""

import ctypes
import functools

# kCGHIDEventTap: post as if from the keyboard
TAP = 0
# CGEventKeyboardSetUnicodeString takes at most 20 UTF-16 units per event
CHUNK = 20

MODIFIERS = {
    "shift": 0x00020000,
    "ctrl": 0x00040000,
    "control": 0x00040000,
    "alt": 0x00080000,
    "opt": 0x00080000,
    "option": 0x00080000,
    "cmd": 0x00100000,
    "command": 0x00100000,
}

# virtual key codes; letters and symbols by US ANSI position
KEYS = {
    **dict(zip("asdfhgzxcv", range(0x00, 0x0A))),
    **dict(zip("qweryt123465", range(0x0C, 0x18))),
    "b": 0x0B, "=": 0x18, "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D, "]": 0x1E, "o": 0x1F,
    "u": 0x20, "[": 0x21, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29,
    "\\": 0x2A, ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E, ".": 0x2F, "`": 0x32,
    "return": 0x24, "enter": 0x24, "tab": 0x30, "space": 0x31, "backspace": 0x33, "escape": 0x35, "esc": 0x35,
    "delete": 0x75, "home": 0x73, "end": 0x77, "pageup": 0x74, "pagedown": 0x79,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
}


@functools.cache
def _lib():
    """Load CoreGraphics and CoreFoundation once."""
    c = ctypes
    cg = c.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = c.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    for fn, args, res in (
        (cg.CGEventCreateKeyboardEvent, [c.c_void_p, c.c_uint16, c.c_bool], c.c_void_p),
        (cg.CGEventKeyboardSetUnicodeString, [c.c_void_p, c.c_ulong, c.POINTER(c.c_uint16)], None),
        (cg.CGEventSetFlags, [c.c_void_p, c.c_uint64], None),
        (cg.CGEventPost, [c.c_uint32, c.c_void_p], None),
        (cg.CGPreflightPostEventAccess, [], c.c_bool),
        (cg.CGRequestPostEventAccess, [], c.c_bool),
        (cf.CFRelease, [c.c_void_p], None),
    ):
        fn.argtypes, fn.restype = args, res
    return cg, cf


def _post(keycode, down, flags, units=None):
    cg, cf = _lib()
    event = cg.CGEventCreateKeyboardEvent(None, keycode, down)
    if not event:
        raise OSError("CGEventCreateKeyboardEvent failed")
    # explicit flags, so modifiers the user is holding don't leak in
    cg.CGEventSetFlags(event, flags)
    if units:
        cg.CGEventKeyboardSetUnicodeString(event, len(units), (ctypes.c_uint16 * len(units))(*units))
    cg.CGEventPost(TAP, event)
    cf.CFRelease(event)


def trusted(prompt=False):
    """Return whether this process may post input events; prompt=True shows the system dialog."""
    cg, _cf = _lib()
    return cg.CGRequestPostEventAccess() if prompt else cg.CGPreflightPostEventAccess()


def write(text):
    """Type text as-is (emoji included), independent of the keyboard layout."""
    chunk = []
    for ch in text:
        units = list(memoryview(ch.encode("utf-16-le")).cast("H"))
        if len(chunk) + len(units) > CHUNK:
            _post(0, True, 0, chunk)
            _post(0, False, 0, chunk)
            chunk = []
        chunk += units
    if chunk:
        _post(0, True, 0, chunk)
        _post(0, False, 0, chunk)


class Combo:
    """A key combo like "ctrl+cmd+space", parsed once and pressed on demand."""

    def __init__(self, text):
        *mods, key = text.lower().split("+")
        unknown = [m for m in mods if m not in MODIFIERS] + ([] if key in KEYS else [key])
        if unknown:
            raise ValueError(f"unknown key name(s) in {text!r}: {', '.join(unknown)}")
        self.text = text
        self.keycode = KEYS[key]
        self.flags = 0
        for m in mods:
            self.flags |= MODIFIERS[m]

    def press(self):
        _post(self.keycode, True, self.flags)
        _post(self.keycode, False, self.flags)
