# Death handling for seamless co-op (Bloodborne 1.09, CUSA00900)

This is static RE only: nothing was launched. All addresses are BN (= RVA + 0x400000). The file offset is BN − 0x3FC000.
Every byte string below was read back from `games/eboot-109-decrypted.elf` (section 7).
In disassembly comments, `# 0x55xxxxx` is an RVA. Add 0x400000 to get the BN pointer slot.

Builds on `re/session-end/session-end.md` (sections 2 and 3), `re/reinsert/reinsert.md`, and the proven M1 kept-session load:
- `+0x1592` set for the SWT call;
- DestroyAllTasks skipped when it is called from `0x1d4720c`;
- tasks rewound to Create 10 s later.

## 0. Summary

| Event | What the plugin does | Patches / hooks |
|---|---|---|
| **Host dies** | Suppress the HostDead broadcast. The host's own `SoloPlayDeath_2` reloads it at its respawn lamp, with the room kept. In that SWT the host sends its respawn row (GameStateMan+0x1528). Guests are **warped, not killed** (ERSC-style). They follow with `lamp_warp(row)` plus `GameDataMan+0x70 = 1`, so they arrive at full HP. | P1 NOP `0x17816d7`; hook2 guard on `HostDead` `0x1781870` (guest); SWT hook: caller `0x1781ee3` |
| **Guest dies** | Option (a). Keep the vanilla death chain: "YOU DIED", the death broadcasts, and the native revive flag. Redirect its final SessionWorldTransition (caller `0x1782668`) from "home" to the host's respawn lamp in the host's world, with the room kept. The load then runs the game's own respawn routine (`0x1cfd460`: HP = max). There is no echo loss and no bloodstain: whites never drop one. | P2 NOP 4063 `0x178201b`; optional P3 NOP `0x178235e`; SWT hook: caller `0x1782668` |
| **After any death load** | Clear latches 4000 and 4047, and the script-dead state. Tell the peer to rewind its task for us. | `event_flag_set`; new channel kinds |

The key facts that make this work:
- **GameStateMan+0x1528 (u32) is the ReturnPointParam row id.** In all 85 lamp rows `returnPointEntityId == row id`; I checked this against the CUSA00900 gameparam.
- `+0x1528` is exactly what the host's death load uses. `SetDefaultMapUid` 0x1732bc0 does this: when `+0x1520 == 0` and `+0x1528 != -1`, it sets map = f(+0x1528), `+0x10 = -1` and `+0x1524 = 1`.
- So `lamp_warp(*(u32*)(gsm+0x1528))` is "the host's respawn lamp".

## 1. Host death

Vanilla chain:
1. `OnEvent_4000` 0x17813d0 runs. When the local type is 0 or 8, `role == 3` and members ≥ 2, it does three things:
   - increments `GameDataMan+0x88` (deaths);
   - schedules `SoloPlayDeath`;
   - calls `LuaCallStart(4000, 1)` at **0x17816d7**. On the guests this is HostDead → HostDead_1 → SWT (caller 0x1781a98) → home.
2. `SoloPlayDeath` 0x1781b60:
   - broadcasts 4090 (the guests only show a text, through OnDeadEvent_HostDead 0x178ca40);
   - sets flag 4047;
   - → TextWait → `_1` → `_2`.
3. `SoloPlayDeath_2` 0x1781de0:
   - sets `EventState = 1`, `SetDefaultMapUid(999-x)`, **SWT (call 0x1781ede, ret 0x1781ee3)**;
   - clears flag 4000;
   - sets `GameDataMan+0x70 = 1` (revive on the next load);
   - drops the bloodstain (0x17d2910).

What the plugin does:
1. **P1:** at `0x17816d7` replace `e8 84 d2 fa ff` with `0f 1f 44 00 00`. The byte check before it is `4c 89 f7`. The result is unused, since the next instruction is `mov al,1`. Guests never get HostDead.
   - Guard: hook2 `HostDead` 0x1781870, n=16, `55 48 89 e5 41 57 41 56 53 50 49 89 f6 49 89 ff`. Skip it (return 1) while seamless role 6. This covers a stale broadcast. Arguments: rdi = script_globals, rsi = ctx, rdx = msg. The return value is ignored.
2. In `on_world_transition` (SWT 0x17cde30, already hooked), when `caller_bn == 0x1781ee3` and `role == 3`:
   - `row = *(i32*)(gsm+0x1528)`;
   - force `gsm+0x1592 = 1` for this call, using the existing restore-next-frame code. The host should already be 1; the trace must show no BeginLeave;
   - `send_all(HostRespawn, {row, anim = *(i32*)(gsm+0x152c)})`.
   - If `row == -1` (the host never rested), send nothing; the guests fall back to the M2 re-summon.
3. Guest, on HostRespawn:
   - `*(u8*)(*(0x593b130)+0x70) = 1` (revive: full HP on the rebuild, see section 3.2);
   - `lamp_warp(row)` and `g_following = 300`. This is the existing follow path; the SWT caller is 0x172e0b9.
4. Both sides: the DestroyAllTasks skip and the rewind are already generic (they key on `ret 0x1d4720c` and a session that is ready).

Enemies are reset by the host's reload. That is fine, and the guest loads the same map.

The host keeps the vanilla death penalty: echo bloodstain and the deaths count.

**Risk:**
- **Map-change leave:** `0x1d3c657 e8 44 41 59 00` (the world-load coordinator) leaves when the new play-region id differs from `PlayerIns+0x3f8` and the role is 3.
  - If the trace shows BeginLeave from 0x1d3c65c after a death in another block, patch it to `31 c0 90 90 90` (before: `48 8b 38`). `al = 0` takes the `je 0x1d3c67b` skip.
- **Invaders:** with P1, an invader no longer gets the "PK success" path. Block invasions in seamless (the planned `invaders = off`).

## 2. Guest death: option (a), respawn in the host's world (recommended)

Vanilla chain (a white, type 1):
1. `OnEvent_4000`:
   - sets `SprjEventMan(*0x593b108)+0x10→+8 = 4` and flag 4000;
   - schedules `PartyGhostDeath` 0x1781fd0.
2. `PartyGhostDeath`:
   - sets flag 4047 twice;
   - **`CustomLuaCallStart(4063)` at 0x178201b**: OnGameLeave sets the peers' slot `+0xa9` to "left";
   - sets `EventState = 2`;
   - text effect → `_wait`.
3. `_wait` 0x1782070: broadcasts 4090 (the host shows "died") → `_1` after 2 s.
4. `_1` 0x1782130:
   - broadcasts DeadInfoMsg 4042;
   - unless `globals+0x0c`, calls `MissionDeadFailed` (**call 0x178235e**);
   - after the menu ends → `_2`.
5. `_2` 0x17824c0:
   - clears the snapshots;
   - bloodstain only for types 2 and 12;
   - sets `GameDataMan+0x70 = 1` and `gsm+0x1520 = 1`;
   - `SetDefaultMapUid` (with `+0x1520` set, map = `+0x14f0`, the guest's home);
   - **SWT call 0x1782663 (ret 0x1782668)**;
   - after the SWT: `GameDataMan+0x8c` and a save request. Both are harmless.

Patches:
- **P2:** at `0x178201b` replace `e8 20 cd fa ff` with `0f 1f 44 00 00` (before: `48 89 df`; the next instruction reloads rax). The guest is not marked "left" on the peers.
- **P3 (optional, cosmetic):** at `0x178235e` replace `e8 5d 49 00 00` with NOPs (before: `4c 89 fe`). This drops the summon-type "failed" text. The `_2` scheduling at 0x178236b stays.

Redirect: in `on_world_transition`, when `caller_bn == 0x1782668`, `role == 6`, local `ChrIns+0x78 == 1` and `g_host_rp > 0`, do the following (main thread, still inside the hook):

```
gsm = *(0x5956678)
gsm+0x1520 = 0                                  // not "returning home"
gsm+0x0c   = (row/100000)<<24 | ((row/10000)%10)<<16   // as WarpNextStage_Bonfire 0x172e050
gsm+0x10   = row
*(u64*)(gsm+0x1528) = row | (u64)anim<<32 ; gsm+0x1524 = 1   // = lamp_warp's SetReturnPointEntity+SetUseReturnPoint
keep_room(gsm+0x1592 = 1, restore next frame)
GameDataMan+0x70 stays 1  -> WORLD_LOAD_STEP_REBUILD_LOCAL_PLAYER 0x1d395c6 calls 0x1cfd460(player):
                             HP = max (+0xfc via 0x1cbcd40), stamina/state reset, then clears +0x70
return 0   (run SWT: no BeginLeave because +0x1592)
```

Alternatives to the hand-written stores:
- `call_guest(0x196d110, row)` and `call_guest(0x196d0e0, 1)` set the return point;
- or skip the SWT (return 1) and queue `g_follow_to = row`. That uses the proven lamp_warp path, but it costs one frame in a half-dead state.

`g_host_rp`: the host sends `HostReturnPoint{row, anim}` on a member join, every 5 s when the value changes, and with every travel. Update it from HostLampTravel too.

After the load (on_world_load, or when the reinsert timer fires), while the local HP is above 0:
- `event_flag_set(4000,0)` and `(4047,0)`. Nothing else clears 4047 on a white: only BlockClear2_3 and SoloBlockClear do.
- If `*(*(*(0x593b108)+0x10)+8) != 0`, set it to 0. OnEvent_4000 returns while it is > 0.
- Restore the `*(*0x593b0d8)` dword ("EventState") to the value saved while alive.
- Send `PeerReloaded`. The receiver sets `g_reinsert_wait = 600`: `reinsert()` rewinds only routes with no character, so it is safe either way. A load by the guest alone is **not yet proven**; M1 was a load by both.

Penalties: none for the guest (vanilla whites lose nothing and leave no bloodstain), and the host is untouched. The guest's own respawn point (`+0x1528`, saved) now holds a lamp in the host's world. Save the u64 at `BB_SESSION_JOINED` (role 6) and write it back at LEFT/CLOSED/KICKED. Follow has the same problem today.

Option (b), a spectator until the host rests, needs a revive with no load. No native phantom revive exists: `OnSpEffectRevive` sets ChrType 0. Defer it.

## 3. Edge cases

1. **Host dies in a boss arena:** the same as section 1. The fog and the boss reset with the load. The guest re-enters the fog behind the host.
2. **Guest dies in the arena while the host fights on:** respawns at the host's lamp. The guest may be unable to pass the fog until the fight ends (check live). That is acceptable (ERSC also benches the dead).
3. **Guest dies 0-15 s after the boss dies:**
   - BlockClear2 (white) set 4000/4047/4092, and B3 clears them only in BlockClear2_3, about 15 s after the kill. OnEvent_4000 then returns at once and **no chain runs**, so the guest lies dead.
   - The same applies to a host with banked echoes.
   - Fix: a **watchdog**. If the local `ChrIns hp` (WorldChrMan+0x60 → +0x3b0 → +0x20 → +0xf8) is ≤ 0 for 8 s and no SWT was seen:
     - clear 4000 and 4047;
     - set `GameDataMan+0x70 = 1`;
     - `lamp_warp(guest ? g_host_rp : own +0x1528)` with keep-room.
4. **Both die:**
   - The guest records `g_host_dying` from a new `HostDying` message (hook2 `OnEvent_4000` 0x17813d0, n=17 `55 48 89 e5 41 57 41 56 41 55 41 54 53 48 83 ec 18`, host side, flag 4000 still clear).
   - If the guest's SWT (from 0x1782668) comes while the host is dying: skip it (return 1) and set `g_revive_pending`. The HostRespawn handler then does `+0x70 = 1` and `lamp_warp`.
   - If HostRespawn comes first: lamp_warp with `+0x70 = 1`. A later stale SWT from 0x1782668 while the local HP is above 0 is skipped (return 1).
5. **Host death while the guest is mid-load:** channel messages wait for WSOM state 4. That is fine.

## 4. Test plan (two-instance rig; main session runs it)

Add `BBHOST_SEAMLESS_KILL=host:N|guest:N|both:N` to the plugin. N s after `party: 1 other member, 1 with a character here`, it calls `sp_effect_apply(nullptr, 4014)`.
- SpEffect 4014 (and 4015) has `changeHpRate 100`, `endurance -1`, which is presumably lethal. Verify it; 5626 is the fallback.
- **`player_stat_set("hp",0)` only writes the GameDataMan record (+0x14), not the live ChrIns HP.** It most likely does not kill.
- Set it in the parent shell: `$env:BBHOST_SEAMLESS_KILL='host:20'; pair.ps1 -Bbhost <bbhost-int> -Seamless -Ping -Trace -KeepRunning -Seconds 300 -MinCommitGB 8.5`.

| # | Case | Pass when |
|---|---|---|
| T1 | host:20 | No HostDead on the guest. SWT from 0x1781ee3 (host) and 0x172e0b9 (guest). No BeginLeave. Role 3/6 and 2 members 60 s later. Both characters made again. Guest HP = max. |
| T2 | guest:20 | SWT from 0x1782668. Map = the host's lamp. Guest HP = max. Flags 4000/4047 = 0. No "returned to your world". The host still sees the guest after ≤ 15 s (PeerReloaded rewind). |
| T3 | guest:20 then guest:120 | The second death runs the chain again (latches cleared). |
| T4 | both:20 (guest 1 s later) | One load per side. The guest is revived. The party holds. |
| T5 | Cleric Beast save; kill the guest about 5 s after the boss | The watchdog revives the guest. |
| T6 | Host dies in another block than its lamp | Look for BeginLeave from 0x1d3c65c; apply the section 1 patch if it appears. |

Log on each side:
- the SWT caller;
- `gsm+0x1528`, `+0x1520`, `+0x1592` and `GameDataMan+0x70` before the SWT;
- the 4000/4047 flags, the dead state and EventState after the load;
- the chr HP and max HP.

## 5. Verified bytes

```
0x17816d4  4c 89 f7 | e8 84 d2 fa ff        P1 (LuaCallStart 4000,1)
0x1782018  48 89 df | e8 20 cd fa ff        P2 (CustomLuaCallStart 4063)
0x178235b  4c 89 fe | e8 5d 49 00 00        P3 (MissionDeadFailed)
0x1d3c654  48 8b 38 | e8 44 41 59 00        host map-change BeginLeave (contingent)
0x1781870  55 48 89 e5 41 57 41 56 53 50 49 89 f6 49 89 ff          HostDead n=16
0x17813d0  55 48 89 e5 41 57 41 56 41 55 41 54 53 48 83 ec 18       OnEvent_4000 n=17
0x17cde30  55 48 89 e5 41 57 41 56 41 55 41 54 53 50                SWT n=14 (hooked)
SWT returns: 0x1781ee3 SoloPlayDeath_2, 0x1782668 PartyGhostDeath_2, 0x1781a98 HostDead_1, 0x172e0b9 lamp travel
```
