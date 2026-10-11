#!/usr/bin/env python3
"""Checks server.py with bbhost's own simulated client (tools/nettest/simclient.py,
read from the bbhost source tree, not copied): login, signs, rooms, events,
signaling resolve and P2P pings, without starting the game.

    python selftest.py [--bbhost-src C:/Users/shift/Desktop/Bloodborne/bbhost-src]

Starts its own server.py on spare ports (HTTP 28671/28672, STUN 23478), runs a
two-player and a three-player co-op summon, a leave and a host leave, and
prints PASS/FAIL per check.
"""
from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


class Bench:
    """What SimClient asks of tools/nettest/bench.py's Bench."""

    def __init__(self, http_port: int, stun_port: int):
        self.http_base = f"http://127.0.0.1:{http_port}"
        self.relay_ip = "127.0.0.1"
        self.stun_addr = ("127.0.0.1", stun_port)
        self.relay_lo, self.relay_hi = 1, 0   # no relay
        self.keepalive = 15.0
        self.heartbeat = 5.0
        self.invite_window = 20.0
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self.n = 0

    def log(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)
        print("   " + line, flush=True)


def wait(cond, timeout: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.2)
    return cond()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbhost-src", default=r"C:/Users/shift/Desktop/Bloodborne/bbhost-src")
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(a.bbhost_src, "tools", "nettest"))
    from simclient import SimClient  # noqa: E402

    log = os.path.join(HERE, "selftest-server.log")
    if os.path.exists(log):
        os.remove(log)
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"), "--http-port", "28671",
                            "--ssinfo-port", "28672", "--stun", "127.0.0.1:23478", "--log", log, "-q",
                            "--heartbeat-timeout", "15", "--claim-seconds", "3", "--available-patch"])
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen("http://127.0.0.1:28671/status", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        ss = base64.b64decode(urllib.request.urlopen("http://127.0.0.1:28672/ss.info", timeout=2).read()).decode()
        b = Bench(28671, 23478)
        results = []

        def check(cond, what):
            results.append((bool(cond), what))
            print(("PASS " if cond else "FAIL ") + what, flush=True)

        check("<ss>0</ss>" in ss and "<api_Login>http://127.0.0.1:28671</api_Login>" in ss, "ss.info served")

        port = [9400]

        def client(name):
            port[0] += 1
            c = SimClient(b, name, nat="none", ip="127.0.0.1", p2p_port=port[0])
            c.start()
            return c

        h = client("Host")
        g1 = client("Guest1")
        g2 = client("Guest2")
        check(h.user_id and g1.user_id and h.user_id != g1.user_id, "login gives distinct UserIds")
        check(h.mapped == ("127.0.0.1", h.p2p_port), f"STUN maps the P2P port ({h.mapped})")

        def summon(host, guest):
            guest.put_sign("coop")
            host.summon(guest, "coop")
            return wait(lambda: guest.room and guest.room == host.room, 25)

        check(summon(h, g1), "Guest1 joins Host's room by coop summon")
        check(wait(lambda: h.hears("Guest1") and g1.hears("Host"), 8), "Host and Guest1 hear each other over P2P")
        check(summon(h, g2), "Guest2 joins too")
        check(wait(lambda: all(x.hears(y.name) for x in (h, g1, g2) for y in (h, g1, g2) if x is not y), 10),
              "three-way P2P mesh")
        check(h.peer_names() == {"Guest1", "Guest2"}, f"host's member list {sorted(h.peer_names())}")
        room = h.room
        g1.leave_room()
        check(wait(lambda: "Guest1" not in h.peer_names(), 6), "host hears Guest1 leave")
        check(g2.room == room, "Guest2 keeps the room")
        time.sleep(4)  # going home takes the game longer than the claim's life here
        check(summon(h, g1), "Guest1 is summoned again")
        h.leave_room()
        check(wait(lambda: not g1.room and not g2.room, 6), "guests hear the room close when the host leaves")
        for c in (h, g1, g2):
            c.stop()
        bad = [w for ok, w in results if not ok]
        print(f"\n{len(results) - len(bad)}/{len(results)} passed; server log {log}")
        return 0 if not bad else 1
    finally:
        srv.terminate()
        srv.wait(5)


if __name__ == "__main__":
    sys.exit(main())
