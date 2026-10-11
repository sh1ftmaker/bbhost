# How a co-op session ends in Bloodborne 1.09 (CUSA00900), and where to stop it

All addresses are **Binary Ninja addresses (image base 0x400000)** unless marked RVA.
RVA = BN - 0x400000 = the ELF vaddr; file offset = RVA + 0x4000.
Everything below was read from `games/eboot-109-decrypted.elf` with llvm-objdump, and the
EMEVD/param/FMG data was parsed from `dvdroot_ps4`. Helper scripts are in this folder:
`eb.py` (disasm, eh_frame function bounds), `xref.py` (call/rip-ref scanner), `ann.py`
(annotated disasm), `emevd.py`/`dumpev.py` (EMEVD parser), and the extracted tables
`ge_map.txt` (227 compiled global-event callbacks), `ge_regs.txt` (their message keys),
`lua_bind.txt` (525 Lua proxy bindings), `boss_ins.txt` (every boss-defeat instruction).

---------------------------------------------------------------------------------------

## 0. The big picture

Bloodborne has **no EventResult / EventPhantomReturn / EventResultManager** (those are DS2/DS3
classes). Every "session is over" decision is made by Demon's-Souls-style **global_event.lua,
compiled to native code** (`SprjLuaEventScriptImitation`). Its 227 functions are registered by
name at BN `0x178d400` (name -> fn table, see `ge_map.txt`) and bound to message keys in
`g_Initialize` BN `0x177d7a0` (see `ge_regs.txt`). The script talks to the engine through the
Lua proxy bindings (`lua_bind.txt`, registry BN `0x173b370`).

There is exactly **one native "leave" primitive**:

| what | BN | RVA | notes |
|---|---|---|---|
| `SprjSessionManager::BeginLeaveSession(mgr)` | `0x22d07a0` | `0x1ed07a0` | single arg, **no reason code**. Roles 0/2/5 -> false. Otherwise role := 7, calls `MatchingSession` leave (`0x108ed70` -> `0x1098070(session, 0xff000023)` - a constant, not a reason), allocates the 0xe0 `CSSessionConnectStateStep` at `mgr+0x280` with request bit 0. |
| `CSSessionConnectStateStep::STEP_LeaveSession` | `0x22ce5d0` | `0x1ece5d0` | only `timer(+0xd0) = 10.0f`. Not called by anyone; it is a step-table entry reached after `STEP_Update` (`0x22ce590`) sees request bit 0. |
| `STEP_LeaveSessionWait` | `0x22ce5e0` | `0x1ece5e0` | waits for request bit 1 (set by ResetToIdle) or 10 s, then Finish. |

So `STEP_LeaveSession` has no callers and no reason codes; **the point of no return is
`BeginLeaveSession`**, which has 42 call sites (all listed in section 6). The "reason" lives
only in *which* script callback / native branch called it. The Lua binding `LeaveSession`
(BN `0x1732f60`) and ~10 inlined copies all do the same thing:

```
if (BeginLeaveSession(SprjSessionManager)) WSOM+0xf0 = PlayerGameData(local)+0xa4 /*chr type*/;
WorldTransition_ResetTracked(GameStateMan+0x16f8);      // BN 0x19bed10
```

Most "go home" paths end in **`SessionWorldTransition`** BN `0x17cde30` (RVA `0x13cde30`):

```
GameStateMan+0x08 = 1                        // request map reload/warp (destination set beforehand
                                             //  by SetDefaultMapUid / WarpNextStage)
PlayerGameData+0xa4 = local ChrIns+0x78     // remember chr type
save(PlayerGameData+0x1d0)
if (GameStateMan+0x1592 == 0)  -> LeaveSession (inline copy, BeginLeave at BN 0x17cdef6)
```

`GameStateMan+0x1592` is the transition-path gate bbhost already documents
(`WORLD_TRANSITION_STATE_BEGIN_LEAVE_GATE_OFFSET`; MoveMapStep writes 0 in WSOM phase 3, 1 otherwise).
That gate is the other agent's "room survives map load" territory - not duplicated here.

### Script messages and how they cross the network

`SprjLuaEventContext` (= `*(SprjLuaEventMan*)0x593b0c8 + 8`) dispatches 4-word keys
`(message, p1, p2, p3)` through `MsgMap_Dispatch` BN `0x179a9d0`:

| binding | BN | message | effect |
|---|---|---|---|
| `LuaCallStart(ctx,id,p1)` | `0x172e960` | 12 | dispatch locally, then (if the matched registration has IsSend) broadcast packet **0x0e** (EVENT_ACTION_STATE, 0x14 bytes) to every route |
| `LuaCallStartPlus(ctx,id,p1,p2)` | `0x172ead0` | 12 | same with 2 params |
| `CustomLuaCallStart(ctx,id,p1)` | `0x172ed40` | 54 | same, message 54 |
| `CustomLuaCallStartPlus(ctx,id,p1,p2)` | `0x172eeb0` | 54 | same, message 54 |
| `DispatchNamed(ctx, "Name")` | `0x1739870` | - | engine -> script by callback name (OnIrregularLeaveSession, OnBeKickOut, OnLeaveMagic, ...) |
| `ScheduleTimed(ctx,id,p1,p2,flag,delay,"Name")` | `0x172b1c0` | 15/16 | timed condition + registration; how the chains below advance |

Peers receive 0x0e in `SprjLuaEventContext::UpdateNetwork` (BN `0x1739b50`) and run the same
registration. Script ids: 4000 death, 4043 leave-player, 4044 kick, 4046 leave-message/limit,
4050 boss clear ("BlockClear"), 4055 white revive count, 4063 "game leave" (marks a player's
GameData slot `+0xa9` "left normally"), 4078 miniboss, 4090 dead-event broadcast.

Callback signature: `fn(void* script_globals /*rdi*/, SprjLuaEventContext* ctx /*rsi*/, msg* /*rdx*/)`.
`script_globals` is the compiled script's persistent global block (OnEvent_4000 writes +0x10/+0x14/+0x18,
SoloPlayDeath_TextWait reads +0x18 later). Two latches matter:
- `script_globals+0x0c` "white cleared the boss" (set by BlockClear2; makes PartyGhostDeath_1/_2 do nothing),
- `script_globals+0x0d` (HostDead checks it),
- `ctx+0x8d` `leave_event_latched` - published every frame to EventActMan+0x58 / ActionButtonMan+0x52
  (disables action buttons/event actions). **Only the ctx constructor (BN `0x1739530`) ever clears it**
  (17 writers set it to 1).

Script "event flags" used as locks (real event flags, set via the `SetEventFlag` binding BN `0x172e5a0`):
**4000** (death being processed - OnEvent_4000 returns immediately while it is set), **4047**
(leave in progress - OnLeaveMagic and the member-leave handler bail out), **4092**, **4020**.

---------------------------------------------------------------------------------------

## 1. Area boss killed -> phantoms sent home

**Driven by EMEVD, decided natively in the compiled global-event script.**

### 1a. EMEVD side

Instruction **2003[12] "Handle Boss Defeat"** (arg: boss entity id). 2003[53] (with banner) exists
in the engine but no map uses it. 2003[15] "Handle Miniboss Defeat" -> script 4078.
All 2003[12] uses (from `boss_ins.txt`; event id / instruction index / entity):

```
m21_00_00_00 12101800 #14 2100800 (Cleric Beast)   12101850 #10 2100810
m22_00_00_00 12201800 #23 2200800
m23_00_00_00 12301800 #15 2300800                  12301700 #19 2300810
m24_00_00_00 12401800 #16 2400800
m24_01_00_00 12411700 #15 2410800   12411800 #22 2410810, #24 2410811
m24_02_00_00 12421800 #15 2420800                  12421700 #37 2420811
m25_00_00_00 12501800 #19 2500800
m26_00_00_00 12601800 #26 2600803                  12601850 #28 2600850
m27_00_00_00 12701800 #30 2700800
m28_00_00_00 12801800 #43 2800803
m29 (chalice, parameterised entity) 12901588 #18, 12901589 #20, 12901590 #11, 12901591 #21,
                                    12901686 #21, 12901690 #17, 12901723 #27, 12901725 #35
m32_00_00_00 13201800 #17 3200800
m33_00_00_00 13301800 #19 3300800
m34_00_00_00 13401800 #21 3400800, #23 3400801     13401850 #16 3400850
m35_00_00_00 13501800 #13 3500800                  13501850 #23 3500850
m36_00_00_00 13601800 #22 3600800, #24 3600801
2003[15]: m21_00_00_00 12101800 #16 (alt path of the same event), m29 12901692 #21
```

Typical boss event (m21 12101800): wait dead -> `2007[02]` banner -> wait 3 s -> skip-if-flag ->
**`2003[12]` / `2003[15]`** -> `2000[02] Set Network Sync State 0` -> item lots, playlog.
No EMEVD instruction sends anyone home; there is no 2003[16]/3[9] use, 2003[14] WarpPlayer is
unused, and 2003[40] ("Battle of Stoicism Exit Request" -> `DispatchNamed("OnLeaveMagic")`, BN
`0x1bc191b`) is unused.

Bank 2003 dispatcher BN `0x1bc0420`, jump table BN `0x1bc2c38` (54 entries, index = instr-1).
Case 12 BN `0x1bc0a5f`, case 53 BN `0x1bc1e82` (both reach `call CustomLuaCallStartPlus` at
BN **`0x1bc1f0b`** `e8 a0 cf b6 ff`, with `(ctx, 4050, entity, banner=1)`); case 15 BN `0x1bc0bfa`
-> `CustomLuaCallStart(ctx, 4078, entity)` -> `MediumBossDestroy` (only resets UI slots).

### 1b. Native chain (every participant runs its *own* copy of the host's map EMEVD; BlockClear2
returns at once when `msg+0x08 != 0`, which looks like the "received over the network" copy - so
each machine's own 2003[12] drives its own chain)

```
2003[12] (BN 0x1bc0a5f) -> CustomLuaCallStartPlus(4050) BN 0x172eeb0 (msg 54, IsSend=1)
 -> BlockClear2            BN 0x17849a0   (key 54/4050/0/0)
      skip if SprjEventMan+0x60->+0x40 > 0 (arena/mission mode)
      host (ChrType 0/8): set 4047,4000,4092 if pending echoes; award banked boss echoes
                          (PlayerGameData+0xf8 -> PlayerIns vf 0x3d0 = AddEchoes BN 0x1cfc600)
      white (ChrType 1):  script_globals+0x0c = 1 (BN 0x1784cfe), ctx+0x8d = 1 (BN 0x1784d03),
                          flags 4047/4000/4092
      invader (2/12):     CustomLuaCallStart(4063) + MissionFailed + LeaveSession NOW (BN 0x1784ed3)
      all: SetClearBonus, schedule "BlockClear2_1" after 5.0 s
 -> BlockClear2_1          BN 0x1785470
      in session (PlayerGameData+0xa4 in {0,1,8} and white-ghost count > 0):
         0x19bda60(state,3,1); white: schedule RecallMenuEvent_MisssionClear_forNormalCoop (1 s)
         schedule "BlockClear2_2" after 2.0 s
      solo host/type 8: SoloBlockClear (3 s/6 s)  - clears 4047/4000/4092, LeaveSession (no-op solo)
      invader: EventState=2, +0x1520=1, SetDefaultMapUid(-1), SessionWorldTransition (BN 0x17856f4)
 -> BlockClear2_2          BN 0x1785760
      white: CustomLuaCallStart(4063 OnGameLeave, own player no)        call BN 0x17857ac
      award banked echoes (vf 0x3d0)
      schedule "BlockClear2_3"      after 8.0 s                           call BN 0x178584d
      schedule "BlockClear2_3Leave" after 5.0 s                           call BN 0x1785877
      MissionSuccessed (BN 0x1786580: "mission complete" action message)
 -> BlockClear2_3Leave     BN 0x17858a0   ALL ROLES: LeaveSession (BeginLeave BN 0x17858f5)
                                          -> the host closes the room 7 s after the kill
 -> BlockClear2_3          BN 0x1785930   UI reset; host types 0/8: clear flags 4047, 4000;
      white (type 1, test at BN 0x178599c): EventState(0x593b0d8)=2, GameDataMan+0x70=1,
      GameStateMan+0x1520=1, SetDefaultMapUid(-1), **tail-jump SessionWorldTransition**
      (BN 0x17859df) = "You have returned to your world"
```

So the white goes home twice over: its own `BlockClear2_3Leave` at +7 s and its own
`SessionWorldTransition` at +15 s; the host leaves the room at +7 s too.

### 1c. Minimal intervention (recommended: byte patches, all verified)

| # | BN | expect | write | effect |
|---|---|---|---|---|
| B1 | `0x1785877` | `e8 44 59 fa ff` | `0f 1f 44 00 00` | BlockClear2_2 never schedules BlockClear2_3Leave -> nobody (host or guest) calls LeaveSession after a boss. rax is not used after the call. |
| B2 | `0x178599c` | `75 46` | `eb 46` | BlockClear2_3: white branch never taken -> no EventState=2 / SetDefaultMapUid / SessionWorldTransition. |
| B3 | `0x1785967` | `74 06` | `eb 06` | BlockClear2_3: every chr type takes the host branch -> clears flags 4047 and 4000 on the white too (otherwise its next death is ignored: OnEvent_4000 returns while 4000 is set, and 4047 blocks Silencing Blank/member-leave handling). |
| B4 | `0x1784cfe` | `41 c6 47 0c 01` | `0f 1f 44 00 00` | BlockClear2 white: don't set script_globals+0x0c (else PartyGhostDeath_1/_2 become no-ops -> a white dying later just lies there). |
| B5 | `0x1784d03` | `41 c6 86 8d 00 00 00 01` | `0f 1f 84 00 00 00 00 00` | BlockClear2 white: don't latch ctx+0x8d (else the white loses action buttons/event actions until its next world load). |
| B6 (opt.) | `0x17857ac` | `e8 8f 95 fa ff` | `0f 1f 44 00 00` | BlockClear2_2 white: don't broadcast 4063 OnGameLeave (would set the white's GameData slot `+0xa9` "left" on every peer; that flag later suppresses the member-leave handler for that slot). rax unused after. |

Kept intact: echoes award, MissionSuccessed (cooperator reward message), RecallMenu,
SetClearBonus, item lots (EMEVD 2003[04]/[36]), banners, the invader branch (invaders still
leave - desirable).

Alternative (hook2, prologues are clean): `BlockClear2_2` BN `0x1785760` (n=14) - run your own
copy of the reward part and skip; `BlockClear2` BN `0x17849a0` (n=17) - capture
`script_globals`/`ctx` pointers. `BlockClear2_3`/`_3Leave` have rip-relative prologues -> patch
call sites, not hook2.

EMEVD alternative (not recommended): rewrite each 2003[12] above to 2003[15] in place (same
4-byte arg) - no BlockClear chain at all, but also no banked boss-echo award, no 4047/4000/4092
handling, no MissionSuccessed; you would have to award the echoes yourself.

Risks: (1) both machines must run the patches - each runs its own BlockClear chain, an unpatched
white still leaves itself. (2) Boss echoes are banked in PlayerGameData+0xf8 and paid out by
BlockClear2 (host) / BlockClear2_2 (white) - keep those functions running. (3) Cooperator Insight
for a boss assist: not located; it is not in BlockClear2*/MissionSuccessed as far as read - check
in a live test that it still arrives. (4) After a boss the event sync for that event is turned off
(`2000[02] 0`) - harmless. (5) The prior room-close by the host also served as "end of mission"
bookkeeping (WSOM+0xf0 type record) - nothing else depends on it that I found.

---------------------------------------------------------------------------------------

## 2. Host dies -> session over for everyone

Registration: `OnCharacterDead(ctx, 99999, 10000 /*local player*/, "OnEvent_4000")` in g_Initialize.

```
Host:
 OnEvent_4000 BN 0x17813d0 (local death; returns if flag 4000 set or msg+8)
   flag 4000 := 1; SprjEventMan+0x10->+0x8 = 4
   host (type 0/8), role==3 (Host) and >=2 presentation members:
     GameDataMan+0x88++ ; schedule SoloPlayDeath ;
     LuaCallStart(4000, 1)  -> broadcast "HostDead"      call BN 0x17816d7  e8 84 d2 fa ff
 SoloPlayDeath BN 0x1781b60: LuaCallStartPlus(4090, chrType) (peers' OnDeadEvent_HostDead VFX),
   flag 4047, -> SoloPlayDeath_TextWait -> _1 -> SoloPlayDeath_2 (2.5 s)
 SoloPlayDeath_2 BN 0x1781de0: clear chr/obj snapshots, SetDefaultMapUid(999-x),
   SessionWorldTransition (call BN 0x1781ede) -> reload at lamp + BeginLeaveSession
   (unless GameStateMan+0x1592); flag 4000 := 0; blood echo mark.
Guests:
 HostDead BN 0x1781870 (key 12/4000/1, i.e. the broadcast above)
   role != 3; white: flag 4047, ctx+0x8d=1, MissionFailed, schedule HostDead_1 (5 s)
   + EventMenuBrake; invader (type 2 / vf558==18): PK-success texts instead.
 HostDead_1 BN 0x1781a60: EventState=2, +0x1520=1, SetDefaultMapUid(-1),
   SessionWorldTransition (call BN 0x1781a93) -> home.
 Also: when the host's member drops from the room, guests run the member-leave handler (sec. 4c).
```

Intervention options:
- Keep guests in the room: NOP `LuaCallStart(4000,1)` at BN **`0x17816d7`** (`e8 84 d2 fa ff` ->
  `0f 1f 44 00 00`, eax unused after - the code reloads `al` itself), or hook2 `HostDead` BN
  `0x1781870` (n=16, clean) and skip. Then the host's own SessionWorldTransition still reloads the
  host at its lamp; that only keeps the room if the map-load/room-survives patches (other agent;
  +0x1592 gate at BN `0x17cdea2`, `80 b8 92 15 00 00 00` / jne BN `0x17cdeae` `75 6c`) are active.
  Guests then need to be moved after the host (lamp_warp to the host's lamp once the host's
  on_world_load fires) - or convert to a clean re-summon (let HostDead run, then auto-re-summon).
- Risks: guests standing in an unloaded host world (desync, the transition-snapshot phase), host
  death penalty/blood echoes unaffected, `SprjEventMan+0x10->+0x8 = 4` "dead" state.

---------------------------------------------------------------------------------------

## 3. Guest (cooperator) dies -> that guest goes home

```
OnEvent_4000 BN 0x17813d0 on the guest (type 1/2/12): schedule PartyGhostDeath (0 s)
 PartyGhostDeath BN 0x1781fd0: flag 4047; CustomLuaCallStart(4063 OnGameLeave, own no);
   EventState=2; OnTextEffectEnd -> PartyGhostDeath_wait ; flag 4000
 PartyGhostDeath_wait BN 0x1782070: LuaCallStartPlus(4090, chrType) (peers: OnDeadEvent_WhiteDead
   BN 0x178cb40 just plays the effect); schedule PartyGhostDeath_1 (2 s)
 PartyGhostDeath_1 BN 0x1782130: LuaCallStartPlus(4042 DeadInfoMsg_*, n) by summon type
   (SprjEventMan+0x60->+0x40: -1 white, -2 black, ...); if !script_globals+0x0c:
   MissionDeadFailed (BN 0x178235e) and OnRequestMenuEnd -> PartyGhostDeath_2
 PartyGhostDeath_2 BN 0x17824c0: clear snapshots; if !script_globals+0x0c && !+0x14 and invader:
   blood mark; GameDataMan+0x70=1; GameStateMan+0x1520=1; SetDefaultMapUid(999-x);
   **SessionWorldTransition** (call BN 0x1782663, `e8 c8 b7 04 00`) -> leave + reload own world
```

Intervention options (in increasing ambition):
1. **Clean re-summon (lowest risk):** let it run; on the host, auto-place/accept the guest again
   (bbhost `src/engine/summon_invite.cpp` path). Nothing to patch.
2. **Stay as a "dead spectator" then respawn with the host:** hook2 `PartyGhostDeath_1`
   BN `0x1782130` (n=14, clean) and skip it (no failure menu, no PartyGhostDeath_2), or NOP the
   SWT call at BN `0x1782663`. The guest stays dead in the host's world with flags 4047/4000 set and
   EventState=2 - the plugin must then revive it (clear flags 4000/4047, reset EventState
   `*(0x593b0d8)`, restore HP / leave the death state) - no ready-made native revive for phantoms:
   `OnSpEffectRevive`'s `0x1d00710` sets ChrType 0 (host type), not usable. A revive-by-reload needs
   SessionWorldTransition aimed at the **host's** map with the room kept (+0x1592 / phase-3 path) -
   the other agent's map-load work.
3. Also NOP the 4063 broadcast in PartyGhostDeath (`CustomLuaCallStart` at BN `0x178201b`) if
   the guest stays; otherwise peers mark its slot "left" (+0xa9).

---------------------------------------------------------------------------------------

## 4. Host / guest leaves deliberately (lamp, Hunter's Dream, items, quit, kick)

### 4a. Lamp / Hunter's Dream / warps (host or guest)
All of these reach **SessionWorldTransition BN 0x17cde30** (callers verified):
`WarpNextStage` BN 0x172e010, `WarpNextStage_Bonfire` BN 0x172e050 (lamp travel),
`WarpSelfBloodMark` BN 0x172e0c0, `WarpNextStageKick` BN 0x1732b80, `Lua_Warp_1` BN 0x178c570,
`Failed_BossAreaMission_LeaveMap` BN 0x178b4e0, `0x17cdf30`, the stateInfo-16 handler (4b), and the
death/boss/kick tails above. Inside, BeginLeave is skipped only when GameStateMan+0x1592 != 0.
Host leaving the room -> guests' member-leave handler (4c) -> guests go home.
Also on any completed map load (`WORLD_LOAD_COORDINATOR_UPDATE` BN 0x1d3ac10): if the new map id
differs from PlayerIns+0x3f8 and role is Host(3)/TryingToHost(1) -> BeginLeave at BN **0x1d3c657**
(`e8 44 41 59 00`); guest side in `WORLD_LOAD_STEP_REBUILD_LOCAL_PLAYER` BN 0x1d39470: role 6 with
<=1 route and !WSOM+0xc5 -> BeginLeave at BN 0x1d39876. (Map-load leaves = other agent's patch set.)

Chokepoint for a plugin: hook2 `SessionWorldTransition` BN `0x17cde30` (n=14, clean;
`55 48 89 e5 41 57 41 56 41 55 41 54 53 50`) - every warp-home passes here; e.g. set
GameStateMan+0x1592 or redirect, or skip for guests while a seamless session is active.

### 4b. Items (SpEffect-driven, PlayerIns SpEffect sweep BN 0x1cfef50)

Goods -> SpEffect -> script (names from item.msgbnd FMG):

| item | goods | SpEffect | trigger in sweep | script / native |
|---|---|---|---|---|
| Silencing Blank | 111 | 7 `requestLeaveSession` (bit 0x400000 of the u64 at +0x15f) | BN 0x1cff690 | role==Client(6) or session_state+0x98 != -1: `DispatchNamed("OnLeaveMagic")` (BN 0x1cff754); else (host) `DismissPresentationMember(state, PlayerIns+0x55c, kick=1)` BN 0x19be2a0 (call BN 0x1cff77a) |
| Hunter's Mark | 100 | 2100 stateInfo 16 | BN 0x1cff9a7 | `0x1717550(ctx, 1)`: SetDefaultMapUid(-1) -> **SessionWorldTransition** |
| Bold Hunter's Mark | 1400 | 2101 stateInfo 16 | same | same |
| (none in goods) | - | 6 `requestKickSession`, 15 `requestNpcInveda`, 1902 st.18 -> "OnBeBlackKickOut", 1904 st.23 -> "OnReviveMagic", 3965 st.17 -> "OnSpEffectRevive", 3960-3964 st.161-164/16 -> `0x1717550(n)` | | |

`OnLeaveMagic` BN 0x17881e0: returns if flag 4047; CustomLuaCallStart(4063); -> `OnLeaveMenu_Yes`
BN 0x1789fb0 -> LuaCallStartPlus(...), **BeginLeave BN 0x178a11e**, flag, schedule `OnLeave_Limit`.
`OnReviveMagic` BN 0x1789d20 (guest): LuaCallStart(4055), 4063, **BeginLeave BN 0x1789ddd**,
schedule OnReviveMagic_1 -> SessionWorldTransition.
`OnLeave_Limit` BN 0x178a4a0: SetDefaultMapUid + **SessionWorldTransition** (call BN 0x178a51c).

Host kick paths: `DismissPresentationMember` BN 0x19be2a0 (state, chrHandle, mode): net member,
mode 1 -> `WSOM_KickPeer` BN 0x1b8dbe0 (4063 + LuaCallStartPlus(4044,1) "OnKickOut" + matching kick
of PlayerIns+0x3d8); mode 0 -> `WSOM_ThxKickOut` BN 0x1b8dd50 (4063 + (4044,2) "OnThxKickOut" +
packet 0x27 THANKS_KICKOUT); AI member -> NPC return. Mode 0 is used by EMEVD 2003[54] "Send NPC
Summon Home" (BN 0x1bc1f62; only m24_01 event 12414480 on an NPC). The kicked guest gets
`OnBeKickOut` (WSOM event handler, BeginLeave BN 0x1b8c118) or packet 0x27 -> `OnBeThxKickOut`
+ BeginLeave BN 0x1b8a7ee (only when WSOM phase != 3).

Interventions: hook2 `OnLeaveMenu_Yes` (n=14) / `OnReviveMagic` (n=14) / `DismissPresentationMember`
(n=20) to veto or redirect; or hook2 `DispatchNamed` BN `0x1739870` (n=20, rsi = callback name)
as one filter for every engine->script named event (OnLeaveMagic, OnReviveMagic, OnBeKickOut,
OnBeThxKickOut, OnIrregularLeaveSession, OnRoomTimeOut, OnRoomDisappeared, OnLanCutError...).
For the hunter's marks: hook2 SessionWorldTransition (4a). Silencing Blank is the intended
"end co-op" item - probably keep it.

### 4c. Guests when the host (or anyone) drops from the room
`WorldTransition_RemovePresentationMember` BN 0x19bc080 -> member-leave handler BN **0x1716640**
(`(luaEventMan, leavingPlayerIns)`, prologue clean n=14): broadcasts (4043, 4|5, playerNo); then
unless flag 4047 or the leaver's GameData slot `+0xa9` ("left normally" via 4063): if the leaver's
ChrType is a non-phantom (table 0x593d750[type].+0x15 == 0, i.e. the host) -> schedule
**OnLeave_Limit** after 5 s (call BN `0x171683d`, `e8 7e 49 01 00`), flag 4047, ctx+0x8d=1,
EventState=2 -> guest goes home. Also sets 0x593e89e=1 for role 6.

### 4d. Quit to title
ReturnTitle chain (`RegistReturnTitle`/`OnReturnTitle` BN 0x178b990..0x178baf0) tears the whole
network down; peers see 4c. Nothing to keep alive there.

---------------------------------------------------------------------------------------

## 5. Timers, health checks and "leave on area change"

All in `WorldSessionObjectMan` update BN **0x1b89b60** unless noted:

| check | site (BN) | condition | action |
|---|---|---|---|
| receive back-pressure | jbe BN `0x1b89c5d` `76 78` | SprjSessionManager +0x2e0 (accumulated rejected-bytes time) > +0x2f0 threshold | OnIrregularLeaveSession + BeginLeave BN 0x1b89cb4 |
| host alone room timer | jbe BN `0x1b89f41` `76 79` | role 3, <=1 route, WSOM+0xb8 timer runs out and not (SprjEventMan+0x60->+0x30->+0x1c8) | "OnRoomTimeOut" + BeginLeave BN 0x1b89f99 (host only, no guests present) |
| matching session lost | BN 0x1b8a1ea/0x1b8a1f4 | `MatchingSessionHandle::IsValid` false | OnIrregularLeaveSession + BeginLeave BN 0x1b8a3a8 |
| **join-sync 60 s timeout** | ja BN `0x1b8a4ef` `77 5d` | WSOM state (+0x84) in 1..3 and WSOM+0x108 += dt >= FD4Time at BN 0x59640d8 (=**60.0 s**, written by static init BN 0x1b90243 `c7 05 93 3e dd 03 00 00 70 42`) | BeginLeave BN 0x1b8a51f (no script event) |
| room disappeared | BN 0x1b8a6be | host gone (+0xfc/+0xbc), phase != 3 | OnRoomDisappeared (unless +0x1592) + BeginLeave |
| thanks-kickout packet 0x27 | BN 0x1b8a7ee | phase != 3 | OnBeThxKickOut + BeginLeave |
| send backlog | `cmp $0x3e9` BN `0x1b897ad` `3d e9 03 00 00` (in WSOM_SendPacket BN 0x1b894e0) | > 1000 queued deferred sends | OnIrregularLeaveSession + BeginLeave BN 0x1b8980d |
| network error code 3 | BN 0x1b8f9a0 | error dispatcher | OnIrregularLeaveSession + BeginLeave BN 0x1b8fa3d |
| LAN cut | `CSNetworkFlowStep::STEP_OnlineMode` BN 0x2257210 and BN 0x17160f0 | GameStateMan+0x1590/+0x1591 | OnLanCutError + BeginLeave |
| SOS room handoff | `SPRJ_EVENT_SOS_SELECTION_TICK...` BN 0x1c72360 | counter 0 and <=1 route | BeginLeave BN 0x1c73df9 |
| WSOM transition step | BN 0x1d470f0 | WSOM+0x84 != 3 | BeginLeave BN 0x1d471b1 |
| post-leave wait | STEP_LeaveSessionWait | 10 s | finish leave |
| OnIrregularLeaveSession | BN 0x1784050 -> 0.1 s -> _1 BN 0x17840e0 | | message box, then OnLeave_Limit (go home) |

Area change: there is no per-area timer; the host leaves when a **map load** completes with a
different map id (BN 0x1d3c537 compare vs PlayerIns+0x3f8, BeginLeave BN 0x1d3c657), and every
warp/lamp goes through SessionWorldTransition. Walking between blocks/areas without a load does
not end the session. For a seamless mod the **60 s join-sync timeout** matters if guests reload
maps in-session: widen it (write the float at BN 0x59640e0 after init, e.g. 600.0) or patch the
`ja` at BN 0x1b8a4ef (`77 5d` -> `eb 5d` disables it entirely - risky: a stuck join never times out).

---------------------------------------------------------------------------------------

## 6. All 42 BeginLeaveSession call sites (BN, containing function)

```
0x171616b jmp  LAN-cut handler 0x17160f0            0x1732fb5 Lua LeaveSession
0x17809b7 OnMatchingCheck                            0x1780a65 OnMatchingError
0x1784ed3 BlockClear2 (invader)                      0x17858f5 BlockClear2_3Leave
0x1785a95 SoloBlockClear                             0x17885b5 OnBeBlackKickOut
0x178874a..0x17893aa OnBeJoinStart_White/Black/ForceJoin/Dragonewt/InvadeBounty/Coliseum/
          Detect/WhiteRescue/BlackRescue/ForceSummon (leave the old room before joining)
0x1789536 SummonReloadStart                          0x1789724 ForceSummonReloadStart
0x1789ddd OnReviveMagic                              0x178a11e OnLeaveMenu_Yes
0x178b410 Failed_BossAreaMission                     0x17cbd0f WarpRestart/WarpRestartNoGrey helper 0x17cbad0
0x17cddf5 vtable-only wrapper 0x17cdda0              0x17cdef6 SessionWorldTransition
0x19becda vtable-only wrapper 0x19bec90              0x1b891cd vtable-only wrapper 0x1b89190
0x1b8980d WSOM_SendPacket backlog                    0x1b89cb4/0x1b89f99/0x1b8a3a8/0x1b8a51f/0x1b8a6be/0x1b8a7ee WSOM update
0x1b8c118 WSOM event handler (OnBeKickOut)           0x1b8f68d vtable-only wrapper 0x1b8f650
0x1b8fa3d WSOM net-error code 3                      0x1c73df9 SOS selection tick
0x1d39876 world-load rebuild player (guest)          0x1d3c657 world-load coordinator (host map change)
0x1d471b1 transition step 0x1d470f0                  0x2257528 CSNetworkFlowStep::STEP_OnlineMode
```

A blanket hook2 on BeginLeaveSession BN `0x22d07a0` (n=17, clean, returns bool) is possible but
**not advisable**: most callers then still run SessionWorldTransition into the player's *own*
world (the guest would load its own world while still in the host's room). Veto at the reason
level instead.

---------------------------------------------------------------------------------------

## 7. hook2-able prologues (14+ bytes, no rip-relative)

```
BlockClear2 0x17849a0 n=17   BlockClear2_1 0x1785470 n=14   BlockClear2_2 0x1785760 n=14
OnEvent_4000 0x17813d0 n=17  HostDead 0x1781870 n=16        PartyGhostDeath 0x1781fd0 n=18
PartyGhostDeath_1 0x1782130 n=14   OnLeaveMenu_Yes 0x1789fb0 n=14   OnReviveMagic 0x1789d20 n=14
member-leave handler 0x1716640 n=14   SessionWorldTransition 0x17cde30 n=14
BeginLeaveSession 0x22d07a0 n=17   OnIrregularLeaveSession_1 0x17840e0 n=16   OnBeKickOut 0x1787760 n=20
DismissPresentationMember 0x19be2a0 n=20   DispatchNamed 0x1739870 n=20
NOT hookable (rip-relative in first 14 bytes): BlockClear2_3, BlockClear2_3Leave, SoloBlockClear,
HostDead_1, SoloPlayDeath_2, PartyGhostDeath_2, OnLeave_Limit, OnLeaveMagic, Lua LeaveSession,
OnIrregularLeaveSession, OnBeThxKickOut  -> patch their call sites (tables above).
```

---------------------------------------------------------------------------------------

## 8. Prior art mapping (Restezzz/Seamless-DarkSouls2, docs/DS2_INTERNALS.md)

| DS2 | what it does there | Bloodborne equivalent |
|---|---|---|
| `0x191C87`/`0x191D17` CALL->NOP (`PatchPhantomDismissalLoops`) | boss kill doesn't send phantoms home | B1+B2 (+B3..B5) in BlockClear2_2/_3 |
| EventResultManager `0x192330` result codes (1 you died, 4 host died, 0x12 boss) | queue of result sequences | compiled global-event callbacks keyed by script ids (4000 death, 4050 boss, 4078 miniboss, 4043/4044/4046/4063 leave/kick) |
| EventPhantomReturn `0x18D660` / `0x190950` -> `0x2C9220(mp, reason)` | phantom warp home | SessionWorldTransition BN 0x17cde30 (+ LeaveSession) |
| `EventConditionNet_IsHost` `0x46FDC0` -> false | scripts think solo | Lua `IsHost` BN 0x172ace0 / EMEVD 3[6], 1003[5/6/105] (multiplayer state) - not needed for session end in BB |
| NetSummonAcceptMultiplayCtrl reasons 0..5 | host-side release | DismissPresentationMember BN 0x19be2a0 (kick / thanks-kick) |
| 'J' packet on white door (area exit sends everyone home) | | no door packet in BB; host map load -> BeginLeave BN 0x1d3c657 |
| `EventResult+0xCE/+0xCF` latches ("don't process again") | | `script_globals+0x0c/+0x0d`, `ctx+0x8d`, flags 4000/4047 |

The DS2 lesson that carries over: the "you will go home" latches must be cleared too, or the next
death/interaction silently does nothing (DS2's `+0xCE`; here 4000/4047, +0x0c, +0x8d) - hence B3-B5.

---------------------------------------------------------------------------------------

## 9. Open questions / to verify live

1. `msg+0x08` early-out in BlockClear2/MediumBossDestroy/OnEvent_4000 read as "received from
   network" - verify with np_test that the white's own 2003[12] (not the host's 0x0e broadcast)
   drives its chain (if not, patches must be on the host only... they are needed on both anyway).
2. Where cooperator Insight for a boss assist is granted (not seen in BlockClear2*, MissionSuccessed).
3. `SprjEventMan+0x60->+0x40` semantics (positive = some arena/mission mode; negative = summon
   type in PartyGhostDeath_1) - BlockClear2 is skipped entirely when > 0.
4. PlayerIns+0x55c target used by the host's Silencing Blank (DismissPresentationMember kicks that
   one handle) - looks like "the phantom hit", unverified.
5. After the boss with B1-B5, flag 4092 stays set on host and white (vanilla host also keeps it);
   find its reader before relying on it.
6. Guest death revive in place: no native phantom-revive found; needs either re-summon or a
   map-reload into the host world with the room kept (other agent's +0x1592 / phase-3 work).
7. The 60 s join-sync timeout (WSOM states 1..3) will fire if a seamless guest's in-session map
   reload takes long; decide whether to widen it.
