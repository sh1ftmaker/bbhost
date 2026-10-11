# Seamless co-op for bbhost: checkpoint (2026-10-10)

> In this fork, the dev material lives in `plugins/seamless/dev/`. The
> `coop-test/` paths below are the local working copy: `coop-test/X` is
> `plugins/seamless/dev/X` here, except `build/`, `runs/` and the copied
> shadp2p sources, which are not in the repo. Remove `dev/` before an
> upstream PR.

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
