# Lamps, travel and the Hunter's Dream in a session (1.09 US, BN addresses)

Static RE only (nothing run). File offset = BN - 0x3FC000. Every byte string below was read
from `games/eboot-109-decrypted.elf`, and the new code was assembled with llvm-mingw clang.

## 0. Summary

* **The lamp prompt is not decided by native action code. It is a talk ESD.** Each lamp
  is a talk script (`script/talk/mXX_..talkesdbnd`, e.g. `t241000..t241003` = the 4 lamps
  of m24_01). Its main machine picks a branch with **talk function f52 = "is multiplayer"**:
  * the non-multi branch (`地上子ワープ椅子_非マルチ中`) offers *light lamp* (ActionButtonParam 6100)
    and *travel* (6101);
  * the multi branch (`..._マルチ中`) offers only *light lamp*. A lit lamp has no action, so there is no prompt.

  The Dream headstones (`t210000..`) and the Doll/NPCs branch on the same f52, and the headstones' multi branch ends at once.
* The native f52 is case 52 of the talk-ESD function evaluator **BN 0x243ad60** (63-case switch,
  table at 0x243f3b4): `(GameState(0x5956678)+0x16f8)->+0x14 > 1 || SprjSessionManager member
  count > 0`. This is the same predicate as the action-updater byte +0x49 (0x16f8315). That byte only picks the lamp's
  SFX variant (90000 vs 90001+level) and the FrpgNetMan bonfire-DB status (0x186e250, item
  +0x48 "cached_valid"). It **is not the prompt**, which is why shadp2p's +0x49 clear did nothing.
  shadp2p's "second presentation gate" is the ESD branch.
* Real lamp/headstone travel never calls WarpNextStage_Bonfire 0x172e050. It goes through
  EMEVD `2003[49]` "Warp Player to Respawn Point", which calls **BN 0x17cdf30(edi = WarpParam id)** (call site 0x1bc1cf0).
  0x172e050 and 0x172e010 are only reached from Lua bytecode, and only `api->lamp_warp` uses 0x172e050. The current
  follow hook therefore never fires for a real lamp.

## 1. Patch: host-only "not multiplayer" for talk ESDs (lamp + headstones + NPCs)

Site **0x243c546** (35 bytes; reached from case 52 at 0x243c505 and the jne at 0x243c520 only). Here `rax` = SprjSessionManager
and `rbx` = slots; the jmp at 0x243c569 (`e9 61 29 00 00` -> 0x243eecf, result in eax) is kept.

before (context): `49 8b 06` (0x243c543)
expect: `48 8b 88 00 01 00 00 48 2b 88 f8 00 00 00 48 c1 e9 05 83 7b 14 01 0f 9f c0 85 c9 0f 9f c1 08 c1 0f b6 c1`
bytes:  `48 8b 88 00 01 00 00 48 3b 88 f8 00 00 00 0f 95 c1 83 b8 24 01 00 00 03 0f 95 c0 20 c8 0f b6 c0 90 90 90`

```
mov rcx,[rax+0x100]; cmp rcx,[rax+0xf8]; setne cl      ; members present
cmp dword [rax+0x124],3; setne al; and al,cl; movzx eax,al  ; ...and not the host
```
* The host (role 3) gets 0, which means the solo branches: lamp travel, headstones, the Doll and NPC talk.
* A guest (6) and anyone else get vanilla, minus the `slots>1` term, which only matters before the member list fills.
* Guests keep no travel prompt. Vanilla also lets a guest *light* an unlit lamp. Event 7200's and 12107000's
  instruction 0 is `1003[06] End if Client`, so a guest's flag could never warp it anyway.
* Side effect: the host can talk to NPCs during co-op. Gate the patch behind an option (`host_lamps`).

Optional, cosmetic (lamp SFX and bonfire DB), same idea at **0x16f8303** (36 bytes; `rax` reloaded at 0x16f8327):

before `49 8b 06`
expect `48 8b 88 00 01 00 00 48 2b 88 f8 00 00 00 48 c1 e9 05 83 7b 14 01 0f 9f c0 85 c9 0f 9f c1 08 c1 41 88 4d 49`
bytes `48 8b 88 00 01 00 00 48 3b 88 f8 00 00 00 0f 95 c1 83 b8 24 01 00 00 03 0f 95 c0 20 c1 41 88 4d 49 90 90 90`

Not checked: the body of f56 (CheckActionButtonArea, case 56 at 0x243c668) has no SessionManager/0x16f8 read in its first ~0x300 bytes. The other blockers in the condition (f14/f19/f26/f27/f31) are player-state checks. The cooperator latch ctx+0x8d is already patched (B5).

What the lamp then offers (from t241000 and t210000):
* A world lamp: unlit -> light it. Lit -> "Travel?" dialog (msg 10010713) -> `SetEventFlag(72000100+map*10000+idx)`
  (e.g. 72410100) -> common event 7200 -> anim 101160 -> `2003[49]` Dream arrival 21029xx.
  **World lamps only go to the Dream.**
* In the Dream, a headstone lists the lit lamps of its areas -> `SetEventFlag(72100000+map*10+idx)`
  -> m21 event 12107000 -> anim 101164 -> `2003[49]` lamp 2412950.. (lamp entity+1000).
* Level up and shops are only at the Doll and Messengers in m21. Lamp-to-lamp travel is via the Dream.

## 2. Travel entry points (all end in SessionWorldTransition 0x17cde30)

| path | entry (hook prologue, verified, no RIP-relative) | argument |
|---|---|---|
| lamp -> Dream, headstone -> lamp, Chalice altar (12107200), event 3503 | **0x17cdf30** `55 48 89 e5 41 57 41 56 53 48 83 ec 38 89 fb` (15) | edi = WarpParam id (2102950.., 2412950..). Writes GS+0x0c, +0x1538 (1 = world, 2 = Dream), +0x153c |
| Hunter's Mark / Bold Mark (SpEffect handler 0x1cfef50 -> 0x1717550 esi=1), host/solo death (HostDead_1, SoloPlayDeath_2), BlockClear2_1/3, OnLeave_Limit | **0x1732bc0** SetDefaultMapUid `55 48 89 e5 41 57 41 56 53 48 83 ec 18 41 89 f6` (16) | esi = -1: map from GS+0x1528 (packed last return point), sets +0x0c, +0x10=-1, +0x1524=1 |
| 0x1717550 cases 2..6 (fixed maps) | `55 48 89 e5 41 56 53 89 f3 48 83 7f 08 00` (14) | esi = 1..6 |
| Lua_Warp_1 (warp menu 0x1b7b900 -> "Lua_Warp") | 0x178c570 `55 48 89 e5 53 50 48 89 fb bf 10 27 00 00` (14) | id at [rdi+0x20] (inlined WarpNextStage_Bonfire) |
| WarpNextStage (Lua only) | 0x172e010 `55 48 89 e5 c1 e6 18 c1 e2 10 81 e2 00 00 ff 00` (16) | esi..r9 = area, block, sub, sub2, point |
| WarpNextStage_Bonfire (Lua and api->lamp_warp only) | 0x172e050 (already hooked) | esi = id |

Plugin plan:
* **Host:** hook 0x17cdf30 and send `HostWarp{id}`. Hook 0x1732bc0 (esi==-1, role 3) and send `HostReturn{GS+0x1528 u64}`.
  Keep the 0x17cde30 hook as a fallback that sends GS+0x0c/+0x10.
* **Guest (main thread):** set `g_following`, then `call_guest(0x17cdf30, id)`. The keep-room byte +0x1592 is set by the existing
  0x17cde30 hook. This is better than lamp_warp, because 0x172e050 does not set +0x1538/+0x153c (Dream mode 2).
  For HostReturn: write GS+0x1528, then `call_guest(0x1732bc0, 0, -1)` and `call_guest(0x17cde30)`.

## 3. The Dream (m21) with a kept session

* No Dream-specific session teardown was found. The only m21 (0x15000000) tests are:
  * 0x1d398c5: storage refill of item 0x40000385 on Dream entry (0x18dbb70/0x18dace0);
  * 0x1d3aa18: skips SprjNetworkClientMan+0x130=1.0 for m21;
  * 0x1d44cea: default map.
* The same function's leave at 0x1d39876 fires only when role is 3 or 6 and there is at most 1 member (empty room).
* m21 EMEVD has only the usual "End if Client" lines.
* SOS area 210000 is let in by `anywhere`, except that summon eligibility 0x1d1a750 still honours the
  disable-flag table 0x4b301b0, whose m21 entry is **flag 2105**. The patch at 0x1d1a8c3 keeps `al`. This
  matters only for re-summoning, not for a kept session.
* **Recommendation: a shared Dream** using the existing kept-session flow.
  * The host's lamp (7200) calls 0x17cdf30(21029xx), and the guests follow with the same id. Tasks are kept and rewound to Create as today.
  * In m21 the host (patch 1) levels up, shops and uses headstones; the headstone travel is followed the same way.
  * Guests (f52=1) are visitors: headstones are inert, the Doll and Messengers stay on their multi branches, and there is no level-up. That avoids DS2-style edits to the host's world.
  * Private intermission is a fallback only, because it gives up the room and needs the M2 re-summon.
