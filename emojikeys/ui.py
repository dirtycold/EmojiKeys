"""Tray app: owns the receiver, fires actions, and edits the mapping in one small window."""

import configparser
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from qtpy.QtCore import QObject, QSocketNotifier, Qt, QTimer, Signal
from qtpy.QtGui import QIcon, QKeySequence, QPainter, QPixmap
from qtpy.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import actions, clipboard, hidpp, inject, instance
from .config import Action, Config
from .hidpp import Reprog

log = logging.getLogger(__name__)

# POP Keys only for now; see docs/plan.md for reading the keys from the device instead
MATCH = "POP Keys"
# the F keys' media functions, by control ID (F4-F6 as seen in Windows mode)
STOCK = {
    0x006E: "Desktop",
    0x010A: "Screenshot",
    0x011C: "?",
    0x00E4: "Previous",
    0x00E5: "Play/Pause",
    0x00E6: "Next",
    0x00E7: "Mute",
    0x00E8: "Volume −",
    0x00E9: "Volume +",
}
KINDS = ("Off", "Type", "Run")
# Qt's portable key names that differ from ours (inject/macos.py)
KEY_NAMES = {"del": "delete", "pgup": "pageup", "pgdown": "pagedown"}


class Listener(QObject):
    """The keyboard side, in a thread: finds the keyboard, diverts keys and fires their actions.

    While the window is open (editing) every mappable key is diverted and a press selects it
    instead of firing; otherwise only mapped keys are diverted. Changing either re-diverts.
    """

    key = Signal(int, bool, bool)
    status = Signal(str)
    fn_lock = Signal(bool)

    def __init__(self, keymap):
        super().__init__()
        self.keymap = keymap
        self.editing = False
        self.quitting = False
        # set to end the current listen(): to re-divert or to quit
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def restart(self):
        self.wake.set()

    def stop(self):
        self.quitting = True
        self.wake.set()
        # listen() undoes the divert on its way out
        self.thread.join(5)

    def on_key(self, control_id, pressed, fn):
        if pressed and not self.editing:
            self.keymap.press(control_id, fn)
        self.key.emit(control_id, pressed, fn)

    def run(self):
        while not self.quitting:
            try:
                found = hidpp.find(MATCH)
                if found:
                    ch, dev, name = found
                    with ch:
                        self.status.emit(name)
                        lock = dev.fn_lock()
                        if lock is not None:
                            self.fn_lock.emit(lock)
                        while not self.quitting:
                            self.wake.clear()
                            control_ids = Reprog.MAPPABLE if self.editing else self.keymap.control_ids
                            dev.listen(control_ids, self.on_key, self.wake, self.fn_lock.emit)
                    continue
                self.status.emit(f"No {MATCH} found; tap a key to wake it")
            except (OSError, hidpp.Error, TimeoutError) as e:
                self.status.emit(f"{MATCH}: {e}")
            self.wake.wait(5)
            self.wake.clear()


def summary(action, width=12):
    if action is None:
        return ""
    return action.value if len(action.value) <= width else action.value[: width - 1] + "…"


def combo_text(sequence):
    """Our {combo} text for a captured QKeySequence; on macOS Qt's Ctrl is Command and Meta is Control."""
    names = {**KEY_NAMES, **({"ctrl": "cmd", "meta": "ctrl"} if sys.platform == "darwin" else {})}
    text = ""
    for chord in sequence.toString(QKeySequence.SequenceFormat.PortableText).split(", "):
        text += "{" + "+".join(names.get(part.lower(), part.lower()) for part in chord.split("+")) + "}"
    return text


def desktop_apps():
    """Return {name: (command, icon)} of the installed apps, from their .desktop files; user's first."""
    home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    dirs = [home, *(os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]
    apps = {}
    for directory in dirs:
        for path in sorted(Path(directory, "applications").rglob("*.desktop")):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if entry.get("Type") != "Application" or "true" in (entry.get("NoDisplay"), entry.get("Hidden")):
                continue
            if "Exec" in entry:
                # field codes (%f, %U, ...) stand for files and URLs, which we never pass
                command = re.sub(r"%[a-zA-Z]", "", entry["Exec"]).replace("%%", "%").strip()
                apps.setdefault(entry.get("Name", path.stem), (command, entry.get("Icon", "")))
    return apps


class AppPicker(QDialog):
    """A filterable list of the installed apps (Linux); choose() returns the app's command."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Choose an app")
        self.filter = QLineEdit(placeholderText="Filter")
        self.list = QListWidget()
        for name, (command, icon) in sorted(desktop_apps().items(), key=lambda item: item[0].lower()):
            item = QListWidgetItem(QIcon(icon) if os.path.isabs(icon) else QIcon.fromTheme(icon), name)
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.list.addItem(item)
        self.filter.textChanged.connect(self.narrow)
        self.list.itemActivated.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(self.filter)
        layout.addWidget(self.list)
        self.resize(360, 480)

    def narrow(self, text):
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(text.lower() not in item.text().lower())

    def choose(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if self.exec() and item else None


def pick_app(parent):
    """Return a run command for an app the user picks, or None."""
    if sys.platform == "darwin":
        path = QFileDialog.getOpenFileName(parent, "Choose an app", "/Applications", "Apps (*.app)")[0]
        return f'open -a "{Path(path).stem}"' if path else None
    return AppPicker(parent).choose()


class Binding(QWidget):
    """Editor row for one trigger of the selected key: the key alone, or with Fn."""

    def __init__(self, window, fn):
        super().__init__()
        self.window = window
        self.fn = fn
        self.key = None
        self.label = QLabel()
        self.label.setMinimumWidth(150)
        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.value = QLineEdit()
        self.capture = QKeySequenceEdit()
        self.capture.hide()
        self.shortcut = QPushButton("Shortcut…")
        self.pick = QPushButton("App…")
        self.test = QPushButton("Test")
        if not inject.SUPPORTED:
            self.shortcut.setToolTip("Key combos need input simulation, not supported yet on Linux")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in (self.label, self.kind, self.value, self.capture, self.shortcut, self.pick, self.test):
            layout.addWidget(widget)
        self.kind.currentIndexChanged.connect(self.save)
        self.value.editingFinished.connect(self.save)
        self.shortcut.clicked.connect(self.start_capture)
        self.capture.editingFinished.connect(self.end_capture)
        self.pick.clicked.connect(self.choose_app)
        self.test.clicked.connect(self.run_test)

    def show_key(self, control_id):
        self.key = (control_id, self.fn)
        action = self.window.config.keys.get(self.key)
        stock = f" ({STOCK[control_id]})" if control_id in Reprog.FKEYS else ""
        self.label.setText(f"{'Fn+' if self.fn else ''}{Reprog.NAMES[control_id]}{stock}")
        self.kind.blockSignals(True)
        self.kind.setCurrentText(action.type.capitalize() if action else "Off")
        self.kind.blockSignals(False)
        self.value.setText(action.value if action else "")
        self.update_buttons()

    def update_buttons(self):
        kind = self.kind.currentText()
        self.value.setEnabled(kind != "Off")
        self.value.setPlaceholderText({"Type": "text, {combo} for keys", "Run": "shell command"}.get(kind, ""))
        self.shortcut.setEnabled(kind == "Type" and inject.SUPPORTED)
        self.pick.setEnabled(kind == "Run")
        self.test.setEnabled(kind != "Off" and bool(self.value.text()))

    def save(self):
        kind, value = self.kind.currentText(), self.value.text()
        self.update_buttons()
        self.window.store(self.key, Action(kind.lower(), value) if kind != "Off" and value else None)

    def start_capture(self):
        self.capture.clear()
        self.capture.show()
        self.capture.setFocus()
        self.window.message.setText("Press the shortcut")

    def end_capture(self):
        self.capture.hide()
        if not self.capture.keySequence().isEmpty():
            self.value.setText(self.value.text() + combo_text(self.capture.keySequence()))
            self.save()

    def choose_app(self):
        command = pick_app(self)
        if command:
            self.value.setText(command)
            self.save()

    def run_test(self):
        message = self.window.message
        if self.kind.currentText() == "Type":
            # typing would land in this window, so a test only copies
            try:
                step = actions.Type(self.value.text())
            except ValueError as e:
                message.setText(str(e))
                return
            self.window.copy(step.text)
            message.setText("Copied; paste to check" + (" (key combos not tested)" if step.combos else ""))
            return
        errors = tempfile.TemporaryFile()
        process = subprocess.Popen(
            self.value.text(), shell=True, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors
        )
        message.setText("Running…")
        QTimer.singleShot(1000, lambda: self.report(process, errors))

    def report(self, process, errors):
        code = process.poll()
        if code is None:
            self.window.message.setText("Started")
        else:
            errors.seek(0)
            first = errors.read().decode(errors="replace").strip().splitlines()[:1]
            self.window.message.setText(f"Exited with {code}" + (f": {first[0]}" if first else ""))
        errors.close()


class Window(QWidget):
    """The mapping editor: two rows of keycaps, then the selected key alone and with Fn."""

    def __init__(self, config, listener, copy):
        super().__init__()
        self.setWindowTitle("EmojiKeys")
        self.config = config
        self.listener = listener
        self.copy = copy
        self.device = "Looking for the keyboard…"
        self.lock = None

        self.header = QLabel()
        self.caps = {}
        group = QButtonGroup(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        for row_keys in (Reprog.EMOJI, Reprog.FKEYS):
            row = QHBoxLayout()
            for control_id in row_keys:
                cap = QToolButton(checkable=True)
                cap.setMinimumWidth(72)
                cap.clicked.connect(lambda checked, c=control_id: self.select(c))
                group.addButton(cap)
                row.addWidget(cap)
                self.caps[control_id] = cap
            row.addStretch()
            layout.addLayout(row)
        self.plain = Binding(self, False)
        self.with_fn = Binding(self, True)
        self.message = QLabel()
        for widget in (self.plain, self.with_fn, self.message):
            layout.addWidget(widget)

        listener.key.connect(self.on_key)
        listener.status.connect(self.on_status)
        listener.fn_lock.connect(self.on_fn_lock)
        self.refresh()
        self.select(Reprog.EMOJI[1])

    def reveal(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        self.listener.editing = True
        self.listener.restart()

    def hideEvent(self, event):
        self.listener.editing = False
        self.listener.restart()

    def on_key(self, control_id, pressed, fn):
        self.caps[control_id].setDown(pressed)
        if pressed and self.isVisible():
            self.select(control_id)

    def on_status(self, text):
        self.device = text
        self.refresh()

    def on_fn_lock(self, state):
        self.lock = state
        self.refresh()

    def refresh(self):
        lock = "" if self.lock is None else f" · media keys on {'F-key alone' if self.lock else 'Fn+F-key'}"
        self.header.setText(self.device + lock)
        for control_id, cap in self.caps.items():
            name = Reprog.NAMES[control_id]
            if control_id in Reprog.FKEYS:
                lines = [name, summary(self.config.keys.get((control_id, True))) or STOCK[control_id]]
            else:
                with_fn = self.config.keys.get((control_id, True))
                lines = [name, summary(self.config.keys.get((control_id, False))) or "–"]
                lines += [f"Fn: {summary(with_fn, 8)}"] if with_fn else []
            cap.setText("\n".join(lines))

    def select(self, control_id):
        self.caps[control_id].setChecked(True)
        # F keys only ever report their task, so they have the Fn row only
        self.plain.setVisible(control_id not in Reprog.FKEYS)
        self.plain.show_key(control_id)
        self.with_fn.show_key(control_id)
        self.message.clear()

    def store(self, key, action):
        """Set or clear (action None) one mapping, then apply and save it."""
        keys = dict(self.config.keys)
        if action:
            keys[key] = action
        else:
            keys.pop(key, None)
        config = Config(keys)
        if config == self.config:
            return
        try:
            self.listener.keymap.set(config)
            config.save()
        except (OSError, ValueError) as e:
            self.message.setText(str(e))
            return
        self.config = config
        self.message.setText("Saved")
        self.refresh()


def tray_icon():
    themed = QIcon.fromTheme("input-keyboard")
    if not themed.isNull():
        return themed
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    font = painter.font()
    font.setPixelSize(48)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "😊")
    painter.end()
    return QIcon(pixmap)


def watch_signals(app, show):
    """SIGUSR1 (a second launch) shows the window; SIGINT and SIGTERM quit, so the divert is undone."""
    read, write = socket.socketpair()
    read.setblocking(False)
    write.setblocking(False)
    signal.set_wakeup_fd(write.fileno())
    for number in (signal.SIGUSR1, signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda number, frame: None)
    notifier = QSocketNotifier(read.fileno(), QSocketNotifier.Type.Read, app)

    def on_signal():
        for number in read.recv(64):
            if number == signal.SIGUSR1:
                show()
            else:
                app.quit()

    notifier.activated.connect(on_signal)
    # the sockets live as long as the app
    app.signal_sockets = (read, write)


def main():
    lock = instance.acquire()
    if lock is None:
        pid = instance.owner()
        if pid:
            os.kill(pid, signal.SIGUSR1)
        print("EmojiKeys is already running; brought up its window")
        return
    app = QApplication(sys.argv)
    app.setApplicationName("EmojiKeys")
    app.setQuitOnLastWindowClosed(False)
    path = Config.path()
    try:
        config = Config.load(path)
    except FileNotFoundError:
        config = Config.default()
    except (OSError, ValueError) as e:
        QMessageBox.critical(None, "EmojiKeys", f"{e}\n\nFix or remove {path}, then start again.")
        sys.exit(1)
    copy = clipboard.Clipboard()
    listener = Listener(actions.Keymap(config, copy))
    window = Window(config, listener, copy)

    tray = QSystemTrayIcon(tray_icon(), app)
    menu = QMenu()
    menu.addAction("Open", window.reveal)
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: window.reveal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()

    watch_signals(app, window.reveal)
    app.aboutToQuit.connect(listener.stop)
    listener.thread.start()
    window.reveal()
    sys.exit(app.exec())
