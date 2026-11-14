"""Dependency, configuration and device-access checks; read-only unless fix is set."""

import shutil
import subprocess
import sys
from pathlib import Path

from . import actions, hidpp, inject
from .config import Config

# before 73-seat-late.rules, which turns the uaccess tag into an ACL for the logged-in user
RULE = Path("/etc/udev/rules.d/70-emojikeys.rules")
APPLY = "udevadm control --reload && udevadm trigger --action=change --subsystem-match=hidraw && udevadm settle"


def udev_rule(devices):
    """Return our udev rule: the HID++ interface of each receiver present, for the logged-in user only.

    Each line matches one receiver interface by sysfs path, so a paired device's node beneath it
    (Linux logitech-dj) and the receiver's other interfaces, which carry typing, stay root-only.
    """
    lines = {}
    for path, pid, product in devices:
        node = (Path("/sys/class/hidraw") / Path(path.decode()).name).resolve()
        # .../<usb port>:<config>.<interface>/<bus>:<vendor>:<product>.<instance>/hidraw/hidrawN
        hid, interface = node.parents[1], node.parents[2]
        if not hid.name.startswith("0003:"):
            continue
        pattern = f"*:{interface.name.rsplit(':', 1)[1]}/{hid.name.rsplit('.', 1)[0]}.????/hidraw/hidraw*"
        lines[f'SUBSYSTEM=="hidraw", DEVPATH=="{pattern}", TAG+="uaccess"'] = None
    return (
        "# EmojiKeys: the HID++ interface of these Logitech receivers, for the logged-in user.\n"
        "# Written by `emojikeys doctor --fix`; run it again after adding a receiver.\n"
        + "".join(line + "\n" for line in lines)
    )


def install(text):
    """Write the udev rule as root and apply it; return whether it worked.

    Root comes from sudo when run from a terminal, else from a polkit dialog (pkexec); never in a Flatpak.
    """
    manual = f"sudo tee {RULE} <<'EOF'\n{text}EOF\nsudo sh -c '{APPLY}'"
    elevate = "sudo" if sys.stdin.isatty() else "pkexec"
    if Path("/.flatpak-info").exists() or not shutil.which(elevate):
        print(f"  Can't ask for root here; run on the host:\n{manual}")
        return False
    print(f"  Writing {RULE} as root via {elevate}...", flush=True)
    result = subprocess.run([elevate, "sh", "-c", f"cat > {RULE} && {APPLY}"], input=text.encode())
    if result.returncode != 0:
        print(f"  {elevate} failed (exit {result.returncode}); to do it by hand:\n{manual}")
        return False
    return True


def access(devices):
    """Open and close each HID++ interface, printing the result; return the ones that can't be opened.

    Opening and closing checks access without reading, writing or diverting keys.
    https://libusb.info/hidapi/group__API.html
    """
    blocked = []
    for device in devices:
        path, pid, product = device
        try:
            with hidpp.Channel(path, product):
                print(f"Device access: {pid:04x} {product} [{path.decode()}] open")
        except OSError as e:
            print(f"Device access: {e}")
            blocked.append(device)
    return blocked


def check(fix=False):
    """Print findings and return whether the checked requirements are met.

    fix: install what's missing; the udev rule on Linux (asks for root), Accessibility on macOS (asks the system).
    """
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
                if not trusted and fix:
                    # shows the system dialog; macOS applies the grant after a restart of this process
                    inject.trusted(prompt=True)
                    print("  Asked macOS for Accessibility; enable it, then run again.")
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
    blocked = access(devices)
    if sys.platform == "linux" and devices:
        # Rule files are evidence of installation, not proof of the active permissions.
        # https://www.freedesktop.org/software/systemd/man/latest/udev.html
        for directory in ("/etc/udev/rules.d", "/run/udev/rules.d", "/usr/lib/udev/rules.d"):
            for rule in sorted(Path(directory).glob("*.rules")):
                name = rule.name.lower()
                if "solaar" in name or "logitech-unify" in name:
                    print(f"udev: {rule} (Solaar's; grants every Logitech hidraw node, typing included)")
                elif "logitech" in name:
                    print(f"udev: {rule} (scope not checked)")
        expected = udev_rule(devices)
        installed = RULE.read_text() if RULE.exists() else None
        if installed == expected:
            print(f"udev: {RULE} up to date")
        # ours is the last resort: installed only when no other rule grants access; kept current once there
        elif installed or blocked:
            print(f"udev: {RULE} {'differs from the receivers present' if installed else 'missing'}")
            if not fix:
                print("  Run doctor --fix to install it (asks for root).")
            elif install(expected) and blocked:
                blocked = access(blocked)
        else:
            print(f"udev: {RULE} not needed; another rule already grants access")
    if blocked:
        ok = False
    print("Keyboard reachability: not checked; use probe with the keyboard awake")
    return ok
