"""HID++ over libhidapi (ctypes): receivers, devices and REPROG_CONTROLS_V4 key diversion."""

import collections
import ctypes
import functools
import logging
import struct
import time

log = logging.getLogger(__name__)

VID = 0x046D
# vendor usage page of the HID++ interface
USAGE_PAGE = 0xFF00
# non-zero (0 = notification), as Solaar
SW_ID = 0x0B

# bare names first (as Solaar), then MacPorts / Homebrew / /usr/local, which dyld doesn't search
LIBRARIES = (
    "libhidapi.dylib",
    "/opt/local/lib/libhidapi.dylib",
    "/opt/homebrew/lib/libhidapi.dylib",
    "/usr/local/lib/libhidapi.dylib",
    "libhidapi-hidraw.so.0",
    "libhidapi-hidraw.so",
)


class Error(Exception):
    """HID++ error reply; code is set for HID++ 1.0 errors."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class Report:
    """HID++ report IDs and their sizes."""

    SHORT, LONG = 0x10, 0x11
    SIZES = {SHORT: 7, LONG: 20}


class Timeout:
    """Reply timeouts in seconds; devices may answer >1 s late after being idle (Solaar base.py)."""

    DEVICE = 4.0
    RECEIVER = 1.0


class Devnum:
    """Device numbers of the receiver itself and of a directly attached device."""

    RECEIVER = DIRECT = 0xFF


class Receiver:
    """HID++ 1.0 receiver registers, notifications and errors."""

    SET = 0x80
    GET = 0x81
    NOTIFICATIONS = 0x00
    WIRELESS = 0x000100
    SOFTWARE = 0x000800
    # device connection notification; flags bit 0x40 = link not established
    PAIRING = 0x41
    # nothing paired in that slot
    UNKNOWN = 0x09


class Feature:
    """HID++ 2.0 feature IDs."""

    ROOT = 0x0000
    NAME = 0x0005
    HOSTS = 0x1815
    REPROG = 0x1B04
    WIRELESS = 0x1D4B
    # Fn lock (Solaar K375S_FN_INVERSION); per host, event 0 = [host, state, ...]
    FN_LOCK = 0x40A3
    PLATFORM = 0x4531


class Platform:
    """PLATFORM (0x4531): OS bits of a platform descriptor."""

    OS = (
        (0x0100, "Windows"),
        (0x0200, "Windows Embedded"),
        (0x0400, "Linux"),
        (0x0800, "ChromeOS"),
        (0x1000, "Android"),
        (0x2000, "macOS"),
        (0x4000, "iOS"),
        (0x8000, "webOS"),
        (0x0001, "Tizen"),
    )


class Wireless:
    """WIRELESS_DEVICE_STATUS (0x1D4B)."""

    EVENT = 0x00
    RECONNECT = 0x01


class Reprog:
    """REPROG_CONTROLS_V4 (0x1B04)."""

    GET_COUNT, GET_INFO, GET_REPORTING, SET_REPORTING = range(4)
    DIVERTED = 0x00
    FN = 0x0034
    EMOJI = (0x0103, 0x0104, 0x0105, 0x0106, 0x0107, 0x0108)
    # F4-F12: only their task (media function) is diverted; F1-F3 (Easy-Switch) can't be
    FKEYS = (0x006E, 0x010A, 0x011C, 0x00E4, 0x00E5, 0x00E6, 0x00E7, 0x00E8, 0x00E9)
    MAPPABLE = FKEYS + EMOJI
    # emoji numbering matches the keyboard (checked 2026-11-09); F-key order follows the task IDs (unconfirmed)
    NAMES = {
        0x00D1: "F1",
        0x00D2: "F2",
        0x00D3: "F3",
        0x006E: "F4",
        0x010A: "F5",
        0x011C: "F6",
        0x00E4: "F7",
        0x00E5: "F8",
        0x00E6: "F9",
        0x00E7: "F10",
        0x00E8: "F11",
        0x00E9: "F12",
        0x0034: "Fn",
        0x0103: "Dictation",
        0x0104: "Emoji 1",
        0x0105: "Emoji 2",
        0x0106: "Emoji 3",
        0x0107: "Emoji 4",
        0x0108: "Emoji panel",
    }
    # getCidInfo bytes 4 and 8 as one bitfield (Solaar hidpp20.KeyFlag)
    KEY_FLAGS = (
        (0x0001, "mse"),
        (0x0002, "is_fn"),
        (0x0004, "nonstandard"),
        (0x0008, "fn_sensitive"),
        (0x0010, "reprog"),
        (0x0020, "divertable"),
        (0x0040, "p_divertable"),
        (0x0080, "virtual"),
        (0x0100, "raw_xy"),
        (0x0200, "force_raw_xy"),
        (0x0400, "analytics"),
        (0x0800, "raw_wheel"),
    )
    # get/setCidReporting bytes 2 and 5 as one bitfield (Solaar hidpp20.MappingFlag)
    MAP_FLAGS = (
        (0x0001, "diverted"),
        (0x0004, "p_diverted"),
        (0x0010, "raw_xy"),
        (0x0040, "force_raw_xy"),
        (0x0100, "analytics"),
        (0x0400, "raw_wheel"),
    )


class _DeviceInfo(ctypes.Structure):
    pass


_DeviceInfo._fields_ = [
    ("path", ctypes.c_char_p),
    ("vendor_id", ctypes.c_ushort),
    ("product_id", ctypes.c_ushort),
    ("serial_number", ctypes.c_wchar_p),
    ("release_number", ctypes.c_ushort),
    ("manufacturer_string", ctypes.c_wchar_p),
    ("product_string", ctypes.c_wchar_p),
    ("usage_page", ctypes.c_ushort),
    ("usage", ctypes.c_ushort),
    ("interface_number", ctypes.c_int),
    ("next", ctypes.POINTER(_DeviceInfo)),
    ("bus_type", ctypes.c_int),
]


@functools.cache
def library():
    """Load libhidapi once."""
    c = ctypes
    for name in LIBRARIES:
        try:
            lib = c.cdll.LoadLibrary(name)
        except OSError:
            continue
        for fn, args, res in (
            ("hid_init", [], c.c_int),
            ("hid_enumerate", [c.c_ushort, c.c_ushort], c.POINTER(_DeviceInfo)),
            ("hid_free_enumeration", [c.POINTER(_DeviceInfo)], None),
            ("hid_open_path", [c.c_char_p], c.c_void_p),
            ("hid_write", [c.c_void_p, c.c_char_p, c.c_size_t], c.c_int),
            ("hid_read_timeout", [c.c_void_p, c.c_char_p, c.c_size_t, c.c_int], c.c_int),
            ("hid_close", [c.c_void_p], None),
            ("hid_error", [c.c_void_p], c.c_wchar_p),
        ):
            f = getattr(lib, fn)
            f.argtypes, f.restype = args, res
        # macOS: don't take the device away from the OS
        if hasattr(lib, "hid_darwin_set_open_exclusive"):
            lib.hid_darwin_set_open_exclusive.argtypes = [c.c_int]
            lib.hid_darwin_set_open_exclusive(0)
        lib.hid_init()
        return lib
    raise OSError("cannot load libhidapi, tried: " + ", ".join(LIBRARIES))


class Channel:
    """One open HID++ interface: a receiver or a directly attached device."""

    def __init__(self, path, product=""):
        self.product = product
        # unsolicited reports read while waiting for a reply
        self._pending = collections.deque(maxlen=64)
        self._lib = library()
        self._handle = self._lib.hid_open_path(path)
        if not self._handle:
            raise OSError(f"cannot open {path.decode()}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @staticmethod
    def enumerate():
        """Return [(path, product_id, product_string)] of Logitech HID++ interfaces."""
        lib = library()
        found = {}
        head = lib.hid_enumerate(VID, 0)
        node = head
        while node:
            info = node.contents
            if info.usage_page == USAGE_PAGE and info.path not in found:
                found[info.path] = (info.path, info.product_id, info.product_string or "?")
            node = info.next
        lib.hid_free_enumeration(head)
        # enumeration order is not stable
        return sorted(found.values(), key=lambda item: (item[1], item[0]))

    @property
    def devnums(self):
        return range(1, 7) if "receiver" in self.product.lower() else (Devnum.DIRECT,)

    def close(self):
        if self._handle:
            self._lib.hid_close(self._handle)
            self._handle = None

    def read(self, timeout_ms):
        buf = ctypes.create_string_buffer(64)
        n = self._lib.hid_read_timeout(self._handle, buf, 64, timeout_ms)
        if n < 0:
            raise OSError(f"hid_read: {self._lib.hid_error(self._handle)}")
        if n:
            log.debug("<< %s", buf.raw[:n].hex(" "))
        return buf.raw[:n]

    def report(self, timeout_ms):
        """Next unsolicited report: one set aside during a request first, else a fresh read."""
        return self._pending.popleft() if self._pending else self.read(timeout_ms)

    def request(self, devnum, b2, b3, params=b"", timeout=Timeout.DEVICE):
        """Send [devnum, b2, b3, params] and return the reply bytes after b3.

        b2/b3 are feature index / function+sw_id (HID++ 2.0) or sub_id / register (HID++ 1.0).
        """
        data = bytes([b2, b3]) + params
        rid = Report.SHORT if len(data) <= Report.SIZES[Report.SHORT] - 2 else Report.LONG
        buf = bytes([rid, devnum]) + data.ljust(Report.SIZES[rid] - 2, b"\0")
        log.debug(">> %s", buf.hex(" "))
        if self._lib.hid_write(self._handle, buf, len(buf)) < 0:
            raise OSError(f"hid_write: {self._lib.hid_error(self._handle)}")
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            r = self.read(int(left * 1000) + 1)
            if len(r) >= 6 and r[0] in (Report.SHORT, Report.LONG) and r[1] == devnum:
                if r[2] == b2 and r[3] == b3:
                    return r[4:]
                if r[2] in (0x8F, 0xFF) and r[3] == b2 and r[4] == b3:
                    hidpp10 = r[2] == 0x8F
                    raise Error(f"HID++ {'1.0' if hidpp10 else '2.0'} error {r[5]:#04x}", r[5] if hidpp10 else None)
            if r:
                self._pending.append(r)
        raise TimeoutError(f"no reply from device {devnum:#04x}")

    def register(self, reg, value=None):
        """Read a receiver register, or write it when value is given."""
        if value is None:
            data = self.request(Devnum.RECEIVER, Receiver.GET, reg, timeout=Timeout.RECEIVER)
            return int.from_bytes(data[:3], "big")
        self.request(Devnum.RECEIVER, Receiver.SET, reg, value.to_bytes(3, "big"), Timeout.RECEIVER)


class Device:
    """A HID++ 2.0 device reachable through a Channel."""

    # link-up (0x41) and reconnection (0x1D4B) arrive together; divert once
    DEBOUNCE = 2.0

    def __init__(self, channel, devnum):
        self.channel = channel
        self.devnum = devnum
        self._features = {Feature.ROOT: 0}

    def ping(self):
        """Return the HID++ protocol version (major, minor)."""
        data = self.channel.request(self.devnum, 0x00, 0x10 | SW_ID, b"\0\0\x5a")
        return data[0], data[1]

    def index(self, feature):
        """Return the feature's index, or None when unsupported."""
        if feature not in self._features:
            data = self.call(Feature.ROOT, 0x0, struct.pack("!H", feature))
            self._features[feature] = data[0] or None
        return self._features[feature]

    def call(self, feature, func, params=b""):
        idx = self.index(feature)
        if idx is None:
            raise Error(f"feature {feature:#06x} not supported")
        return self.channel.request(self.devnum, idx, (func << 4) | SW_ID, params)

    def name(self):
        count = self.call(Feature.NAME, 0x0)[0]
        name = b""
        while len(name) < count:
            chunk = self.call(Feature.NAME, 0x1, bytes([len(name)])).rstrip(b"\0")
            if not chunk:
                break
            name += chunk[: count - len(name)]
        return name.decode(errors="replace")

    def divert(self, cids, on=True):
        """setCidReporting: divert bit plus its valid bit (0x02), no remap."""
        for cid in cids:
            self.call(Feature.REPROG, Reprog.SET_REPORTING, struct.pack("!HBH", cid, 0x03 if on else 0x02, 0))

    def fn_lock(self):
        """Return True when the F keys give their media function without Fn, or None if unsupported."""
        if self.index(Feature.FN_LOCK) is None:
            return None
        host = self.call(Feature.HOSTS, 0x0)[3] if self.index(Feature.HOSTS) is not None else 0
        return bool(self.call(Feature.FN_LOCK, 0x0, bytes([host]))[1])

    def platform(self):
        """Return the name of the keyboard's current OS mode, or None if unsupported."""
        if self.index(Feature.PLATFORM) is None:
            return None
        info = self.call(Feature.PLATFORM, 0x0)
        current = info[6]
        for i in range(info[2]):
            index, _, os_flags = struct.unpack("!BBH", self.call(Feature.PLATFORM, 0x1, bytes([i]))[:4])
            if index == current:
                return ", ".join(name for bit, name in Platform.OS if os_flags & bit) or f"platform {current}"
        return f"platform {current}"

    def listen(self, cids, on_key, stop, on_fn_lock=None):
        """Divert cids and call on_key(cid, pressed, fn) until the stop Event is set.

        fn tells whether Fn was held. on_fn_lock(state) is called when the user toggles Fn lock.
        Blocking; run it in a thread. Re-diverts after a reconnect and undoes the divert on exit.
        """
        ch = self.channel
        reprog = self.index(Feature.REPROG)
        wireless = self.index(Feature.WIRELESS)
        lock = self.index(Feature.FN_LOCK)
        flags = None
        if self.devnum != Devnum.DIRECT:
            # have the receiver report link up/down (0x41), as Solaar does
            flags = ch.register(Receiver.NOTIFICATIONS)
            ch.register(Receiver.NOTIFICATIONS, flags | Receiver.WIRELESS | Receiver.SOFTWARE)
        self.divert(cids)
        log.info("diverted %s", ", ".join(Reprog.NAMES.get(c, f"{c:#06x}") for c in cids))
        diverted = time.monotonic()
        down = ()
        fn_held = False
        try:
            while not stop.is_set():
                r = ch.report(500)
                if len(r) < 7 or r[0] not in (Report.SHORT, Report.LONG) or r[1] != self.devnum:
                    continue
                if r[2] == reprog and r[3] == Reprog.DIVERTED:
                    held = struct.unpack("!4H", r[4:12])
                    fn = Reprog.FN in held
                    if fn != fn_held:
                        log.info("Fn %s", "down" if fn else "up")
                        fn_held = fn
                    now = tuple(c for c in held if c in cids)
                    for cid in now:
                        if cid not in down:
                            on_key(cid, True, fn)
                    for cid in down:
                        if cid not in now:
                            on_key(cid, False, fn)
                    down = now
                    continue
                if r[2] == lock and r[3] == 0x00:
                    if on_fn_lock:
                        on_fn_lock(bool(r[5]))
                    continue
                if r[2] == wireless and r[3] == Wireless.EVENT:
                    up = r[4] == Wireless.RECONNECT
                    log.info("wireless status %#04x%s", r[4], " (reconnected)" if up else "")
                elif r[2] == Receiver.PAIRING:
                    up = not r[4] & 0x40
                    log.info("link %s", "up" if up else "down")
                else:
                    continue
                if not up:
                    continue
                down = ()
                if time.monotonic() - diverted > self.DEBOUNCE:
                    for attempt in range(1, 4):
                        try:
                            self.divert(cids)
                            log.info("re-diverted")
                            break
                        except (Error, TimeoutError) as e:
                            log.warning("re-divert attempt %d failed: %s", attempt, e)
                            time.sleep(0.5)
                    diverted = time.monotonic()
        finally:
            try:
                self.divert(cids, False)
                log.info("divert removed")
            except (Error, TimeoutError) as e:
                log.warning("could not undo the divert (%s); power-cycling the device resets it", e)
            if flags is not None:
                ch.register(Receiver.NOTIFICATIONS, flags)
                log.info("receiver notification flags restored")


def find(match):
    """Return (channel, device, name) of the first device whose name contains match, or None.

    The returned channel stays open; close it when done.
    """
    for path, _pid, product in Channel.enumerate():
        try:
            ch = Channel(path, product)
        except OSError as e:
            log.warning("%s", e)
            continue
        for devnum in ch.devnums:
            dev = Device(ch, devnum)
            try:
                dev.ping()
                name = dev.name()
            except (Error, TimeoutError):
                continue
            if match.lower() in name.lower():
                return ch, dev, name
        ch.close()
    return None
