#!/usr/bin/env python3
"""Does typing pass through the HID++ interfaces? Prints report IDs and lengths only, never content."""

import collections
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from emojikeys import hidpp, terminal

SECONDS = 8


lib = hidpp.library()
# hidapi lists a node once per top-level collection; test each node once
paths = {}
head = lib.hid_enumerate(hidpp.VID, 0)
node = head
while node:
    i = node.contents
    # 0xff00 via a receiver, 0xff43 via Bluetooth
    if i.usage_page in (0xFF00, 0xFF43) and i.path not in paths:
        # Field meanings: https://libusb.info/hidapi/structhid__device__info.html
        paths[i.path] = (i.product_id, i.usage_page, i.product_string or "?")
    node = i.next
lib.hid_free_enumeration(head)

buf = ctypes.create_string_buffer(64)
for path, (pid, page, product) in sorted(paths.items(), key=lambda item: (item[1][0], item[0])):
    # receivers are 0xc5xx; others are a paired device's node (Linux logitech-dj) or Bluetooth
    kind = "receiver" if pid & 0xFF00 == 0xC500 else "device"
    print(f"{pid:04x} {product} {page:#06x} {kind} [{path.decode()}]", flush=True)
    handle = lib.hid_open_path(path)
    if not handle:
        print("  cannot open")
        continue
    print(f"  type a few letters now ({SECONDS} s, not echoed)...", flush=True)
    counts = collections.Counter()
    end = time.monotonic() + SECONDS
    try:
        with terminal.quiet():
            while time.monotonic() < end:
                n = lib.hid_read_timeout(handle, buf, 64, 200)
                # keep only the report ID and length
                if n > 0:
                    counts[(buf[0][0], n)] += 1
    except KeyboardInterrupt:
        print("  stopped")
        sys.exit(1)
    finally:
        lib.hid_close(handle)
    print("  seen:", ", ".join(f"id {rid:#04x} x{c} ({n} bytes)" for (rid, n), c in sorted(counts.items())) or "nothing")
