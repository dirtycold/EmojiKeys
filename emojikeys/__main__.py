"""CLI: list receivers and devices, probe a device's controls, monitor key presses."""

import argparse
import logging
import struct
import sys
import threading
import time

from . import hidpp


class Command:
    """The sub-commands; one instance per run, holding the parsed arguments."""

    def __init__(self, args):
        self.args = args

    def doctor(self):
        from . import doctor

        sys.exit(0 if doctor.check() else 1)

    def list(self):
        for path, pid, product in hidpp.Channel.enumerate():
            print(f"{pid:04x} {product}  [{path.decode()}]")
            try:
                ch = hidpp.Channel(path, product)
            except OSError as e:
                print(f"  {e}")
                continue
            with ch:
                for devnum in ch.devnums:
                    dev = hidpp.Device(ch, devnum)
                    try:
                        major, minor = dev.ping()
                        print(f"  #{devnum:<3} HID++ {major}.{minor}  {dev.name()}")
                    except TimeoutError:
                        print(f"  #{devnum:<3} no reply within {hidpp.Timeout.DEVICE:g}s (off or out of range?)")
                    except hidpp.Error as e:
                        if e.code != hidpp.Receiver.UNKNOWN or self.args.verbose:
                            print(f"  #{devnum:<3} {e}")
        print(f"hidapi: {hidpp.library()._name}")

    def probe(self):
        def flags(value, table):
            return ",".join(name for bit, name in table if value & bit) or "-"

        found = hidpp.find(self.args.match)
        if not found:
            sys.exit(f"no reachable device matching {self.args.match!r} (tap a key to wake it, then retry)")
        ch, dev, name = found
        with ch:
            idx = dev.index(hidpp.Feature.REPROG)
            if idx is None:
                sys.exit(f"{name}: REPROG_CONTROLS_V4 not supported")
            count = dev.call(hidpp.Feature.REPROG, hidpp.Reprog.GET_COUNT)[0]
            print(f"#{dev.devnum} {name}: REPROG_CONTROLS_V4 at feature index {idx}, {count} controls\n")
            rows = []
            for i in range(count):
                info = dev.call(hidpp.Feature.REPROG, hidpp.Reprog.GET_INFO, bytes([i]))
                cid, tid, f1, _pos, group, gmask, f2 = struct.unpack("!HHBBBBB", info[:9])
                rep = dev.call(hidpp.Feature.REPROG, hidpp.Reprog.GET_REPORTING, struct.pack("!H", cid))
                _cid, m1, remap, m2 = struct.unpack("!HBHB", rep[:6])
                rows.append((
                    f"{i:2} {cid:#06x} {tid:#06x} {group:3} {gmask:#04x}  {remap:#06x}",
                    flags(f1 | f2 << 8, hidpp.Reprog.KEY_FLAGS),
                    flags(m1 | m2 << 8, hidpp.Reprog.MAP_FLAGS),
                    hidpp.Reprog.NAMES.get(cid, ""),
                ))
            # size the flag columns to their longest entry
            kw = max(len("key flags"), *(len(r[1]) for r in rows))
            mw = max(len("reporting"), *(len(r[2]) for r in rows))
            print(
                f"  {'#':>2} {'CID':6} {'TID':6} {'grp':>3} {'gmask':5} {'remap':6} "
                f"{'key flags':{kw}} {'reporting':{mw}} name"
            )
            for ids, key_flags, map_flags, cid_name in rows:
                print(f"  {ids} {key_flags:{kw}} {map_flags:{mw}} {cid_name}".rstrip())

    def monitor(self):
        found = hidpp.find(self.args.match)
        if not found:
            sys.exit(f"no reachable device matching {self.args.match!r} (tap a key to wake it, then retry)")
        ch, dev, name = found
        print(f"#{dev.devnum} {name}: diverting all mappable keys; press keys, Ctrl-C to stop")
        # Fn lock state -> where the media functions are
        media = {True: "F-key alone", False: "Fn+F-key"}
        with ch:
            mode = dev.platform()
            if mode is not None:
                print(f"OS mode: {mode}")
            lock = dev.fn_lock()
            if lock is not None:
                print(f"Fn lock: media keys on {media[lock]}")
            dev.listen(
                hidpp.Reprog.MAPPABLE,
                lambda cid, pressed, fn: print(
                    f"{time.strftime('%H:%M:%S')} {'press  ' if pressed else 'release'} "
                    f"{'Fn+' if fn else ''}{hidpp.Reprog.NAMES[cid]}"
                ),
                threading.Event(),
                lambda state: print(f"{time.strftime('%H:%M:%S')} Fn lock: media keys on {media[state]}"),
            )


def main():
    parser = argparse.ArgumentParser(prog="emojikeys", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="also log raw HID++ traffic")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list receivers and paired devices")
    sub.add_parser("doctor", help="check dependencies, config and device access without changing settings")
    for name, help_text in (
        ("probe", "dump REPROG_CONTROLS_V4 controls"),
        ("monitor", "divert the mappable keys and print presses until Ctrl-C (testing only)"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--match", default="POP Keys", help="device name substring (default: %(default)s)")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
    )
    try:
        getattr(Command(args), args.cmd)()
    except KeyboardInterrupt:
        print()
    except OSError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
