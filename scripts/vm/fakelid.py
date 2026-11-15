#!/usr/bin/python3 -I
"""Fake laptop lid for the PowerDeck test VM (runs as root inside the VM only).

Creates a uinput input device with the SW_LID switch, which logind and upower treat like a real
lid. Commands come from the FIFO /run/fakelid: "close" or "open" (one per line).

    echo close | sudo tee /run/fakelid
"""
import fcntl
import os
import struct
import sys
import time

FIFO = "/run/fakelid"
EV_SYN, EV_SW, SW_LID = 0, 5, 0
UI_SET_EVBIT = 0x40045564   # _IOW('U', 100, int)
UI_SET_SWBIT = 0x4004556D   # _IOW('U', 109, int)
UI_DEV_SETUP = 0x405C5503   # _IOW('U', 3, struct uinput_setup), sizeof = 92
UI_DEV_CREATE = 0x5501      # _IO('U', 1)
BUS_VIRTUAL = 0x06


def emit(fd: int, etype: int, code: int, value: int) -> None:
    now = time.time()
    os.write(fd, struct.pack("llHHi", int(now), int(now % 1 * 1e6), etype, code, value))


def set_lid(fd: int, closed: bool) -> None:
    emit(fd, EV_SW, SW_LID, int(closed))
    emit(fd, EV_SYN, 0, 0)


def main() -> int:
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_SW)
    fcntl.ioctl(fd, UI_SET_SWBIT, SW_LID)
    setup = struct.pack("HHHH80sI", BUS_VIRTUAL, 0x1209, 0x0001, 1, b"PowerDeck fake lid", 0)
    fcntl.ioctl(fd, UI_DEV_SETUP, setup)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    set_lid(fd, False)
    if not os.path.exists(FIFO):
        os.mkfifo(FIFO, 0o600)
    print("fake lid ready, write 'close'/'open' to", FIFO, flush=True)
    while True:
        with open(FIFO) as f:  # blocks until a writer opens it
            for line in f:
                cmd = line.strip()
                if cmd in ("close", "open"):
                    set_lid(fd, cmd == "close")
                    print("lid", cmd, flush=True)
                elif cmd:
                    print("ignored:", cmd[:40], file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
