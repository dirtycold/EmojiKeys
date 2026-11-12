# Linux quick check

Run from the repo root, with Solaar quit. Paste the output back.

1. hidapi and the Logitech hidraw nodes (bus 0003 = USB, 0005 = Bluetooth) with their permissions:

```
/sbin/ldconfig -p | grep hidapi; for n in /sys/class/hidraw/hidraw*; do grep -qi 046d $n/device/uevent && echo "$(ls -l /dev/${n##*/}) $(grep -h HID_NAME $n/device/uevent) $(readlink -f $n)"; done; ls /etc/udev/rules.d /usr/lib/udev/rules.d 2>/dev/null | grep -i logitech
```

2. Receivers, devices and the POP Keys controls:

```
python3 -m emojikeys list && python3 -m emojikeys probe
```

3. Keys: press the emoji keys, Dictation and Fn+F12, then Ctrl-C:

```
python3 -m emojikeys monitor
```

4. Typing pass-through: type a few letters when asked. Only report IDs and lengths are printed. IDs other than `0x10`/`0x11` mean typing reaches the HID++ node (emojikeys drops it unread):

```
python3 tools/passthrough.py
```

If a step says `cannot open` or `no reachable device`, run it once with `sudo` to tell a permission problem from a missing device.
