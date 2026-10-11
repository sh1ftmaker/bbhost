#!/usr/bin/env python3
"""A local, private Bloodborne co-op test server for bbhost (stdlib only).

One process, everything on localhost:

  HTTP 127.0.0.1:18671  the game's own API (ss.info, /basic_utils/login,
                        /summon_messenger/*, every other FromSoftware API as
                        an empty OK), bbhost's NP session service
                        (/mp/matching2/*, /np/events/*, /np/signaling/*),
                        the play-log PUTs and stub /auth/* answers
  HTTP 127.0.0.1:20443  the same handler: the game fetches ss.info from
                        https://ss4.scej-network.jp:20443/..., which bbhost
                        rewrites to http://<online.host>:20443/...
  UDP  127.0.0.1:3478   STUN (RFC 3489/5389 Binding, MAPPED-ADDRESS +
                        XOR-MAPPED-ADDRESS), optionally bbhost's relay
                        (--relay; off by default - two instances on one PC
                        reach each other directly over loopback)

Every request and answer is logged (body included) to the console and to
--log (default: server.log beside this file).

    python server.py [--log FILE] [--http-port 18671] [--ssinfo-port 20443]
                     [--stun 127.0.0.1:3478] [--relay] [--any-area] [-q]
                     [--summon-poll S] [--sign-refresh S] [--no-ruleset-match]
                     [--invite-ttl S] [--invite-same-area]

The protocol notes are in ../README.md. Nothing here talks to any host but
127.0.0.1.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import http.server
import json
import os
import secrets
import socket
import socketserver
import struct
import sys
import threading
import time
import traceback
import urllib.parse

# --------------------------------------------------------------------------
# logging

_log_lock = threading.Lock()
_log_file = None
_quiet = False


def log(msg: str) -> None:
    line = f"{_dt.datetime.now().strftime('%H:%M:%S.%f')[:-3]} {msg}"
    with _log_lock:
        if not _quiet:
            try:
                print(line, flush=True)
            except UnicodeEncodeError:
                print(line.encode("ascii", "replace").decode(), flush=True)
        if _log_file:
            _log_file.write(line + "\n")
            _log_file.flush()


def short(b: bytes | str, limit: int = 4000) -> str:
    if isinstance(b, bytes):
        try:
            s = b.decode("utf-8")
        except UnicodeDecodeError:
            s = "b64:" + base64.b64encode(b).decode()
    else:
        s = b
    return s if len(s) <= limit else s[:limit] + f"... ({len(s)} chars)"


# --------------------------------------------------------------------------
# ss.info: the game's server list. An XML-ish text the game searches for
# tags (eboot sub_1eb65e0) after base64-decoding the body: <ss>N</ss> (status, must be a bare number; 0 is
# "up"), then for its server index i (FrpgNetMan+0x9e8) a <gameurl{i}> block
# holding one <api_X>base URL</api_X> per API (all 37 required), the game
# appending its fixed path ("%s/basic_utils/login"). Optional numeric tuning
# tags (<SummonDataCreateInterval{i}> ...) keep the game's defaults when
# absent. The index was 1 in every run here; blocks are written for 0..7.

API_NAMES = [
    "Login", "ServerTimeGet", "SyncCharaId", "NoticeNormalGet", "NoticeEmergencyGet", "UserAgreementGet",
    "BloodMessCreate", "BloodMessGetList", "BloodMessEvaluate", "BloodMessGetEvaluate", "BloodMessRemove",
    "BloodMessSearchAdd", "ChannelUpload", "ChannelShare", "ChannelSearch", "ChannelWordSearch",
    "ChannelGetDetailsInfo", "ChannelGetInfo", "ChannelRandomJoin", "ChannelAddMaterial",
    "ChannelAddMaterialCompleteNotify", "MessengerShellUpload", "MultiPlayNetError", "UserPropertiesMoveCount",
    "UserPropertiesMoveCountCheck", "SummonDataCreate", "SummonDataGetList", "SummonDataRemove",
    "SummonDataSummon", "ChairMessGetList", "ChairMessRespawnPointNotice", "TombMessCreate", "TombMessGetList",
    "DeathVisionGet", "TombMessRemove", "WanderingGhostCreate", "WanderingGhostGet",
]

# Path -> the MessageId of the answer (the request's MessageId with
# Request -> Response is used when the body names one).
PATH_MESSAGES = {
    "/basic_utils/login": "LoginResponse",
    "/basic_utils/get_datetime": "ServerTimeGetResponse",
    "/basic_utils/sync_chara_id": "SyncCharaIdResponse",
    "/basic_utils/get_normal_notice": "NoticeNormalGetResponse",
    "/basic_utils/get_emergency_notice": "NoticeEmergencyGetResponse",
    "/basic_utils/get_user_agreement": "UserAgreementGetResponse",
    "/blood_messenger/create": "BloodMessCreateResponse",
    "/blood_messenger/message_area": "BloodMessGetListResponse",
    "/blood_messenger/evaluate_message": "BloodMessEvaluateResponse",
    "/blood_messenger/evaluation": "BloodMessGetEvaluateResponse",
    "/blood_messenger/delete": "BloodMessRemoveResponse",
    "/blood_messenger/exist_messages": "BloodMessSearchAddResponse",
    "/channel/upload": "ChannelUploadResponse",
    "/channel/share": "ChannelShareResponse",
    "/channel/search": "ChannelSearchResponse",
    "/channel/word_search": "ChannelWordSearchResponse",
    "/channel/get_details_info": "ChannelGetDetailsInfoResponse",
    "/channel/get_info": "ChannelGetInfoResponse",
    "/channel/random_join": "ChannelRandomJoinResponse",
    "/channel/add_material": "ChannelAddMaterialResponse",
    "/channel/notify_add_material_complete": "ChannelAddMaterialCompleteNotifyResponse",
    "/messenger_shell/upload": "MessengerShellUploadResponse",
    "/penalty/notify_multi_play_error": "MultiPlayNetErrorResponse",
    "/penalty/notify_user_properties_move_count": "UserPropertiesMoveCountResponse",
    "/penalty/check_user_priority_move_count": "UserPropertiesMoveCountCheckResponse",
    "/summon_messenger/create": "SummonDataCreateResponse",
    "/summon_messenger/get": "SummonDataGetListResponse",
    "/summon_messenger/delete": "SummonDataRemoveResponse",
    "/summon_messenger/request": "SummonDataSummonResponse",
    "/chair_messenger/get": "ChairMessGetListResponse",
    "/chair_messenger/update": "ChairMessRespawnPointNoticeResponse",
    "/tomb_messenger/create": "TombMessCreateResponse",
    "/tomb_messenger/message_area": "TombMessGetListResponse",
    "/tomb_messenger/death_vision_get": "DeathVisionGetResponse",
    "/tomb_messenger/delete": "TombMessRemoveResponse",
    "/wandering_ghost/create": "WanderingGhostCreateResponse",
    "/wandering_ghost/get": "WanderingGhostGetResponse",
}

# Empty lists the list-returning answers carry, so a parser that wants the
# key finds it (names from the eboot's own key table).
EMPTY_LISTS = {
    # What each answer's handler reads (eboot sub_1e7f* response switch,
    # jump table at 0x1e896f4): a list it wants as an array (JSON type 6)
    # fails the request ("Failed to record note search information" ...)
    # when it is missing.
    "/basic_utils/get_normal_notice": ["NoticeList"],
    "/basic_utils/get_emergency_notice": ["NoticeList"],
    "/blood_messenger/create": ["BloodMessIdList"],
    "/blood_messenger/message_area": ["BloodMessList"],
    "/blood_messenger/evaluation": ["BloodMessEvaluationList"],
    "/blood_messenger/exist_messages": ["BloodMessEvaluationList", "LostBloodMessIdList"],
    "/channel/search": ["ChannelList"],
    "/channel/word_search": ["ChannelList"],
    "/channel/get_info": ["ChannelInfoList", "LostChannelIdList"],
    "/chair_messenger/get": ["ChairMessList"],
    "/tomb_messenger/message_area": ["TombMessList"],
    "/wandering_ghost/get": ["WanderingGhostList"],
    "/summon_messenger/get": ["SummonDataList"],
}


def build_ss_info(base: str, extra: dict[str, str] | None = None, indices=range(8)) -> str:
    parts = ["<ss>0</ss>"]
    for i in indices:
        apis = "".join(f"<api_{n}>{base}</api_{n}>" for n in API_NAMES)
        parts.append(f"<gameurl{i}>{apis}</gameurl{i}>")
        for k, v in (extra or {}).items():
            parts.append(f"<{k}{i}>{v}</{k}{i}>")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# state

class State:
    def __init__(self, any_area: bool, heartbeat_timeout: float):
        self.lock = threading.RLock()
        self.any_area = any_area
        self.heartbeat_timeout = heartbeat_timeout
        # game sessions (the game's own login)
        self.next_user = 1001
        self.users: dict[str, dict] = {}       # PlatformAccountId -> {UserId, SessionId, ...}
        self.sessions: dict[str, dict] = {}    # SessionId -> user record
        # signs: key (SessionId, UserId) -> record
        self.signs: dict[tuple, dict] = {}
        self.sign_ttl = 130.0
        # NP: players by online id
        self.players: dict[str, dict] = {}
        self.next_room = 1
        self.rooms: dict[int, dict] = {}       # RoomId -> room
        self.room_sids: dict[str, int] = {}    # session id -> RoomId
        # events
        self.next_event = 1
        self.events: dict[str, list] = {}      # online id -> [event]
        # friend invites / parties: (from online id, to online id) -> record
        self.invites: dict[tuple, dict] = {}
        self.invite_ttl = 1800.0

    # ---- invites / parties ---------------------------------------------
    def invited_by(self, online: str) -> set:
        """The players `online` has invited (its sign search sees only them)."""
        return {to for (fr, to) in self.invites if fr == online} if online else set()

    def inviters_of(self, online: str) -> set:
        """The players that invited `online` (its sign is kept for them)."""
        return {fr for (fr, to) in self.invites if to == online} if online else set()

    def touch_invite(self, fr: str, to: str, state: str | None = None) -> None:
        rec = self.invites.get((fr, to))
        if rec:
            rec["t"] = time.time()
            if state and rec["state"] != state:
                rec["state"] = state
                log(f"invite {fr} -> {to}: {state}")

    # ---- events ---------------------------------------------------------
    def push(self, online: str, name: str, **fields) -> None:
        with self.lock:
            ev = {"EventId": self.next_event, "Name": name, **fields}
            self.next_event += 1
            self.events.setdefault(online, []).append(ev)
        log(f"EVENT -> {online}: {json.dumps(ev)}")

    # ---- endpoints -------------------------------------------------------
    @staticmethod
    def endpoint_of(body: dict) -> dict:
        return {k: body.get(k) for k in ("OnlineId", "LocalAddr", "LocalPort", "PublicAddr", "PublicPort",
                                         "MappedAddr", "MappedPort") if k in body}

    def member_record(self, room: dict, mid: int) -> dict:
        m = room["members"][mid]
        ep = m["endpoint"]
        local = ep.get("LocalAddr") or "127.0.0.1"
        lport = int(ep.get("LocalPort") or 0)
        addr = ep.get("PublicAddr") or local
        port = int(ep.get("PublicPort") or lport)
        mapped, mport = ep.get("MappedAddr") or "", int(ep.get("MappedPort") or 0)
        # A STUN-mapped address that differs from the local one is the one
        # that reaches the peer (bbhost's net/session.cpp applies the same).
        if mapped and mport and mapped != local:
            addr, port = mapped, mport
        return {"RoomId": room["id"], "MemberId": mid, "OnlineId": m["online"], "Addr": addr, "Port": port,
                "LocalAddr": local, "LocalPort": lport, "MappedAddr": mapped, "MappedPort": mport,
                "IsOwner": 1 if mid == room["owner"] else 0}

    def resolve(self, online: str):
        p = self.players.get(online)
        if not p:
            return None
        local, lport = p.get("SignalingAddr") or "127.0.0.1", int(p.get("SignalingPort") or 0)
        mapped, mport = p.get("MappedAddr") or "", int(p.get("MappedPort") or 0)
        if mapped and mport and mapped != local:
            return mapped, mport
        return local, lport

    def remove_member(self, room: dict, mid: int, reason: str) -> None:
        m = room["members"].pop(mid, None)
        if not m:
            return
        self.room_sids.pop(m["sid"], None)
        log(f"room {room['id']}: member {mid} ({m['online']}) left ({reason})")
        if mid == room["owner"]:
            for omid, om in list(room["members"].items()):
                self.push(om["online"], "room_closed", RoomId=room["id"], Reason="host_left")
                self.room_sids.pop(om["sid"], None)
            room["members"].clear()
            self.rooms.pop(room["id"], None)
            log(f"room {room['id']} closed (host left)")
            return
        for om in room["members"].values():
            self.push(om["online"], "room_member_left", RoomId=room["id"], MemberId=mid, OnlineId=m["online"],
                      Reason=reason)

    def leave_all(self, online: str, reason: str) -> None:
        for room in list(self.rooms.values()):
            for mid, m in list(room["members"].items()):
                if m["online"] == online:
                    self.remove_member(room, mid, reason)

    def reap(self) -> None:
        now = time.time()
        with self.lock:
            for room in list(self.rooms.values()):
                for mid, m in list(room["members"].items()):
                    if now - m["beat"] > self.heartbeat_timeout:
                        self.remove_member(room, mid, "timeout")
            for k, s in list(self.signs.items()):
                if now - s["t"] > (900 if s["state"] in ("claimed", "delivered") else self.sign_ttl):
                    self.signs.pop(k, None)
            for k, inv in list(self.invites.items()):
                if now - inv["t"] > self.invite_ttl:
                    self.invites.pop(k, None)
                    log(f"invite {k[0]} -> {k[1]}: expired")


STATE: State | None = None
OPTS = None

# The current request's bbhost headers (set per request by Handler._handle).
_req = threading.local()


def req_rules() -> str:
    return getattr(_req, "rules", "") or "vanilla"


def req_adopt() -> str:
    return getattr(_req, "adopt", "") or ""


# --------------------------------------------------------------------------
# rulesets (X-BBHost-Ruleset, built by bbhost's host/plugins.cpp own_ruleset):
# "vanilla", or the gameplay plugins' entries sorted and joined by ",", each
# "name[;elem...]" ("randomizer;seed=x", "seamless;v=2;pw=ab12cd34ef56;free").
# Players meet only when their entries are the same (the upstream server's
# documented behaviour, docs/online.md "Modded worlds stay apart"), except the
# entries of plugins the visitor adopts (X-BBHost-Adopt), which bbhost's
# plugins_enter_world also leaves out. A "pw=<token>" element makes a session
# private: it must be the same on both sides, adopted plugin or not.

def rule_entries(rules: str) -> list[str]:
    return sorted(p.strip() for p in (rules or "").split(",") if p.strip() and p.strip() != "vanilla")


def _entry_name(e: str) -> str:
    return e.split(";", 1)[0]


def _entry_pw(e: str) -> str:
    return next((el[3:] for el in e.split(";")[1:] if el.startswith("pw=")), "")


def _strip_pw(e: str) -> str:
    return ";".join(el for el in e.split(";") if not el.startswith("pw="))


def rules_compatible(host_rules: str, visitor_rules: str, visitor_adopt: str = "") -> tuple[bool, str]:
    """Whether a visitor playing by `visitor_rules` (and adopting the plugins
    named in `visitor_adopt`) may meet a host playing by `host_rules`."""
    if OPTS.no_ruleset_match:
        return True, "ruleset matching off"
    h, v = rule_entries(host_rules), rule_entries(visitor_rules)
    adopt = {a.strip() for a in (visitor_adopt or "").split(",") if a.strip()}
    hs = [_strip_pw(e) for e in h if _entry_name(e) not in adopt]
    vs = [_strip_pw(e) for e in v if _entry_name(e) not in adopt]
    if hs != vs:
        return False, f"rules differ ({','.join(hs) or 'vanilla'} vs {','.join(vs) or 'vanilla'})"
    hp = {_entry_name(e): _entry_pw(e) for e in h if _entry_pw(e)}
    vp = {_entry_name(e): _entry_pw(e) for e in v if _entry_pw(e)}
    for name in sorted(set(hp) | set(vp)):
        a, b = hp.get(name, ""), vp.get(name, "")
        if a != b:
            if not a or not b:
                return False, f"{name}: private (pw) vs public"
            return False, f"{name}: password differs"
    what = "private" if hp else "same rules"
    adopted = sorted(adopt & ({_entry_name(e) for e in h} | {_entry_name(e) for e in v}))
    return True, f"{what} '{','.join(hs) or 'vanilla'}'" + (f" (adopting {','.join(adopted)})" if adopted else "")


# --------------------------------------------------------------------------
# game API handlers (JSON in, JSON out)

def ok(msg: str, **fields) -> dict:
    return {"ResKind": 0, "MessageId": msg, **fields}


def h_login(body: dict, q: dict) -> dict:
    st = STATE
    acct = str(body.get("PlatformAccountId", "")) or secrets.token_hex(4)
    with st.lock:
        u = st.users.get(acct)
        if not u:
            u = {"UserId": st.next_user, "acct": acct}
            st.next_user += 1
            st.users[acct] = u
        sid = secrets.token_hex(8)
        u["SessionId"] = sid
        st.sessions[sid] = u
    log(f"login: account {acct} -> UserId {u['UserId']} SessionId {sid}")
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return ok("LoginResponse", SessionId=sid, UserId=u["UserId"], UserStatus=0, ServerVersion=1,
              WarningMessage="", ServerTime=now)


def _sign_key(b: dict):
    return (str(b.get("SessionId", "")), int(b.get("UserId", -1) or -1))


def _available(sign: dict) -> dict:
    """The sign as a search result: SummonData's byte 0x79 (the result count
    the game advertises as 0) set to 1, as shadnet-p2p's broker does."""
    s = dict(sign)
    if not OPTS.available_patch:
        return s
    if int(s.get("SummonDataVersion", -1) or -1) == 3 and isinstance(s.get("SummonData"), str):
        try:
            raw = bytearray(base64.b64decode(s["SummonData"]))
            if len(raw) == 0xE0 and raw[0x79] == 0:
                raw[0x79] = 1
                s["SummonData"] = base64.b64encode(bytes(raw)).decode()
        except Exception:
            pass
    return s


def h_summon_create(body: dict, q: dict) -> dict:
    st = STATE
    key = _sign_key(body)
    now = time.time()
    who = {"rules": req_rules(), "adopt": req_adopt(), "online": online_of_user(key[1])}
    with st.lock:
        s = st.signs.get(key)
        if not s or s["state"] == "consumed":
            st.signs[key] = {"ad": body, "state": "advertised", "t": now, "claim": None, **who}
            log(f"sign: advertised user {key[1]} ({who['online']}) type {body.get('SummonType')} area "
                f"{body.get('AreaId')} region {body.get('AreaRegionId')} level {body.get('MatchingLevel')} "
                f"ruleset {who['rules']!r}" + (f" adopts {who['adopt']!r}" if who["adopt"] else ""))
            return ok("SummonDataCreateResponse")
        # A claim is delivered for 30 s (the guest's game reads it from its
        # next create); a create after that is a fresh sign (the guest came
        # home and rang again without a delete reaching us).
        if s["state"] in ("claimed", "delivered") and now - s["claimed_at"] > OPTS.claim_seconds:
            st.signs[key] = {"ad": body, "state": "advertised", "t": now, "claim": None, **who}
            log(f"sign: re-advertised user {key[1]} (old claim expired), ruleset {who['rules']!r}")
            return ok("SummonDataCreateResponse")
        s["ad"], s["t"] = body, now
        s.update(who)
        if s["state"] == "claimed":
            s["state"] = "delivered"
        if s["state"] == "delivered" and s["claim"]:
            resp = {"ResKind": 0, "MessageId": "SummonDataCreateResponse"}
            for k, v in s["claim"].items():
                if k not in ("MessageId", "ResKind"):
                    resp[k] = v
            log(f"sign: delivered the claim to user {key[1]}")
            return resp
        return ok("SummonDataCreateResponse")


def _matches(req: dict, sign: dict, ignore_area: bool = False) -> tuple[bool, str]:
    if int(sign.get("UserId", -1)) == int(req.get("UserId", -2)) or sign.get("SessionId") == req.get("SessionId"):
        return False, "own sign"
    for k in ("SummonDataVersion",):
        if k in req and req[k] is not None and req[k] != sign.get(k):
            return False, f"{k} {sign.get(k)} != {req[k]}"
    if not OPTS.any_area and not ignore_area:
        for k in ("AreaId", "AreaRegionId"):
            if k in req and req[k] is not None and req[k] != sign.get(k):
                return False, f"{k} {sign.get(k)} != {req[k]}"
    types = {int(f.get("SummonType")) for f in req.get("SummonTypeList", []) if isinstance(f, dict)
             and "SummonType" in f}
    if types and int(sign.get("SummonType", -1)) not in types:
        return False, f"SummonType {sign.get('SummonType')} not in {sorted(types)}"
    rw, sw = req.get("SummonWord") or "", sign.get("SummonWord") or ""
    if rw and rw != sw:
        return False, "password differs"
    return True, ""


def _invite_gate(me: str, owner: str) -> tuple[bool, str, bool]:
    """Friend invites (POST /seamless/invite): a player who invited someone
    sees only the invited players' signs, and an invited player's sign is
    kept for its inviters. Returns (allowed, reason, invited pair)."""
    st = STATE
    targets, inviters = st.invited_by(me), st.inviters_of(owner)
    if targets and owner not in targets:
        return False, f"{me} invited {sorted(targets)}, not {owner or '?'}", False
    if inviters and me not in inviters:
        return False, f"kept for {sorted(inviters)}'s invite", False
    return True, "", bool(targets) and owner in targets


def sign_visible(body: dict, s: dict, me: str) -> tuple[bool, str]:
    """Whether the searcher (body, its request's ruleset, online id `me`)
    may see sign record `s`, and why."""
    owner = s.get("online") or online_of_user(int(s["ad"].get("UserId", 0) or 0))
    allowed, why, invited = _invite_gate(me, owner)
    if not allowed:
        return False, why
    good, reason = _matches(body, s["ad"], ignore_area=invited and not OPTS.invite_same_area)
    if not good:
        return False, reason
    good, reason = rules_compatible(req_rules(), s.get("rules", "vanilla"), s.get("adopt", ""))
    if not good:
        return False, f"ruleset: {reason}"
    return True, reason + (" (invited)" if invited else "")


def h_summon_get(body: dict, q: dict) -> dict:
    st = STATE
    st.reap()
    out, why, hit = [], [], []
    with st.lock:
        me = online_of_user(int(body.get("UserId", 0) or 0))
        for key, s in sorted(st.signs.items(), key=lambda kv: -kv[1]["t"]):
            if s["state"] != "advertised":
                why.append(f"user {key[1]}: {s['state']}")
                continue
            good, reason = sign_visible(body, s, me)
            if good:
                out.append(_available(s["ad"]))
                hit.append(f"user {key[1]}: {reason}")
                if s.get("online") in st.invited_by(me):
                    st.touch_invite(me, s["online"])
            else:
                why.append(f"user {key[1]}: {reason}")
    out = out[: int(body.get("GetMaxCount", 20) or 20)]
    log(f"sign search by user {body.get('UserId')} ({me}, ruleset {req_rules()!r}): {len(out)} found"
        + (f" {hit}" if hit else "") + (f"; skipped {why}" if why else ""))
    return {"SummonDataList": out, "ResKind": 0, "MessageId": "SummonDataGetListResponse"}


def h_summon_request(body: dict, q: dict) -> dict:
    st = STATE
    target_user = body.get("TargetUserId")
    with st.lock:
        target = None
        for key, s in st.signs.items():
            ad = s["ad"]
            if target_user is not None and int(ad.get("UserId", -1)) == int(target_user):
                if body.get("TargetCharaId") is not None and ad.get("CharaId") != body.get("TargetCharaId"):
                    continue
                if target is None or s["t"] > target[1]["t"]:
                    target = (key, s)
        if not target:
            log(f"summon request by user {body.get('UserId')} for user {target_user}: no such sign")
            return ok("SummonDataSummonResponse")
        key, s = target
        if s["state"] in ("claimed", "delivered"):
            log(f"summon request for user {key[1]}: already {s['state']}")
            return ok("SummonDataSummonResponse")
        me = online_of_user(int(body.get("UserId", 0) or 0))
        allowed, why, invited = _invite_gate(me, s.get("online", ""))
        good, reason = rules_compatible(req_rules(), s.get("rules", "vanilla"), s.get("adopt", ""))
        if not allowed or not good:
            log(f"summon request by user {body.get('UserId')} for user {key[1]}: refused "
                f"({why if not allowed else 'ruleset: ' + reason})")
            return ok("SummonDataSummonResponse")
        s["state"], s["claim"], s["t"] = "claimed", body, time.time()
        s["claimed_at"] = s["t"]
        if invited:
            st.touch_invite(me, s["online"], "claimed")
    log(f"summon request: user {body.get('UserId')} claimed user {key[1]}'s sign ({reason})")
    if not OPTS.no_guest_invite:
        send_guest_invite(int(body.get("UserId", 0) or 0), key[1])
    return ok("SummonDataSummonResponse")


def online_of_user(uid: int) -> str:
    for acct, u in STATE.users.items():
        if u["UserId"] == uid:
            return acct  # PlatformAccountId is the online id under bbhost
    return ""


def send_guest_invite(host_uid: int, guest_uid: int) -> None:
    """bbhost's guest_invite event (hle/np_matching2.cpp): the host's room
    and addresses, to the summoned player. bbhost seeds the host's peer
    entry from it (and with BBHOST_SUMMON_INVITE=1 hands the game a type-1
    item itself)."""
    st = STATE
    with st.lock:
        host, guest = online_of_user(host_uid), online_of_user(guest_uid)
        room = next((r for r in st.rooms.values() if r["members"].get(r["owner"], {}).get("online") == host), None)
        if not host or not guest or not room:
            log(f"guest_invite: not sent (host {host!r} guest {guest!r} room {room['id'] if room else None})")
            return
        rec = st.member_record(room, room["owner"])
        extra = room.get("host_extra", {})
        st.push(guest, "guest_invite", RoomId=room["id"], HostOnlineId=host, HostAddr=rec["Addr"], HostPort=rec["Port"],
                HostLocalAddr=rec["LocalAddr"], HostLocalPort=rec["LocalPort"], HostMappedAddr=rec["MappedAddr"],
                HostMappedPort=rec["MappedPort"], MemberTag=extra.get("MemberTag", 1),
                HostArea=extra.get("HostArea", 0), HostLevel=extra.get("HostLevel", 0),
                HostPos=extra.get("HostPos", [0, 0, 0]))


def h_summon_delete(body: dict, q: dict) -> dict:
    st = STATE
    n = 0
    with st.lock:
        for key, s in st.signs.items():
            ad = s["ad"]
            if body.get("SessionId") and ad.get("SessionId") != body.get("SessionId"):
                continue
            if body.get("UserId") is not None and int(ad.get("UserId", -1)) != int(body.get("UserId")):
                continue
            if s["state"] != "consumed":
                s["state"] = "consumed"
                n += 1
    log(f"sign delete by user {body.get('UserId')}: {n} removed")
    return ok("SummonDataRemoveResponse")


def h_sync_chara_id(body: dict, q: dict) -> dict:
    """The game asks for CharaIdNum character ids (an array of objects with
    PublishCharaId, eboot sub_1e7f* reads type 6 = array of type 7 = object)."""
    n = max(1, min(int(body.get("CharaIdNum", 1) or 1), 16))
    uid = int(body.get("UserId", 0) or 0)
    ids = [{"PublishCharaId": uid * 100 + i + 1} for i in range(n)]
    return ok("SyncCharaIdResponse", PublishCharacterIdList=ids)


GAME_HANDLERS = {
    "/basic_utils/login": h_login,
    "/basic_utils/sync_chara_id": h_sync_chara_id,
    "/summon_messenger/create": h_summon_create,
    "/summon_messenger/get": h_summon_get,
    "/summon_messenger/request": h_summon_request,
    "/summon_messenger/delete": h_summon_delete,
}


def game_default(path: str, body: dict) -> dict:
    mid = body.get("MessageId") if isinstance(body, dict) else None
    msg = PATH_MESSAGES.get(path) or (mid[:-7] + "Response" if isinstance(mid, str) and mid.endswith("Request")
                                      else "Response")
    r = ok(msg)
    for k in EMPTY_LISTS.get(path, []):
        r[k] = []
    if path == "/tomb_messenger/create":
        r["TombMessId"] = int(time.time() * 1000) % 2_000_000_000
    if path == "/wandering_ghost/create":
        r["WanderingGhostId"] = int(time.time() * 1000) % 2_000_000_000
    if path == "/basic_utils/get_user_agreement":
        r["UserAgreementText"] = ""
    if path == "/basic_utils/get_datetime":
        r["ServerTime"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return r


# --------------------------------------------------------------------------
# NP session service (bbhost's net/session.cpp, hle/np_matching2.cpp)

def np_context_start(b: dict) -> dict:
    st = STATE
    online = b.get("OnlineId", "")
    with st.lock:
        st.players[online] = {**b, "t": time.time()}
        st.events[online] = []  # a new run starts with an empty queue
        st.leave_all(online, "member_disappeared")
        for fr in sorted(st.inviters_of(online)):
            push_invite_event(st.invites[(fr, online)])
    log(f"np: {online} online, signaling {b.get('SignalingAddr')}:{b.get('SignalingPort')} "
        f"mapped {b.get('MappedAddr')}:{b.get('MappedPort')}")
    return {"ResKind": 0}


def np_signaling_update(b: dict) -> dict:
    st = STATE
    online = b.get("OnlineId", "")
    with st.lock:
        p = st.players.setdefault(online, {"OnlineId": online})
        p["MappedAddr"], p["MappedPort"] = b.get("MappedAddr"), b.get("MappedPort")
        rid = st.room_sids.get(b.get("SessionId", ""))
        room = st.rooms.get(rid) if rid else None
        if room:
            m = room["members"].get(int(b.get("MemberId", 0) or 0))
            if m:
                m["endpoint"]["MappedAddr"], m["endpoint"]["MappedPort"] = b.get("MappedAddr"), b.get("MappedPort")
    return {"ResKind": 0}


def np_resolve(b: dict) -> dict:
    st = STATE
    with st.lock:
        r = st.resolve(b.get("OnlineId", ""))
    if not r:
        log(f"np: resolve {b.get('OnlineId')}: unknown")
        return {"ResKind": 1, "Error": "not registered"}
    return {"ResKind": 0, "OnlineId": b.get("OnlineId"), "Addr": r[0], "Port": r[1]}


def np_create_room(b: dict, ruleset: str) -> dict:
    st = STATE
    online = b.get("OnlineId", "")
    with st.lock:
        st.leave_all(online, "member_disappeared")
        rid = st.next_room
        st.next_room += 1
        sid = secrets.token_hex(8)
        room = {"id": rid, "owner": 1, "max": int(b.get("MaxMembers", 5) or 5), "ruleset": ruleset,
                "members": {1: {"online": online, "sid": sid, "endpoint": st.endpoint_of(b), "beat": time.time()}},
                "next_mid": 2, "host_extra": {k: b.get(k) for k in ("HostArea", "HostLevel", "HostPos", "MemberTag")
                                              if k in b}}
        st.rooms[rid] = room
        st.room_sids[sid] = rid
    log(f"np: room {rid} created by {online} (max {room['max']})")
    return {"ResKind": 0, "RoomId": rid, "SessionId": sid, "MemberId": 1, "MaxMembers": room["max"],
            "OwnerMemberId": 1}


def np_join_room(b: dict) -> dict:
    st = STATE
    online = b.get("OnlineId", "")
    rid = int(b.get("RoomId", 0) or 0)
    with st.lock:
        room = st.rooms.get(rid)
        if not room:
            log(f"np: {online} join room {rid}: no such room")
            return {"ResKind": 1, "Error": "no such room"}
        for mid, m in list(room["members"].items()):
            if m["online"] == online:
                st.remove_member(room, mid, "member_disappeared")
        if rid not in st.rooms:
            return {"ResKind": 1, "Error": "room closed"}
        if len(room["members"]) >= room["max"]:
            log(f"np: {online} join room {rid}: full")
            return {"ResKind": 1, "Error": "room full"}
        good, reason = rules_compatible(room["ruleset"], req_rules(), req_adopt())
        log(f"np: {online} join room {rid}: ruleset {req_rules()!r} vs room {room['ruleset']!r}: "
            + ("ok, " if good else "refused, ") + reason)
        if not good:
            return {"ResKind": 1, "Error": f"ruleset mismatch: {reason}"}
        mid = room["next_mid"]
        room["next_mid"] += 1
        sid = secrets.token_hex(8)
        room["members"][mid] = {"online": online, "sid": sid, "endpoint": st.endpoint_of(b), "beat": time.time()}
        st.room_sids[sid] = rid
        members = [st.member_record(room, m) for m in room["members"] if m != mid]
        me = st.member_record(room, mid)
        for omid, om in room["members"].items():
            if omid != mid:
                st.push(om["online"], "room_member_joined", **me)
        owner = room["members"][room["owner"]]["online"]
        if (owner, online) in st.invites:
            st.touch_invite(owner, online, "joined")
    log(f"np: {online} joined room {rid} as member {mid} ({len(members)} other member(s))")
    return {"ResKind": 0, "RoomId": rid, "SessionId": sid, "MemberId": mid, "OwnerMemberId": room["owner"],
            "MaxMembers": room["max"], "Members": members, "HostRuleset": room["ruleset"]}


def _member_by_sid(b: dict):
    st = STATE
    sid = b.get("SessionId", "")
    rid = st.room_sids.get(sid)
    room = st.rooms.get(rid) if rid else None
    if not room:
        return None, 0
    for mid, m in room["members"].items():
        if m["sid"] == sid:
            return room, mid
    return None, 0


def np_leave_room(b: dict) -> dict:
    st = STATE
    with st.lock:
        room, mid = _member_by_sid(b)
        if room:
            st.remove_member(room, mid, "leave_room")
    return {"ResKind": 0}


def np_heartbeat(b: dict) -> dict:
    st = STATE
    st.reap()
    with st.lock:
        room, mid = _member_by_sid(b)
        if not room:
            return {"ResKind": 0, "InRoom": 0}
        room["members"][mid]["beat"] = time.time()
    return {"ResKind": 0, "InRoom": 1}


def np_kick(b: dict) -> dict:
    st = STATE
    with st.lock:
        room, kicker = _member_by_sid(b)
        target = int(b.get("MemberId", 0) or 0)
        if not room or kicker != room["owner"] or target not in room["members"]:
            return {"ResKind": 1, "Error": "not allowed"}
        m = room["members"][target]
        st.push(m["online"], "room_member_kicked", RoomId=room["id"], MemberId=target, OptData=b.get("OptData", ""))
        room["members"].pop(target)
        st.room_sids.pop(m["sid"], None)
        for om in room["members"].values():
            st.push(om["online"], "room_member_left", RoomId=room["id"], MemberId=target, OnlineId=m["online"],
                    Reason="kicked")
    return {"ResKind": 0}


def np_events_poll(b: dict) -> dict:
    st = STATE
    online = b.get("OnlineId", "")
    cursor = int(b.get("Cursor", 0) or 0)
    with st.lock:
        q = st.events.get(online, [])
        for ev in q:
            if ev["EventId"] > cursor:
                payload = {k: v for k, v in ev.items() if k not in ("EventId", "Name")}
                # bbhost reads the fields at the top level, tools/nettest's
                # simulated client from Payload: both are given.
                return {"HasEvent": 1, "EventId": ev["EventId"], "Name": ev["Name"], "NextCursor": ev["EventId"],
                        "Category": "matching2", "Payload": payload, **payload}
        last = q[-1]["EventId"] if q else 0
        return {"HasEvent": 0, "NextCursor": max(cursor, last)}


def np_events_ack(b: dict) -> dict:
    st = STATE
    online = b.get("OnlineId", "")
    cursor = int(b.get("Cursor", 0) or 0)
    with st.lock:
        st.events[online] = [e for e in st.events.get(online, []) if e["EventId"] > cursor]
    return {"ResKind": 0}


def np_session_blob(q: dict) -> dict:
    return {"ResKind": 0}


# --------------------------------------------------------------------------
# friend invites ("seamless" parties). The stock client never calls these; a
# plugin (or curl) does. See ../README.md "Friend invites".

def push_invite_event(inv: dict) -> None:
    """Tells the invited player's bbhost (logged as "np: event #N
    seamless_invite"; bbhost ignores the name otherwise) - for a plugin hook
    or a tool watching the log."""
    STATE.push(inv["to"], "seamless_invite", From=inv["from"], To=inv["to"], Ruleset=inv["rules"],
               State=inv["state"])


def _sender(b: dict) -> str:
    """The inviting player as the server knows it: its online id (From), or
    the game login's UserId / SessionId."""
    st = STATE
    fr = str(b.get("From") or b.get("from") or "")
    if not fr and b.get("SessionId") in st.sessions:
        fr = st.sessions[b["SessionId"]]["acct"]
    if not fr and b.get("UserId") is not None:
        fr = online_of_user(int(b.get("UserId") or 0))
    return fr


def seamless_invite(b: dict) -> dict:
    st = STATE
    fr, to = _sender(b), str(b.get("To") or b.get("to") or "")
    if not fr or not to or fr == to:
        return {"ResKind": 1, "Error": "need From (or UserId/SessionId) and a different To"}
    with st.lock:
        known = fr in st.players or fr in st.users
        inv = st.invites.get((fr, to))
        if not inv:
            inv = {"from": fr, "to": to, "state": "pending", "rules": req_rules(), "since": time.time()}
            st.invites[(fr, to)] = inv
        inv["t"], inv["rules"] = time.time(), req_rules()
        push_invite_event(inv)
        targets, inviters = sorted(st.invited_by(fr)), sorted(st.inviters_of(to))
    log(f"invite {fr} -> {to}: recorded (ruleset {inv['rules']!r}" + ("" if known else f"; {fr} is not online yet")
        + f"); {fr}'s sign search now shows only {targets}, {to}'s sign is kept for {inviters}")
    return {"ResKind": 0, "Invite": {k: inv[k] for k in ("from", "to", "state", "rules")}}


def seamless_invite_cancel(b: dict) -> dict:
    """From cancels its invite(s) (all, or the one to To); an invited player
    declines every invite to it with {"Decline": "<its online id>"}."""
    st = STATE
    fr, to = _sender(b), str(b.get("To") or b.get("to") or "")
    dec = str(b.get("Decline") or "")
    with st.lock:
        gone = [k for k in st.invites if (dec and k[1] == dec) or
                (not dec and fr and k[0] == fr and (not to or k[1] == to))]
        for k in gone:
            st.invites.pop(k, None)
    log(f"invite cancel {b}: removed {gone}")
    return {"ResKind": 0, "Removed": [{"from": a, "to": c} for a, c in gone]}


def seamless_invites(q: dict) -> dict:
    st = STATE
    who = q.get("online", "")
    with st.lock:
        out = [{k: inv[k] for k in ("from", "to", "state", "rules")} | {"age": round(time.time() - inv["since"])}
               for inv in st.invites.values() if not who or who in (inv["from"], inv["to"])]
    return {"ResKind": 0, "Invites": out}


QUIET_PATHS = {"/np/events/poll", "/np/events/ack", "/mp/matching2/heartbeat"}


# --------------------------------------------------------------------------
# HTTP

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "coop-test/1"

    def log_message(self, fmt, *args):  # the default goes to stderr; ours is log()
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            return self.rfile.read(n)
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            out = b""
            while True:
                size = int(self.rfile.readline().strip() or b"0", 16)
                if not size:
                    self.rfile.readline()
                    break
                out += self.rfile.read(size)
                self.rfile.readline()
            return out
        return b""

    def _handle(self) -> None:
        port = self.server.server_address[1]
        url = urllib.parse.urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        q = dict(urllib.parse.parse_qsl(url.query))
        raw = self._read_body() if self.command in ("POST", "PUT") else b""
        ua = self.headers.get("User-Agent", "")
        rules = self.headers.get("X-BBHost-Ruleset", "")
        _req.rules, _req.adopt = rules, self.headers.get("X-BBHost-Adopt", "")
        quiet = path in QUIET_PATHS
        if not quiet or OPTS.verbose:
            log(f"[{port}] {self.command} {self.path} ua={ua!r} ruleset={rules!r}"
                + (f" adopt={_req.adopt!r}" if _req.adopt else "") + f" body={short(raw)}")
        try:
            code, ctype, out = self._route(path, q, raw, rules)
        except Exception:
            log("handler error:\n" + traceback.format_exc())
            code, ctype, out = 500, "application/json", b'{"ResKind":1,"Error":"server error"}'
        if not quiet or OPTS.verbose:
            log(f"[{port}]   -> {code} {short(out, 2000)}")
        elif b'"HasEvent": 1' in out:
            log(f"[{port}] {self.command} {path} -> {short(out, 2000)}")
        self._send(code, out, ctype)

    def _route(self, path: str, q: dict, raw: bytes, rules: str):
        cmd = self.command
        j = lambda d: (200, "application/json", json.dumps(d).encode())  # noqa: E731
        body = {}
        if raw and cmd == "POST":
            try:
                body = json.loads(raw)
            except Exception:
                body = {}
        if cmd == "GET" and path in ("/status", "/"):
            return j({"ok": True, "service": "coop-test", "players": sorted(STATE.players),
                      "rooms": {r: [m["online"] for m in room["members"].values()] for r, room in STATE.rooms.items()},
                      "signs": len(STATE.signs),
                      "room_rules": {r: room["ruleset"] for r, room in STATE.rooms.items()},
                      "invites": [f"{a} -> {b}: {inv['state']}" for (a, b), inv in STATE.invites.items()]})
        if cmd == "GET" and path == "/seamless/invites":
            return j(seamless_invites(q))
        if cmd == "POST" and path == "/seamless/invite":
            return j(seamless_invite(body))
        if cmd == "POST" and path == "/seamless/invite/cancel":
            return j(seamless_invite_cancel(body))
        if cmd == "GET" and path == "/mp/matching2/session_blob":
            return j(np_session_blob(q))
        if cmd == "PUT":  # the play log (bbhost rewrites the S3 uploads here)
            return 200, "application/xml", b""
        if path.startswith("/auth/"):
            return j({"ResKind": 1, "Message": "this local test server has no accounts (set require_account = false)"})
        np_routes = {
            "/mp/matching2/context_start": np_context_start,
            "/mp/matching2/signaling_update": np_signaling_update,
            "/np/signaling/resolve": np_resolve,
            "/mp/matching2/join_room": np_join_room,
            "/mp/matching2/leave_room": np_leave_room,
            "/mp/matching2/heartbeat": np_heartbeat,
            "/mp/matching2/kick_member": np_kick,
            "/np/events/poll": np_events_poll,
            "/np/events/ack": np_events_ack,
        }
        if cmd == "POST" and path == "/mp/matching2/create_room":
            return j(np_create_room(body, rules or "vanilla"))
        if cmd == "POST" and path in np_routes:
            return j(np_routes[path](body))
        if cmd == "POST" and path in GAME_HANDLERS:
            return j(GAME_HANDLERS[path](body, q))
        if cmd == "POST" and (path in PATH_MESSAGES or (isinstance(body, dict) and "MessageId" in body)):
            return j(game_default(path, body))
        if cmd in ("GET", "HEAD"):
            # ss.info, whatever its path (the game asks for it on 20443).
            base = f"http://{OPTS.public_host}:{OPTS.http_port}"
            text = build_ss_info(base, OPTS.ssinfo_extra)
            log("ss.info (sent base64-encoded): " + short(text, 600))
            # The game base64-decodes the body before it parses it (eboot
            # sub_1ea92e0 between the HTTP response and sub_1eb65e0).
            return 200, "text/plain", base64.b64encode(text.encode())
        if cmd == "POST":
            return j({"ResKind": 0})
        return 404, "text/plain", b"not found"

    do_GET = do_POST = do_PUT = do_HEAD = _handle


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


# --------------------------------------------------------------------------
# STUN (and bbhost's optional relay)

STUN_REQ, STUN_RESP = 0x0001, 0x0101
A_MAPPED, A_XOR_MAPPED, A_HELLO, A_RELAY = 0x0001, 0x0020, 0x8100, 0x8101


class Stun(threading.Thread):
    def __init__(self, host: str, port: int, relay: bool):
        super().__init__(daemon=True, name="stun")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.relay = relay
        self.by_token: dict[bytes, dict] = {}   # token -> {vport, addr}
        self.by_vport: dict[int, bytes] = {}
        self.next_vport = 4000
        self.seen: set = set()

    def run(self) -> None:
        while True:
            try:
                data, src = self.sock.recvfrom(65536)
            except OSError:
                continue
            try:
                self.on_packet(data, src)
            except Exception:
                log("stun error:\n" + traceback.format_exc())

    def on_packet(self, data: bytes, src) -> None:
        if len(data) >= 12 and data[0] == 0xFB and data[1] == ord("R"):
            if not self.relay:
                return
            token, dst = data[2:10], struct.unpack_from("!H", data, 10)[0]
            me = self.by_token.get(token)
            peer_tok = self.by_vport.get(dst)
            if me and peer_tok and peer_tok in self.by_token:
                to = self.by_token[peer_tok]["addr"]
                self.sock.sendto(bytes([0xFB, ord("r")]) + struct.pack("!H", me["vport"]) + data[12:], to)
            return
        if len(data) < 20:
            return
        mtype, mlen = struct.unpack_from("!HH", data, 0)
        if mtype != STUN_REQ:
            return
        txid = data[4:20]
        hello = None
        off = 20
        while off + 4 <= 20 + mlen and off + 4 <= len(data):
            at, al = struct.unpack_from("!HH", data, off)
            if at == A_HELLO and al >= 12 and data[off + 4:off + 8] == b"bbr1":
                hello = data[off + 8:off + 16]
            off += 4 + ((al + 3) & ~3)
        ip = socket.inet_aton(src[0])
        attrs = struct.pack("!HHBBH", A_MAPPED, 8, 0, 1, src[1]) + ip
        xport = src[1] ^ struct.unpack_from("!H", txid, 0)[0]
        xip = bytes(a ^ b for a, b in zip(ip, txid[:4]))
        attrs += struct.pack("!HHBBH", A_XOR_MAPPED, 8, 0, 1, xport) + xip
        if self.relay and hello is not None:
            tok = hello if hello in self.by_token else None
            if tok is None:
                tok = secrets.token_bytes(8)
                vport = self.next_vport
                self.next_vport += 1
                self.by_token[tok] = {"vport": vport, "addr": src}
                self.by_vport[vport] = tok
            rec = self.by_token[tok]
            rec["addr"] = src
            val = b"bbr1" + tok + struct.pack("!H", rec["vport"]) + ip + struct.pack("!H", src[1])
            attrs += struct.pack("!HH", A_RELAY, len(val)) + val
        self.sock.sendto(struct.pack("!HH", STUN_RESP, len(attrs)) + txid + attrs, src)
        if src not in self.seen:
            self.seen.add(src)
            log(f"stun: binding from {src[0]}:{src[1]}" + (" (relay hello)" if hello is not None else ""))


# --------------------------------------------------------------------------

def main() -> int:
    global STATE, OPTS, _log_file, _quiet
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bind", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1)")
    ap.add_argument("--public-host", default="127.0.0.1", help="the host ss.info hands the game (default 127.0.0.1)")
    ap.add_argument("--http-port", type=int, default=18671)
    ap.add_argument("--ssinfo-port", type=int, default=20443)
    ap.add_argument("--stun", default="127.0.0.1:3478", help="STUN bind host:port, or 'off'")
    ap.add_argument("--relay", action="store_true", help="answer bbhost's relay HELLO and relay frames")
    ap.add_argument("--any-area", action="store_true", help="sign search ignores AreaId/AreaRegionId")
    ap.add_argument("--heartbeat-timeout", type=float, default=30.0)
    ap.add_argument("--available-patch", action="store_true",
                    help="set SummonData byte 0x79 to 1 in search results (shadnet-p2p's broker does; off by default)")
    ap.add_argument("--no-guest-invite", action="store_true",
                    help="do not send the guest_invite event (room + host address) on a summon request")
    ap.add_argument("--claim-seconds", type=float, default=30.0,
                    help="how long a summoned sign hands its claim to the guest's creates")
    ap.add_argument("--summon-poll", type=int, default=0, metavar="SECONDS",
                    help="ss.info SummonDataGetListInterval: how often a ringing bell searches for signs "
                         "(the game's default is 60 s; 0 leaves it out)")
    ap.add_argument("--sign-refresh", type=int, default=0, metavar="SECONDS",
                    help="ss.info SummonDataCreateInterval: how often a sign is re-sent (the game's default is 30 s; 0 leaves it out)")
    ap.add_argument("--no-ruleset-match", action="store_true",
                    help="match signs and rooms across rulesets (X-BBHost-Ruleset) and passwords")
    ap.add_argument("--invite-ttl", type=float, default=1800.0,
                    help="seconds an unused friend invite / party record lives (refreshed on use)")
    ap.add_argument("--invite-same-area", action="store_true",
                    help="invited pairs must also share AreaId/AreaRegionId (off: an invite matches anywhere)")
    ap.add_argument("--ssinfo-set", default="", help="extra ss.info values: Name=value,Name=value "
                                                     "(e.g. SummonDataGetListInterval=5)")
    ap.add_argument("--log", default=os.path.join(here, "server.log"))
    ap.add_argument("-v", "--verbose", action="store_true", help="also log event polls, acks and heartbeats")
    ap.add_argument("-q", "--quiet", action="store_true", help="log to the file only")
    OPTS = ap.parse_args()
    OPTS.ssinfo_extra = {}
    if OPTS.summon_poll > 0:
        OPTS.ssinfo_extra["SummonDataGetListInterval"] = str(OPTS.summon_poll)
    if OPTS.sign_refresh > 0:
        OPTS.ssinfo_extra["SummonDataCreateInterval"] = str(OPTS.sign_refresh)
    OPTS.ssinfo_extra.update(kv.split("=", 1) for kv in OPTS.ssinfo_set.split(",") if "=" in kv)
    _quiet = OPTS.quiet
    os.makedirs(os.path.dirname(os.path.abspath(OPTS.log)), exist_ok=True)
    _log_file = open(OPTS.log, "a", encoding="utf-8")
    STATE = State(OPTS.any_area, OPTS.heartbeat_timeout)
    STATE.invite_ttl = OPTS.invite_ttl

    servers = []
    for port in sorted({OPTS.http_port, OPTS.ssinfo_port}):
        s = ThreadingHTTPServer((OPTS.bind, port), Handler)
        threading.Thread(target=s.serve_forever, daemon=True, name=f"http-{port}").start()
        servers.append(s)
    stun_desc = "off"
    if OPTS.stun != "off":
        h, p = OPTS.stun.rsplit(":", 1)
        Stun(h, int(p), OPTS.relay).start()
        stun_desc = f"{h}:{p}" + (" + relay" if OPTS.relay else "")

    def reaper():
        while True:
            time.sleep(5)
            try:
                STATE.reap()
            except Exception:
                log("reaper error:\n" + traceback.format_exc())
    threading.Thread(target=reaper, daemon=True).start()

    log(f"server up: http {OPTS.bind}:{OPTS.http_port} (game API + NP), ss.info also on :{OPTS.ssinfo_port}, "
        f"stun {stun_desc}; log {os.path.abspath(OPTS.log)}")
    log(f"matching: rulesets {'ignored' if OPTS.no_ruleset_match else 'must match (pw= makes it private)'}, "
        f"area {'any' if OPTS.any_area else 'same'}; ss.info extra {OPTS.ssinfo_extra or 'none'}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log("server stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
