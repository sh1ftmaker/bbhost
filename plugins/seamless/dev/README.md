# coop-test: a local Bloodborne co-op test server for bbhost

These tools run a vanilla co-op summon between two bbhost instances on this PC, against a private server that only listens on 127.0.0.1. Nothing here contacts thehuntersdream.com or any other outside host.

Proof run: `runs\20261010-184255\` (vanilla, no plugins except the test helper). The verdict lines are:

```
host:  [bbhost] np: member 2 (GuestHunter) joined room 1
guest: [bbhost] np: joined room 1 as member 2 with 1 peers
```

`runs\20261010-183508\shot-21716.png` is a screenshot of the host's window. It shows the message "The bell's resonance has beckoned GuestHunter." and the blue "Cooperator GuestHunter" phantom in Central Yharnam.

## Running it

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\shift\Desktop\Bloodborne\coop-test\pair.ps1
```

The script does the following:

1. Starts `server\server.py`, logging to `runs\<timestamp>\server.log`.
2. Starts the host instance, HostHunter, on P2P port 9307. Once the host is healthy, it starts the guest instance, GuestHunter, on port 9308. Each instance has:
   - its own `BBHOST_CONFIG_DIR` (`runs\<ts>\<role>\cfg\bbhost.toml`);
   - its own data folder, `runs\instances\<role>\data`. The folder is kept between runs so the shader caches survive. The saves are copied in fresh each run from `saves\saves\SPRJ0005` (userdata0000, userdata0010, sce_param.bin, icon0.png).
3. Drives the title screen with `BBHOST_AUTOPRESS`: any button, Down ×4, **Log In**, then Continue. The game starts offline, and "Log In" is what fetches ss.info and logs in.
4. Takes each character out of the Hunter's Dream to Central Yharnam's lamp, using the test plugin `plugin\coop_lamp.dll` and `BBHOST_TEST_WARP=1`.
5. Rings the bells:
   - The guest's Small Resonant Bell (goods 205, SpEffect 9005) rings 30 s after it reaches the lamp.
   - The host's Beckoning Bell (goods 200, SpEffect 9000) rings 60 s after.
   
   The plugin applies the goods row's refId SpEffect to the player, which is what using the item does. `BBHOST_TEST_INSIGHT=5` provides the Insight.
6. Stops everything 60 s after both logs show the join, writes `verdict.txt`, and kills only the PIDs it started.

The whole run takes about 4–5 minutes.

### Useful switches

| Switch | Effect |
|---|---|
| `-Seconds N` | Overall limit. `-KeepRunning` stays up for the full N seconds instead of stopping 60 s after the join. |
| `-Only host` / `-Only guest` | Start a single instance. |
| `-Third` | Add a second guest (GuestHunter2, port 9309). **Untested.** Not enough commit charge was free here (see below). |
| `-GuestRenders` | The guest renders fully. By default the guest runs with `BBHOST_RENDER_MIN=1`, so its window is black. |
| `-Seamless` | Copies `build\seamless.dll` into every instance and sets `[plugins] seamless = true`. Co-op worked with it as well (run 20261010-183508). |
| `-SummonInvite` | Sets `BBHOST_SUMMON_INVITE=1` on the guest, so bbhost hands the game the server's guest_invite as its own type-1 item. Not needed; the vanilla P2P path works. |
| `-HostBell 0 -GuestBell 0 -HostTest host:90 -GuestTest guest:30` | Ring through BBHOST_NP_TEST's Lua events instead. **This does not post a sign**: the Lua events ran, but no summon_messenger/create followed. Use the SpEffect bells. |
| `-LampRp <row>` | ReturnPointParam row to travel to. 0 means stay in the Dream. |
| `-MinCommitGB N` | Free commit charge required before each launch. The default is 11; 9 worked here. |
| `-ServerArgs "..."` | Extra arguments for server.py, e.g. `--any-area`, `--relay`, `--summon-poll 5`, `--no-ruleset-match`, `-v`. |

### Memory: why a window can go black

Each bbhost maps a fixed 6 GiB direct-memory section (`kDmemBaseSize`) and commits about 4–5 GiB more, so each instance needs about 10 GiB of commit charge.

If Windows runs out of commit, the instance logs `CreateFileMapping dmem 0x180000000 failed: 1450`, then `gpu: mirror mapping of dmem failed` and `shared GPU device unavailable`. It then never presents a frame, so the window stays black.

`pair.ps1` checks free commit before each launch and aborts if a log shows those lines. The abort is written to `verdict.txt` as `ABORTED: ...`.

On this PC the commit limit was 36 GB. With Chrome and Steam closed about 21 GB was free: enough for two instances, but not three. A third instance needs more programs closed or a larger pagefile.

## The server (`server\server.py`, Python 3.12 stdlib)

```
python server\server.py [--log FILE] [-v] [-q] [--any-area] [--relay] [--available-patch]
                        [--summon-poll S] [--sign-refresh S] [--no-ruleset-match]
                        [--invite-ttl S] [--invite-same-area] [--ssinfo-set Name=v,...]
```

| Option | Effect |
|---|---|
| `--summon-poll S` | Puts `<SummonDataGetListInterval{i}>S` in ss.info: how often a ringing bell searches for signs. The game's default is 60 (seconds; the parser stores 60 in the field when the tag is absent). Use 5 for fast re-summons. |
| `--sign-refresh S` | Puts `<SummonDataCreateInterval{i}>S` in ss.info: how often a sign is re-sent (default 30). The guest's game reads the host's claim from its next create, so this also bounds that path. |
| `--no-ruleset-match` | Turns ruleset segregation off: everyone matches everyone (the old behaviour). |
| `--invite-ttl S` | How long a friend invite or party record lives without use (default 1800 s; every match, claim or join refreshes it). |
| `--invite-same-area` | Invited pairs must also share AreaId/AreaRegionId. By default an invite matches anywhere. |
| `--ssinfo-set Name=v,...` | Any other ss.info tuning tag. It wins over `--summon-poll`/`--sign-refresh`. |

The server provides:

- **HTTP on 127.0.0.1:18671**: the game's API and bbhost's NP session service.
- **HTTP on 127.0.0.1:20443**: the same handler. ss.info is served here.
- **STUN on UDP 127.0.0.1:3478**: MAPPED-ADDRESS and XOR-MAPPED-ADDRESS. bbhost's relay (HELLO/frames) is available with `--relay`, but it is not needed on one PC.

Every request and response is logged with its body. Event polls, acks and heartbeats are only logged with `-v`, except polls that deliver an event.

`server\selftest.py` runs bbhost's own simulated client (`bbhost-src\tools\nettest\simclient.py`) against a private copy of the server on spare ports. It covers login, signs, summon, a 3-way P2P mesh, a leave, a rejoin and the host leaving (the original 12 checks), then the seamless matching below: `--summon-poll` in ss.info, ruleset segregation (v=2 vs v=1 vs vanilla), passwords (same matches, different or none does not, also on `join_room`), and a friend invite (exclusive sign search across areas, the `seamless_invite` event, the summon, the party record, cancel). 28/28 checks pass.

### Seamless matching (rulesets, private sessions, friend invites)

**Ruleset segregation.** bbhost sends `X-BBHost-Ruleset` (and `X-BBHost-Adopt`) on the game's requests and on its NP calls. The value is `vanilla`, or the gameplay plugins' entries sorted and joined by `,` (`host/plugins.cpp` `own_ruleset`), e.g. `seamless;v=1;free;anywhere;boss;keep`. The server now:

- stores the ruleset with every sign (`summon_messenger/create`) and every room (`create_room`);
- shows a sign to a searcher (`summon_messenger/get`), lets a host claim it (`summon_messenger/request`, which is what sends `guest_invite`), and lets a player into a room (`join_room`, `ResKind 1` "ruleset mismatch: ...") only when the two rulesets are compatible.

Compatible means the same plugin entries, element for element (vanilla with vanilla), as docs/online.md says the upstream server does. The entries of plugins named in the visitor's `X-BBHost-Adopt` are left out, as bbhost's `plugins_enter_world` does. Every decision is logged: `sign search by user N (Name, ruleset '...'): k found [...]; skipped ['user M: ruleset: rules differ (...)', ...]` and `np: X join room R: ruleset '...' vs room '...': ok|refused, <reason>`.

**Private sessions.** A `pw=<token>` element inside a plugin's entry (`seamless;v=2;pw=ab12cd34ef56;free;...`) must be equal on both sides; it is checked even for adopted plugins. So the same password matches, a different password does not ("password differs"), and private does not match public ("private (pw) vs public"). No `pw` means public within the ruleset. The log prints the rules without the token.

**Friend invites.** The stock client never calls these endpoints; a plugin (it can use WinHTTP) or `curl` does:

| Call | What |
|---|---|
| `POST /seamless/invite {"From": "<my online id>", "To": "<friend's online id>"}` | Records the invite. `From` can be replaced by the game login's `UserId` or `SessionId`. The request's `X-BBHost-Ruleset` is recorded. It pushes the NP event `seamless_invite` {From, To, Ruleset, State} to the friend. bbhost logs it as `np: event #N seamless_invite` and otherwise ignores it. The event is pushed again when the friend's bbhost comes online (`context_start`). |
| `POST /seamless/invite/cancel {"From": ...[, "To": ...]}` or `{"Decline": "<my online id>"}` | Removes the inviter's invites (or one of them), or every invite to the decliner. |
| `GET /seamless/invites[?online=X]` | Lists them: `from`, `to`, `state` (`pending`, `claimed`, `joined`), `rules`, `age`. `GET /status` shows them too. |

While an invite exists:

- the inviter's sign search returns **only** the invited players' signs, in any area unless `--invite-same-area` is set;
- the invited player's sign is **kept** for its inviters: other hosts don't see it, and their requests for it are refused.

Rulesets still have to be compatible: an invite narrows matching, it never widens it. So the stock bbhost flow does the rest. The host's game auto-picks the only sign it is given, claims it (the server logs `invite A -> B: claimed` and sends `guest_invite`), and the guest joins (`invite A -> B: joined`). The record then stays as a **party**: every re-summon after a lamp travel, death or crash pairs the same two players again, until it is cancelled or unused for `--invite-ttl`.

What the server does and what the plugin must do:

| Server (done here) | Plugin (to do) |
|---|---|
| Ruleset and password segregation of signs, claims and rooms | Put `pw=<sha256(password)[:12]>` (lowercase hex) into its `set_rules` entry |
| Invite/party records, exclusive sign search, kept signs, `seamless_invite` event | Send `POST /seamless/invite` (F9 "Invite <name>"), or have a tool or player send it |
| `guest_invite` to the guest when the host claims (unchanged) | **Ring both bells automatically**: the host's Beckoning Bell (SpEffect 9000) and the guest's Small Resonant Bell (goods 205 / SpEffect 9005). The guest's game needs its own passive entry, a rung bell, before bbhost's `summon_invite_deliver` or the P2P type-1 item can be accepted, and the host's game only searches while its bell rings. Neither can come from the server. |
| `--summon-poll` / `--sign-refresh` for fast searches | Re-ring after a failed attempt or a load while the party record exists; cancel the invite when the party ends |

Not verified in game yet: whether the game itself filters search results by area client-side, which matters for invites across areas, and whether it clamps small `SummonDataGetListInterval` values.

### Protocol notes (found by RE of the 1.09 eboot and the logs)

**ss.info**

- The game fetches `GET https://ss4.scej-network.jp:20443/bb-us/ss.info`. bbhost rewrites this to `http://127.0.0.1:20443/...`.
- **The body is base64.** The game base64-decodes it (eboot `sub_1ea92e0`) before parsing (`sub_1eb65e0`). Plain XML gives "Failed to resolve Bloodborne server status".
- The decoded text is tag soup:
  - `<ss>0</ss>`: the status, a bare number.
  - Optional `<msgN>`/`<langN>`.
  - `<gameurl{i}>`: one `<api_X>BASE</api_X>` for **all 37 APIs**. `i` is FrpgNetMan+0x9e8, which was 1 here; blocks 0..7 are written. The game appends its own path (`%s/basic_utils/login`, …). The tag tables are at 0x5350ca0/0x5350dd0.
  - Optional numeric tuning tags such as `<SummonDataCreateInterval{i}>`. Without them the game uses its defaults. `--ssinfo-set` adds them. The full list is in the eboot next to the `<gameurl%u>` string. For signs it includes `SummonDataGetListInterval`, `SummonDataCreateInterval`, `SummonDataGetListGuardTimer`, `SummonDataGetListGetMaxCount` and the `SummonDataCoopMatchingLevel{Lower,Upper}{Rel,Abs}` tags. The parser (BN 0x22b8180..) stores CreateInterval at +0x89c and GetListInterval at +0x8a0 of its object, with defaults 30 (0x1e) and 60 (0x3c), stored just before each tag is parsed.

**API calls**

- All calls are `POST <base><path>?user_id=N` with a JSON body carrying `MessageId: "XxxRequest"`.
- Every answer needs `ResKind: 0` (a number) and HTTP 200. The handler switch is the jump table at 0x1e896f4, indexed by API number.
- **Login** (`/basic_utils/login`):
  - The request carries `PlatformAccountId` = the bbhost online id, `AuthorizationCode` "DUMMY", and `NatType` (which is actually an IPv4 address).
  - The answer is `SessionId` (a string), `UserId` (a number), `UserStatus`, `ServerVersion` (shown as "Server ver.") and `WarningMessage`.
- **`/basic_utils/sync_chara_id`** must answer `PublishCharacterIdList: [{"PublishCharaId": n}, …]` with CharaIdNum entries. Otherwise the game shows "Failed to reset online character data to default".
- **List answers** must contain their arrays, even empty. Otherwise the request fails, e.g. "Failed to record note search information":
  - `/blood_messenger/exist_messages`: `BloodMessEvaluationList` and `LostBloodMessIdList`
  - `/blood_messenger/evaluation`: `BloodMessEvaluationList`
  - `/blood_messenger/message_area`: `BloodMessList`
  - `/tomb_messenger/message_area`: `TombMessList`
  - `/wandering_ghost/get`: `WanderingGhostList`
  - `/chair_messenger/get`: `ChairMessList`
  - notices: `NoticeList`
  - `/channel/get_info`: `ChannelInfoList`
  - `/summon_messenger/get`: `SummonDataList`
- **Signs:**
  - `/summon_messenger/create` advertises (the game repeats it about every 60 s).
  - `/summon_messenger/get` returns matching signs as advertised: same AreaId/AreaRegionId (unless `--any-area` or an invite), a requested SummonType, not your own, a compatible ruleset, and the invite rules above.
  - `/summon_messenger/request` claims a sign by `TargetUserId`.
  - `/summon_messenger/delete` consumes it.
  - The create, request and delete answers carry no data (their handlers read only ResKind).
- **Do not alter `SummonData`.** shadnet-p2p sets byte 0x79 to 1 in search results. Here that run's summon never completed, while the untouched blob worked first time. The patch is kept behind `--available-patch`.
- **NP session service.** These are bbhost's own calls:
  - `/mp/matching2/context_start`, `create_room`, `join_room` (answer has `Members`), `leave_room`, `heartbeat` (`InRoom`), `kick_member` and `signaling_update`
  - `/np/signaling/resolve`
  - `/np/events/poll` and `/np/events/ack`. Event fields are sent both at the top level (what bbhost reads) and in `Payload` (what simclient reads).
  - Events: `room_member_joined`, `room_member_left`, `room_closed`, `room_member_kicked`, and `guest_invite`. `guest_invite` is sent to the summoned player when the host claims its sign; the UserId is mapped back to the online id through the login's PlatformAccountId.
- **The vanilla summon:**
  1. guest create
  2. host get
  3. host CreateJoinRoom
  4. host request
  5. both resolve and activate NpSignaling
  6. the host's game sends its type-1 item over P2P vport 40
  7. guest JoinRoom
  8. room_member_joined reaches the host.
- **Accounts:** `require_account = false` keeps bbhost signed in without an account (`sceNpGetState` = 2), so no `/auth/*` calls happen. The server answers them with ResKind 1.
- The play-log PUTs are answered 200 and discarded.

## Files

| Path | What |
|---|---|
| `server\server.py`, `server\selftest.py` | The server and its simclient self-test. |
| `pair.ps1` | The launcher (server and instances, logs, verdict). |
| `shot.ps1 -ProcessId N -Out x.png` | Screenshot of an instance's window. |
| `plugin\coop_lamp.c` / `.dll` | Test plugin: Dream → lamp travel, bell SpEffects, SpEffect logging. Build with `clang --target=x86_64-w64-mingw32 -shared -O2 -I..\bbhost-src\include -o coop_lamp.dll coop_lamp.c`. |
| `build\seamless.dll` | The seamless plugin build, used only with `-Seamless`. |
| `runs\<ts>\` | `server.log`, `<role>\bbhost.log`, `<role>\env.txt`, `<role>\cfg\bbhost.toml`, `verdict.txt`. |

## Known gaps

- The first summon attempt in some runs did not complete. The guest's passive entry stayed `sessionReady 0`. The host's game gives up after about 55 s, leaves the room, and its next attempt succeeded. Both of those runs had `-Seamless`; one also had the 0x79 patch. The clean vanilla run joined at the first attempt.
- Three instances have not been tried (commit charge).
- The guest renders at minimum by default to save GPU and commit.
