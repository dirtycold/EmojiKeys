"""Clipboard for the copy fallback of type actions. Qt; imported by the UI and doctor only.

On Wayland a client may set the clipboard only right after its own input event, and the keys
arrive over hidraw (verified 2026-11-12, see docs/plan.md). There Klipper's D-Bus is used (KDE),
else wl-copy; QClipboard everywhere else.
"""

import logging
import os
import shutil
import subprocess

from qtpy.QtCore import QObject, Signal, Slot
from qtpy.QtGui import QGuiApplication

log = logging.getLogger(__name__)

KLIPPER = "org.kde.klipper"


def wayland():
    app = QGuiApplication.instance()
    return app.platformName() == "wayland" if app else bool(os.environ.get("WAYLAND_DISPLAY"))


def method():
    """Return how text is copied here: "klipper", "wl-copy" or "qt"; None when it can't be. Read-only."""
    if not wayland():
        return "qt"
    from qtpy.QtDBus import QDBusConnection

    if QDBusConnection.sessionBus().interface().isServiceRegistered(KLIPPER).value():
        return "klipper"
    if shutil.which("wl-copy"):
        return "wl-copy"
    return None


class Clipboard(QObject):
    """Callable as copy(text) from any thread; the copy itself runs on the GUI thread.

    How to copy is detected once, at creation (after the QApplication).
    """

    requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.how = method()
        log.info("clipboard: %s", self.how or "none")
        # queued when emitted from the listener thread
        self.requested.connect(self.copy)

    def __call__(self, text):
        self.requested.emit(text)

    @Slot(str)
    def copy(self, text):
        # an exception escaping a slot aborts PyQt6
        try:
            if self.how == "klipper":
                from qtpy.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

                klipper = QDBusInterface(KLIPPER, "/klipper", "org.kde.klipper.klipper", QDBusConnection.sessionBus())
                reply = klipper.call("setClipboardContents", text)
                if reply.type() == QDBusMessage.MessageType.ErrorMessage:
                    raise OSError(f"Klipper: {reply.errorMessage()}")
            elif self.how == "wl-copy":
                # wl-copy forks and keeps serving the text after we return
                subprocess.run(
                    ["wl-copy"],
                    input=text.encode(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=2,
                )
            elif self.how == "qt":
                QGuiApplication.clipboard().setText(text)
            else:
                log.warning("cannot copy on this Wayland desktop without Klipper; install wl-clipboard (wl-copy)")
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("copy failed: %s", e)
