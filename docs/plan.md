# EmojiKeys plan

Use the emoji keys of the Logitech POP Keys on macOS and Linux with a small Python tool. Not affiliated with Logitech.

## Decisions

- Python stdlib for the library; ctypes for libhidapi and CoreGraphics. qtpy for the UI only.
- One UI app owns the receiver. The HID++ listener runs in a thread inside it. Never a daemon.
- Single instance: a PID file held with `fcntl.flock`. A second launch sends `SIGUSR1` to the running app, which shows its window (`signal.set_wakeup_fd` + `QSocketNotifier`), then exits. No QtNetwork.
- JSON is the saved format only. At runtime the library and UI share dataclasses. Actions are resolved once; the runner swaps the key mapping in one assignment.
- CLI is read-only: `list`, `probe`, `doctor`. Exception: `monitor`, a testing tool that diverts the mappable keys and prints presses until Ctrl-C.
- No dependency on Solaar being installed: own udev rule and docs.
- Never read keystrokes. The tool opens only the HID++ interface, which carries the reprogrammable controls, Fn and status, never typing. Any feature that would need to read typing is dropped. So modifier keys are not supported; mappings tell key from Fn+key only (Fn comes over HID++). macOS enforces this (no Input Monitoring permission); on Linux the udev rule grants only the HID++ hidraw nodes, to be tested on Linux.

## Layout

```
emojikeys/
  __main__.py      no args: UI app; list / probe / doctor: CLI
  hidpp.py         Channel, Device, divert, listener thread
  config.py        Action and Config dataclasses, JSON load/save
  actions.py       prepare and fire actions: type, run (Keymap)
  inject/macos.py  CoreGraphics: type text, key combos, Accessibility check
  doctor.py        checks; --fix installs the udev rule, prompts Accessibility
  ui.py            tray app, single instance
```

Files:

| | macOS | Linux |
|---|---|---|
| Config | `~/Library/Application Support/EmojiKeys/config.json` | `$XDG_CONFIG_HOME/emojikeys/config.json` |
| PID file | `$TMPDIR/emojikeys.pid` | `$XDG_RUNTIME_DIR/emojikeys.pid` |

## Milestones

| # | Module | Check |
|---|---|---|
| 1 | `hidpp.py` | `python3 -m emojikeys list` / `probe` match the prototype |
| 2 | `config.py` | default config written and read back |
| 3 | `inject/macos.py` | a one-liner types an emoji and sends ⌃⌘Space |
| 4 | `actions.py` | one-liner per action type |
| 5 | `doctor.py` | `python3 -m emojikeys doctor` reports deps and permissions |
| 6 | `ui.py` | keys work end to end; second launch brings up the first; `monitor` refuses while the app runs (same PID-file lock) |

Mappable keys: F4–F12, Dictation, Emoji 1–4, Emoji panel. Only mapped keys are diverted. F1–F3 are Easy-Switch and not divertable.

Config keys: `Emoji 1` and `Fn+Emoji 1` (likewise Dictation, Emoji panel) are separate mappings. F keys are named `Fn+F4` … `Fn+F12` only and fire whatever Fn shows, since only their task is ever reported.

Actions: `type` types text, with `{combo}` for keys (`{ctrl+cmd+space}`, `hi{return}`; `{{` and `}}` are literal braces). `run` runs a shell command without waiting; apps are `open -a "Name"`, which the UI offers through an app picker. Actions fire on press; release is ignored.

On an F key only its task (media function) is diverted, whichever combo produces it under the current Fn-lock mode; the plain F key still reaches the system. Verified 2026-11-09 with F12: plain F12 reached the terminal, Fn+F12 arrived as a divert event (Fn 0x0034 held) and the volume didn't change.

The UI shows each key's stock function (from its task ID, our own labels) and the Fn-lock state, so the user knows whether the media function is F-key or Fn+F-key. Fn-lock is feature 0x40A3 (index 11 on POP Keys): read fn0 at start (per host, current host from HOSTS_INFO 0x1815), and event 0 reports each toggle as [host, state, 01, 01] with state 01 = media keys without Fn. Seen 2026-11-09 while toggling with Fn+Esc.

Default mapping: the four emoji keys type their emoji, the panel key sends ⌃⌘Space (emoji picker), Dictation opens Screenshot.app. The F keys are unmapped, so they keep their normal function.

## Requirements (to document in the README)

| | macOS | Linux |
|---|---|---|
| hidapi | MacPorts `hidapi`, else Homebrew `hidapi` | system package (`libhidapi-hidraw0` / `hidapi`) |
| Device access | none | udev rule: Logitech (`046d`) hidraw nodes of the HID++ interface only, `TAG+="uaccess"` (a `uinput` line only if Linux input simulation is added). If Solaar's rule is installed it grants all Logitech hidraw nodes anyway; `doctor` says so |
| Key injection | Accessibility for the app process | not supported yet (reserved) |
| UI | qtpy + a Qt binding | same, Flatpak preferred |

## Input injection

macOS: typing text and key combos both post CoreGraphics events and need only Accessibility; nothing is deferred. The grant follows the process (the terminal while run from one; a `.app` bundle later). Secure input (password fields, Terminal's Secure Keyboard Entry) may block synthetic events by design. `doctor` uses `CGPreflightPostEventAccess` / `CGRequestPostEventAccess`.

Linux: input simulation is not supported yet (reserved). `text` and `keys` actions report "not supported yet on Linux"; `app`, `run` and the user's own scripts cover the rest. `/dev/uinput` is one node and can't be scoped; write access lets any process of the user type into any app (injection, not reading). Options, to decide if it's ever added:

| Way | Scope | Cost |
|---|---|---|
| `/dev/uinput` via udev `uaccess` | any process of the logged-in user | simple, works everywhere |
| XTest | X11 sessions only | no permission, no Wayland |
| RemoteDesktop portal + libei | per app, approved in a system dialog | KDE and GNOME on Wayland; D-Bus and libei, heaviest |

Many Linux actions need no injection: they are `run` commands, e.g. `plasma-emojier` (KDE emoji picker), `gnome-characters`, `spectacle` (KDE screenshot).

Linux without any permission granted:

| Granted | What works |
|---|---|
| nothing | nothing: hidraw nodes are root-only, so the keyboard can't be opened (unless Solaar's rule is installed) |
| HID++ hidraw rule only | diversion and all events (keys, Fn, Fn lock, OS mode); `app` and `run` actions; text as clipboard copy (Qt in the UI) for the user to paste; on X11 also text and combos via XTest |
| plus `/dev/uinput` or the portal | text and key combos on Wayland too |

`doctor --fix`: installs the udev rule with `pkexec` (prints the `sudo` command as fallback, print-only inside Flatpak), reloads udev, then asks to replug the receiver. Never loads kernel modules or installs packages; it prints those commands.

## Later

- Mapping editor in the UI.
- OS mode: show and change the keyboard's OS mode (MULTIPLATFORM 0x4531), with a description of what it changes. On POP Keys the choices are Windows 0, macOS 1, iOS 2. fn0 byte 6 is the current mode, and fn3 `[0xFF, platform]` sets it for the current host; the keyboard shortcuts are Fn+P (Windows) and Fn+O (macOS) held about 3 s, and the F1 light flashes on a switch. The mode explains surprises: in Windows mode on a Mac, the task keys send Windows shortcuts (Fn+F4 Win+D gave ∂, Fn+F5 Win+Shift+S gave Í, Fn+F6 did nothing), and Command and Option/Control land differently. Media keys (F7-F12) work in any mode. Phase 1 assumes Windows mode, with keys assigned by hand. The mode is shared when one dongle is moved between machines with a hardware switch.
- Linux input simulation, `inject/linux.py` (WIP, reserved: uinput, XTest or portal; see Input injection).
- Key repeat: the keyboard reports only press and release for diverted keys (no repeats; Solaar's Backspace mapping didn't repeat either). An opt-in `repeat` for `type` actions would run our own timer while the key is held, using macOS's repeat delay and rate. Never for `run`, which would launch many times.
- `copy` action: put text (e.g. an emoji) on the clipboard for the user to paste; no input simulation, so it fits Linux too.
- Bluetooth (vendor page 0xFF43, devnum 0xFF). macOS: the POP Keys shows the 0xFF43 interface, but opening it failed (2026-11-10); likely needs Input Monitoring because typing shares the device, which conflicts with "never read keystrokes". To test by granting it briefly. Linux: cover BlueZ too (hidraw over bus 0005; the udev rule must match Bluetooth nodes, e.g. `KERNELS=="0005:046D:*"`).
- `.app` bundle on macOS, so Accessibility is granted to the app alone.