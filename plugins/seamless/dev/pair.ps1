<#
.SYNOPSIS
  Local co-op test: starts the private test server and two (or three) bbhost
  instances against it, collects every log under runs\<timestamp>\.

.DESCRIPTION
  server\server.py serves the game's API, bbhost's NP session service and
  STUN on 127.0.0.1 only. Each instance gets its own BBHOST_CONFIG_DIR
  (runs\<timestamp>\<role>\cfg\bbhost.toml), its own online id and P2P port,
  and a data folder under runs\instances\<role>\data that is kept between
  runs (shader caches), with the save files copied in fresh every run from
  -SaveDir. Neither the user's own config (%APPDATA%\bbhost) nor the real
  saves are touched.

  The bells: BBHOST_NP_TEST makes bbhost dispatch the bell's Lua event
  itself (host: OnEvent_Call_SOS = Beckoning Bell, guest:
  OnEvent_SendSoulSign_NormalCoop = Small Resonant Bell) a set time after
  the world is up, and BBHOST_TEST_INSIGHT gives the character the Insight
  the bells cost. The guest rings first (its sign must be on the server
  when the host searches).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File pair.ps1
  powershell -ExecutionPolicy Bypass -File pair.ps1 -Seconds 600 -Third
  powershell -ExecutionPolicy Bypass -File pair.ps1 -Only host -Seconds 120
#>
param(
    [int]$Seconds = 300,                 # kill everything after this long at most (0 = wait for Enter)
    [string]$Only = "",                  # "host" or "guest": start just that one
    [switch]$Third,                      # also a second guest (GuestHunter2)
    [string]$Bbhost = "C:\Users\shift\Desktop\Bloodborne\bbhost-win-v0.2.18\bbhost.exe",
    [string]$App0 = "C:/Users/shift/Desktop/Bloodborne/games/CUSA00900",
    [string]$Eboot = "C:/Users/shift/Desktop/Bloodborne/games/eboot-109-decrypted.elf",
    [string]$SaveDir = "C:\Users\shift\Desktop\Bloodborne\saves\saves\SPRJ0005",
    [string]$Python = "python",
    # Title (60 fps, intro skipped): any button, Down x4 to "Log In" (the
    # game starts offline; Log In fetches ss.info and logs in), then Continue.
    [string]$TitlePress = "8:cross,11:down:150,12:down:150,13:down:150,14:down:150,15.5:cross:150,24:cross:150",
    # Extra presses after the title, per role (BBHOST_AUTOPRESS syntax, appended).
    [string]$HostPress = "",
    [string]$GuestPress = "40:cross:150,43:circle:150",  # a second Continue for a slow title (in the world: a menu opened and closed)
    # BBHOST_NP_TEST per role: "<role>:<seconds after the world is up>".
    [string]$HostTest = "probe",
    [string]$GuestTest = "probe",
    [int]$Insight = 5,
    # ReturnPointParam row of the lamp the coop_lamp plugin travels to from the
    # Hunter's Dream (2412951: Central Yharnam's lamp); 0 = stay.
    [int]$LampRp = 2412951,
    [string]$ServerArgs = "",
    [switch]$GuestRenders,               # the guest renders fully (default: BBHOST_RENDER_MIN=1)
    [switch]$KeepRunning,                # do not stop 60 s after the join; run the full -Seconds
    [double]$MinCommitGB = 11,           # free commit charge each launch needs (0 = no check)
    [switch]$Seamless,                   # also load build\seamless.dll ([plugins] seamless = true) in every instance
    # Ring by SpEffect (coop_lamp plugin) instead of / besides BBHOST_NP_TEST:
    # the EquipParamGoods id whose refId SpEffect is applied, per role (0 = off).
    [int]$HostBell = 200,                 # Beckoning Bell (refId SpEffect 9000)
    [int]$GuestBell = 205,                # Small Resonant Bell (SpEffect 9005)
    [double]$BellAt = 3,                 # guest: seconds after reaching the lamp
    [double]$HostBellAt = 3,              # host: after reaching the lamp, once the guest's sign is up
    [int]$HoldAfterJoin = 15,            # without -ReloadAfter: stop this long after both report the join
    [double]$LampDelay = 2,              # seconds in the Dream before the lamp travel
    [int]$ReloadAfter = 0,                # both: lamp travel again this many s after the other player shows up
    [int]$Loads = 1,                     # with -ReloadAfter: that many lamp travels in a row (COOP_LOADS)
    [switch]$Trace,                      # BBHOST_SEAMLESS_TRACE=1: log who calls BeginLeaveSession / SessionWorldTransition
    [switch]$Ping,                       # BBHOST_SEAMLESS_PING=1: the seamless plugin's channel test
    [switch]$HostOnlyReload,             # only the host reloads (-ReloadAfter); the guest follows it or not
    [switch]$SummonInvite                # guest: BBHOST_SUMMON_INVITE=1 (bbhost hands the game the server's guest_invite as its type-1 item)
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Run = Join-Path $Root "runs\$Stamp"
New-Item -ItemType Directory -Force $Run | Out-Null
Write-Host "run folder: $Run"

function Wait-Port([int]$Port, [int]$TimeoutSec = 15) {
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $TimeoutSec) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect("127.0.0.1", $Port); $c.Close(); return $true
        } catch { Start-Sleep -Milliseconds 300 }
    }
    return $false
}

# ---- the server -------------------------------------------------------------
foreach ($p in 18671, 20443) {
    $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($busy) { throw "port $p is already in use (pid $($busy[0].OwningProcess)); stop the other server first" }
}
$srvArgs = @("-u", "`"$Root\server\server.py`"", "--log", "`"$Run\server.log`"", "-q")
if ($ServerArgs) { $srvArgs += $ServerArgs.Split(" ") }
$srv = Start-Process -FilePath $Python -ArgumentList $srvArgs -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$Run\server-console.log" -RedirectStandardError "$Run\server-stderr.log"
if (-not (Wait-Port 18671)) { throw "the server did not start; see $Run\server-stderr.log" }
Write-Host "server pid $($srv.Id) up on 127.0.0.1:18671/20443, STUN 127.0.0.1:3478"

# ---- the instances ----------------------------------------------------------
function New-Instance([string]$Role, [string]$OnlineId, [int]$P2pPort) {
    $dir = Join-Path $Run $Role
    $cfg = Join-Path $dir "cfg"
    $data = Join-Path $Root "runs\instances\$Role\data"
    $saves = Join-Path $data "saves\SPRJ0005"
    New-Item -ItemType Directory -Force $cfg, $saves | Out-Null
    foreach ($f in "userdata0000", "userdata0010", "sce_param.bin", "icon0.png") {
        Copy-Item -Force (Join-Path $SaveDir $f) $saves
    }
    # The test plugin that takes the character out of the Hunter's Dream.
    $plugins = Join-Path $data "plugins"
    New-Item -ItemType Directory -Force $plugins | Out-Null
    Copy-Item -Force (Join-Path $Root "plugin\coop_lamp.dll") $plugins
    if ($Seamless) { Copy-Item -Force (Join-Path $Root "build\seamless.dll") $plugins }
    else { Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $plugins "seamless.dll") }
    $pluginToml = if ($Seamless) { "`n[plugins]`nseamless = true`n" } else { "" }
    $dataFwd = $data -replace "\\", "/"
    @"
# Written by coop-test\pair.ps1 for one test instance ($Role). Only this
# instance reads it (BBHOST_CONFIG_DIR).
[paths]
app0 = "$App0"
eboot = "$Eboot"
data = "$dataFwd"

[online]
host = "127.0.0.1"
scheme = "http"
verify_tls = false
require_account = false
online_id = "$OnlineId"
np_server = "http://127.0.0.1:18671"
auth_server = "http://127.0.0.1:18671"
stun_server = "127.0.0.1:3478"
p2p_addr = "127.0.0.1"
p2p_port = $P2pPort

[player]
name = "$OnlineId"
ime = "auto"

[video]
width = 1280
height = 720
window_mode = "Windowed"
obs_capture = "off"

[startup]
skip_intro = true
setup_window = false

[update]
check = false

[bbhost]
config_version = 4
$pluginToml
"@ | Set-Content -Encoding ascii (Join-Path $cfg "bbhost.toml")
    return @{ Role = $Role; Dir = $dir; Cfg = $cfg; Online = $OnlineId }
}

$common = @{
    BBHOST_SETUP_WINDOW = "0"; BBHOST_SKIP_INTRO = "1"; BBHOST_PRESENT_MODE = "fifo";
    BBHOST_NP_TRACE = "1"; BBHOST_TEST_INSIGHT = "$Insight"; BBHOST_GAME_FPS = "60";
    BBHOST_TEST_WARP = "1"; BBHOST_TEST_UNLOCK_LAMPS = "1"; COOP_LAMP_RP = "$LampRp"; COOP_RELOAD_AFTER = "$ReloadAfter"; COOP_LOADS = "$Loads"; COOP_LAMP_DELAY = "$LampDelay"; BBHOST_SEAMLESS_PING = $(if ($Ping) { "1" } else { "0" }); BBHOST_SEAMLESS_TRACE = $(if ($Trace) { "1" } else { "0" })
}

function Start-Instance($inst, [hashtable]$envs) {
    $saved = @{}
    $all = $common.Clone()
    foreach ($k in $envs.Keys) { $all[$k] = $envs[$k] }
    $all["BBHOST_CONFIG_DIR"] = $inst.Cfg
    foreach ($k in $all.Keys) {
        $saved[$k] = [Environment]::GetEnvironmentVariable($k, "Process")
        [Environment]::SetEnvironmentVariable($k, $all[$k], "Process")
    }
    ($all.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }) |
        Set-Content -Encoding ascii (Join-Path $inst.Dir "env.txt")
    $p = Start-Process -FilePath $Bbhost -WorkingDirectory $inst.Dir -PassThru `
        -RedirectStandardError (Join-Path $inst.Dir "bbhost.log") -RedirectStandardOutput (Join-Path $inst.Dir "stdout.log")
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], "Process") }
    Write-Host "$($inst.Role) ($($inst.Online)) pid $($p.Id): $($inst.Dir)\bbhost.log"
    return $p
}

function Press([string]$extra) { if ($extra) { "$TitlePress,$extra" } else { $TitlePress } }

# ---- commit charge: each bbhost maps a fixed 6 GiB "direct memory" section
# (kDmemBaseSize) and commits ~4-5 GiB more. When Windows has no commit
# left, CreateFileMapping fails with 1450, the GPU path is never set up and
# the window stays black - so check before each launch and watch the log.
function Get-FreeCommitGB {
    $o = Get-CimInstance Win32_OperatingSystem
    return [math]::Round($o.FreeVirtualMemory / 1MB, 1)
}
# Any error after this point: stop what this script started, never leave
# instances behind.
trap {
    Write-Host "error: $_"
    foreach ($q in $procs) { if ($q -and -not $q.HasExited) { & taskkill /PID $q.Id /T /F | Out-Null } }
    if ($srv -and -not $srv.HasExited) { & taskkill /PID $srv.Id /T /F | Out-Null }
    "ABORTED: $_" | Out-File -Append -FilePath "$Runerdict.txt"
    exit 3
}
function Stop-All([string]$why) {
    foreach ($q in $procs) { if (-not $q.HasExited) { & taskkill /PID $q.Id /T /F | Out-Null } }
    if (-not $srv.HasExited) { & taskkill /PID $srv.Id /T /F | Out-Null }
    "ABORTED: $why" | Tee-Object -FilePath "$Run\verdict.txt"
    exit 2
}
function Assert-Commit([string]$role) {
    $free = Get-FreeCommitGB
    Write-Host "free commit before starting ${role}: $free GB (need about $MinCommitGB)"
    if ($free -lt $MinCommitGB) {
        Stop-All ("only $free GB of commit charge free before starting $role (each instance needs about " +
            "$MinCommitGB GB: a 6 GiB direct-memory section plus the rest). Close other programs or give " +
            "Windows a larger pagefile, then run again. -MinCommitGB 0 skips this check.")
    }
}
function Wait-Healthy($inst, $p, [int]$Sec = 45) {
    $log = Join-Path $inst.Dir "bbhost.log"
    $bad = "CreateFileMapping dmem .* failed|mirror mapping of dmem failed|shared GPU device unavailable|dmem mirror view failed"
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $Sec) {
        Start-Sleep -Seconds 2
        $hit = Select-String -Path $log -Pattern $bad -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) {
            Stop-All ("$($inst.Role): '" + $hit.Line.Trim() + "' - Windows is out of commit charge (free now: " +
                "$(Get-FreeCommitGB) GB); this instance would run without its GPU path (black window). Close " +
                "other programs or enlarge the pagefile.")
        }
        if ($p.HasExited) { Stop-All "$($inst.Role) exited during start (code $($p.ExitCode)); see $log" }
        if (Select-String -Path $log -Pattern "present: |gpu: dmem chunk 0:" -Quiet -ErrorAction SilentlyContinue) { return }
    }
}

$procs = @()
$guestEnv = @{ BBHOST_NP_TEST = $GuestTest; BBHOST_AUTOPRESS = (Press $GuestPress); SDL_AUDIO_DRIVER = "dummy";
    COOP_BELL = "$GuestBell"; COOP_BELL_AT = "$BellAt" }
if ($HostOnlyReload) { $guestEnv["COOP_RELOAD_AFTER"] = "0" }
if ($SummonInvite) { $guestEnv["BBHOST_SUMMON_INVITE"] = "1" }
if (-not $GuestRenders) { $guestEnv["BBHOST_RENDER_MIN"] = "1" }
# The host first: it renders, so it should get the memory first.
if ($Only -ne "guest") {
    if ($MinCommitGB -gt 0) { Assert-Commit "host" }
    $h = New-Instance "host" "HostHunter" 9307
    $hp = Start-Instance $h @{ BBHOST_NP_TEST = $HostTest; BBHOST_AUTOPRESS = (Press $HostPress);
        COOP_BELL = "$HostBell"; COOP_BELL_AT = "$HostBellAt"; COOP_BELL_GATE = (Join-Path $Run "guest-sign") }
    $procs += $hp
    Wait-Healthy $h $hp
}
if ($Only -ne "host") {
    if ($MinCommitGB -gt 0) { Assert-Commit "guest" }
    $g = New-Instance "guest" "GuestHunter" 9308
    $gp = Start-Instance $g $guestEnv
    $procs += $gp
    Wait-Healthy $g $gp
}
if ($Third -and -not $Only) {
    if ($MinCommitGB -gt 0) { Assert-Commit "guest2" }
    $g2 = New-Instance "guest2" "GuestHunter2" 9309
    $g2p = Start-Instance $g2 $guestEnv
    $procs += $g2p
    Wait-Healthy $g2 $g2p
}

# ---- wait, then stop only what this script started --------------------------
if ($Seconds -gt 0) {
    Write-Host "running for $Seconds s (Ctrl+C leaves the processes running; their pids are above)"
    $t0 = Get-Date
    $joinedAt = $null
    while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
        if (-not ($procs | Where-Object { -not $_.HasExited })) { break }
        Start-Sleep -Seconds 2
        # The guest's sign is on the server: the host may ring (its search
        # then finds the sign at once instead of a minute later).
        $gate = Join-Path $Run "guest-sign"
        if (-not (Test-Path $gate) -and (Select-String -Path (Join-Path $Run "server.log") -Pattern "summon_messenger/create" -Quiet -ErrorAction SilentlyContinue)) {
            New-Item -ItemType File $gate | Out-Null
            Write-Host ("guest's sign is up after {0:N0} s; the host rings" -f ((Get-Date) - $t0).TotalSeconds)
        }
        if ($Only -or $KeepRunning) { continue }
        if ($ReloadAfter -gt 0) {
            # Stop once both sides say whether the session survived the reload.
            $results = @("host", "guest") | Where-Object {
                Select-String -Path (Join-Path $Run "$_\bbhost.log") -Pattern "coop_lamp: RESULT" -Quiet -ErrorAction SilentlyContinue }
            if (@($results).Count -ge 2) { Write-Host "both sides have a result"; break }
            if (@($results).Count -eq 1 -and -not $joinedAt) { $joinedAt = Get-Date }
            if ($joinedAt -and ((Get-Date) - $joinedAt).TotalSeconds -gt 60) { break }
            continue
        }
        # Both sides in the room: keep them together a little, then stop.
        if (-not $joinedAt) {
            $hj = Select-String -Path (Join-Path $Run "host\bbhost.log") -Pattern "np: member \d+ \(.*\) joined room" -Quiet -ErrorAction SilentlyContinue
            $gj = Select-String -Path (Join-Path $Run "guest\bbhost.log") -Pattern "np: joined room \d+" -Quiet -ErrorAction SilentlyContinue
            if ($hj -and $gj) { $joinedAt = Get-Date; Write-Host ("both sides report the join after {0:N0} s; stopping in $HoldAfterJoin s" -f ((Get-Date) - $t0).TotalSeconds) }
        }
        if ($joinedAt -and ((Get-Date) - $joinedAt).TotalSeconds -gt $HoldAfterJoin) { break }
    }
} else {
    Read-Host "press Enter to stop the instances and the server"
}
foreach ($p in $procs) { if (-not $p.HasExited) { & taskkill /PID $p.Id /T /F | Out-Null } }
if (-not $srv.HasExited) { & taskkill /PID $srv.Id /T /F | Out-Null }

# ---- verdict ------------------------------------------------------------------
$hl = Join-Path $Run "host\bbhost.log"; $gl = Join-Path $Run "guest\bbhost.log"
$hostJoin = if (Test-Path $hl) { Select-String -Path $hl -Pattern "np: member \d+ \(.*\) joined room" } else { $null }
$guestJoin = if (Test-Path $gl) { Select-String -Path $gl -Pattern "np: joined room \d+" } else { $null }
"host:  " + $(if ($hostJoin) { $hostJoin[0].Line.Trim() } else { "no member joined" }) | Tee-Object -FilePath "$Run\verdict.txt"
"guest: " + $(if ($guestJoin) { $guestJoin[0].Line.Trim() } else { "did not join a room" }) | Tee-Object -FilePath "$Run\verdict.txt" -Append
foreach ($r in "host", "guest") {
    $res = Select-String -Path (Join-Path $Run "$r\bbhost.log") -Pattern "coop_lamp: RESULT.*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($res) { "${r}: " + $res.Matches[0].Value | Tee-Object -FilePath "$Run\verdict.txt" -Append }
}
Write-Host "logs: $Run"
