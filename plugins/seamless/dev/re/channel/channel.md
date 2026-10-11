# A plugin control channel between co-op players (Bloodborne 1.09, bbhost)

All addresses are **Binary Ninja addresses (base 0x400000)** unless marked RVA.
RVA = BN - 0x400000; ELF file offset = BN - 0x3FC000. Note: the `bb::Rva{...}`
constants in `include/bbhost/engine/**` are RVAs - `WORLD_SESSION_SEND_PACKET_FN{0x17894e0}`
is **BN 0x1b894e0**. Everything below was read from `games/eboot-109-decrypted.elf`
(no game was run). Helpers in this folder: `d.py` (BN-addressed disassembly / callers /
rip-refs / function bounds / raw bytes, built on `../session-end/eb.py` + `xref.py`) and
`scan.py` (every immediate packet type passed to the send/pop entry points).

---------------------------------------------------------------------------------------

## TL;DR - recommendation

**Option A, as a new WorldSession packet type `0x1a`, sent with the game's own
`WorldSessionObjectMan::SendPacket` and received by polling the game's own per-peer
`PopPeerMessage` from `on_frame` (both through `call_guest`). No hooks, no patches.**

| what | BN | args (SysV) | returns |
|---|---|---|---|
| `WSOM::SendPacket` | `0x1b894e0` | rdi = WSOM, rsi = `SprjSessionObjectRef*` peer, edx = type, rcx = payload, r8d = size | bool (see caveats) |
| `WSOM::SendToAll` | `0x1b89980` | rdi = WSOM, esi = type, rdx = payload, ecx = size | 1 if every send returned 1 |
| `WSOM::SendToHost` | `0x1b89a40` | rdi = WSOM, esi = type, rdx = payload, ecx = size | SendPacket's result (peer = SessMgr+0xd0) |
| `WSOM::PopPeerMessage` | `0x1b89200` | rdi = WSOM, rsi = `SprjSessionObjectRef*` peer, edx = type, rcx = out buf, r8d = capacity | int bytes copied (0 = nothing) |
| `ObjectRef::IsLocal` | `0x10a1670` | rdi = ref | al: ref's object valid and object+0xc != 0 (the local member) |
| `ObjectRef::MatchesTarget` | `0x10a1c70` | rdi = ref a, rsi = ref b | al: same peer (object vfunc +0x58) |
| `g_WorldSessionObjectMan` | `0x593d6d0` (ptr) | | |
| `g_SprjSessionManager` | `0x5940290` (ptr) | | |
| `g_WorldChrMan` | `0x593e878` (ptr) | | |

Why: type `0x1a` is never sent or popped by the game (verified for every call site, sec. A3);
types >= 7 go on the game's reliable/stream channel and are retried from a deferred queue
on transport failure; the receiver queues an unconsumed type silently (no drop, no crash, no
disconnect); payload up to 0x63f8 bytes; the peer identity comes for free (you pop per
peer); everything runs on the main thread, exactly the way the game's own Lua-network pump
(`SprjLuaEventContext::UpdateNetwork` BN `0x1739b50`) consumes packet 0x0e.

Rejected: **B** (`register_hle` on sceNetSendto/Recvfrom) is impossible as the host stands -
the plugin's replacement is silently overwritten and could not call the original anyway
(sec. B). **C** (own UDP socket) has no source of peer addresses and no NAT/relay story
(sec. C). The **Lua-call 0x0e carrier** works but carries 8 bytes, also fires locally, has no
sender identity reachable from hook2, and needs a hot-path hook (sec. A6).

---------------------------------------------------------------------------------------

## A. The game's own WorldSession packets

### A1. Send: `WorldSessionObjectMan::SendPacket` BN 0x1b894e0 (RVA 0x17894e0)

Signature (from the prologue spills): `bool SendPacket(WSOM* rdi, SprjSessionObjectRef* rsi
peer, u32 edx type, const void* rcx payload, u32 r8d size)`. **Unicast to one peer.**
Prologue (20 bytes, clean, hook2-able): `55 48 89 e5 41 57 41 56 41 55 41 54 53 48 81 ec 88 00 00 00`.

Body, in order (BN):
1. `0x1b8951b` take the WSOM send lock (`WSOM+0x130` vfunc +0x18; released at `0x1b8991f` vfunc +0x28).
2. `0x1b8956b` `SessMgr+0x18` matching session valid (`0x108f570`), else return 0.
3. `0x1b895ab` route count `(SessMgr+0x100 - SessMgr+0xf8) >> 5 >= 2`, else 0.
4. `0x1b895c9` `ObjectRef::IsValid(peer)` (`0x10a1400`), else 0.
5. `0x1b8960b..0x1b8962d` build the *local* member's ObjectRef (`0x108fb30`) and return 0 if
   `peer` is ourselves (`MatchesTarget 0x10a1c70`).
6. **State gate** `0x1b8963b`: if `WSOM+0x84 != 4` (not HostSyncReady), only types in
   `{7,8,0xa,0xb,0xc,0xd,0x10,0x11,0x19,0x42}` pass (`(type-7) < 0x3c && bt 0x080000000004067b`);
   everything else returns 0 **without queueing**.
7. `0x1b8967b` `size + 8 <= 0x6400`, else 0 (max payload 0x63f8).
8. Stage `[type][size][payload]` in the global buffer BN `0x59640f0` (`WORLD_SESSION_SEND_STAGING_BUFFER`).
9. `0x1b89717` `MatchingSession_Send(SessMgr+0x18, channel = (type > 6), peer, staging, size+8)`
   (`0x108f0a0` -> `0x10976c0`, which picks the channel whose id equals that 0/1).
   Success: `WSOM+0x88 += size+8`, `0x108f110(...)`, **return 1**.
10. Failure and `type >= 7` (`0x1b89742`): if the deferred-send backlog (sum of per-peer queue
    counts + SessMgr+0x1b0) is `>= 0x3e9` -> **"OnIrregularLeaveSession" + BeginLeaveSession**
    (`0x1b897b8..0x1b8980d`); otherwise copy the staged bytes into a heap buffer and
    `QueuePeerSend` (`0x22d2310`) for retry by the session update - and **return 0 anyway**.
    Failure with `type <= 6`: dropped, return 0.

Channels (`SprjSessionManager::ConfigureReceiveChannels` BN `0x22d00d0`): channel id 0 is
created with kind 1, channel id 1 with kind 2, both 0x10000/0x10000 buffers, both delivering
to `SprjSessionManager::ReceivePayloadCallback` BN `0x22d6780`. So **types 0..6 go on
channel 0 (kind 1, framed datagrams, may be lost), types >= 7 on channel 1 (kind 2)**. The
receiver parses kind-2 data with an incremental header/body reassembler that tolerates frames
split across deliveries (sec. A2), and the sender retries failed kind-2 sends from a queue -
i.e. channel 1 is the reliable, ordered stream. (Inference from structure; the NexusRevolution
transport itself was not traced.)

Broadcast helpers (same file region, both clean prologues):
- `SendToAll` BN `0x1b89980`: `(WSOM, esi type, rdx payload, ecx size)`; walks
  `SessMgr+0xf8..+0x100` (stride 0x20, each entry is a `SprjSessionObjectRef` - SendPacket takes
  `&entry`), skips `IsLocal` (`0x10a1670`), SendPacket to each; returns 1 only if all returned 1.
  No direct callers in the image (the game inlines this loop, e.g. LuaCallStart `0x172ea53`).
- `SendToHost` BN `0x1b89a40`: `(WSOM, esi type, rdx payload, ecx size)`; peer = `SessMgr+0xd0`
  (the active route = the room owner seen from a client); used by the game for 0x15/0x16/0x2c
  (guest->host).

**Who receives:** exactly the peer passed (SendPacket) / every non-local route
(SendToAll) / the host (SendToHost). Routes = the room's members as `SessMgr+0xf8` lists them,
including the local member (skip it).

### A2. Receive path and what happens to an unknown id

```
network thread: SprjSessionManager::ReceivePayloadCallback BN 0x22d6780 (peerRef, channelKind, bytes, size, mgr)
   copies the bytes into SessMgr+0x290 under the +0x2b8 mutex (1800 retries per entry)
main thread:  WSOM update BN 0x1b89b60 -> SessMgr update BN 0x22d0b20 (call at 0x1b89c1a)
   -> consumer vfunc +8 = WSOM::ReceiveCallback BN 0x1b8d510 (wsom, peerRef, edx kind, rcx bytes, r8d size)
        finds the per-peer packet record in WSOM+0x28 by MatchesTarget; none -> return false
        (the session manager then requeues the entry; rejected bytes feed the back-pressure timer
        SessMgr+0x2e0 vs +0x2f0 -> OnIrregularLeaveSession + BeginLeave at 0x1b89cb4)
        found -> PeerReceiveBytes(record, mode = (kind == 2), bytes, size) and return TRUE
   -> PeerReceiveBytes BN 0x1b90c20 (record, esi mode, rdx bytes, ecx size)
```

`PeerReceiveBytes` (prologue 17 bytes clean `55 48 89 e5 41 57 41 56 41 55 41 54 53 48 83 ec 48`):
- **mode 0 (channel 0)** `0x1b90c4d`: needs `size >= 8` and `size == hdr.size + 8`; type
  `> 0x43` -> dropped (`0x1b90c76`); if `WSOM+0x84 != 4` and `type-1 <= 0x13` and bit set in
  `0xe003b` (types 1,2,4,5,6,0x12,0x13,0x14) -> dropped; else allocated and appended to
  `record->queue_table[type]` (`0x1b91b30`), then the queue is trimmed to
  `MaxRegularPacketNumPerPacketType` (int at BN `0x59282cc`, image value 20) by freeing the oldest.
- **mode 1 (channel 1)** `0x1b90cb9`: incremental reassembly (header at record+0x50, cursors
  +0x58/+0x60, body +0x68/+0x70/+0x78); on a complete frame: type `> 0x43` -> body freed
  (`0x1b90e31`), same pre-sync filter as above, else appended to `queue_table[type]`
  (`0x1b90e1a`). **No count limit in mode 1.**

There is **no switch/dispatcher over packet ids**: the receive side only files payloads into 68
(`0x44`) per-type queues per peer, and each game subsystem *pulls* its own type with
`PopPeerMessage`/`PopCurrentRouteMessage`. Consequences:
- unknown id `>= 0x44`: silently dropped (no crash, no disconnect; the callback still returns
  true, so no requeue/back-pressure);
- unused id `< 0x44` (e.g. 0x1a): **queued and never consumed** by the game - harmless (a few
  bytes per message on a peer without the plugin; seamless peers all have it, the server pairs
  only same-ruleset players);
- a hook is not needed to intercept: the plugin just pops its own type.

### A3. Free packet ids (verified)

`scan.py` resolves the immediate type at every call/jmp to SendPacket, SendToHost,
PopPeerMessage (`0x1b89200`), PopCurrentRouteMessage (`0x1b893e0`) and PeerPopQueued
(`0x1b910f0`); the only unresolved sites are the wrappers themselves.

```
sent:   4 5 6 7 8 a b c d e f 10 11 12 13 14 15 16 17 19 22 24 25 26 27 29 2a..34 36..39 3b..43
popped: 4 5 6 7 8 a b c d e f 10 11 12..17 19 22 24 25 26 27 29 2a..34 36..39 3b..3e 3f 40..43
never:  0 1 2 3 9 18 1a 1b 1c 1d 1e 1f 20 21 23 28 35 3a
```

Pick from **0x1a..0x21**: besides being unused they are
- `>= 7` -> channel 1 (reliable) and deferred-retry on send failure;
- not in the receive pre-sync drop mask (`type-1 > 0x13`);
- in PopPeerMessage's "no PlayerIns needed" window: `0x1b89288` `(type-0x1a) <= 0x10 && !bt 0x9e00`
  lets 0x1a..0x22 (minus 0x23..0x26, 0x29) through without the check that a PlayerIns with that
  peer ref exists in WorldChrMan (`0x1b8929f..0x1b89302`, players at WorldChrMan+0x38 stride 0x38,
  ref at PlayerIns+0x3d8). Types 0x0..0x3, 0x9, 0x18 are <= 6 or need that check; avoid them.

**Use 0x1a** (and 0x1b.. if a second lane is ever wanted).

Gating to remember: type 0x1a is **not** in the "allowed before sync" set
`{7,8,a,b,c,d,10,11,19,42}`, so
- the sender's `SendPacket` returns 0 (not queued) unless the **sender's** `WSOM+0x84 == 4`;
- the receiver queues it in any state, but `PeerPopQueuedMessage` (`0x1b9111b`) returns 0 until the
  **receiver's** `WSOM+0x84 == 4` - messages wait in the queue across the receiver's transition
  (state 3) and pop once it is back in 4.

State 4 (`HostSyncReady`) is written on the host by `WSOM::DispatchSessionEvent` (BN
`0x1b8bddb`) and on a guest by `WSOM::SetSyncReadyAndBroadcast` (BN `0x1b8d9e2`, called from
the guest world-load rebuild `0x1d39497`). A WSOM reset (`0x1b8a900`, state 0) discards the
per-peer records and anything still queued.

### A4. Pop: `WSOM::PopPeerMessage` BN 0x1b89200 (RVA 0x1789200)

`int PopPeerMessage(WSOM* rdi, SprjSessionObjectRef* rsi peer, u32 edx type, void* rcx out,
u32 r8d capacity)`; prologue 17 bytes clean
`55 48 89 e5 41 57 41 56 41 55 41 54 53 48 83 ec 28`.
Session valid -> type gate (A3) -> find the record for `peer` in WSOM+0x28 -> tail-jmp
`PeerPopQueuedMessage(record, type, out, cap)` BN `0x1b910f0`: if `WSOM+0x84 != 4` only the
pre-sync types; walks `queue_table[type]` from the front, **skips (leaves queued) entries whose
size > cap**, copies the first that fits (`memcpy 0x2fbe578`), frees it, returns its size.
Pass a capacity >= your largest message so delivery is FIFO.

It takes no lock; it must run on the thread that runs the WSOM update (which also runs
`ReceiveCallback`). That is the game's main thread: the WSOM update is called from the world
step tasks (`0x1d5b954` in `0x1d5b920`, `0x1d47402`, `0x1d63378`, `0x1f394af`), and the game's own
consumer of 0x0e, `SprjLuaEventContext::UpdateNetwork` BN `0x1739b50`, pops exactly this way
(`0x1739bc8..0x1739c0d`: for each `SessMgr+0xf8` route, `PopPeerMessage(wsom, route, 0xe, buf,
0x14)`). bbhost's `on_frame` runs on that same main thread, so polling there is safe.

### A5. Who sent it; local role; member list

- **Sender** = the route you popped from. Map it to the character with
  `ObjectRef::MatchesTarget(&PlayerIns->peer_object_ref /*+0x3d8*/, route)` over
  `WorldChrMan->player_chr_set` (WorldChrMan+0x30: u32 capacity, +0x38 entries, entry stride
  0x38, `PlayerIns*` at entry+0; entry 0 is the local player). `PlayerIns+0x3b8` = multiplayer
  player number, `+0x3c0` = its GameDataMan player record (`+0xa4` chr type: 0 host, 1 white,
  2/12 invader, 8 ...). The WSOM member list (WSOM+0x68, 0x38-byte nodes, ref at +0x10,
  `SprjSessionMemberSlot` at +0x30: GameData slot index 0..3, generated id) gives the GameData slot.
  The PlayerIns may not exist yet (still loading) - also carry the sender's player number in the
  payload.
- **Local role**: `SprjSessionManager+0x124` (`SprjSessionRole`): 3 Host, 6 Client, 1 TryingToHost,
  4 TryingToJoin, 7 Leaving, 0 Idle. Host <=> role 3.
- **Members**: `SessMgr+0xf8` (begin) .. `+0x100` (end), stride 0x20, each a
  `SprjSessionObjectRef` (the header calls them `SprjSessionRouteConfig`; SendToAll proves they
  are ObjectRefs); one of them `IsLocal`. Count >= 2 means a live session.
- **The host's ref from a guest**: `SessMgr+0xd0` (`active_route`), as SendToHost/PopCurrentRoute use it.
- **Ready to talk**: `*(0x593d6d0) && *(0x5940290) && role in {3,6} && WSOM+0x84 == 4`.

### A6. The Lua-call (packet 0x0e) carrier - works, but worse

`LuaCallStart(ctx, id, p1)` BN `0x172e960` / `LuaCallStartPlus(ctx, id, p1, p2)` BN `0x172ead0`
(ctx = `*(SprjLuaEventMan*)0x593b0c8 + 8`): first `MsgMap_Dispatch` **locally** (`0x172e9c8`),
then, **if `ctx+0x8b != 0`** (not the registration's IsSend), for every non-local route
`SendPacket(wsom, route, 0x0e, &pkt, 0x14)` with `pkt = {u32 msg (12; 54 for Custom*), u32 id,
u32 p1, u32 p2 (0 for the 1-param form), u32 sender player no = (GameDataMan+8)->+0x10}`.
Peers: `UpdateNetwork` BN `0x1739b50` pops it and calls `MsgMap_Dispatch` BN `0x179a9d0`
(`rdi = ctx+0x38, esi = msg, edx = id, ecx = p1, r8d = p2, r9d = 0, [rsp] = ctx, [rsp+8] = sender
no, [rsp+0x10] = 1 (received)`). An id nobody registered dispatches to nothing - harmless.

So a plugin *could* send `LuaCallStartPlus(ctx, 9xxxx, a, b)` and hook2 `MsgMap_Dispatch`
(prologue 20 bytes clean `55 48 89 e5 41 57 41 56 41 55 41 54 53 48 81 ec 58 06 00 00`) filtering
`esi == 12 && edx == its id`. Drawbacks: 8 bytes of payload; the sender also dispatches it to
itself; the sender number and "received" flag are **stack** arguments, which `BbHookCtx` does not
expose; `MsgMap_Dispatch` is on every script message's path; it depends on the `ctx+0x8b` flag.
The raw-type approach has none of these.

### A7. Other places you could hook (not needed)

- `WSOM::ReceiveCallback` BN `0x1b8d510` (17 bytes clean `55 48 89 e5 41 57 41 56 41 55 41 54 53
  48 83 ec 18`): sees raw channel chunks; for channel 1 a chunk may hold a partial frame or several
  frames, so you would have to re-implement the reassembler. Don't.
- `PeerReceiveBytes` BN `0x1b90c20` (17 bytes clean, above): same problem.
- `SendPacket` BN `0x1b894e0` (20 bytes clean): to log/trace your own traffic.
- WSOM update BN `0x1b89b60` (20 bytes clean `55 48 89 e5 41 57 41 56 41 55 41 54 53 48 81 ec 18 21 00 00`):
  if you want to pop right before the game's own packet pump instead of at frame start.

---------------------------------------------------------------------------------------

## B. Intercepting sceNetSendto / sceNetRecvfrom with register_hle - not possible today

1. **The replacement is lost.** `api_register_hle` (`src/host/plugins.cpp:123`) calls
   `register_hle_fn` -> `g_hle[name] = fn` (`src/core/imports.cpp:35`). `main.cpp:1008` runs
   `plugins_load()` (plugins' `bb_plugin_init`) and *then* `register_hle()` at `main.cpp:1012`, whose
   `hle_register_net` does `REG("sceNetSendto", hle_net_sendto)` / `REG("sceNetRecvfrom", ...)`
   (`src/hle/net.cpp:1331-1332`) with the same unconditional assignment - so bbhost's own function
   overwrites the plugin's for **every import bbhost implements**, although the log says
   "replaced by a plugin". (Looks like a host bug worth fixing independently: register the HLE
   table first, or have `register_hle_fn` not overwrite plugin entries.)
2. Even if it stuck, the API offers no way to get or call the original (`hle_net_sendto` is a
   file-local function), and the plugin would have to re-implement bbhost's whole P2P port: vport
   header `[0xff][flags][src][dst]`, the per-port reader thread and inboxes, STUN, hole punching and
   the server relay framing (`net.cpp:215-640`, `net_p2p.h`).
3. Threading: these imports are called from the game's network worker threads, not the main
   thread, and the datagrams carry the NexusRevolution transport (sequence/ack/frames), so a
   foreign datagram on vport 30/40 would have to be stripped before the game sees it.

## C. The plugin's own UDP socket - not recommended

No API tells a plugin the peers' addresses (they live in bbhost's signaling/np state and the
`net:` log lines); bbhost's NAT handling (STUN on the P2P port, hole punching, relay through the
server for hard NATs) applies only to the game's P2P port, so a second socket would need its own
punching and would fail exactly for the relayed players. It would also need its own reliability.
Everything option A gets for free.

---------------------------------------------------------------------------------------

## D. Implementation sketch (seamless.cpp)

```cpp
// ---- control channel: WorldSession packet type 0x1a over the game's own session ----
#include <cstdint>
#include <cstring>
#include <functional>

namespace chan {

constexpr std::uint64_t kWsomPtr = 0x593d6d0;     // WorldSessionObjectMan*  (RVA 0x553d6d0)
constexpr std::uint64_t kSessMgrPtr = 0x5940290;  // SprjSessionManager*     (RVA 0x5540290)
constexpr std::uint64_t kWorldChrManPtr = 0x593e878;
constexpr std::uint64_t kSendPacket = 0x1b894e0;  // (wsom, peerRef*, type, data, size) -> bool
constexpr std::uint64_t kSendToHost = 0x1b89a40;  // (wsom, type, data, size) -> bool
constexpr std::uint64_t kPopPeer = 0x1b89200;     // (wsom, peerRef*, type, out, cap) -> bytes
constexpr std::uint64_t kRefIsLocal = 0x10a1670;  // (ref*) -> bool
constexpr std::uint64_t kRefMatches = 0x10a1c70;  // (ref*, ref*) -> bool
constexpr std::uint32_t kType = 0x1a;             // never sent/popped by the game
constexpr std::uint32_t kMagic = 0x434d4c53;      // "SLMC"
constexpr std::uint32_t kMax = 0x400;             // our cap (game allows 0x63f8)

enum Kind : std::uint8_t { Hello = 1, HostLampTravel, Resummon, EventFlag, HostRespawned, Ack };

#pragma pack(push, 1)
struct Header {
    std::uint32_t magic;
    std::uint8_t version, kind;
    std::uint16_t seq;          // per-sender, for dedupe (a failed send may still be delivered later)
    std::int32_t sender_no;     // sender's PlayerIns+0x3b8 (fallback identity)
    std::uint16_t body_size;
    std::uint16_t _pad;
};
#pragma pack(pop)

struct Peer { std::uint8_t* ref; std::uint8_t* player; };  // ref = &SessMgr route entry, valid this frame only

std::uint64_t call(std::uint64_t bn, std::uint64_t a = 0, std::uint64_t b = 0, std::uint64_t c = 0,
                   std::uint64_t d = 0, std::uint64_t e = 0) {
    BbCallRegs r{};
    r.arg[0] = a; r.arg[1] = b; r.arg[2] = c; r.arg[3] = d; r.arg[4] = e;
    return g.api->call_guest(bn, &r) == 0 ? r.ret : 0;
}
std::uint8_t* deref(std::uint64_t bn_slot) {
    const std::uint64_t at = g.api->guest_addr(bn_slot);
    return at ? *reinterpret_cast<std::uint8_t**>(at) : nullptr;
}
template <class T> T& at(std::uint8_t* p, std::size_t off) { return *reinterpret_cast<T*>(p + off); }

// Session snapshot; all of it main-thread only (on_frame).
struct Session {
    std::uint8_t* wsom = nullptr;
    std::uint8_t* mgr = nullptr;
    int role = 0;  // SprjSessionManager+0x124: 3 host, 6 client
    bool ready() const { return wsom && mgr && (role == 3 || role == 6) && at<std::int32_t>(wsom, 0x84) == 4; }
    bool is_host() const { return role == 3; }
};
Session session() {
    Session s;
    s.wsom = deref(kWsomPtr);
    s.mgr = deref(kSessMgrPtr);
    if (s.mgr) s.role = at<std::int32_t>(s.mgr, 0x124);
    return s;
}

// The PlayerIns whose peer ref (+0x3d8) is this route, or null (not loaded yet).
std::uint8_t* player_of(std::uint8_t* ref) {
    std::uint8_t* wcm = deref(kWorldChrManPtr);
    if (!wcm) return nullptr;
    const std::uint32_t n = at<std::uint32_t>(wcm, 0x30);
    std::uint8_t* entries = at<std::uint8_t*>(wcm, 0x38);
    for (std::uint32_t i = 1; entries && i < n; ++i) {           // entry 0 = the local player
        std::uint8_t* pi = at<std::uint8_t*>(entries, i * 0x38);
        if (pi && (call(kRefMatches, (std::uint64_t)(pi + 0x3d8), (std::uint64_t)ref) & 0xff)) return pi;
    }
    return nullptr;
}

// Every remote member (route) of the room.
template <class F> void for_each_peer(const Session& s, F&& f) {
    std::uint8_t* it = at<std::uint8_t*>(s.mgr, 0xf8);
    std::uint8_t* end = at<std::uint8_t*>(s.mgr, 0x100);
    for (; it && it < end; it += 0x20) {
        if (call(kRefIsLocal, (std::uint64_t)it) & 0xff) continue;
        f(Peer{it, player_of(it)});
    }
}

std::uint16_t g_seq;
std::int32_t local_no() {  // the local PlayerIns' player number
    std::uint8_t* wcm = deref(kWorldChrManPtr);
    std::uint8_t* me = wcm ? at<std::uint8_t*>(wcm, 0x60) : nullptr;  // WorldChrMan::main_player
    return me ? at<std::int32_t>(me, 0x3b8) : -1;
}
std::size_t build(std::uint8_t* out, Kind kind, const void* body, std::uint16_t n) {
    Header h{kMagic, 1, kind, ++g_seq, local_no(), n, 0};
    std::memcpy(out, &h, sizeof h);
    if (n) std::memcpy(out + sizeof h, body, n);
    return sizeof h + n;
}

// send(msg): to everyone (host -> guests, or a guest -> everyone), only to peers whose
// character exists here (i.e. fully joined - a peer without its WSOM record would make its
// session manager requeue our bytes). Returns how many peers took it. Main thread.
int send_all(Kind kind, const void* body, std::uint16_t n) {
    Session s = session();
    if (!s.ready() || sizeof(Header) + n > kMax) return 0;
    static std::uint8_t buf[kMax];
    const std::size_t len = build(buf, kind, body, n);
    int ok = 0;
    for_each_peer(s, [&](const Peer& p) {
        if (!p.player) return;
        // 1 = handed to the reliable channel. 0 = refused (no state 4/self/size) or the transport
        // was busy and the game queued it for retry - so never resend blindly; use seq + Ack.
        if (call(kSendPacket, (std::uint64_t)s.wsom, (std::uint64_t)p.ref, kType, (std::uint64_t)buf, len) & 0xff) ++ok;
    });
    return ok;
}
// Guest -> host only.
bool send_host(Kind kind, const void* body, std::uint16_t n) {
    Session s = session();
    if (!s.ready() || s.is_host() || sizeof(Header) + n > kMax) return false;
    static std::uint8_t buf[kMax];
    const std::size_t len = build(buf, kind, body, n);
    return call(kSendToHost, (std::uint64_t)s.wsom, kType, (std::uint64_t)buf, len) & 0xff;
}

// on_receive(msg): called from poll() for each message, with who sent it.
std::function<void(const Peer& from, const Header& h, const std::uint8_t* body)> on_receive;

// Drain our queue from every peer; run every frame (on_frame = main thread, the same thread
// that fills the queues inside the WSOM update).
void poll() {
    Session s = session();
    if (!s.ready()) return;  // pop returns 0 before state 4 anyway; queued messages wait
    static std::uint8_t buf[0x6400];  // >= any sender's size, so the pop stays FIFO
    for_each_peer(s, [&](const Peer& p) {
        for (int i = 0; i < 32; ++i) {
            const int n = (int)call(kPopPeer, (std::uint64_t)s.wsom, (std::uint64_t)p.ref, kType,
                                    (std::uint64_t)buf, sizeof buf);
            if (n <= 0) break;
            if (n < (int)sizeof(Header)) continue;
            Header h;
            std::memcpy(&h, buf, sizeof h);
            if (h.magic != kMagic || sizeof h + h.body_size > (std::size_t)n) continue;
            // dedupe on (p.ref->object or h.sender_no, h.seq) here
            if (on_receive) on_receive(p, h, buf + sizeof h);
        }
    });
}

}  // namespace chan

// in bb_plugin_image:
//   g.every_frame([] { chan::poll(); /* then act on what arrived, send what is pending */ });
```

Usage examples:
- host before lamp travel: `chan::send_all(chan::HostLampTravel, &return_point, 4)`; guests on
  receipt `lamp_warp(return_point)` (or re-summon) once the host's next `on_world_load` fires.
- host on `on_event_flag(id, v)` for flags the plugin cares about: `send_all(EventFlag, {id, v})`;
  guests `event_flag_set`.
- guest after its own respawn: `send_host(Resummon, ...)`.
- reliability: channel 1 is reliable while the room exists; acknowledge state-changing messages
  (`Ack{seq}`) and resend only after a timeout, with dedupe on (sender, seq), because a send that
  returned 0 may still be delivered from the game's deferred queue.

Thread summary: `send_*`, `poll`, `on_receive` all on the main thread (`on_frame`, or a hook2 on a
main-thread function). `call_guest` must not be used from `on_world_rules` (network thread).

## E. Caveats / to verify live

1. Sends only while the **sender's** `WSOM+0x84 == 4`; during a guest's in-session map load
   (state 3, keep_on_load) sends fail and the plugin must hold them; received messages wait in the
   queue until state 4 again. A WSOM reset (session end) drops queued messages.
2. Backlog: >= 1001 deferred reliable sends across peers makes the game itself leave the session
   (`0x1b897ad`); a few messages a minute is nothing, but never loop on a failing send.
3. Sending to a route whose peer has no WSOM record yet (still joining) makes the receiver's
   session manager requeue those bytes (up to 1800 times) and count them toward its
   receive back-pressure leave (`0x1b89c4d`); hence "only peers with a PlayerIns".
4. Channel 1 reliability/ordering is inferred (stream reassembly + sender retry queue), not traced
   into NexusRevolution.
5. Not checked: whether bbhost's relay path ever reorders/duplicates datagrams of channel 1
   (it should be transparent - it carries the game's transport unchanged).
6. Live smoke test: host `send_all` once per second with a counter, guest logs `poll` results and
   `player_of(ref)`; then the reverse with `send_host`; then across a guest map reload.
