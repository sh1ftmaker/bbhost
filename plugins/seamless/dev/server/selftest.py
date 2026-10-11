#!/usr/bin/env python3
"""Checks server.py with bbhost's own simulated client (tools/nettest/simclient.py,
read from the bbhost source tree, not copied): login, signs, rooms, events,
signaling resolve and P2P pings, without starting the game.

    python selftest.py [--bbhost-src C:/Users/shift/Desktop/Bloodborne/bbhost-src]

Starts its own server.py on spare ports (HTTP 28671/28672, STUN 23478), runs a
two-player and a three-player co-op summon, a leave and a host leave, then the
seamless matching rules (X-BBHost-Ruleset segregation, pw= private sessions,
friend invites, --summon-poll), and prints PASS/FAIL per check.
"""
from __future__ import annotations

import argparse
import base64
import json
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
                            "--heartbeat-timeout", "15", "--claim-seconds", "3", "--available-patch",
                            "--summon-poll", "5"])
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

        def client(name, rules="vanilla", **kw):
            port[0] += 1
            c = SimClient(b, name, nat="none", ip="127.0.0.1", p2p_port=port[0], **kw)
            c.api.headers["X-BBHost-Ruleset"] = rules  # what bbhost sends on every request
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

        # ---- seamless matching: rulesets, passwords, invites -------------------
        check("<SummonDataGetListInterval1>5</SummonDataGetListInterval1>" in ss, "--summon-poll 5 is in ss.info")
        R2, R1 = "seamless;v=2;free;anywhere", "seamless;v=1;free;anywhere"
        PA, PB = "seamless;v=2;pw=aaaa1111bbbb;free;anywhere", "seamless;v=2;pw=cccc2222dddd;free;anywhere"
        sh, sg, sold = client("SeamHost", R2), client("SeamGuest", R2), client("SeamOld", R1)
        vh, vg = client("VanHost"), client("VanGuest")
        ph, pg, px = client("PwHost", PA), client("PwGuest", PA), client("PwOther", PB)
        everyone = (sh, sg, sold, vh, vg, ph, pg, px)
        by_uid = {c.user_id: c.name for c in everyone}
        for c in (sg, sold, vg, pg, px):
            c.put_sign("coop")

        def seen(host):
            return {by_uid.get(x.get("UserId"), x.get("UserId")) for x in host.find_signs("coop")}

        v = seen(sh)
        check(v == {"SeamGuest"}, f"seamless v=2 host sees only the same ruleset's signs {sorted(v)}")
        v = seen(vh)
        check(v == {"VanGuest"}, f"vanilla host sees only vanilla signs {sorted(v)}")
        v = seen(ph)
        check(v == {"PwGuest"}, f"same password matches, other/no password does not {sorted(v)}")
        v = seen(px)
        check(not v, f"a different password sees nobody {sorted(v)}")
        check(summon(sh, sg), "same-ruleset seamless summon completes")
        check(summon(ph, pg), "same-password summon completes")
        check(not vg.join_room(sh.room) and any("ruleset" in e for _, e in vg.join_failures),
              f"a vanilla player cannot join a seamless room {vg.join_failures[-1:]}")
        check(not px.join_room(ph.room), "a different password cannot join a private room")

        # friend invite: InvHost invited InvFriend (in another area); InvStranger
        # rings too, and OtherHost searches with the same rules
        ih, ox = client("InvHost", R2), client("OtherHost", R2)
        fr, st_ = client("InvFriend", R2, area=0x18020000), client("InvStranger", R2)
        for c in (ih, ox, fr, st_):
            by_uid[c.user_id] = c.name

        def post(path, body, rules=R2):
            req = urllib.request.Request("http://127.0.0.1:28671" + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "X-BBHost-Ruleset": rules},
                                         method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=3).read())

        r = post("/seamless/invite", {"From": "InvHost", "To": "InvFriend"})
        check(r.get("ResKind") == 0, f"POST /seamless/invite records it {r}")
        fr.put_sign("coop")
        st_.put_sign("coop")
        v = seen(ih)
        check(v == {"InvFriend"}, f"the inviter sees only the invited friend's sign, any area {sorted(v)}")
        v = seen(ox)
        check("InvStranger" in v and "InvFriend" not in v, f"another host does not get the friend's sign {sorted(v)}")
        check(wait(lambda: any(e.get("Name") == "seamless_invite" for e in fr.events), 3),
              "the friend gets a seamless_invite event")
        check(summon(ih, fr), "the invited friend is summoned")
        inv = json.loads(urllib.request.urlopen("http://127.0.0.1:28671/seamless/invites?online=InvHost",
                                                timeout=3).read()).get("Invites", [])
        check([i.get("state") for i in inv] == ["joined"], f"the invite is now a party record {inv}")
        r = post("/seamless/invite/cancel", {"From": "InvHost"})
        st_.put_sign("coop")
        v = seen(ih)
        check(len(r.get("Removed", [])) == 1 and "InvStranger" in v, f"after cancelling, the inviter sees others {sorted(v)}")
        for c in everyone + (ih, ox, fr, st_):
            c.stop()
        bad = [w for ok, w in results if not ok]
        print(f"\n{len(results) - len(bad)}/{len(results)} passed; server log {log}")
        return 0 if not bad else 1
    finally:
        srv.terminate()
        srv.wait(5)


if __name__ == "__main__":
    sys.exit(main())
