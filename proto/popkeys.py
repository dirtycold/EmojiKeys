#!/usr/bin/env python3
"""Logitech POP Keys HID++ prototype.

Stdlib + ctypes over libhidapi only.
  list    - list Logitech receivers and the HID++ devices paired to them (read-only)
  probe   - dump REPROG_CONTROLS_V4 (0x1B04) controls of a device (read-only)
  listen  - divert the emoji keys and print their press/release events until Ctrl-C;
            re-diverts on reconnect, restores the keys and receiver notification flags on exit
"""

import argparse
import collections
import ctypes
import struct
import sys
import time


class Hidpp:
    """HID++ over libhidapi: constants, the hidapi loader, Channel and Device; libhidapi is loaded once, on first use."""

    # Logitech
    VID = 0x046D
    # vendor page of the HID++ interface
    USAGE_PAGE = 0xFF00
    SHORT_ID, LONG_ID = 0x10, 0x11
    SHORT_SIZE, LONG_SIZE = 7, 20
    # same choice as Solaar: non-zero (0 = notification), high bit set
    SW_ID = 0x0B
    # Solaar base.py: devices may answer >1 s late after being idle
    DEVICE_TIMEOUT = 4.0
    # Solaar base.py uses 0.9 s; the receiver answers at once
    RECEIVER_TIMEOUT = 1.0
    # device attached directly (Bluetooth / USB), not via a receiver
    DIRECT_DEVNUM = 0xFF
    # the receiver itself
    RECEIVER_DEVNUM = 0xFF

    class Error(Exception):
        """HID++ error reply; code is set for HID++ 1.0 errors (see ERR_*)."""

        def __init__(self, message, code=None):
            super().__init__(message)
            self.code = code

    # HID++ 1.0 receiver registers, notifications and errors (Solaar hidpp10_constants)
    SUB_SET_REGISTER = 0x80
    SUB_GET_REGISTER = 0x81
    REG_NOTIFICATIONS = 0x00
    # report device link up/down (0x41)
    NOTIF_WIRELESS = 0x000100
    NOTIF_SOFTWARE_PRESENT = 0x000800
    # device connection notification; flags byte bit 0x40 = link NOT established
    DJ_PAIRING = 0x41
    # nothing paired in that slot
    ERR_UNKNOWN_DEVICE = 0x09

    # libhidapi via ctypes.
    # Bare names first (what Solaar does), then MacPorts / Homebrew / /usr/local,
    # because macOS dyld does not search /opt/local/lib or /opt/homebrew/lib by default.
    NAMES = (
        "libhidapi.dylib",
        "/opt/local/lib/libhidapi.dylib",
        "/opt/homebrew/lib/libhidapi.dylib",
        "/usr/local/lib/libhidapi.dylib",
        "libhidapi-hidraw.so.0",
        "libhidapi-hidraw.so",
    )

    class DeviceInfo(ctypes.Structure):
        pass

    DeviceInfo._fields_ = [
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
        ("next", ctypes.POINTER(DeviceInfo)),
        ("bus_type", ctypes.c_int),
    ]

    name = None
    lib = None

    @classmethod
    def load(cls):
        if cls.lib:
            return cls.lib
        for name in cls.NAMES:
            try:
                lib = ctypes.cdll.LoadLibrary(name)
            except OSError:
                continue
            lib.hid_init.restype = ctypes.c_int
            lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
            lib.hid_enumerate.restype = ctypes.POINTER(cls.DeviceInfo)
            lib.hid_free_enumeration.argtypes = [ctypes.POINTER(cls.DeviceInfo)]
            lib.hid_open_path.argtypes = [ctypes.c_char_p]
            lib.hid_open_path.restype = ctypes.c_void_p
            lib.hid_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
            lib.hid_write.restype = ctypes.c_int
            lib.hid_read_timeout.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
            lib.hid_read_timeout.restype = ctypes.c_int
            lib.hid_close.argtypes = [ctypes.c_void_p]
            lib.hid_error.argtypes = [ctypes.c_void_p]
            lib.hid_error.restype = ctypes.c_wchar_p
            # macOS: don't grab the device from the OS
            if hasattr(lib, "hid_darwin_set_open_exclusive"):
                lib.hid_darwin_set_open_exclusive.argtypes = [ctypes.c_int]
                lib.hid_darwin_set_open_exclusive(0)
            lib.hid_init()
            cls.name, cls.lib = name, lib
            return lib
        sys.exit("cannot load libhidapi, tried: " + ", ".join(cls.NAMES))

    class Feature:
        """HID++ 2.0 feature IDs used here (Solaar SupportedFeature)."""

        ROOT = 0x0000
        DEVICE_NAME = 0x0005
        WIRELESS_STATUS = 0x1D4B
        REPROG_V4 = 0x1B04

    class WirelessStatus:
        """WIRELESS_DEVICE_STATUS (0x1D4B)."""

        # status broadcast: status, request, reason
        EVENT = 0x00
        RECONNECTION = 0x01

    class ReprogControls:
        """REPROG_CONTROLS_V4 (0x1B04)."""

        # up to 4 CIDs currently held (also reports Fn 0x0034)
        DIVERTED_BUTTONS_EVENT = 0x00
        EMOJI_CIDS = (0x0103, 0x0104, 0x0105, 0x0106, 0x0107, 0x0108)
        # from Solaar special_keys.CONTROL
        KNOWN_CIDS = {
            0x0034: "Fn",
            0x0103: "Dictation",
            0x0104: "Emoji Smiley Heart Eyes",
            0x0105: "Emoji Crying Face",
            0x0106: "Emoji Smiley",
            0x0107: "Emoji Smilie With Tears",
            0x0108: "Emoji (panel)",
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

    class Channel:
        """One open HID++ interface (a receiver, or a directly attached device)."""

        def __init__(self, path, verbose=False):
            self.path = path
            self.verbose = verbose
            # unsolicited reports read while awaiting a reply
            self._pending = collections.deque(maxlen=64)
            self.lib = Hidpp.load()
            self.handle = self.lib.hid_open_path(path)
            if not self.handle:
                raise OSError(f"cannot open {path.decode()}")

        @staticmethod
        def enumerate():
            """Return [(path, product_id, product_string)] of Logitech HID++ interfaces, one per path."""
            found = {}
            lib = Hidpp.load()
            head = lib.hid_enumerate(Hidpp.VID, 0)
            node = head
            while node:
                info = node.contents
                if info.usage_page == Hidpp.USAGE_PAGE and info.path not in found:
                    found[info.path] = (info.path, info.product_id, info.product_string or "?")
                node = info.next
            lib.hid_free_enumeration(head)
            # enumeration order is not stable
            return sorted(found.values(), key=lambda item: (item[1], item[0]))

        def close(self):
            if self.handle:
                self.lib.hid_close(self.handle)
                self.handle = None

        def write(self, devnum, data):
            if len(data) <= Hidpp.SHORT_SIZE - 2:
                buf = bytes([Hidpp.SHORT_ID, devnum]) + data.ljust(Hidpp.SHORT_SIZE - 2, b"\0")
            else:
                buf = bytes([Hidpp.LONG_ID, devnum]) + data.ljust(Hidpp.LONG_SIZE - 2, b"\0")
            if self.verbose:
                print("  >>", buf.hex(" "))
            if self.lib.hid_write(self.handle, buf, len(buf)) < 0:
                raise OSError(f"hid_write: {self.lib.hid_error(self.handle)}")

        def read(self, timeout_ms):
            buf = ctypes.create_string_buffer(64)
            n = self.lib.hid_read_timeout(self.handle, buf, 64, timeout_ms)
            if n < 0:
                raise OSError(f"hid_read: {self.lib.hid_error(self.handle)}")
            if self.verbose and n:
                print("  <<", buf.raw[:n].hex(" "))
            return buf.raw[:n]

        def next_report(self, timeout_ms):
            """Next unsolicited report: one set aside during a request first, else a fresh read."""
            if self._pending:
                return self._pending.popleft()
            return self.read(timeout_ms)

        def exchange(self, devnum, b2, b3, params=b"", timeout=None):
            """Send [devnum, b2, b3, params] and wait for the matching reply; return the bytes after b3.

            b2/b3 are feature index / function+sw_id (HID++ 2.0) or sub_id / register (HID++ 1.0).
            Other reports read meanwhile are kept for next_report().
            """
            self.write(devnum, bytes([b2, b3]) + params)
            timeout = timeout or Hidpp.DEVICE_TIMEOUT
            deadline = time.monotonic() + timeout
            while (left := deadline - time.monotonic()) > 0:
                r = self.read(int(left * 1000) + 1)
                if len(r) >= 6 and r[0] in (Hidpp.SHORT_ID, Hidpp.LONG_ID) and r[1] == devnum:
                    if r[2] == b2 and r[3] == b3:
                        return r[4:]
                    if r[2] in (0x8F, 0xFF) and r[3] == b2 and r[4] == b3:
                        kind = "1.0" if r[2] == 0x8F else "2.0"
                        raise Hidpp.Error(f"HID++ {kind} error {r[5]:#04x}", r[5] if kind == "1.0" else None)
                if r:
                    self._pending.append(r)
            raise TimeoutError(f"no reply from device {devnum:#04x}")

        def request(self, devnum, feat_idx, func, params=b"", timeout=None):
            return self.exchange(devnum, feat_idx, (func << 4) | Hidpp.SW_ID, params, timeout)

        def get_register(self, reg):
            value = self.exchange(Hidpp.RECEIVER_DEVNUM, Hidpp.SUB_GET_REGISTER, reg, timeout=Hidpp.RECEIVER_TIMEOUT)
            return int.from_bytes(value[:3], "big")

        def set_register(self, reg, value):
            self.exchange(
                Hidpp.RECEIVER_DEVNUM, Hidpp.SUB_SET_REGISTER, reg, value.to_bytes(3, "big"), Hidpp.RECEIVER_TIMEOUT
            )

    class Device:
        """A HID++ 2.0 device reachable through a Channel."""

        def __init__(self, channel, devnum):
            self.channel = channel
            self.devnum = devnum
            self._features = {Hidpp.Feature.ROOT: 0}

        def ping(self):
            data = self.channel.request(self.devnum, 0x00, 0x1, b"\0\0\x5a")
            # protocol major, minor
            return data[0], data[1]

        def feature_index(self, feature):
            if feature not in self._features:
                data = self.call(Hidpp.Feature.ROOT, 0x0, struct.pack("!H", feature))
                self._features[feature] = data[0] or None
            return self._features[feature]

        def call(self, feature, func, params=b""):
            idx = self.feature_index(feature) if feature != Hidpp.Feature.ROOT else 0
            if idx is None:
                raise Hidpp.Error(f"feature {feature:#06x} not supported")
            return self.channel.request(self.devnum, idx, func, params)

        def name(self):
            count = self.call(Hidpp.Feature.DEVICE_NAME, 0x0)[0]
            name = b""
            while len(name) < count:
                chunk = self.call(Hidpp.Feature.DEVICE_NAME, 0x1, bytes([len(name)])).rstrip(b"\0")
                if not chunk:
                    break
                name += chunk[: count - len(name)]
            return name.decode(errors="replace")

        def set_divert(self, cids, on):
            """setCidReporting: divert bit + its 'valid' bit (0x02), no remap."""
            for cid in cids:
                self.call(Hidpp.Feature.REPROG_V4, 0x3, struct.pack("!HBH", cid, 0x03 if on else 0x02, 0))

        def redivert(self, cids, tries=3):
            for attempt in range(1, tries + 1):
                try:
                    self.set_divert(cids, True)
                    print("  re-diverted")
                    return True
                except (Hidpp.Error, TimeoutError) as e:
                    print(f"  re-divert attempt {attempt} failed: {e}")
                    time.sleep(0.5)
            return False


class Command:
    """The sub-commands; one instance per run, holding the parsed arguments."""

    # 0x41 link-up and 0x1D4B reconnection arrive together; divert once
    REDIVERT_DEBOUNCE = 2.0

    def __init__(self, args):
        self.args = args

    @staticmethod
    def _flag_names(value, table):
        return ",".join(name for bit, name in table if value & bit) or "-"

    @staticmethod
    def _stamp():
        return time.strftime("%H:%M:%S")

    def _scan(self):
        """Yield (channel, product_string, Device, name, protocol) for every reachable HID++ 2.0 device."""
        verbose = self.args.verbose
        for path, pid, product in Hidpp.Channel.enumerate():
            try:
                channel = Hidpp.Channel(path, verbose)
            except OSError as e:
                print(f"{pid:04x} {product}: {e}")
                continue
            devnums = range(1, 7) if "receiver" in product.lower() else (Hidpp.DIRECT_DEVNUM,)
            print(f"{pid:04x} {product}  [{path.decode()}]")
            for devnum in devnums:
                dev = Hidpp.Device(channel, devnum)
                try:
                    proto = dev.ping()
                    name = dev.name()
                except TimeoutError:
                    print(f"  #{devnum:<3} no reply within {Hidpp.DEVICE_TIMEOUT:g}s (off or out of range?)")
                    continue
                except Hidpp.Error as e:
                    if e.code != Hidpp.ERR_UNKNOWN_DEVICE or verbose:
                        print(f"  #{devnum:<3} {e}")
                    continue
                yield channel, product, dev, name, proto
            channel.close()

    def _find_device(self):
        """Return (channel, Device) of the first device whose name contains --match; channel stays open."""
        for channel, _product, dev, name, proto in self._scan():
            if self.args.match.lower() in name.lower():
                print(f"  #{dev.devnum} HID++ {proto[0]}.{proto[1]}  {name}  <- match")
                return channel, dev
        print(f"no reachable device matching {self.args.match!r} (tap a key to wake it, then retry)")
        return None, None

    def list(self):
        for _channel, _product, dev, name, proto in self._scan():
            print(f"  #{dev.devnum:<3} HID++ {proto[0]}.{proto[1]}  {name}")
        print(f"hidapi: {Hidpp.name}")

    def probe(self):
        channel, dev = self._find_device()
        if not dev:
            return
        idx = dev.feature_index(Hidpp.Feature.REPROG_V4)
        if idx is None:
            print("  REPROG_CONTROLS_V4 not supported")
            channel.close()
            return
        count = dev.call(Hidpp.Feature.REPROG_V4, 0x0)[0]
        print(f"  REPROG_CONTROLS_V4 at feature index {idx}, {count} controls\n")
        print(f"  {'#':>2} {'CID':6} {'TID':6} {'grp':>3} {'gmask':5} {'remap':6} {'key flags':40} {'reporting':12} name")
        for i in range(count):
            info = dev.call(Hidpp.Feature.REPROG_V4, 0x1, bytes([i]))
            cid, tid, f1, _pos, group, gmask, f2 = struct.unpack("!HHBBBBB", info[:9])
            rep = dev.call(Hidpp.Feature.REPROG_V4, 0x2, struct.pack("!H", cid))
            _rcid, m1, remap, m2 = struct.unpack("!HBHB", rep[:6])
            key_flags = self._flag_names(f1 | f2 << 8, Hidpp.ReprogControls.KEY_FLAGS)
            map_flags = self._flag_names(m1 | m2 << 8, Hidpp.ReprogControls.MAP_FLAGS)
            print(
                f"  {i:2} {cid:#06x} {tid:#06x} {group:3} {gmask:#04x}  {remap:#06x} "
                f"{key_flags:40} {map_flags:12} {Hidpp.ReprogControls.KNOWN_CIDS.get(cid, '')}"
            )
        channel.close()

    def _print_key_changes(self, down, now):
        for cid in now:
            if cid not in down:
                print(f"{self._stamp()} press   {cid:#06x} {Hidpp.ReprogControls.KNOWN_CIDS.get(cid, '')}")
        for cid in down:
            if cid not in now:
                print(f"{self._stamp()} release {cid:#06x} {Hidpp.ReprogControls.KNOWN_CIDS.get(cid, '')}")

    def listen(self):
        channel, dev = self._find_device()
        if not dev:
            return
        cids = Hidpp.ReprogControls.EMOJI_CIDS
        reprog_idx = dev.feature_index(Hidpp.Feature.REPROG_V4)
        status_idx = dev.feature_index(Hidpp.Feature.WIRELESS_STATUS)
        old_flags = None
        # ask the receiver to report link up/down (0x41), like Solaar does
        if dev.devnum != Hidpp.DIRECT_DEVNUM:
            old_flags = channel.get_register(Hidpp.REG_NOTIFICATIONS)
            new_flags = old_flags | Hidpp.NOTIF_WIRELESS | Hidpp.NOTIF_SOFTWARE_PRESENT
            channel.set_register(Hidpp.REG_NOTIFICATIONS, new_flags)
            print(f"  receiver notification flags {old_flags:#08x} -> {new_flags:#08x}")
        dev.set_divert(cids, True)
        last_divert = time.monotonic()
        print(f"  diverted {', '.join(f'{c:#06x}' for c in cids)}; press emoji keys, Ctrl-C to stop\n")
        down = ()
        try:
            while True:
                r = channel.next_report(1000)
                if len(r) < 7 or r[0] not in (Hidpp.SHORT_ID, Hidpp.LONG_ID) or r[1] != dev.devnum:
                    continue
                reconnected = False
                if r[2] == reprog_idx and r[3] == Hidpp.ReprogControls.DIVERTED_BUTTONS_EVENT:
                    # drops Fn
                    now = tuple(c for c in struct.unpack("!4H", r[4:12]) if c in cids)
                    self._print_key_changes(down, now)
                    down = now
                elif r[2] == status_idx and r[3] == Hidpp.WirelessStatus.EVENT:
                    print(f"{self._stamp()} wireless status {r[4]:#04x} request {r[5]:#04x} reason {r[6]:#04x}")
                    reconnected = r[4] == Hidpp.WirelessStatus.RECONNECTION
                elif r[2] == Hidpp.DJ_PAIRING:
                    reconnected = not (r[4] & 0x40)
                    print(f"{self._stamp()} link {'up' if reconnected else 'down'}")
                if reconnected:
                    down = ()
                    if time.monotonic() - last_divert > self.REDIVERT_DEBOUNCE:
                        dev.redivert(cids)
                        last_divert = time.monotonic()
        except KeyboardInterrupt:
            print()
        finally:
            try:
                dev.set_divert(cids, False)
                print("  divert removed")
            except (Hidpp.Error, TimeoutError) as e:
                print(f"  could not remove divert ({e}); power-cycling the keyboard resets it")
            if old_flags is not None:
                channel.set_register(Hidpp.REG_NOTIFICATIONS, old_flags)
                print(f"  receiver notification flags restored to {old_flags:#08x}")
            channel.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="dump raw HID++ traffic")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list receivers and paired devices")
    for name, help_text in (
        ("probe", "dump REPROG_CONTROLS_V4 controls"),
        ("listen", "divert emoji keys and print their events"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--match", default="POP Keys", help="device name substring (default: %(default)s)")
    args = parser.parse_args()
    getattr(Command(args), args.cmd)()


if __name__ == "__main__":
    main()
