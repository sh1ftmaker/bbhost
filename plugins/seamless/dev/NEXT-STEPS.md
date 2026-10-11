# Seamless co-op for bbhost: lessons from prior art, target experience, roadmap

Written 2026-10-11 from the checkpoint (`CHECKPOINT.md`), the plugin (`bbhost-src/plugins/seamless/seamless.cpp`), the RE notes (`re/patch-sites.md`, `re/session-end/session-end.md`, `re/channel/channel.md`), the local copy of shadp2p's RE doc (`re/shadp2p/sp_documents_bloodborne-seamless-re.md`), the bbhost docs/headers, the latest pair run (`runs/20261010-230118`), and the web sources listed at the end. Nothing was run; no file outside this one was changed.

Legend: **[fact]** = read in code, RE notes, logs or a primary source; **[inferred]** = follows from facts but not observed live; **[speculation]** = a guess that a test must confirm.

---

## 0. Executive summary

1. **The game already has an "reload a map without leaving the room" mode** - the summoned guest's `SummonedMapReload` path (WorldSessionObjectMan `+0x84` = phase 3, GameStateMan `+0x1592` as the leave gate). shadp2p proved live that a guest can reload *into another map* with the room retained when the serializer runs before the transition; what killed its room was (a) the per-area payload destructor (we already patch it, `keep_on_load`) and then (b) `SprjSessionManager::OnMatchingCheck` ~9 s later, when its WorldChrMan lookup ran against a world still being rebuilt **[fact, shadp2p doc]**. Our run never got that far: the leave came almost immediately on the host's lamp travel, and the most likely caller is `SessionWorldTransition`'s own inline `BeginLeaveSession` (`0x17cdef6`), which only `+0x1592 != 0` skips **[inferred]**. Milestone 1 is a short ablation/tracing campaign over the four candidate callers plus one real attempt at the phase-3 path on both sides.
2. **Build the fast auto re-summon anyway.** Every shipping seamless mod needs a "something broke, get back together" path (ERSC: "even if you disconnect, you can very quickly re-join"), and for us it is also the honest fallback for lamp travel, host death and the Dream if the phase-3 path doesn't survive the host's own reload (no native precedent for a *host* reloading in-session **[fact]**). shadp2p already demonstrated the pieces: auto-ringing the Small Resonant Bell through `UseItemNativeApply(player, 205, 17)` after the map settles **[fact]**, and our rig rings the Beckoning Bell through SpEffect 9000 **[fact]**. With `anywhere` + `free_bells` and our channel carrying the destination, "the host travels, the guest is back in about a minute, with no input from either" is achievable with today's plugin API alone.
3. **Design the product so the host and the guest are peers, not a host and a phantom**: one-click join by password (the ruleset string already segregates matchmaking, so a password hash inside `set_rules` gives private sessions for free **[inferred]**), everyone follows the host's lamp with a banner, deaths never end the party, boss credit is journaled to the guest's own save, the Dream is a private "intermission" that auto-resumes, and every break auto-heals. Most of this is plugin-only; the upstream asks are small and listed in section 5.
4. **Steal three lessons from the DS2/ER mods**: (a) clear the "you are going home" latches or the next death/interaction silently does nothing (already applied to the boss chain; must be applied to death); (b) sync flags one way, host to guest, only a whitelist (bosses, lamps, shortcuts) and journal them rather than writing them while in the host's world - ERSC's quest-flag sync is its most complained-about feature; (c) "rest at a grace" is the universal desync reset in ER; in Bloodborne a lamp travel is exactly that, so "everyone follows the host's lamp" doubles as the desync fix.

---

## 1. What other seamless co-op projects do (and what players complain about)

### 1.1 LukeYui: Elden Ring Seamless Co-op (ERSC)

Confirmed from the mod description, changelog thread and docs site (links in section 8):

| Topic | ERSC behaviour |
|---|---|
| Session lifetime | "theoretically possible to play the game from the tutorial up to the final boss completely in one co-op session". Bosses/area clears no longer end the session; fog walls removed. Own netcode (Steam networking), EAC off, **not** the game's native session. |
| Death | Player respawns at their last rested grace *in the same world*; "The session will not be terminated." Dying in a boss fight = spectator "until either the boss is dead or every other player dies". Death applies a stackable random "rot" debuff, removed only by resting (ini `death_debuffs`). |
| Fast travel / graces | Rest at a grace resets the world state for everyone "to prevent enemy desync"; players inside boss rooms are ejected when someone rests. Warping to a grace "will now inform other players in the session which bonfire you have warped to" (1.7.9). Fast travel is by **vote**: all players must agree, then everyone warps together (reported in community guides; the exact mechanics live in the mod, not in the public docs). |
| Progression | "Game progression events completed in online play will also progress the game in your own world." FAQ: "Progress is saved based on Host progression. Wanderer's progression will not sync if Host has progressed less." Items gained in conversations are given to all players (changelog). |
| Join flow | Items: Tiny Great Pot (host), Effigy of Malenia (join), Separation Mist (leave). Password in `ersc_settings.ini`/`cooppassword.ini`, same for all; restart needed after a change. In-game password display option. |
| Saves / versions | Separate save extension (`.co2`); "All players must use the same version"; "only people with the same mods will be able to connect" (regulation/mod check). Mod update notification in game (1.9.0). |
| HUD | Overhead player display options including "Display ping AND soul level" (1.7.9); player death count in the banner; shared map waypoints (1.8.3). |
| Scaling | Enemy/boss health, damage and posture scaling per extra player, all ini-tunable; 3 phantoms instead of 2. |
| Invasions | Opt-in (`allow_invaders`), disabled during boss fights (1.7.3), cooldowns. |

**What players hit most** (FAQ, workarounds page, Steam/Reddit threads):
- infinite loading screens caused by "invalid warp targets or invalid map IDs";
- quest/flag desync: "Seamless has a lot of bad interactions with questlines if someone has progressed further than others... it usually breaks the chain for everyone too"; burnt/unburnt capital, festival not starting, wrong Erdtree state (flags inherited from invasions);
- missing rewards: "Rewards from certain events may not drop for all"; field bosses with wrong health bars and no rewards;
- spectator traps (evergaols, partner disconnects mid-boss);
- "rest at a grace" is the answer to most desync; after someone rests, others temporarily can't talk to NPCs;
- don't go to the Roundtable Hold (hub) without severing co-op (older advice); shops depend on the host's items;
- version/password mismatch gives unhelpful errors; everyone must restart after changing the password.

### 1.2 LukeYui: DS3 / DSR / Nightreign / Sekiro Online / AC6

- **DS3 Seamless (alpha 0.1.x)**: respawn at last bonfire, session continues; boss kills don't send phantoms home; fog walls removed; rest at a bonfire resets the world for all; "if one player sits at a bonfire while others are in boss rooms, those players will be removed"; "your online progression counts in your own world"; pendants as join items; 6 players; alpha bugs: enemy HP recovery, DLC access, boss vulnerabilities not resetting. **[fact, Steam guide]**
- **DSR Seamless**: same model (Nexus pages were 403 to the fetcher; the feature set is the DS3 one).
- **Nightreign Seamless**: no matchmaking servers/EAC, password, duos, "even if you do disconnect, you can very quickly re-join and continue where you left off". **[fact, press]**
- **Sekiro Online**: host + up to 5 guests, guests scaled to the host's level, visitors respawn, "Coward's Bell" to leave. **[fact, press]**
- **AC6 co-op (alpha)**: party persists between missions, per-player difficulty scaling chosen by the host. **[fact, press]**

Lesson: every LukeYui mod ships (1) death = respawn in place, (2) progression credited to the guest's own world, (3) a trivial rejoin, (4) scaling as a host-controlled knob, (5) a password. Those five are the baseline expectation of a "seamless" label.

### 1.3 DS2 clones (open source, most comparable to us: they keep the *native* session alive)

**scheissgeist/Seamless** (ds3os-derived private server, MIT): "drops the messages that would end a co-op session" at the protobuf layer; overlay menu, password, real names, player count on HUD, join/leave notifications, phantom timer maxed, hollowing cleared, 6 players. Known limits: "Phantoms currently cannot rest at bonfires", "A phantom who dies is sent back to their own world instead of respawning at the host's bonfire", "World state (fog walls, doors, chests) is not synced". **[fact, README]**

**Restezzz/Seamless-DarkSouls2** (`docs/DS2_INTERNALS.md`) is the richest source of mechanics and bugs **[fact, doc]**:
- Auto-summon: a sign is generated under the host automatically; the host auto-picks it, retrying every 3 s for 2 min; "busy" rejections come from a counter held during bonfire menus, cutscenes and (always, in Majula) the event manager - they override the "area takes multiplayer" predicate.
- Fresh start together, shared bonfires (host and guest rest; rest restores enemies for both), independent loot ("items vanish from the guest's solo world post-collection"), NPC dialogue rewards per player.
- Death: guest respawns at its last rested bonfire inside the host's world ("under host's feet" fallback); host death doesn't disconnect guests (guest "lies down waiting for host respawn"). The respawn still goes through a warp gate (`0x248940`) they could not replace: "Full solution requires replacing this gateway, currently unimplemented".
- Boss doors: a `BossDoorCrossed` packet opens the fog for the guest; boss state packet every 4 s.
- **Flag sync**: one-directional host to guest, diff once per second, only 0 to 1 transitions, at most 256 per pass, a full `FlagBulk` at join, a backup file written before the first write, a permission boundary on which ids a guest may write. Bugs: a flag group that "recompiles empty" fools the diff; boss state desync; guest's own doors/shortcuts don't persist after a rejoin; dialogue progress resets.
- Phantom NPC non-spawn: the "world loaded via MP-warp" predicate skips enemy spawn completion for guests; a second system renders NPCs as ghosts; both need hooks.
- Their stated lessons: hook at decision gates instead of replacing systems; latches ("don't process again", `+0xCE/+0xCF`) must be cleared or the next death does nothing; "menu-to-menu" busy flags are indirect and fragile.
- Complaints in v0.3.0: enemies dying where they stood after a rest while a new one appears, animation desync, bosses with cutscenes not aggroing guests, guest deaths in boss fights give items but not souls, "duty fulfilled" banner shows although nobody left.

### 1.4 Wozzardman/shadp2p (Bloodborne, shadPS4 + shadNet) - the direct ancestor

From the RE doc and repo **[fact]**:
- Same 13 byte patches we ported (bells anywhere, cross-map summon) and the per-area-payload-destructor retarget (our `keep_on_load`).
- **Cross-map summon works only as a "pre-match warp"**: shadNet marks the responder "Preparing", ships the host's placement in an HTTP header, the responder warps to the host's map *before any room exists*, then auto-rings the Small Resonant Bell via `UseItemNativeApply(player, 205, 17)` after 20 stable ticks (retry once after 15 s), and the ordinary same-map summon follows.
- The post-join reload attempt (room exists, guest reloads into the host's map) got the guest to the host's exact coordinates, but the room was lost: first from the payload destructor (fixed by the retarget), then from `OnMatchingCheck` about nine seconds after the reload reached phase 4, because its WorldChrMan lookup "failed to find the multiplayer character while the destination world was being rebuilt". They left that unpatched ("it is the real disconnect path").
- Calling `SummonedMapReload` before a room exists "loaded default world progression instead of the character's real save state" - the serializer is a guest-only thing.
- The host's **lamp prompt disappears while a guest is present** (action object byte `+0x49`); clearing it was not enough, a second presentation gate is unresolved. This matters for us: in a seamless session the host must be able to use lamps. (Our `lamp_warp` API bypasses the prompt, but the real player needs the prompt.)
- Open issues: #2 "Seamless doesn't seem to work" - users set an env var on each client and a server flag and still got "traditional co-op" because the setup is invisible and unverifiable. Lesson: the mode must be **visible in game** (banner on start, ruleset shown in F9) and **negotiated** (server pairs only same-ruleset players, which bbhost already does).
- Repo activity stopped 2026-08-16 (last commit); nothing on lantern travel while in session was validated ("Remaining coverage should... exercise lantern travel while the session is established").

### 1.5 ds3os

Open-source DS3/DS2 server; its value for us is design, not code: sharding (players choose a server/shard), ban safety for modded play, and the "disconnect-blocking technique" the DS2 clones reuse. bbhost's private server already plays this role. **[fact]**

---

## 2. Judgement for our situation

Constraints that shape every choice **[fact]**: native game netcode we can patch/hook (prologue hooks of >= 14 clean bytes, byte patches, `call_guest`, no mid-function hooks and no return address in `BbHookCtx`); a working reliable control channel (type `0x1a`, sends only while the sender's WSOM state is 4, receives queue across the receiver's state 3); a closed-source upstream server but our own local one for tests; GPL, opt-in, ruleset-segregated, PR-able; two local instances max, 2.5 min per run.

### 2.1 Keep the native session alive through a load vs. tear down and auto re-summon

| | Keep alive (phase-3 path) | Tear down + auto re-summon |
|---|---|---|
| Smoothness | Best: ~one load, both arrive together, no bell UI | ~load + up to 40 s matchmaking + 10-20 s join; bells ring themselves |
| Native precedent | Yes for a **guest** reloading into the host's map (`SummonedMapReload`, phase 3, `+0x1592`); **none for the host** reloading while guests stay | Fully native; shadp2p and our rig do each half already |
| Known enemies | `SessionWorldTransition` inline leave (`0x17cdef6`), payload destructor (`0x1d471b1`, patched), host map-change leave (`0x1d3c657`), guest rebuild leave (`0x1d39876`, only with <= 1 route), `OnMatchingCheck` (`0x17809b7`), 60 s join-sync timeout (`0x1b8a4ef`), WSOM state must return to 4 on both sides | The "1 in 4 first summons stall" flake; sign polling interval; Insight/bell availability (both patched) |
| Failure mode | Stuck room, invisible peers, desync of presentation members, world-snapshot confusion | Longer gap; a rare second retry |
| Test cost | Many 2.5-min runs with ablation | Few runs; mostly timing tuning |

**Recommendation**: do both, in this order. Spend a bounded budget (about 8-12 pair runs) on the phase-3 path with a strict diagnostic ladder (section 4, M1). Whatever the outcome, build auto re-summon (M2), because it is needed for crash/disconnect recovery and for guest death, and it is the fallback for every host-initiated load. If M1 succeeds for the guest side only (likely **[speculation]**), use the hybrid: the host's load tears the room down, the guests follow and re-summon; if it succeeds on both sides, re-summon becomes the recovery path only.

### 2.2 Feature-by-feature verdicts

| Feature | Verdict | Why |
|---|---|---|
| Lamp travel (host) | Everyone follows the host, automatically, with a banner. No vote at first; later an option "ask guests first" (ERSC-style vote) | ERSC's vote exists because any player can warp; in BB only the host's world matters. A guest using a lamp in the host's world should be refused with a banner, or treated as "leave the party" (option). |
| Hunter's Dream | **Private intermission**: when the host travels to the Dream, guests go home to their own Dream (session ends cleanly, or kept if M1 proves it), both see "In the Dream - the party resumes when HostHunter leaves"; on the host's next lamp travel guests auto-follow/re-summon. Shared Dream (guest as phantom in the host's Dream) later, optional | Guests can't talk to the Doll/Gehrman or use the shop as phantoms (ERSC's "don't go to the Roundtable" advice, DS2's dialogue-mask bug). The Dream is where each player levels up; sending everyone to their own is the fair design. |
| Host death | Guests stay; host respawns at its lamp; guests follow via the same reload primitive, else re-summon | Vanilla broadcasts `HostDead` (`0x17816d7`) then `SessionWorldTransition`. Needs M1's primitive on the host side; fallback is identical to lamp travel. |
| Guest death | v1: let `PartyGhostDeath` run (guest goes home), auto re-summon to the host's position. v2: dead spectator + revive (needs the in-session reload for the guest, which M1 proves or disproves) | session-end.md: "Clean re-summon (lowest risk)"; DS2 clones never solved respawn in place either. Add the latch-clearing (flags 4000/4047, `+0x0c`, `+0x8d`) if the guest stays. |
| Boss kill | Keep `stay_after_boss` (B1-B5, +B6), verify Insight/echoes live | Already patched; untested in game. |
| Rewards | Echoes/drops native; **journal** the host's boss/lamp/shortcut flags to the guest's plugin_dir and apply when the guest is next in its own world (role 0) | Avoids ERSC's quest desync and DS2's "guest doors don't persist after rejoin". Whitelist only. |
| Scaling | None in v1 (BB already scales boss HP per phantom); expose "enemy HP %" later or point to `mutators` | Keep the ruleset simple for matching. |
| Join | Password in F9 -> part of the ruleset string; "Open my world" / "Join" buttons ring the bells; auto-retry | Everything is plugin-side. Friend invites need the server (section 5). |
| Invasions | Off by default in a seamless session (option) | ERSC default; Sinister bell is `anywhere`-patched too, so without a block any seamless player could invade any other. |
| HUD | Banners via `show_message`; overhead names/ping need an upstream drawing API | The F9 overlay exists in bbhost; a `draw_text`/overlay API is a small upstream ask. |
| Version/mod match | `seamless;v=N;...` ruleset already includes the switches; add a protocol version and a hash of the plugin's patch set | Mismatch = never matched, which is better than ERSC's unhelpful errors - but show why in F9 ("your partner's seamless version differs"). |

---

## 3. Target experience ("luxury" for both players)

### 3.1 Joining

- **F9 > Seamless**: `password` (text), `Open my world` (action), `Join a world` (action), `Leave the party` (action), status line ("Party: HostHunter (host), you (guest), 2 members, ping -").
- The ruleset becomes `seamless;v=2;pw=<sha256(password)[:12]>;free;anywhere;boss;keep` so the server only pairs players with the same password **[inferred: docs/online.md says the server keeps same-ruleset players together; verify on the local server that `X-BBHost-Ruleset` is compared as a whole string]**. Empty password = public seamless.
- `Open my world` applies SpEffect 9000 (the Beckoning Bell, free) and keeps re-ringing while the party is incomplete; `Join a world` applies 9005 (Small Resonant) the same way. Nobody opens the inventory, nobody pays Insight. Wherever they stand, including the Dream and cleared areas (`anywhere`).
- Banners: "Looking for HostHunter's world...", "GuestHunter has joined the party", "Party complete".
- Automatic retry: if the summon stalls (the known `sessionReady 0` flake), ring again after 20 s instead of the game's 55 s; log the reason.

### 3.2 Staying together

- **Host uses a lamp**: banner on both sides "HostHunter travels to Central Yharnam"; guests load the same lamp automatically (`follow_host`), and the party is intact on arrival (M1) or re-forms within ~60 s without any input (M2). A guest who is mid-fight gets 3 s of warning; an option "ask guests first" turns it into an ERSC-style vote later.
- **Guest tries a lamp**: refused with "Only the host travels; press Leave the party to go home" (hook `WarpNextStage_Bonfire` on guests and skip, `ctx->ret = 0`). Lamp **lighting** by a guest should still register for the guest's own save via the journal (bonfire_db.hpp notes role 6 is excluded from the anchor write).
- **Hunter's Dream**: host goes to the Dream -> intermission (section 2.2). Banner on guests: "HostHunter is in the Dream. You are in yours; the party resumes when they leave." Guest is sent to its own Dream (`lamp_warp` to a Dream headstone row, or let the vanilla leave take it home). On the host's next lamp travel, auto-follow + re-summon.
- **Host dies**: guests see "HostHunter has fallen - hold on", the host respawns at its lamp, guests arrive there (M3). No "you have returned to your world".
- **Guest dies**: "GuestHunter has fallen - returning", and the guest is back next to the host within ~60 s, automatically (M4 v1). Later: stay as a dead spectator and revive when the host rests at a lamp (M4 v2).
- **Boss dies**: "Prey slaughtered" for everyone; the echoes, the Insight and the drops are paid; nobody leaves (`stay_after_boss`).
- **Someone crashes / loses connection**: the other side keeps the party record (host identity, password, last lamp); when the lost player is back in game the plugin re-rings on both sides, and the banner reads "Reconnecting to HostHunter's world...".

### 3.3 Fair rewards and the guest's own save

- Echoes and item drops are native and per player.
- **Progress journal** (`plugin_dir("seamless")/journal-<char>.txt`): the host sends `EventFlag{id, 1}` for a whitelist (boss defeat flags from `boss_ins.txt`'s events, lamp-lit flags, shortcut/door flags); the guest appends them with the host's name and time, and **applies them only when it is next in its own world** (`on_world_load` with role 0) via `event_flag_set`, with an F9 option `progress_sync = bosses+lamps | bosses | off` (default bosses+lamps). Never NPC/quest flags (ERSC's main complaint).
- Insight for a boss assist: verify it still arrives with the boss patches (session-end.md open question 2); if not, grant it from the plugin on the host's boss flag message.

### 3.4 Settings (F9) - sensible defaults

| Key | Default | Note |
|---|---|---|
| `password` | "" | text; empty = public seamless |
| `free_bells`, `anywhere` | on | existing |
| `stay_after_boss`, `keep_on_load`, `follow_host` | on | existing |
| `dream_mode` | `intermission` | `intermission | shared` (shared later) |
| `guest_death` | `resummon` | `resummon | spectate` (spectate later) |
| `progress_sync` | `bosses+lamps` | journal whitelist |
| `invaders` | off | block Sinister/invasion roles in seamless sessions |
| `banners` | on | `show_message` notifications |
| `follow_warning_seconds` | 3 | delay before guests follow |
| actions | Open my world / Join a world / Leave the party | |

### 3.5 No surprises

- Only the host moves the party; guests are never warped by anything but a host travel they were told about.
- Every automatic action is announced by a banner first.
- The ruleset carries the protocol version; a mismatch is reported in F9 instead of silently never matching.
- `anywhere` and `free_bells` only apply inside seamless sessions (they already do, by ruleset segregation).

---

## 4. Roadmap

Each milestone lists steps, addresses (BN), the test on the two-instance rig, verification, risks and "done".

### M0. Hygiene (half a day)

- Commit the branch `plugin/seamless` as it is (not pushed) so every later run is reproducible.
- Add a `BBHOST_SEAMLESS_TRACE=1` mode to the plugin: log-only hooks (section M1a) plus a per-frame line of `role, WSOM+0x84, GameStateMan+0x1592, routes, map` whenever any of them changes. This is the instrument for everything below.
- Teach `pair.ps1` to grep and print those lines in the verdict.

### M1. The blocker: the session ends on the host's lamp travel

**Goal**: know exactly which `BeginLeaveSession` caller fires on each side, and get one load with the room retained.

**M1a. Diagnose (2-3 runs).** `BbHookCtx` has no return address, so use *marker hooks + ablation*:
1. hook2 (log-only) on `SessionWorldTransition` `0x17cde30` (n=14): log role, `+0x1592`, WSOM `+0x84`, current map, and set `g_marker = "SWT"`.
2. hook2 on `BeginLeaveSession` `0x22d07a0` (n=17): log role, WSOM state, the marker and the frame counter; clear the marker. (Don't skip it yet.)
3. hook2 on the member-leave handler `0x1716640` (n=14), `OnMatchingError` `0x17809f0`, `OnIrregularLeaveSession_1` `0x17840e0` (n=16) and `DispatchNamed` `0x1739870` (n=20, log `rsi` = callback name): markers.
4. `OnMatchingCheck` `0x17808b0`: its prologue is 10 bytes (`55 48 89 e5 41 57 41 56 53 50`); disassemble the next instructions with `dis.sh 0x17808b0 0x17808d0` - if the first 14 bytes are whole and not rip-relative, hook2 it with `prologue = NULL`; otherwise mark it by ablation (step 6).
5. Correlate with bbhost's `np:` lines (`leave_room`, `room_closed`) and the server log timestamps.
6. **Ablation** (one change per run; our run budget allows it): (i) at the host's `on_warp_bonfire`, write `GameStateMan+0x1592 = 1` (GameStateMan = `*(0x5956678)`; BN of RVA 0x5556678) before the function runs; (ii) retarget the host map-change leave at `0x1d3c657` (`e8 44 41 59 00` -> `e8 44 3a 59 00`, call `0x22d00a0`, the same trick as `keep_on_load`); (iii) retarget `OnMatchingCheck`'s call at `0x17809b7` (`e8 e4 fd b4 00` -> `e8 e4 f6 b4 00`); (iv) widen the join-sync timeout (write float 300.0 at `0x59640e0` after init, or ablate the `ja` at `0x1b8a4ef`). Whichever change moves the first leave later tells us the caller.

Expected **[speculation]**: the first leave on the host is `SessionWorldTransition` (`+0x1592 == 0` for a host), which also closes the room for the guest (`member left`/`room_closed`); the guest's own `lamp_warp` then runs the same function.

**M1b. The phase-3 attempt (3-5 runs).** On both sides, in the lamp-travel hook when the session is ready:
1. host: send `HostLampTravel` (exists); then `call 0x1b8d9a0(wsom)` (WSOM `+0x84 = 3`, "transition snapshot phase"), write `+0x1592 = 1`, let the warp run; also retarget `0x1d3c657` for the host.
2. guest on receipt: same two writes, then `lamp_warp(rp)` (which calls `WarpNextStage_Bonfire` -> `SessionWorldTransition`).
3. after the load (`on_world_load`): check whether the game brought WSOM back to 4 by itself. The guest's rebuild step calls `SetSyncReadyAndBroadcast` `0x1b8d9c0` at `0x1d39497` (bbhost `WORLD_SESSION_OBJECT_MAN_SET_SYNC_READY_AND_BROADCAST_FN`) **[fact]**, presumably only in phase 3 **[inferred]**; the host's state 4 is normally written by `DispatchSessionEvent` `0x1b8bddb` on member events **[fact]** and may not re-fire - if the host stays at 3, call `0x1b8d9c0(wsom)` on the host once its player exists, and watch the guest's queued channel messages pop again.
4. If `OnMatchingCheck` fires ~9 s later as in shadp2p: first find its trigger (hook2 `DispatchSessionEvent`/the matching-event path and log the event id while the world rebuilds); if it is a re-delivered member event, defer it during the reload window; only as a last resort retarget `0x17809b7` (note the tail call to `0x19bed10` still resets tracked entities).
5. Do **not** call `SummonedMapReload` on the host (guest-only serializer; shadp2p: default world progression). On the guest, try the lamp travel first without it; if the guest's world state is wrong on arrival, try `0x1736b90` on the guest after the descriptor exists (shadp2p's working order: serializer after the target descriptor, then the phase setter).

**Verify**: both logs keep role 3/6 and 2 members after the load; pings resume (sender's state 4) within 60 s; `chr_list` on each side contains the other player (`player_of(route) != null`); no `np: ... leave_room`; the guest stands at the host's lamp; and a second lamp travel works too.

**Risks**: presentation-member bookkeeping (`WorldTransition_RemovePresentationMember` `0x19bc080`, the four-slot set `0x15bcd30/0x15bcf20`) may leave stale slots -> the lamp prompt vanishes (`+0x49` gate reads the slot count); world snapshots `+0x1690/+0x16b0/+0x16d0` applied to the wrong side; a stuck state 3 with no leave means silent desync - keep a 90 s watchdog that falls back to a clean leave + re-summon.

**Done**: either (a) 5/5 runs keep the session through the host's lamp travel with both characters visible, or (b) a written verdict naming the callers and why the host side cannot be kept, with the guest-side result recorded. Both outcomes unblock M2.

### M2. Fast auto re-summon (the fallback and the recovery path)

Steps:
1. Party record (both sides, in memory + `plugin_dir`): partner online name/identity (from the WSOM member list / `FrpgNetMan+0xa68` identity), password hash, last known lamp, role.
2. Guest: after any load while the party is "wanted" (`on_world_load`), wait until the SOS area is settled and the player exists (shadp2p: 20 stable ticks), then `call_guest(0x1cf9720, player, 205, 17)` (`UseItemNativeApply`, Small Resonant Bell) or `sp_effect_apply(NULL, 9005)` as `coop_lamp` does; retry every 20 s; stop when role becomes 6.
3. Host: same with the Beckoning Bell (`200`/SpEffect 9000) until role 3 with the partner present; re-ring after a failed/stalled attempt (`sessionReady 0`) at 20 s.
4. Server knobs for speed on our test server: `--ssinfo-set SummonDataGetListInterval=5` (and `SummonDataCreateInterval`); upstream: ask for the same defaults in seamless rulesets (section 5).
5. Guest filtering: only accept the recorded host (sign identity) while a party is wanted.
6. Banners for each stage.

**Test**: `pair.ps1 -Seamless -ReloadAfter 15 -HostOnlyReload` with `keep_on_load` off; measure "host warp -> both `role` set and `player_of` non-null". Target < 90 s, 5/5 runs; then with `-ReloadAfter` on the guest only (simulated guest crash: kill and restart the guest instance, expect automatic rejoin).

**Risks**: the first-summon flake (document its frequency with the shortened retry); double-ringing conflicts with the game's own bell state (SpEffect 9000 already active); bells in a boss arena with a live boss (`anywhere` allows the sign; the summon builder's live-boss checks remain native).

**Done**: no manual input at all from a host lamp travel to both standing together; guest crash auto-heals.

### M3. Host death and the boss, verified in game

1. Boss: with a save at Cleric Beast (m21 flag/entity `2100800`), both instances present; expect "5 patches in"; verify no leave, echoes and Insight on both, the host's lamp prompt still works afterwards, and the guest's next death still triggers `OnEvent_4000` (B3-B5 latches). Add B6 (`0x17857ac`) if peers mark the guest "left".
2. Host death: NOP `LuaCallStart(4000,1)` at `0x17816d7` (`e8 84 d2 fa ff` -> `0f 1f 44 00 00`) or hook2 `HostDead` `0x1781870` (n=16) and skip on guests; let `SoloPlayDeath_2` reload the host; then either M1's primitive (host reload in session) or M2's re-summon brings the guests. Clear latches on guests (flag 4047, `ctx+0x8d`, `EventState`).
3. Test: `player_stat_set("hp", 0)` on the host via a test hook, or walk into a drop; verify the guest never sees "returned to your world" and is back within the M2 budget.

**Done**: boss kill and host death both end with the party intact or re-formed automatically; rewards correct.

### M4. Guest death

v1: let the vanilla chain run; M2 brings the guest back to the host's lamp ("GuestHunter has fallen - returning"). v2 (after M1 proves the guest-side in-session reload): hook2 `PartyGhostDeath_1` `0x1782130` (n=14) and skip; keep the guest as a dead spectator; on the host's next lamp travel (or a guest "Revive" action), run the in-session reload for the guest, then clear flags 4000/4047, `EventState` (`*(0x593b0d8)`), `ctx+0x8d`; also NOP the 4063 broadcast at `0x178201b`. Test with `player_stat_set("hp", 0)` on the guest.

### M5. The Hunter's Dream (intermission mode)

1. Detect the host's Dream travel in `on_warp_bonfire` (ReturnPointParam rows `2102950/2102952...` -> m21) and send `HostToDream`; guests: banner, then vanilla leave (or `lamp_warp` to their own Dream row); party stays "wanted".
2. On the host's next non-Dream travel, M2/M1 reunite. Also handle the host's Dream *departure* when the guest is already waiting (the guest should be told the destination lamp and go there first, then ring).
3. Shared Dream (later, option `dream_mode = shared`): needs guest NPC talk and shop access as a phantom - DS2's dialogue-mask lesson says this is a separate project; keep it out of v1.

### M5b. Hunter's Dream co-op (shared Dream) - P2

Priority P2: after M1-M5 and M7, before the PR polish. M5's private intermission is the default, and the shared Dream is an option (`dream_mode = private | shared`, default private until this milestone is proven).

Goal: both hunters can be in **the host's Dream** together, so the hub feels like a shared home base rather than a place where co-op breaks.

1. **Admit the summon.** The Dream is SOS area 210000. Check which of the `anywhere` patches already admit it (the bell availability checks, `IS_SUMMON_AREA_ELIGIBLE`, the SOS area restriction). Then try the guest following the host's headstone/lamp travel into m21 with `keep_on_load` and the M1 fix.
2. **Phantom limits in the hub.**
   - A summoned guest can't talk to the Doll or Gehrman, shop at the Messengers, level up, or use the Workshop tools. This is DS2's dialogue-mask lesson, and ERSC tells players to leave co-op before going to its hub (the Roundtable Hold).
   - Find the phantom gate on the action and talk prompts. Hooks into the action updater (0x16f5c40) and the talk/ESD condition that checks ChrType or "is client" are the candidates (see session-end.md, item 7).
   - Lift the gate only for the guest's *own* menus, which run on the guest's save:
     - leveling uses the guest's echoes;
     - the shop uses the guest's inventory;
     - the Workshop (weapon upgrades, blood gems, runes) edits the guest's save.
   - Never touch the host's NPC state.
3. **Headstones.** A guest using a headstone is a travel request: send it to the host on the channel. The host either accepts, or it becomes an ERSC-style vote. Everyone then travels together through follow_host.
4. **Safety.** No event flags change in the Dream while role is 6, and no Doll/Gehrman dialogue progression for the guest. Their dialogue only reads state, but check the talk scripts for flag writes.
5. **Test.** Host and guest both in m21. The guest levels up, buys from the Messengers and upgrades a weapon. Check the guest's save afterwards (level, echoes, inventory), check that the host's save is unchanged apart from its own actions, and check that headstone travel takes both players.
6. **Done:** a shared Dream session survives the host's departure to a lamp. The guest's own progress persists, and no host flags change.

Risk: medium-high. The hub menus are main-thread UI driven by the local player's GameData, so the guest may already be editing its own save, but a phantom may be refused at the prompt level. If the prompts can't be lifted cleanly, keep `dream_mode = private`.

### M6. Progress journal (guest's own save)

1. Host: `on_event_flag` -> whitelist (boss defeat flags from the boss events in `boss_ins.txt`; lamp-lit flags per `bonfire_db.hpp` `LIT_FLAG_DELTA = 10`; shortcut flags later) -> `send_all(EventFlag{id, v})`.
2. Guest: append to the journal; on `on_world_load` with role 0 (own world), `event_flag_set` each pending id once, banner "Progress from HostHunter's world applied: Cleric Beast".
3. Test: kill the boss as guest, leave, load the guest's own world, verify the flag and that the boss fog is gone; verify nothing is written while role is 6.

### M7. Join UX (password, buttons, retries)

`BB_OPT_TEXT password`, `BB_OPT_ACTION` buttons with `on_action`, the ruleset update via `set_rules` (live), auto-ring loops from M2, status line via `show_message`. Verify on the local server that two different passwords never match and equal ones do.

### M8. Polish and settings; M9. PR

Banners for every transition; `invaders` block (refuse role 2/12 joins and Sinister bell in seamless sessions); the 3 s follow warning; docs (`docs/plugins.md` row, README lists, `tools/package_plugins.sh`); credit shadp2p for the offsets; "not tested on Linux"; keep `BB_PLUGIN_OPT_IN | BB_PLUGIN_GAMEPLAY`; strip the test-only env vars or document them.

---

## 5. Plugin API vs. upstream asks

**Doable with the API today**: all byte patches and retargets; prologue hooks on the functions listed in session-end.md section 7; `call_guest` for `UseItemNativeApply`, `SetTransitionSnapshotPhase`, `SetSyncReadyAndBroadcast`, the placement setters; the channel; `lamp_warp`, `player_warp`; `event_flag_*`, `on_event_flag`; `show_message`; F9 options/actions; `set_rules` with a password token; `plugin_dir` for the journal/party record.

**Propose upstream (bbhost)**:
1. `BbHookCtx.ret_addr` (or `rsp`) so a hook on `BeginLeaveSession` can name its caller - the single most useful RE tool for this plugin (M1a would be one run instead of six).
2. Fix `register_hle` being overwritten by bbhost's own table (`main.cpp` order, documented in channel.md section B) - independent bug.
3. A small overlay-drawing API (text at screen/world position) for overhead names, HP and ping; or at least `show_message` with a position/lane. Ping is available in bbhost's net layer (`net:` log lines) and could be exposed as `peer_info()`.
4. Session events to plugins: `on_session(event, member)` for joined/left/room_closed (bbhost already logs them as `np:` lines), so the plugin doesn't poll `SprjSessionManager`.
5. Config: `online.sign_timeout_seconds` already exists; add `online.summon_poll_seconds` (the ss.info `SummonDataGetListInterval`) so seamless re-summons are fast on the public server.
6. (Server, closed source) Private sessions by password token in the ruleset if the whole-string match doesn't already give that; a "friend invite" (`guest_invite` to a named online id, which the server already emits when a host claims a sign); the `MaxCoop`/invader counts per ruleset.
7. Widen the 60 s join-sync timeout for seamless rulesets (or let the plugin do it; it's a data write).

---

## 6. Risks and open questions (carry-over)

- Where cooperator Insight for a boss assist is granted (session-end.md q2) - verify in M3.
- `+0x1592` semantics: bbhost calls it `action_event_selector`; "SessionWorldTransition begins session leave only while this byte is zero" and "a guest keeps that byte zero to apply a host's snapshot" **[fact, bbhost headers]**; whether a *host* with it set to 1 still saves/transitions correctly is **[speculation]** until M1b.
- The host's lamp prompt with a guest present (`+0x49`, second gate unresolved by shadp2p) - without it the host can't travel from the UI; `lamp_warp` works around it only for tests. Needs its own RE item (patch-sites.md section 3).
- Memory: two instances only; 3-player tests need a bigger page file (CHECKPOINT) - 3-player behaviour (two guests, one lagging) stays untested before the PR; say so.
- The first-summon flake: measure and shorten the retry rather than hunt it now.

---

## 7. First three milestones in one paragraph each

**M1 - Resolve the blocker.** Instrument (`BBHOST_SEAMLESS_TRACE`), run the host-lamp scenario, read which marker precedes `BeginLeaveSession`, ablate the four candidate gates one per run (`+0x1592`, `0x1d3c657`, `0x17809b7`, the 60 s timer), then attempt the phase-3 reload on both sides (`0x1b8d9a0` + `+0x1592 = 1` before the warp, `0x1b8d9c0` after the load if the state stays 3). Done when 5/5 runs keep the room with both characters visible, or when the host side is proven unkeepable and the guest side recorded.

**M2 - Fast auto re-summon.** Party record + auto bells (`UseItemNativeApply(205,17)` / SpEffect 9000) + 20 s retries + fast polling on the local server + banners. Done when a host lamp travel reunites the pair in < 90 s with no input, and a killed-and-restarted guest rejoins by itself.

**M3 - Boss and host death in game.** Verify `stay_after_boss` (rewards, latches, lamp prompt), then keep guests through host death (`0x17816d7` NOP or `HostDead` skip) with the M1/M2 reunite. Done when neither event shows "returned to your world" and rewards are correct on both sides.

---

## 8. Sources

Local (ground truth): `coop-test/CHECKPOINT.md`; `bbhost-src/plugins/seamless/seamless.cpp`; `coop-test/re/patch-sites.md`; `coop-test/re/session-end/session-end.md`; `coop-test/re/channel/channel.md`; `coop-test/re/shadp2p/sp_documents_bloodborne-seamless-re.md`; `bbhost-src/docs/plugins.md`, `docs/online.md`, `include/bbhost_plugin.h`, `include/bbhost/engine/sprj/network_script_state.hpp`, `.../world_session_object_man.hpp`, `.../frpg/bonfire_db.hpp`, `src/engine/summon_invite.cpp`; `coop-test/runs/20261010-230118/*/bbhost.log`.

Web:
- ERSC mod page: https://www.nexusmods.com/eldenring/mods/510 (403 to the fetcher; description via https://www.gamepressure.com/download.asp?ID=81387)
- ERSC changelog thread (Steam): https://steamcommunity.com/app/1245620/discussions/0/4406291166503639205 (pages ?ctp=13, ?ctp=20)
- ERSC docs: https://ersc-docs.github.io/how-to-install-and-update/ , https://ersc-docs.github.io/faq/ , https://ersc-docs.github.io/workarounds/
- ERSC GitHub mirror: https://github.com/LukeYui/EldenRingSeamlessCoopRelease
- ERSC spectate/rot/grace rules (press): https://wccftech.com/elden-ring-seamless-co-op-mod/ , https://exputer.com/guides/elden-ring-seamless-co-op-mod/
- ERSC complaints: https://steamcommunity.com/app/1245620/discussions/0/4406291673455092945/ , https://steamcommunity.com/app/1245620/discussions/0/4406291470272639010 , https://redlib.ssps.io/r/EldenRingMods/comments/1l1li4f/seamless_coop_mod_really_buggy
- DS3 Seamless guide: https://steamcommunity.com/sharedfiles/filedetails/?id=3434634411 ; mod: https://www.nexusmods.com/darksouls3/mods/1895
- DSR Seamless: https://www.nexusmods.com/darksoulsremastered/mods/899 ; Patreon posts https://www.patreon.com/LukeYui/posts/announcement-co-124173792 , https://www.patreon.com/LukeYui/posts/update-dark-co-125449564 (403 to the fetcher)
- Nightreign Seamless: https://www.nexusmods.com/eldenringnightreign/mods/3 ; https://theouterhaven.net/modder-lukeyui-adds-duo-support-to-elden-ring-nightreign-on-pc
- Sekiro Online: https://www.siliconera.com/sekiro-online-mod-adds-co-op-and-pvp-on-pcs/ , https://kotaku.com/sekiro-multiplayer-mod-lets-friends-die-horribly-toget-1843265922
- AC6 co-op: https://automaton-media.com/en/news/20231122-23218/ , https://wccftech.com/armored-core-6-co-op-mod/amp/
- DS2 clones: https://github.com/scheissgeist/Seamless ; https://github.com/Restezzz/Seamless-DarkSouls2 ; https://raw.githubusercontent.com/Restezzz/Seamless-DarkSouls2/main/docs/DS2_INTERNALS.md
- shadp2p: https://github.com/Wozzardman/shadp2p (issues #1, #2; `documents/bloodborne-seamless-re.md`; last commit 2026-08-16 "feat(bloodborne): add native seamless cross-map co-op handoff")
- bbhost issue #31: https://github.com/droogie/bbhost/issues/31
- ds3os: https://github.com/mengwei1/ds3os (mirror), https://github.com/goblock2021/ds3os (mirror)
