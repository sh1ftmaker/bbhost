# shadp2p seamless co-op patch sites, ported to bbhost (US CUSA00900 1.09)

Sources: `src/core/bloodborne_re.cpp` (`InstallSeamlessCoopPatches`, `TraceEntry`,
`Apply*` handlers), `documents/bloodborne-seamless-re.md`,
`documents/README-bloodborne-internals.md` in Wozzardman/shadp2p (fetched read-only
via `gh api`; local copies are `sp_*` in this folder). Seamless mode is gated on
`SHADPS4_BLOODBORNE_SEAMLESS_COOP` and is otherwise driven by
`libraries/network/http.cpp` (the host-placement HTTP header), with no other
code patches.

## 0. Address mapping (verified)

* ELF `eboot-109-decrypted.elf`: PT_LOAD#0 has p_vaddr 0 at file offset 0x4000.
  PT_LOAD#1 (data) has p_vaddr 0x50dc000 at file offset 0x50e0000, so the delta
  is again +0x4000.
* bbhost: `Rva` = ELF p_vaddr, and **BN = RVA + 0x400000** (`base.hpp`
  `kImageBase`). File offset = BN - 0x3FC000.
* shadp2p "offsets" are relative to the loaded eboot base, so they are the same
  thing as an RVA.
* **The EU (CUSA03173) and US (CUSA00900) 1.09 code is identical in every
  region checked. The RVA delta is 0.** I checked all 38 of shadp2p's
  byte-signature sites (patch originals, hook prologues and native-call
  prologues). Every one matches byte-for-byte at the same RVA in our eboot,
  including RIP-relative displacements into .data. For example the
  placement setters' `mov rax,[rip+0x3FE9761]` resolves to the same global
  0x5556678. bbhost's own symbols also coincide with shadp2p's addresses:
  0x191a750, 0x14b5e10, 0x1872360, 0x1874710, 0x1ed07a0, 0x13cde30,
  0x132e010/050, 0x178d9a0, 0x5540290, 0x553d6d0, 0x5556678 and 0x553e878.
  **Porting is mechanical: OUR_BN = EU_offset + 0x400000.** rel32
  displacements stay the same because caller and callee shift together. I
  still recomputed each one below.
* Disassembly helper: `dis.sh BN_start BN_stop` (llvm-objdump with
  `--adjust-vma=0x400000`). Jump/call targets print as BN. The `# 0x...`
  comments on RIP-relative operands print the RVA, not the BN.

## 1. Byte patches (all applied by `InstallSeamlessCoopPatches`, all expect-checked)

The `expect` column gives the original bytes at the patch address (the patched
length plus context where useful). bbhost `api->patch(bn, expect, bytes, n)`
only checks n bytes, so pass `expect` = the first n original bytes, or verify
the context first with `api->read`.

| # | Purpose | EU off | **Our BN** | Original bytes (patched part **bold**, + context) | shadp2p replacement | Conf. / verification |
|---|---|---|---|---|---|---|
| 1a | Beckoning Bell (goods 200) inventory availability: area range check. Unsigned `ja` (area > 999999) makes every negative SOS area fail. The fix makes it signed. Inside the availability fn EU 0x0157F200 (**BN 0x197F200**), Beckoning branch at BN 0x197F855 (`cmp ebx,0xC8`). | 0x0157F8C8 | **0x197F8C8** | `81 FB 3F 42 0F 00` **`0F 87 90 00 00 00`** `4C 8D 3D F3 B7 FB 03` | `0F 8F 90 00 00 00` (jg, same target BN 0x197F95E) | High. Disassembled: `cmp ebx,0xF423F; ja 0x197F95E`. The area comes from player+0x278/+0x27C via vtable +0x628. |
| 1b | Beckoning "area flag" result. This is the **bell event-flag bypass**. The region gate `sub_171D7B0(SprjLuaEventMan,&area)` (EU 0x0131D7B0, BN 0x171D7B0; nonzero means refuse, e.g. flag 2800/2410 for cleared-boss areas) flows through `xor al,1 (BN 0x197F913); jmp 0x197F960; xor al,1`. The out-of-range and no-world paths enter at 0x197F95A/95E with eax=0, so blocked=1. | 0x0157F960 | **0x197F960** | `31 C0 EB 02 31 C0` **`34 01`** `89 85 10 FE FF FF` | `31 C0` (xor eax,eax: blocked flag stored at [rbp-0x1F0] is always 0) | High. Disassembled. After this patch 1a only matters for skipping the gate call on negative areas, because every path into 0x197F960 now yields "allowed". |
| 1c | Small Resonant (205) / Sinister (225) availability: force result. Branch at BN 0x197F686 `cmp r12d,0xCD` / `0xE1`. | 0x0157F6D1 | **0x197F6D1** | `E8 7F B0 39 00` **`88 C3`** (mov bl,al after `call 0x1D1A750`) | `B1 01` (mov cl,1 = return value) | High. The call is WORLD_CHR_MAN_IS_SUMMON_AREA_ELIGIBLE (bbhost RVA 0x191a750). |
| 1d | Same branch: skip the remaining predicates (`call 0x1CC96D0` etc.) and jump to the canary-checked epilogue, which returns `cl`. | 0x0157F6D3 | **0x197F6D3** | **`4C 89 F7 E8 F5 9F 34 00`** `84 DB 0F 84 CD` | `E9 E9 09 00 00 90 90 90` = `jmp 0x19800C1` (next 0x197F6D8 + 0x9E9). Same displacement in our build. | High. BN 0x19800C1 is the stack-canary compare, then 0x19800D1 `mov al,cl; add rsp,0x1E8; ...; ret`. Only reached for goods 205/225. |
| 1e | Active-Bell periodic updater (EU fn 0x01506820, **BN 0x1906820**): area range check, unsigned to signed. | 0x015068BB | **0x19068BB** | `81 FB 3F 42 0F 00` **`77 4A`** `4C 8D 3D 04 48 03 04` | `7F 4A` (jg → 0x1906907) | High. Disassembled. |
| 1f | Active-Bell updater: area/event-flag result forced to "allowed". Gives effect 9003 (allowed) instead of 9004 (Bell X). | 0x015068FC | **0x19068FC** | `E8 B4 6E E1 FF` **`88 C3 80 F3 01`** `EB 06 31 DB` | `B3 01 90 90 90` (mov bl,1) | High. Original: `call 0x171D7B0; mov bl,al; xor bl,1`. |
| 1g | Responder search predicate WORLD_CHR_MAN_IS_SUMMON_AREA_ELIGIBLE (EU/RVA 0x0191A750, **BN 0x1D1A750**): final `area < 1000000` reduction. Signed jge-fail instead of sbb carry. | 0x0191A8C3 | **0x1D1A8C3** | `41 81 FE 40 42 0F 00` **`18 C9 20 C1 EB 02`** `31 C9 88 C8` | `7D 04 88 C1 EB 02` (jge 0x1D1A8C9 [xor ecx,ecx = fail]; mov cl,al; jmp 0x1D1A8CB) | High. Disassembled. The fn indexes SUMMON_AREA_DISABLE_FLAG_TABLE (bbhost RVA 0x47301b0) at BN 0x1D1A88E, which confirms the bbhost anchor. |
| 1h | SOS-status producer (EU fn 0x0186FE40, **BN 0x1C6FE40**): area-derived restriction boolean (gate result OR sign bit), which later becomes restriction enum 3. Forced to 0. | 0x018700D3 | **0x1C700D3** | `E8 DD D6 AA FF` **`84 C0 41 BD FF FF FF FF 44 0F 44 EB 41 C1 ED 1F`** `49 8B 84 24 C8 01 00 00` | `45 31 ED` + 13×`90` (xor r13d,r13d) | High. Disassembled: `test al,al; mov r13d,-1; cmove r13d,ebx; shr r13d,31`. |
| 2a | Summon builder / "filter" (EU 0x01874710, **BN 0x1C74710**; bbhost `SOS_SIGN_MAN_FILTER_REQUEST_ENTRY_HELPER`, np_test `kSosFilter`). **Boss-cleared / world-state reject**: `call 0x171D7B0` (region event-flag gate) then `jne reject`. | 0x018749E8 | **0x1C749E8** | `E8 CA 8D AA FF 84 C0` **`0F 85 48 01 00 00`** `85 DB 0F 88` | 6×`90` | High. Reject target BN 0x1C74B36. |
| 2b | Summon builder: negative-area-event reject (`test ebx,ebx; js reject`). Needed for areas like -241109. | 0x018749F0 | **0x1C749F0** | `85 DB` **`0F 88 40 01 00 00`** `4C 8B 45 B8` | 6×`90` | High. |
| 2c | Summon builder: cross-map reject. The world area/10 and request area (+0x18)/10 compare at 0x1C74B77, then `je continue`. | 0x01874B79 | **0x1C74B79** | `01 D1 39 C8` **`74 0D`** `E9 57 03 00 00` | `EB 0D` (jmp 0x1C74B88) | High. |
| 2d | Candidate filter in SosSignMan update (EU 0x014B5E10, **BN 0x18B5E10**; bbhost `SOS_SIGN_MAN_UPDATE_AND_QUEUE_SUMMON_API_JOBS`): manager area (+0xA80)/10 vs candidate area (+0x98)/10, `jne reject`. | 0x014B714A | **0x18B714A** | `01 D6 39 F0` **`0F 85 C0 03 00 00`** `48 89 DF` | 6×`90` | High. Reject target BN 0x18B7510. |
| 3a | **Room teardown on stage transition.** In the dtor EU 0x019470F0 (**BN 0x1D470F0**, vtable RVA 0x5330540) of the per-area event payload built by EU 0x01946CE0 (BN 0x1D46CE0). If `[WorldSessionObjectMan(0x553D6D0)]+0x84 != 3` it calls `SprjSessionManager::BeginLeaveSession` (EU 0x01ED07A0, **BN 0x22D07A0**; shadp2p calls this "CSMultiPlayMan::Stop", reason 0xFF000023 through 0x00C8ED70 → 0x00C98070). | 0x019471B1 | **0x1D471B1** | `49 8B 3F` **`E8 EA 95 58 00`** `84 C0 74 1A` | `E8 EA 8E 58 00` = call **BN 0x22D00A0** (0x22D00A0 − 0x1D471B6 = 0x588EEA; same as EU) | High. Disassembled both. The original disp 0x5895EA → 0x22D07A0. |

### What the 3a retarget target is (BN 0x22D00A0 / RVA 0x1ED00A0)

`add rdi,0x18; jmp 0x108F570`. It is a SprjSessionManager(0x5540290) method that
moves `this` to the embedded matching-session handle at +0x18
(bbhost `SPRJ_SESSION_MANAGER_MATCHING_SESSION_HANDLE_OFFSET`) and tail-calls
BN 0x108F570 (RVA 0xC8F570). That function locks the wrapper (+0x08 vtable
slots 0x18/0x28) and returns `[handle] != 0`, i.e. "a matching controller exists".

The retarget therefore keeps the dtor's success branch (`test al,al` → writes
`WorldSessionObjectMan+0xF0 = GameDataMan->+8->+0xA4`) without changing
role/state +0x124 and without enqueuing the stop task. Every other
BeginLeaveSession caller is untouched.

BeginLeaveSession itself (BN 0x22D07A0) returns false for state ∈ {0,2,5}
(`bt 0x25`). Otherwise it sets +0x124 = 7 and calls 0x108F570 and then
0x108ED70.

### 3b. Second teardown: OnMatchingCheck (not patched by shadp2p)

`OnMatchingCheck` EU 0x013808B0 → **BN 0x17808B0** (prologue `55 48 89 E5 41 57 41 56 53 50`).

* When `[SprjSessionManager]+0x124 != 6`, or the multiplayer chr lookup
  `0x1738CE0(WorldChrMan?, flag, id)` fails, it calls BeginLeaveSession at
  **BN 0x17809B7**.
  * Bytes there: `49 8B 3F` **`E8 E4 FD B4 00`** `84 C0` (call 0x22D07A0, returns to 0x17809BC).
  * Then it tail-jumps to BN 0x19BED10, which is
    WORLD_TRANSITION_RESET_TRACKED_ENTITIES_FN (bbhost RVA 0x15bed10).
* shadp2p **deliberately leaves this unpatched**: it is the real
  disconnect path. Instead it avoids the situation with a pre-match warp, so
  the room is only created after both players are on the same map.
* If you want the same retarget anyway:
  * bytes `E8 E4 F6 B4 00` (0x22D00A0 − 0x17809BC = 0xB4F6E4).
  * Note the tail call to the tracked-entity reset at 0x19BED10 still runs.
    Retargeting alone would not stop that cleanup.

## 2. Functional mid-function hooks shadp2p installs (not byte patches)

shadp2p uses a mid-instruction guest-code hook with a full register
snapshot. **bbhost `hook`/`hook2` are function-entry prologue hooks only**:
they see rdi..r9 and xmm0-7, and need ≥14 bytes of whole, non-RIP-relative
instructions. Each mid-function site therefore needs an entry-hook or
on_frame substitute, as noted.

| Purpose | EU off | **Our BN** | Orig bytes at site | shadp2p logic | bbhost-compatible substitute |
|---|---|---|---|---|---|
| Host-placement rewrite at summon-builder entry | 0x01874710 | **0x1C74710** | `55 48 89 E5 41 57 41 56 41 55 41 54 53 48 83 EC 28` (17 bytes, use n=17) | rsi = request descriptor: +0x04 packed map, +0x08/0C/10 xyz, +0x14 heading, +0x18 signed SOS area. When the map differs from the host's map, overwrite with the host's live placement and cache it for the HTTP header. | Direct `hook2` at entry. Signature `(rdi=SosSelectionState*, rsi=request, edx=force)` (np_test). |
| Cross-map guest handoff, after the received placement is copied to the +0x14A0/+0x14D0 banks | 0x01E4FF3A | **0x224FF3A** (in CSMultiPlayerInsTask `STEP_StartMultiPlayWait` BN 0x224FD50; shadp2p calls it StartNotifyWait) | `48 8D 83 ED 00 00 00` | rbx = task (role +0xE8 == 0 means summoned client). Write host placement to GameState+0x14C4/+0x14D0/+0x14E0, call the setters + selector + StageTransition (see §4). | `hook2` at the step entry BN 0x224FD50 (rdi = task; prologue `55 48 89 E5 41 57 41 56 41 54 53 48 89 FB`, 14 bytes), or poll from on_frame. Runs one tick earlier than shadp2p's site. |
| Stage-descriptor finalize: force reload phase 3 | 0x01944F23 | **0x1D44F23** (inside fn BN 0x1D43F10) | `B8 00 00 00 FF 41 23 41 08` | r9 = load descriptor (+0x08 map). When it equals the pending target: call SummonedMapReload (BN 0x1736B90). If `WorldSessionObjectMan+0x84 != 3`, call SetTransitionSnapshotPhase (BN 0x1B8D9A0). | No entry equivalent with r9. Use on_frame polling of GameState/WorldSessionObjectMan. shadp2p's current flow (§3 of its doc) mostly avoids this by warping before the room exists. |
| Per-frame driver: pre-match warp, deferred reload, responder auto-ring | 0x01872360 | **0x1C72360** (bbhost `SPRJ_EVENT_SOS_SELECTION_STATE_TICK`) | `55 48 89 E5 41 57 41 56 41 55 41 54 53 48 81 EC E8 06 00 00` (20 bytes) | Calls the placement setters/selector/StageTransition before the match. After 20 stable ticks it calls UseItemNativeApply(player, 205, 17) (EU 0x018F9720 → **BN 0x1CF9720**, `(rdi=PlayerIns*, esi=goods, edx=arg) → bool`) and waits for SpEffect 9005. | `hook2` here or simply `on_frame`. |
| Lamp prompt: clear +0x49 for host | 0x012F836E | **0x16F836E** | `41 80 7D 48 00 74 04` (contains rel8 `je`) | r13 = action object. If +0x49 != 0 and the local player has SpEffect 9001 (multiplayer host), write +0x49 = 0. | Not entry-hookable: r13, mid-function, rel8 jump. Byte-patch alternative: at **BN 0x16F8315** replace `83 7B 14 01 0F 9F C0 85 C9 0F 9F C1 08 C1` (14 bytes, ending before `41 88 4D 49`) with `31 C9` + 12×`90`. That makes +0x49 always 0 for everyone. Toggle it per-frame from the host status if it must be host-only. |

## 3. Lamp-prompt gate details (action updater)

* Action object update **BN 0x16F5C40** (EU 0x012F5C40). Prologue
  `55 48 89 E5 41 57 41 56 41 55 41 54 53 48 81 EC 48 28 00 00`. Signature
  `(rdi=action obj, rsi=?, rdx=frame/time ctx)`. Huge frame (0x2848).
* The four gate bytes:
  * **+0x48** (BN 0x16F8260/0x16F82BD): "special lamp". (entity id % 10000 == 1960) and `0x1799BF0(...)` is true.
  * **+0x49** (BN 0x16F8315..0x16F8323): `(GameState(0x5556678)->+0x16F8)->+0x14 > 1` (party/slot count) OR `(SprjSessionManager+0x100 − +0xF8)/32 > 0` (pending summon list).
    * Bytes: `83 7B 14 01 0F 9F C0 85 C9 0F 9F C1 08 C1 41 88 4D 49`.
  * **+0x4A** (BN 0x16F836A): the entity lookup `0x17C9AF0` failed or the entity's +0x270 == 0.
  * **+0x4B** (BN 0x16F83C5): `0x1D1B6E0(WorldChrMan, id)` when the others are clear.
* Final enable (BN 0x16F8BA8..0x16F8EBF):
  * All four must be 0.
  * Then global RVA 0x553B0C8 must be non-null, and the entity
    `0x17C9930(10000, &+0x4C)` must exist, with `[ent+0x78]|8 == 8`, plus distance/angle checks against
    +0x68.
  * `call 0x186E250(edi=entity id, esi=enable, edx=(s8)+0x28)` (EU 0x0146E250).
* **BN 0x186E250** looks up the entity in the FrpgNetMan (0x553B120)+0xC70
  list and writes record **+0x48 = enable, +0x49 = kind**.
* **"Second presentation gate": shadp2p did NOT identify it.** Its doc
  says that after clearing +0x49 (all four bytes 0) neither client got the
  prompt.
* Unconfirmed leads, from static reading only:
  * (a) the consumer of the FrpgNetMan+0xC70 record (+0x48/+0x49);
  * (b) the `[ent+0x78]` state / range branch at BN 0x16F8C0D–0x16F8E34;
  * (c) message selection at BN 0x16F83CF–0x16F83F0 (text ids 90000/90005
    = 0x15F90/0x15F95), which picks a "cannot use" message whenever
    +0x48/+0x49/al is set.
* Not resolved.

## 4. Guest placement / warp natives (called, not patched)

All entries are byte-identical to shadp2p's expected prologues. SysV ABI. Call
with `api->call_guest(bn, regs)` from the main thread.

| Function | EU off | **Our BN** | Prologue / body | Signature (best understanding) |
|---|---|---|---|---|
| SetForcedSummonMap | 0x0156CF10 | **0x196CF10** | `48 8B 05 61 97 FE 03 8B 0F 89 88 F0 14 00 00 C3` | `void(const u32* map)`: GameState(RVA 0x5556678)+0x14F0 = *rdi |
| SetForcedSummonPosition | 0x0156CF20 | **0x196CF20** | `48 8B 05 51 97 FE 03 C5 F8 28 07 C5 F8 29 80 00 15 00 00 C3` | `void(const float4* pos)`: +0x1500 (xyz,1.0) |
| SetForcedSummonOrientation | 0x0156CF40 | **0x196CF40** | `48 8B 05 31 97 FE 03 C5 F8 28 07 C5 F8 29 80 10 15 00 00 C3` | `void(const float4* ori)`: +0x1510 (0,heading,0,0) |
| SetForcedSummonWarp | 0x0156CF60 | **0x196CF60** | `48 8B 05 11 97 FE 03 C6 80 20 15 00 00 01 C3` | `void()`: +0x1520 = 1 |
| SelectSummonedPlacement | 0x01332BC0 | **0x1732BC0** | `55 48 89 E5 41 57 41 56 53 48 83 EC 18` | `bool(u64 ignored, s32 warp_info_id)`: esi only. When +0x1520 is set it selects map +0x14F0 into GameState+0x0C. shadp2p calls `(0,-1)`. |
| SummonedMapReload | 0x01336B90 | **0x1736B90** | `55 48 89 E5 48 8D 05 2D 45 20 04 48 83 38 00` | `void()`: dispatches 0x0131E5C0 (bbhost SUMMONED_MAP_RELOAD_SERIALIZE_WORLD_STATE_FN), which sets WorldSessionObjectMan+0x84 = 3 |
| SetSummonReloadState | 0x0178D9A0 | **0x1B8D9A0** | `C7 87 84 00 00 00 03 00 00 00 C3` | `void(WorldSessionObjectMan*)`: +0x84 = 3 (bbhost WORLD_SESSION_OBJECT_MAN_SET_TRANSITION_SNAPSHOT_PHASE_FN) |
| StageTransition | 0x013CDE30 | **0x17CDE30** | `55 48 89 E5 41 57 41 56 41 55 41 54 53 50` | `s32()`: arms GameState+0x08 = 1 and starts the load (bbhost SESSION_WORLD_TRANSITION_FN). |
| WarpNextStage | 0x0132E010 | **0x172E010** | `55 48 89 E5 C1 E6 18 C1 E2 10 ...` | Lua binding `bool(ctx, esi=area, edx=block, ecx=sub, r8b=sub2, r9d=warp point)`. Packs the map into GameState+0x0C, warp point into +0x10, calls StageTransition, returns 1. |
| WarpNextStage_Bonfire | 0x0132E050 | **0x172E050** | `55 48 89 E5 48 63 C6 48 69 C8 89 B5 F8 14 ...` | `(ctx, esi=ReturnPoint id)`: derives area/block from the id, writes +0x0C/+0x10, calls StageTransition (bbhost WARP_NEXT_STAGE_BONFIRE_FN; used by `api->lamp_warp`). |
| RemoteWarpPlayer (executor) | 0x0154D180 | **0x194D180** | `55 48 89 E5 41 57 41 56 41 55 41 54 53 48 81 EC 78 01 00 00` | Lua remote-command executor `(rdi=?, rsi=args, rdx=result)`. Needs WorldChrMan->+0x60 (player). Args parsed by 0x180D2C0. Medium-low confidence on the args. |
| RemoteWarpPlayerWorldPos (executor) | 0x0154DC50 | **0x194DC50** | same 20-byte prologue as above | same shape as RemoteWarpPlayer. Medium-low. |
| UseItemNativeApply | 0x018F9720 | **0x1CF9720** | `55 48 89 E5 41 57 41 56 41 55 41 54 53 48 83 EC 18` | `bool(PlayerIns*, s32 goods, s32 arg)`. shadp2p uses (player, 205, 17). |

Globals (RVA; BN = +0x400000), with bbhost names:

* 0x5556678 = GameState / WORLD_TRANSITION_STATE_PTR (+0x08 transition armed, +0x0C selected map, +0x14C4/+0x14D0/+0x14E0 summoned placement, +0x14F0/+0x1500/+0x1510 forced, +0x1520 forced flag, +0x1620.. received placement, +0x16F8 net-flow slots).
* 0x553D6D0 = WorldSessionObjectMan (+0x84 transition/reload phase).
* 0x5540290 = SprjSessionManager (shadp2p's "CSMultiPlayMan"; ctor RVA 0x1ece640 matches; +0x124 role/state).
* 0x553E878 = WorldChrMan.
* 0x553B120 = FrpgNetMan (shadp2p "SummonManagerRoot"; +0xA80 area).
* 0x553B148 = WorldResLoadContext (shadp2p "CurrentMapList").

## 5. Other shadp2p seamless-mode effects (enumerated from source)

* All code changes are exactly the 13 byte patches above (1a–1h, 2a–2d, 3a),
  plus the 5 functional hooks in §2. The rest of `bloodborne_re.cpp` is a
  read-only trace (`SHADPS4_BLOODBORNE_RE_TRACE`).
* Non-code pieces:
  * The `X-ShadPS4-Bloodborne-Host-Placement` header (54-byte versioned
    placement, `GetSeamlessHostPlacementHeader`/`Set...`) is added to
    `/summon_messenger/get` (search) and `/request`, and read back from
    `/create` responses.
  * The `X-ShadPS4-Bloodborne-Seamless: 1` header reroutes summon HTTP to
    shadNet.
  * shadNet-side: SummonData raw +0x79 "available count" 0 → 1, and the
    Preparing/hide logic.
* A bbhost plugin must replace that transport with its own channel, e.g. its
  own P2P message.
* Not patched by shadp2p:
  * OnMatchingCheck (3b).
  * The stage-UID stop at EU 0x0193C657 (BN 0x1D3C657) in WORLD_LOAD_COORDINATOR_UPDATE (RVA 0x193ac10), which only applies for states 1/3.
  * The emevd event 7200 instruction 0 (proposed only).

## 6. Caveats

* bbhost and shadp2p name some of the same objects differently:
  * RVA 0x5540290 is "SprjSessionManager" in bbhost and "CSMultiPlayMan" in
    shadp2p. bbhost's separate CS_MULTI_PLAY_MAN_SINGLETON_PTR is 0x5540230.
  * shadp2p's CSMultiPlayerIns step names are shifted one slot against
    bbhost's RTTI-derived step table. For example, EU 0x01E4FD50 is bbhost
    `STEP_StartMultiPlayWait`.
  * bbhost says 0x1946ce0 "allocates the event SOS selection state". It
    actually constructs the 0x50-byte per-area payload (vtable 0x5330540).
    That payload allocates the 0x240-byte selection state at +0x38 via
    0x1870c50, which matches bbhost's `SprjEventManCurrentAreaState`.
    0x19470F0 is that payload's destructor.
* shadp2p's live validation covers the EU build on shadPS4. Our bytes are
  identical, but nothing here has been run on bbhost.
* Patch 1h removes the area restriction in the SOS status for all goods. 1b
  also makes "no world object" (0x197F95A) count as allowed.
* No site was missing.
