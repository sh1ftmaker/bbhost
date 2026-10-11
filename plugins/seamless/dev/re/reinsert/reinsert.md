# Re-creating the other player's character after an in-session load

Static RE of the 1.09 eboot (BN addresses: BN = RVA + 0x400000). Tags: [fact] = read from
disassembly; [inferred] = follows from code but not seen live; [spec] = guess.

## 1. How the remote PlayerIns is made natively

Chain, per peer route R (both sides):

1. **Member join, WSOM event 0x0e** (`DispatchSessionEvent` 0x1b8b5e0, case at 0x1b8b902) [fact]
   - R == local -> ignored. R already in the WSOM member list (+0x68, node objref +0x10, slot +0x30)
     with slot != -1 -> **nothing** (0x1b8c145).
   - else: find/ensure a `CSMultiPlayerInsTask` in CSMultiPlayMan (ptr 0x5940230; primary vector
     +0x10..+0x18) - mode **2** if R matches SprjSessionManager+0xd0 (the guest's host), else mode **1**:
     alloc `(*[0x5940438])->vtbl[0x58](alloc, 0x100, 8)`, ctor `0x224f9f0(task, R, mode)`,
     push `0x2255a70(mpm+8, &task)` (0x1b8c217..0x1b8c311).
   - appends R to the active-peer list (+0x48), adds the member entry + GameData slot, sends packet
     **0x42** (1 byte = GameStateMan+0x16f4) to R (0x1b8c865).
2. **Readiness flags** on the task (+0xed):
   - 0x04 MEMBER_READY + enterleave mode (+0xec): receiving **0x42** (0x1b84cba..0x1b84dd2).
   - 0x08 FIRST_SYNC_READY: receiving **0x0c** (player status, 0x80 B; also slot state |= 4) (0x1b833a1..0x1b8351c).
   - 0x01 CREATE_REQUESTED: per frame in the area payload update 0x1d47310: if WSOM+0x84 == 4,
     `0x1b8b4d0` pops one peer from the active list (+0x48) whose GameData slot state byte
     (GameDataMan(0x593b130)+0x18)[slot] == 0x1f, and ORs 1 into its task (0x1d474e5).
   - The 0x0b/0x0c/... bootstrap is sent **only as the reply to a 0x0a "sync ready" packet** while the
     replier is in state 4 (0x1b83eda..; host also sends world state 0x19bd0e0). 0x0a is sent by
     `SetSyncReadyAndBroadcast` 0x1b8d9c0 to every peer still in the active list (+0x48), and by
     `0x1b81a70(_, route)`.
3. **Task steps** (dispatcher 0x2252fe0 runs step[+0x54] each tick and commits +0x50 = +0x54):
   Init 0x224fcd0 -> NotifyWait 0x224fcf0 (needs 0x04) -> StartMultiPlayWait 0x224fd50 ->
   FirstSyncWait 0x224ff70 (mode 1/2 need 0x08) -> **Create 0x2250090** (needs 0x01; finds member
   slot 0..3, `WorldChrMan::CreateRemotePlayer` 0x1cdb500(wcm+0x30, slot.chrType(+0xa4), slot+0x10, R),
   `AddPresentationMember` 0x19bc590) -> CreateWait 0x22505e0 (clears 0x01) -> **Update 0x2250830**
   (only watches 0x02 exit). Tasks are ticked from 0x1b89b60 (TICK_AND_ERASE 0x22556f0) inside 0x1d47310.

## 2. Why it does not happen again after our load

- Map unload runs the area-payload destructor **0x1d470f0**: if WSOM+0x84 != 3 it calls
  BeginLeaveSession (our keep_on_load patch at 0x1d471b1 neutralises that) and then
  **`CSMultiPlayMan::DestroyAllTasks` 0x2254830 at 0x1d47207** - every task is deleted (no exit
  steps). With state 3 (native summoned-guest load) it jumps past both (0x1d47178). [fact]
- After the load nothing re-creates: the member entry still exists with a slot, so event 0x0e is a
  no-op even if re-delivered; the active list (+0x48) was emptied when the peer was first created,
  so `SetSyncReadyAndBroadcast` sends nothing and 0x1d47310 never raises CREATE_REQUESTED. [fact]
- If a task survives (phase 3 set), it sits in Update, which never revisits Create. [fact]
- `SummonedMapReload` (0x1736b90 -> 0x171e5c0, sets +0x84 = 3) is the native *first-arrival* load:
  it works only because the tasks are still pre-Create and the peers still pending in +0x48. It has
  no reinsert. [inferred]

## 3. Recommendation (ranked)

**A (best, ~70%): keep the tasks through the load, then rewind them to Create.**
1. `hook2(0x2254830, 55 48 89 e5 41 57 41 56 41 55 41 54 53 50, n=14)`: if `caller_bn == 0x1d4720c`
   and session ready (role 3/6, WSOM+0x84 == 4, >= 1 peer route) -> return 1 (skip). Log tasks.
2. After the load, on_frame (main thread), once WorldChrMan+0x60 exists, WSOM+0x84 == 4 and the
   peer has signalled "world ready" on the channel (new kind; send to every route, bypassing the
   `player_of` gate) or 10 s passed: for each route R with `player_of(R) == null`, find the task
   with `0x10a1c70(task+0xc8, R)`, mode 1 or 2, step +0x50 == 6:
   `task[+0xed] |= 0x0d; task[+0x54] = 4; task[+0x50] = 4;`
   Next ticks: Create -> CreateWait -> Update, with a new PlayerIns.
3. Optional (second run): `call_guest(0x1b81a70, 0, R)` (sends 0x0a) to have R resend its
   bootstrap so the GameData slot is fresh. The host's reply includes world state: try guest->host only.

**A' (no hook): build fresh tasks** for routes without one, exactly as event 0x0e does (alloc via
`[0x5940438]` vtbl+0x58(…,0x100,8); ctor `0x224f9f0(task, R, mode)`; `0x2255a70(mpm+8, &taskptr)`),
then `+0xec = byte GameStateMan+0x16f4` [spec: same on both sides], `+0xed |= 0x0d`. It walks Init -> Update
by itself in about 6 ticks. The guest's own mode-0 task is lost (leave presentation) [inferred].

Low: re-dispatch event 0x0e (no-op while the member entry exists; useful only if +0x70 == 0);
`SetSyncReadyAndBroadcast` (empty +0x48 -> sends nothing); `SummonedMapReload` on the guest
(its tasks stay at Update and the host's still get destroyed).

## 4. What to log next run
- 0x2254830 hook: caller, per task mode/step(+0x50)/flags(+0xed)/+0xec, skipped or not; WSOM+0x84 at unload.
- After load, each second: WSOM +0x84, +0x50 (active), +0x70 (members) with each entry's slot (+0x30),
  (GameDataMan+0x18)[slot], slot chrType (GameDataMan+0x10 + slot*0x6a0 + 0xa4); CSMultiPlayMan task count
  and step/flags/mode; `player_of(R)`.
- After the rewind: step per tick until 6; the new PlayerIns, its +0x78, and its position over 5 s
  (proves NetWorldChrSync moves it); pings resume.
