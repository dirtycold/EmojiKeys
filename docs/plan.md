# EmojiKeys plan

Use the emoji keys of the Logitech POP Keys on macOS and Linux with a small Python tool. Not affiliated with Logitech.

## Decisions

- Python stdlib for the library; ctypes for libhidapi and CoreGraphics. qtpy for the UI only.
- One UI app owns the receiver. The HID++ listener runs in a thread inside it. Never a daemon.
- Single instance: a PID file held with `fcntl.flock`. A second launch sends `SIGUSR1` to the running app, which shows its window (`signal.set_wakeup_fd` + `QSocketNotifier`), then exits. No QtNetwork.
- JSON is the saved format only. At runtime the library and UI share dataclasses. Actions are resolved once; the runner swaps the key mapping in one assignment.
- CLI is read-only: `list`, `probe`, `doctor`. Exception: `monitor`, a testing tool that diverts the mappable keys and prints presses until Ctrl-C.
- No dependency on Solaar being installed: own udev rule and docs.
- Never read keystrokes. The tool opens only the HID++ interface, which carries the reprogrammable controls, Fn and status, never typing. Any feature that would need to read typing is dropped. So modifier keys are not supported; mappings tell key from Fn+key only (Fn comes over HID++). macOS enforces this only while the app has no Accessibility, i.e. with `run` actions alone: Accessibility, which `type` actions need, also lets a process read input (event taps, opening the keyboard). With Accessibility the guarantee is our code. Linux: the udev rule grants only the HID++ hidraw nodes. Typing on the POP Keys via Bolt reached none of them (`tools/passthrough.py`, 2026-11-12: the c548 and c52b receiver nodes and the M325 node saw nothing). Untested: a keyboard on a receiver in DJ mode (none at hand; the driver may forward typing to the receiver node) and Bluetooth (likely one node). Our code drops such reports unread either way.

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

macOS: typing text and key combos both post CoreGraphics events and need only Accessibility; nothing is deferred. Verified 2026-11-10 in iTerm2 with no permissions, on a second POP Keys and Bolt receiver: `list`, `probe`, `monitor` and `run` actions work; `type` actions do nothing, silently, so `doctor` must check `trusted()`. Accessibility is asked for only when needed: the config has a `type` action (in the UI, when one is picked) or the keyboard is on Bluetooth. Never at startup otherwise; without either, `doctor` reports it as not needed. Input Monitoring is never asked for. The grant follows the process (the terminal while run from one; a `.app` bundle later). Secure input (password fields, Terminal's Secure Keyboard Entry) may block synthetic events by design. `doctor` uses `CGPreflightPostEventAccess` / `CGRequestPostEventAccess`.

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
- Linux fixes, from the first test (2026-11-10, openSUSE, Solaar's rule installed; hidapi loads, access works, the POP Keys answers on its Bolt receiver c548 and `probe` reads all 20 controls):
  - The receiver driver adds a hidraw node per paired device (here the M325, pid 0x4094), which hidapi reports as "USB Receiver". We pinged its six slots, 5 × 4 s = 20 s, so `list`, `probe` and `monitor` paused and `monitor` never started diverting. Fix: open only receivers (pid 0xC5xx) and Bluetooth HID++ (0xFF43); paired devices stay reachable through their receiver.
  - `tools/passthrough.py` must print each interface's product ID and node; two of three interfaces saw no typing, but which receivers they were is unknown. Rerun steps 2-4 of docs/linux-check.md after the fix.
  - `ldconfig` isn't in the user's PATH there; use `/sbin/ldconfig` in step 1.
- Linux, second test (2026-11-12): the pause is gone and `monitor` diverts and reports every key, Fn and Fn lock. Found:
  - One `monitor` run stopped at once on `hid_write: Broken pipe` (EPIPE, a USB stall); the next run worked. Which receiver is unknown; USB autosuspend is off for both. Now a failed write is retried once after 100 ms and logged with its node, and `find`/`list` skip a failing receiver instead of aborting. If it recurs, the warning names the node. Seen again in `probe` the same day: the Unifying receiver (c52b, `/dev/hidraw1`, under `logitech-djreceiver`) stalled the run's first write and the retry recovered; the Bolt receiver hasn't stalled.
  - Topology here: the Unifying receiver (c52b) is bound to `logitech-djreceiver`, with the M325's node (4094) beneath it; the Bolt receiver (c548) is bound to `hid-generic`, so it has three plain interfaces and no per-device node, and the POP Keys typing comes on interface 0, not the HID++ one.
  - `tools/passthrough.py` echoed the test typing, which stayed in the tty and ran in the shell afterwards (Alt-H ran `run-help`), and tested one node per top-level collection (the M325 node three times). Now echo is off during the test, pending input is flushed after it, each node is tested once and labeled receiver or device, and Ctrl-C stops cleanly. Rerun on 2026-11-12: no typing on any HID++ node, Bolt included.
  - `monitor` echoed the keys it doesn't divert (plain F12, Insert, Delete, Home, End, Page Up/Down), which also stayed in the tty for the shell. It now runs with echo off and flushes pending input on exit, like the pass-through test; checked on 2026-11-12.
- Linux input simulation, `inject/linux.py` (WIP, reserved: uinput, XTest or portal; see Input injection).
- Key repeat: the keyboard reports only press and release for diverted keys (no repeats; Solaar's Backspace mapping didn't repeat either). An opt-in `repeat` for `type` actions would run our own timer while the key is held, using macOS's repeat delay and rate. Never for `run`, which would launch many times.
- `copy` action: put text (e.g. an emoji) on the clipboard for the user to paste; no input simulation, so it fits Linux too.
- Bluetooth (vendor page 0xFF43, devnum 0xFF). macOS (tested 2026-11-10): over Bluetooth all of the keyboard's interfaces, typing included, are one device, and opening it needs Input Monitoring or Accessibility (macOS prompts for Input Monitoring; works after grant and restart; Ghostty, which has Accessibility, opened it without). Bluetooth is a nice-to-have, not required. On macOS it is supported only through Accessibility: the tool never asks for Input Monitoring, which is too broad. Caveat, verified 2026-11-10: the Bluetooth keyboard is one device, so its typing reports (ID 0x01, 8 bytes, press and release) reach our process too. Our code must drop every non-HID++ report on arrival, before logging or queueing, so the guarantee is our code, as with `type` actions. Also found: HID++ over Bluetooth takes long reports only, and the Bluetooth host keeps its own OS mode and Fn lock. Linux: cover BlueZ too (hidraw over bus 0005; the udev rule must match Bluetooth nodes, e.g. `KERNELS=="0005:046D:*"`).
- `.app` bundle on macOS, so Accessibility is granted to the app alone.