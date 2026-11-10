#!/usr/bin/env python3
"""Does typing pass through the HID++ interfaces? Prints report IDs and lengths only, never content."""

import collections
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from emojikeys import hidpp

SECONDS = 8

lib = hidpp.library()
paths = []
head = lib.hid_enumerate(hidpp.VID, 0)
node = head
while node:
    i = node.contents
    # 0xff00 via a receiver, 0xff43 via Bluetooth
    if i.usage_page in (0xFF00, 0xFF43):
        paths.append((i.path, i.usage_page, i.product_string or "?"))
    node = i.next
lib.hid_free_enumeration(head)

buf = ctypes.create_string_buffer(64)
for path, page, product in paths:
    handle = lib.hid_open_path(path)
    if not handle:
        print(f"{product} {page:#06x}: cannot open")
        continue
    print(f"{product} {page:#06x}: type a few letters now ({SECONDS} s)...", flush=True)
    counts = collections.Counter()
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        n = lib.hid_read_timeout(handle, buf, 64, 200)
        # keep only the report ID and length
        if n > 0:
            counts[(buf[0][0], n)] += 1
    lib.hid_close(handle)
    print("  seen:", ", ".join(f"id {rid:#04x} x{c} ({n} bytes)" for (rid, n), c in sorted(counts.items())) or "nothing")
