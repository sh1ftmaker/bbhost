# Seamless co-op for bbhost: checkpoint (2026-10-10)

Goal: an Elden Ring Seamless Co-op-style **native plugin for bbhost**, to PR back to
droogie/bbhost (upstream asks for it in issue #31, which has no reply yet). The route
we chose is the hybrid:
- keep the game's own co-op (its rooms, signaling and character sync);
- patch out the rules that end a session;
- later, add a control channel so guests follow the host and re-summon automatically.

## Where things are

| What | Path |
|---|---|
| Plugin source | `bbhost-src/plugins/seamless/seamless.cpp` |
| CMake entry | `bbhost-src/CMakeLists.txt` (`bb_plugin(seamless ...)`, next to mutators) |
| Branch | `plugin/seamless` in `bbhost-src`, made from `origin/master` (c421ea0, v0.2.18). **Not committed or pushed yet.** |
| Built plugin | `coop-test/build/seamless.dll` |
| Local test server | `coop-test/server/server.py` (plus `selftest.py`: 12/12 pass against bbhost's simclient) |
| Two-instance runner | `coop-test/pair.ps1` (usage in `coop-test/README.md`) |
| Test-only plugin | `coop-test/plugin/coop_lamp.c` / `.dll`: leaves the Dream for a lamp, rings bells through their SpEffects, and (new) reloads after the join |
| RE notes: patch sites | `coop-test/re/patch-sites.md`, with `dis.sh`, an llvm-objdump wrapper that prints BN addresses |
| RE notes: session end | `coop-test/re/session-end/session-end.md`, with helper scripts and extracted tables |
| shadp2p sources | `coop-test/re/shadp2p/` (GPL, Wozzardman; reference only, never paste into bbhost) |
| Proof runs | `coop-test/runs/20261010-184255` (vanilla join), `20261010-183508` (seamless join, screenshot) |

Build the plugin (llvm-mingw is installed through winget), from `bbhost-src`:

```
clang++ --target=x86_64-w64-mingw32 -std=c++17 -O2 -shared -Iinclude -fvisibility=hidden -static -static-libgcc -static-libstdc++ -o ../coop-test/build/seamless.dll plugins/seamless/seamless.cpp
```

bbhost itself can't be built on this PC because the Vulkan and SDL headers are missing. Plugins build fine.

Run a pair, from `coop-test`:

```
powershell -ExecutionPolicy Bypass -File pair.ps1 -Seamless -KeepRunning -Seconds 420 -ReloadAfter 30
```

## Key findings

- **Addresses.** BN = RVA + 0x400000, and the file offset in our ELF = BN − 0x3FC000. The EU 1.09 build (CUSA03173, the one shadp2p targets) and our US 1.09 (CUSA00900) have **identical code at the same RVAs**, so shadp2p's offsets port by adding 0x400000. All 38 signatures matched.
- **Plugin patch groups.** Each group is all-or-nothing, and every patch also checks the bytes just before it.
  - **`anywhere` (12 patches, shadp2p):** the bell availability checks (0x197F200), the ringing-bell updater (0x1906820), `IS_SUMMON_AREA_ELIGIBLE` (0x1D1A750), the SOS status (0x1C6FE40), the summon builder (0x1C74710: cleared / negative area / other map), and SosSignMan's filter (0x18B714A). Applies in-game.
  - **`keep_on_load` (1 patch):** at 0x1D471B1 the per-area payload's destructor stops calling BeginLeaveSession (0x22D07A0) and calls 0x22D00A0 instead. Applies in-game. **Not yet proven to keep the room across a load.**
  - **`stay_after_boss`:** removes the leave steps from the boss-clear chain.
    - Functions involved: BlockClear2 (0x17849A0) → BlockClear2_2 (0x1785760) → BlockClear2_3Leave and BlockClear2_3.
    - Patches: the latches at 0x1784CFE (13 bytes), the flags at 0x1785967, scheduling the leave at 0x1785877, and the cooperator's return at 0x178599C.
    - The first version failed: two of its patches overlapped, so the group rolled back. That's fixed and rebuilt, **but the fix hasn't been tested in-game yet.**
  - **`free_bells`:** EquipParamGoods 200 `consumeHeroPoint` = 0 (Insight in Bloodborne is the "humanity" field). Applies.
- **How sessions end** (details in `session-end.md`):
  - All endings are compiled global_event.lua callbacks; there's no DS2-style EventResult.
  - The only native leave is `SprjSessionManager::BeginLeaveSession` at 0x22D07A0, with 42 callers.
  - Most "go home" paths run through `SessionWorldTransition` at 0x17CDE30.
  - Host death broadcasts HostDead through the call at 0x17816D7.
  - A guest's death ends at `PartyGhostDeath_2`; letting that happen and re-summoning is the cleanest option.
  - A join-sync timeout of 60 s (0x1B8A4EF) could fire if a reload is slow.
- **Local server protocol** (in `coop-test/README.md`):
  - `ss.info` must be base64-encoded and list all 37 APIs.
  - `sync_chara_id` must return `PublishCharacterIdList`.
  - List responses need their arrays, even when empty.
  - At the title, choose "Log In" before Continue (the game starts offline).
  - The bells are rung by applying SpEffect 9000 (Beckoning) or 9005 (Small Resonant). The `BBHOST_NP_TEST` Lua events don't post a sign.
- **Memory limit.** Each bbhost instance needs about 11 GB of commit (a fixed 6 GiB direct-memory section plus overhead). Even with apps closed, the PC has about 15–21 GB free, so two instances are marginal. `pair.ps1` checks free commit first (`-MinCommitGB`). **Fix: a larger page file** (initial 16 GB, maximum 48 GB), or close Chrome and Steam. The guest runs with `BBHOST_RENDER_MIN=1`, so its black window is expected.
- **Known flake:** the first summon sometimes stalls with `sessionReady 0`, and the host retries after about 55 s. This matches upstream's known "1 in 4 first summons fail".

## Next steps

1. **Map-load test.** Rerun the pair with `-ReloadAfter 30`. In the logs, check `coop_lamp: other players in the world` before and after the reload, and look for any np leave lines. This tells us whether `keep_on_load` keeps the room across a load.
2. **Boss test.** Check that the `stay after a boss` group now logs "5 patches in", then test a boss kill. This needs a save at a boss, or a cheap boss, with both instances present.
3. **Control channel** for following the host's lamp travel and re-summoning. The best option is a custom packet through the game's own world-session P2P queue (`WORLD_SESSION_SEND_PACKET_FN` 0x17894e0, `include/bbhost/engine/sprj/world_session_packet.hpp`), plus a hook on the receive dispatcher. That works inside the plugin with no changes to bbhost's core. The alternative is proposing a plugin API for sending network messages upstream.
4. **Death handling:** the host respawns instead of the session ending, and guests re-summon automatically.
5. **Before the PR:**
   - Add the plugin to docs/plugins.md, `tools/package_plugins.sh`, and the README plugin lists.
   - Keep `BB_PLUGIN_OPT_IN | BB_PLUGIN_GAMEPLAY`.
   - Credit shadp2p for the offsets in the code comments.
   - Note that it hasn't been tested on Linux.

## Update (2026-10-10, evening)

- **Control channel works.** It's in seamless.cpp (`namespace chan`). Messages go as WorldSession packet type 0x1a over the game's own reliable channel. Design and addresses are in `re/channel/channel.md`.
  - Run `20261010-230118`: pings went both ways in order, host to guest and guest to host. Test it with `BBHOST_SEAMLESS_PING=1` (`pair.ps1 -Ping`).
  - **Address catch:** the WSOM, SprjSessionManager and WorldChrMan "SINGLETON_PTR"s are pointer slots: BN 0x593d6d0, 0x5940290 and 0x593e878. Dereference them through `guest_addr`. `api->read` returned nothing for them.
- **Follow the host works as far as the hand-off.**
  - The plugin hooks WarpNextStage_Bonfire (BN 0x172e050, 14-byte prologue) and sends the host's lamp to the guest.
  - The guest gets it and queues `lamp_warp` to the same lamp.
- **The session does NOT survive the load yet.**
  - The host's lamp travel made the guest leave: `np: member 2 left (leave_room)`, and both sides ended at role 0.
  - `keep_on_load` (the per-area payload destructor) is not the path that ends it here.
  - Likely cause: the guest's own lamp travel goes through `SessionWorldTransition` (0x17cde30), which calls BeginLeaveSession unless GameStateMan+0x1592 != 0 (see `re/session-end/session-end.md`). The host's side may also be the world-transition leave at 0x1d3c657 or 0x1d39876.
  - **Next:** trace which BeginLeaveSession caller fires on each side. Hook2 BeginLeaveSession (0x22d07a0) in log-only mode and log the return address, if hook2 can see it; otherwise hook each candidate caller. Then gate it while a seamless follow is in progress.
  - The fallback is to let it end and re-summon automatically after the load (the hybrid plan).
- **The test runner is event-driven now:**
  - The host's bell waits for the guest's sign (`COOP_BELL_GATE`).
  - The run stops once both sides log a `coop_lamp: RESULT`.
  - A trap kills the instances if the script errors.
  - A run takes about 2.5 minutes (it was 7). The join comes about 40 s after the guest's sign.
  - Command: `pair.ps1 -Seamless -Ping -ReloadAfter 15 -HostOnlyReload -MinCommitGB 8.5`
- **The test plugin** (`coop_lamp`) logs the session role and member count from SprjSessionManager, and after any load it logs whether the session is still on.

## Update (2026-10-11): M1 solved - the party survives the host's lamp travel

- **Work lives on branch `seamless/integration`** (worktree `Bloodborne/wt-integration`, pushed to the fork). It holds upstream master + the API branches (`api/hook-ret-addr`, `api/overlay-text`, `api/session-events`) + the plugin.
- **Building:**
  - bbhost builds locally: MSYS2 UCRT64 + `win-deps`, run `tools/win_build_msys2.sh` with `WIN_DEPS_ROOT=/c/Users/shift/Desktop/Bloodborne/win-deps`.
  - The test exe goes in `coop-test/bbhost-int/bbhost.exe` (copy it from `wt-integration/build-win`).
  - Plugin build: `clang++ ... -Iinclude ... plugins/seamless/seamless.cpp` in wt-integration, output to `coop-test/build/seamless.dll`.
- **The fix, three parts, all in seamless.cpp:**
  1. **Guest follows the host.** The guest gets the host's lamp over the channel and travels there too (`lamp_warp`).
  2. **The guest's travel keeps the room.** The plugin sets GameStateMan(ptr 0x5956678)+0x1592 for the guest's SessionWorldTransition only, and restores it the next frame. Without it, the inline BeginLeaveSession at 0x17cdef6 takes the guest out of the room.
  3. **The characters come back.**
     - During the session the plugin skips DestroyAllTasks (0x2254830) when it's called from the unload (return 0x1d4720c).
     - 10 s after the load, each CSMultiPlayerInsTask (CSMultiPlayMan ptr 0x5940230, vector +0x10..+0x18) whose player has no character is rewound to Create (+0x50/+0x54 = 4, +0xed |= 0x0d).
     - Notes are in `re/reinsert/reinsert.md`.
- **Verified 4/4 runs:**
  - role 3/6 and 2 members held 75 s after the load;
  - world session state 4;
  - each side makes the other's character again;
  - channel pings resume.
- **Command:** `pair.ps1 -Bbhost <bbhost-int exe> -Seamless -Ping -ReloadAfter 15 -HostOnlyReload -MinCommitGB 8.5` (add `-Trace` for the leave trace).
- **Not yet checked:**
  - The guest is visually at the host's lamp; the guest renders minimally, so this needs a screenshot or `-GuestRenders`.
  - A second lamp travel in a row.
  - The lamp prompt for the host while in a session (shadp2p's +0x49 gate).
  - Travel to and from the Hunter's Dream.
- **Next:** M2 (auto re-summon as a safety net), M3 (boss and host death in game), the Dream (M5/M5b), and the join UX with the new server features.
