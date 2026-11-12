"""Read-only dependency, configuration and device-access checks."""

import sys
from pathlib import Path

from . import actions, hidpp, inject
from .config import Config


def check():
    """Print findings and return whether the checked requirements are met."""
    ok = True
    config = None
    path = Config.path()
    try:
        config = Config.load(path)
        print(f"Config: {path}")
    except FileNotFoundError:
        config = Config.default()
        print(f"Config: {path} missing; checking defaults (not saved)")
    except (OSError, ValueError, TypeError, AttributeError) as e:
        print(f"Config: {e}")
        ok = False

    # keys whose type action holds key combos, which can't fall back to the clipboard
    needs_type, combo_keys = False, []
    if config is not None:
        for name, key in Config.names().items():
            action = config.keys.get(key)
            if action is None or action.type != "type":
                continue
            needs_type = True
            try:
                if actions.Type(action.value).combos:
                    combo_keys.append(name)
            except (OSError, ValueError) as e:
                print(f"Config: key {name!r}: {e}")
                ok = False
    if sys.platform == "darwin":
        if needs_type:
            # Preflight checks without prompting:
            # https://developer.apple.com/documentation/coregraphics/cgpreflightposteventaccess()
            try:
                trusted = inject.trusted()
                print(f"Accessibility: {'granted' if trusted else 'not granted; type actions copy their text to the clipboard'}")
                if not trusted and combo_keys:
                    print(f"  Key combos need it: {', '.join(combo_keys)}.")
                    print("  Enable this terminal or app in System Settings > Privacy & Security > Accessibility.")
                    ok = False
            except OSError as e:
                print(f"Accessibility: {e}")
                ok = False
        elif config is not None:
            print("Accessibility: not needed for run actions over a receiver")
        else:
            print("Accessibility: requirement unknown until config is valid")
        print("Bluetooth: not checked; discovery currently supports receivers only")
    elif sys.platform == "linux":
        print("Input simulation: not supported yet on Linux; type actions copy their text to the clipboard")
        if combo_keys:
            print(f"  Key combos do nothing on Linux: {', '.join(combo_keys)}; use run actions.")
            ok = False
        # Rule files are evidence of installation, not proof of the active permissions.
        # https://www.freedesktop.org/software/systemd/man/latest/udev.html
        for directory in ("/etc/udev/rules.d", "/run/udev/rules.d", "/usr/lib/udev/rules.d"):
            for rule in sorted(Path(directory).glob("*.rules")):
                if "solaar" in rule.name.lower() or "logitech" in rule.name.lower():
                    print(f"udev: {rule} (scope not checked)")
    else:
        print(f"OS: {sys.platform} is not supported")
        ok = False

    # the copy fallback of type actions; used whenever text can't be typed
    if needs_type:
        try:
            from . import clipboard

            how = clipboard.method()
            print(f"Clipboard: {how or 'none'}{' (Wayland)' if clipboard.wayland() else ''}")
            if how is None:
                print("  Without Klipper, Wayland needs wl-clipboard (wl-copy) for type actions to copy.")
                ok = False
        except ImportError as e:
            print(f"Clipboard: needs qtpy and a Qt binding ({e})")
            ok = False

    try:
        print(f"hidapi: {hidpp.library().name}")
        devices = hidpp.Channel.enumerate()
    except OSError as e:
        print(f"hidapi/discovery: {e}")
        if sys.platform == "darwin":
            print("  Install hidapi with MacPorts (port install hidapi) or Homebrew (brew install hidapi).")
        elif sys.platform == "linux":
            print("  Check the system hidapi package (libhidapi-hidraw0 or hidapi) and hidraw nodes.")
        return False

    if not devices:
        print("Device access: no receiver HID++ interfaces found; check the receiver connection")
        ok = False
    # Opening and closing checks access without reading, writing or diverting keys.
    # https://libusb.info/hidapi/group__API.html
    for path, pid, product in devices:
        try:
            with hidpp.Channel(path, product):
                print(f"Device access: {pid:04x} {product} [{path.decode()}] open")
        except OSError as e:
            print(f"Device access: {e}")
            if sys.platform == "linux":
                print("  Check hidraw permissions and udev rules, then replug the receiver.")
            ok = False
    print("Keyboard reachability: not checked; use probe with the keyboard awake")
    return ok
