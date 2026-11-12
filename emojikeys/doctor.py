"""Read-only dependency, configuration and device-access checks."""

import sys
from pathlib import Path

from . import hidpp, inject
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

    needs_type = config is not None and any(action.type == "type" for action in config.keys.values())
    if sys.platform == "darwin":
        if needs_type:
            # Preflight checks without prompting:
            # https://developer.apple.com/documentation/coregraphics/cgpreflightposteventaccess()
            try:
                trusted = inject.trusted()
                print(f"Accessibility: {'granted' if trusted else 'required for type actions'}")
                if not trusted:
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
        print("Input simulation: not supported yet on Linux")
        if needs_type:
            print("  Config has type actions; use run actions on Linux.")
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

    try:
        print(f"hidapi: {hidpp.library()._name}")
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
