// Seamless co-op: an official bbhost plugin (docs/plugins.md) in the spirit
// of the Souls games' Seamless Co-op mods - the game's own co-op, with fewer
// of the rules that end it. The game still carries every player (its rooms,
// its signaling, its character sync); this plugin changes when a session may
// start and when it has to end.
//
//   [plugins]
//   seamless = true
//   [seamless]
//   free_bells = true      # ringing a bell costs no Insight
//   anywhere = true        # bells work where the game forbids them (cleared areas, the Dream's edges)
//   stay_after_boss = true # cooperators stay when the boss dies
//   keep_on_load = true    # the room outlives a map load instead of ending with it
//   follow_host = true     # guests travel to the lamp the host travels to
//
// Every player in a session needs it: it announces itself in the session's
// rules (X-BBHost-Ruleset), so the server only matches seamless players with
// each other.
#include "bbhost/sdk.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" BB_PLUGIN_EXPORT const BbPluginInfo bb_plugin_info = {
    BB_PLUGIN_API_VERSION,
    BB_PLUGIN_OPT_IN | BB_PLUGIN_GAMEPLAY,
    "seamless",
    "Seamless Co-op",
    "0.1.0",
    "bbhost",
    "The game's own co-op with fewer of the rules that end it: free bells, bells anywhere, and sessions that survive a map load. Everyone in a session needs it.",
};

extern "C" BB_PLUGIN_EXPORT const BbOption bb_plugin_options[] = {
    {BB_OPT_HEADING, nullptr, "Joining", "From the next start."},
    {BB_OPT_BOOL, "free_bells", "Free bells", "Ringing the Beckoning Bell costs no Insight.", "true"},
    {BB_OPT_BOOL, "anywhere", "Bells anywhere", "Bells ring in cleared areas and wherever the game forbids them.", "true"},
    {BB_OPT_HEADING, nullptr, "Staying together"},
    {BB_OPT_BOOL, "stay_after_boss", "Stay after a boss", "Cooperators stay in the host's world when the boss dies.", "true"},
    {BB_OPT_BOOL, "keep_on_load", "Keep the session through loads", "A map load no longer ends the session.", "true"},
    {BB_OPT_BOOL, "follow_host", "Follow the host", "When the host travels to a lamp, the guests travel there too.", "true"},
    {BB_OPT_END}};

namespace {

bb::Plugin g;

constexpr std::uint32_t kBeckoningBell = 200;  // EquipParamGoods

bool g_free_bells = true;
bool g_anywhere = true;
bool g_stay_after_boss = true;
bool g_keep_on_load = true;
bool g_follow = true;

bool option(const char* key, bool def) {
    const std::string v = g.api->config((std::string("seamless.") + key).c_str());
    if (v.empty()) return def;
    return v == "true" || v == "1";
}

// A code change at a Binary Ninja address of the 1.09 eboot: the bytes the
// game has there and what goes in their place, after `before` - bytes that
// stay as they are and are only checked, so a patch lands only where the
// code around it is the code it was made for.
struct Patch {
    const char* what;
    std::uint64_t at;
    std::vector<std::uint8_t> before, expect, bytes;
};

// The bell checks. With them the Beckoning, Small Resonant and Sinister
// Bells ring wherever the game would refuse - areas whose boss is dead
// (event flags 2410/2800), areas the game marks with a negative SOS area -
// and a summon is not refused for coming from another map. Found by
// Wozzardman's shadp2p (documents/bloodborne-seamless-re.md) in the EU 1.09
// build, whose code is the same as CUSA00900's at the same addresses.
const std::vector<Patch> kAnywhere = {
    // The Beckoning Bell's availability (0x197f200): the area range check
    // signed (ja -> jg), so a negative area is not out of range...
    {"Beckoning Bell area range", 0x197f8c8, {0x81, 0xfb, 0x3f, 0x42, 0x0f, 0x00},
     {0x0f, 0x87, 0x90, 0x00, 0x00, 0x00}, {0x0f, 0x8f, 0x90, 0x00, 0x00, 0x00}},
    // ... and the region's event-flag gate (0x171d7b0) never refuses
    // (xor al,1 -> xor eax,eax: "blocked" is 0).
    {"Beckoning Bell area flag", 0x197f960, {0x31, 0xc0, 0xeb, 0x02, 0x31, 0xc0}, {0x34, 0x01}, {0x31, 0xc0}},
    // The Small Resonant and Sinister Bells: available (mov bl,al -> mov
    // cl,1), and straight to the epilogue past the other checks.
    {"Resonant Bell area", 0x197f6d1, {0xe8, 0x7f, 0xb0, 0x39, 0x00}, {0x88, 0xc3}, {0xb1, 0x01}},
    {"Resonant Bell checks", 0x197f6d3, {}, {0x4c, 0x89, 0xf7, 0xe8, 0xf5, 0x9f, 0x34, 0x00},
     {0xe9, 0xe9, 0x09, 0x00, 0x00, 0x90, 0x90, 0x90}},
    // A ringing bell's updater (0x1906820): the same range check signed, and
    // its area flag allowed (SpEffect 9003, not 9004 "the bell is silent").
    {"ringing bell area range", 0x19068bb, {0x81, 0xfb, 0x3f, 0x42, 0x0f, 0x00}, {0x77, 0x4a}, {0x7f, 0x4a}},
    {"ringing bell area flag", 0x19068fc, {0xe8, 0xb4, 0x6e, 0xe1, 0xff}, {0x88, 0xc3, 0x80, 0xf3, 0x01},
     {0xb3, 0x01, 0x90, 0x90, 0x90}},
    // WORLD_CHR_MAN_IS_SUMMON_AREA_ELIGIBLE (0x1d1a750): area < 1000000 as a
    // signed compare (sbb -> jge), so negative areas are eligible.
    {"summon area eligible", 0x1d1a8c3, {0x41, 0x81, 0xfe, 0x40, 0x42, 0x0f, 0x00},
     {0x18, 0xc9, 0x20, 0xc1, 0xeb, 0x02}, {0x7d, 0x04, 0x88, 0xc1, 0xeb, 0x02}},
    // The SOS status (0x1c6fe40): no area restriction (xor r13d,r13d).
    {"SOS area restriction", 0x1c700d3, {0xe8, 0xdd, 0xd6, 0xaa, 0xff},
     {0x84, 0xc0, 0x41, 0xbd, 0xff, 0xff, 0xff, 0xff, 0x44, 0x0f, 0x44, 0xeb, 0x41, 0xc1, 0xed, 0x1f},
     {0x45, 0x31, 0xed, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90}},
    // The summon builder (SOS_SIGN_MAN_FILTER_REQUEST_ENTRY_HELPER,
    // 0x1c74710): no rejects for a cleared area or a negative area...
    {"summon cleared area", 0x1c749e8, {0xe8, 0xca, 0x8d, 0xaa, 0xff, 0x84, 0xc0},
     {0x0f, 0x85, 0x48, 0x01, 0x00, 0x00}, {0x90, 0x90, 0x90, 0x90, 0x90, 0x90}},
    {"summon negative area", 0x1c749f0, {0x85, 0xdb}, {0x0f, 0x88, 0x40, 0x01, 0x00, 0x00},
     {0x90, 0x90, 0x90, 0x90, 0x90, 0x90}},
    // ... and none for a sign from another map (je -> jmp)...
    {"summon other map", 0x1c74b79, {0x01, 0xd1, 0x39, 0xc8}, {0x74, 0x0d}, {0xeb, 0x0d}},
    // ... nor in SosSignMan's candidate filter (0x18b5e10).
    {"sign other map", 0x18b714a, {0x01, 0xd6, 0x39, 0xf0}, {0x0f, 0x85, 0xc0, 0x03, 0x00, 0x00},
     {0x90, 0x90, 0x90, 0x90, 0x90, 0x90}},
};

// A boss's death ends the session. Every player runs the boss-clear chain
// of the compiled global_event.lua from its own map script (EMEVD 2003[12]
// raises script event 4050): BlockClear2 (0x17849a0) pays the echoes,
// BlockClear2_2 (0x1785760) then schedules BlockClear2_3Leave (everyone
// leaves the room) and BlockClear2_3 (the cooperator goes home). The echoes,
// the "prey slaughtered" message and the drops stay; the leaving goes.
const std::vector<Patch> kStayAfterBoss = {
    // BlockClear2: not the two latches a cooperator sets on its way out
    // (script globals +0xc, and ctx +0x8d - which takes its action buttons
    // until the next load).
    {"cooperator latches", 0x1784cfe, {0x85, 0xee, 0x00, 0x00, 0x00},
     {0x41, 0xc6, 0x47, 0x0c, 0x01, 0x41, 0xc6, 0x86, 0x8d, 0x00, 0x00, 0x00, 0x01},
     {0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90}},
    // BlockClear2_2: everyone clears flags 4047/4000 (a cooperator's next
    // death would be ignored otherwise; je -> jmp) ...
    {"boss clear flags", 0x1785967, {0x2c, 0x83, 0x79, 0x78, 0x00}, {0x74, 0x06}, {0xeb, 0x06}},
    // ... BlockClear2_3Leave is never scheduled ...
    {"leave after the boss", 0x1785877, {0x00, 0x00, 0x48, 0x89, 0xdf}, {0xe8, 0x44, 0x59, 0xfa, 0xff},
     {0x90, 0x90, 0x90, 0x90, 0x90}},
    // ... nor is the cooperator's return (jne -> jmp).
    {"cooperator returns", 0x178599c, {0x4c, 0x83, 0x78, 0x78, 0x01}, {0x75, 0x46}, {0xeb, 0x46}},
};

// A map load ends the session: the per-area event payload's destructor
// (0x1d470f0) calls SprjSessionManager's BeginLeaveSession (0x22d07a0,
// reason 0xff000023). In its place it calls the manager's "a matching
// controller exists" (0x22d00a0), so it takes the same branch afterwards
// and the room stays.
const std::vector<Patch> kKeepOnLoad = {
    {"leave on map load", 0x1d471b1, {0x49, 0x8b, 0x3f}, {0xe8, 0xea, 0x95, 0x58, 0x00}, {0xe8, 0xea, 0x8e, 0x58, 0x00}},
};

// Writes a patch (or takes it back) with the bytes before it as part of the
// check.
int put(const Patch& p, bool undo) {
    std::vector<std::uint8_t> from(p.before), to(p.before);
    from.insert(from.end(), undo ? p.bytes.begin() : p.expect.begin(), undo ? p.bytes.end() : p.expect.end());
    to.insert(to.end(), undo ? p.expect.begin() : p.bytes.begin(), undo ? p.expect.end() : p.bytes.end());
    return g.api->patch(p.at - p.before.size(), from.data(), to.data(), to.size());
}

// Applies a group of patches, all or none: one that does not match leaves
// the ones before it undone.
bool apply(const char* group, const std::vector<Patch>& patches) {
    std::size_t done = 0;
    for (; done < patches.size(); ++done) {
        const Patch& p = patches[done];
        const int r = put(p, false);
        if (r != 0) {
            g.log("seamless: %s: %s at 0x%llx %s", group, p.what, static_cast<unsigned long long>(p.at),
                  r == 1 ? "has other bytes; left alone" : "cannot be written");
            break;
        }
    }
    if (done == patches.size()) {
        g.log("seamless: %s: %zu patch%s in", group, done, done == 1 ? "" : "es");
        return true;
    }
    while (done-- > 0) {
        put(patches[done], true);
    }
    return false;
}

// ---- The control channel ---------------------------------------------------
// Small messages between the players' copies of this plugin, carried by the
// game's own session as a WorldSession packet of a type the game never sends
// or reads (0x1a, on its reliable channel: types above 6). The receiver files
// it in that peer's queue for the type, where only this plugin takes it out.
// A peer without the plugin keeps the few bytes queued and never reads them.
namespace chan {

constexpr std::uint64_t kWsomPtr = 0x593d6d0;         // WorldSessionObjectMan*
constexpr std::uint64_t kSessMgrPtr = 0x5940290;      // SprjSessionManager*
constexpr std::uint64_t kWorldChrManPtr = 0x593e878;  // WorldChrMan*
constexpr std::uint64_t kSendPacket = 0x1b894e0;      // (wsom, route*, type, data, size) -> sent
constexpr std::uint64_t kPopPeer = 0x1b89200;         // (wsom, route*, type, out, cap) -> bytes
constexpr std::uint64_t kRouteIsLocal = 0x10a1670;    // (route*) -> bool
constexpr std::uint64_t kRouteMatches = 0x10a1c70;    // (route*, route*) -> bool
constexpr std::uint32_t kType = 0x1a;
constexpr std::uint32_t kMagic = 0x434d4c53;  // "SLMC"
constexpr std::size_t kMax = 0x400;

enum Kind : std::uint8_t { Ping = 1, HostLampTravel = 2 };

#pragma pack(push, 1)
struct Header {
    std::uint32_t magic;
    std::uint8_t version, kind;
    std::uint16_t seq;
    std::int32_t sender_no;  // the sender's player number (PlayerIns +0x3b8)
    std::uint16_t body_size, pad;
};
#pragma pack(pop)

template <class T>
T& at(std::uint8_t* p, std::size_t off) {
    return *reinterpret_cast<T*>(p + off);
}
std::uint8_t* deref(std::uint64_t bn_slot) {
    const std::uint64_t a = g.api->guest_addr(bn_slot);
    return a ? *reinterpret_cast<std::uint8_t**>(a) : nullptr;
}
std::uint64_t call(std::uint64_t bn, std::uint64_t a = 0, std::uint64_t b = 0, std::uint64_t c = 0, std::uint64_t d = 0,
                   std::uint64_t e = 0) {
    BbCallRegs r{};
    r.arg[0] = a, r.arg[1] = b, r.arg[2] = c, r.arg[3] = d, r.arg[4] = e;
    return g.api->call_guest(bn, &r) == 0 ? r.ret : 0;
}
std::uint64_t u(const void* p) { return reinterpret_cast<std::uint64_t>(p); }

// The session: role at SprjSessionManager +0x124 (3 the host, 6 a guest);
// messages flow once WorldSessionObjectMan's state (+0x84) is 4, synced.
struct Session {
    std::uint8_t* wsom = nullptr;
    std::uint8_t* mgr = nullptr;
    int role = 0;
    bool ready() const { return wsom && mgr && (role == 3 || role == 6) && at<std::int32_t>(wsom, 0x84) == 4; }
    bool host() const { return role == 3; }
};
Session session() {
    Session s;
    s.wsom = deref(kWsomPtr);
    s.mgr = deref(kSessMgrPtr);
    if (s.mgr) s.role = at<std::int32_t>(s.mgr, 0x124);
    return s;
}

// A peer's character in this world (null while it is still joining):
// WorldChrMan's players (+0x38, 0x38 each, +0x30 of them; the first is the
// local one) by their route (+0x3d8).
std::uint8_t* player_of(std::uint8_t* route) {
    std::uint8_t* wcm = deref(kWorldChrManPtr);
    if (!wcm) return nullptr;
    const std::uint32_t n = at<std::uint32_t>(wcm, 0x30);
    std::uint8_t* entries = at<std::uint8_t*>(wcm, 0x38);
    for (std::uint32_t i = 1; entries && i < n; ++i) {
        std::uint8_t* pi = at<std::uint8_t*>(entries, i * 0x38);
        if (pi && (call(kRouteMatches, u(pi + 0x3d8), u(route)) & 0xff)) return pi;
    }
    return nullptr;
}

// Every other member's route (SprjSessionManager +0xf8..+0x100, 0x20 each).
template <class F>
void for_each_peer(const Session& s, F&& f) {
    std::uint8_t* it = at<std::uint8_t*>(s.mgr, 0xf8);
    std::uint8_t* end = at<std::uint8_t*>(s.mgr, 0x100);
    for (; it && it < end; it += 0x20) {
        if (call(kRouteIsLocal, u(it)) & 0xff) continue;
        f(it);
    }
}

std::int32_t local_no() {
    std::uint8_t* wcm = deref(kWorldChrManPtr);
    std::uint8_t* entries = wcm ? at<std::uint8_t*>(wcm, 0x38) : nullptr;
    std::uint8_t* me = entries ? at<std::uint8_t*>(entries, 0) : nullptr;
    return me ? at<std::int32_t>(me, 0x3b8) : -1;
}

std::uint16_t g_seq;

// To every member whose character is here (one still joining would only
// queue it). The number of peers that took it. Main thread.
int send_all(Kind kind, const void* body, std::uint16_t n) {
    const Session s = session();
    if (!s.ready() || sizeof(Header) + n > kMax) return 0;
    static std::uint8_t buf[kMax];
    const Header h{kMagic, 1, kind, ++g_seq, local_no(), n, 0};
    std::memcpy(buf, &h, sizeof h);
    if (n) std::memcpy(buf + sizeof h, body, n);
    int sent = 0;
    for_each_peer(s, [&](std::uint8_t* route) {
        if (!player_of(route)) return;
        if (call(kSendPacket, u(s.wsom), u(route), kType, u(buf), sizeof h + n) & 0xff) ++sent;
    });
    return sent;
}

// Takes every message that has arrived, oldest first: fn(header, body).
// Main thread (the queues fill in the game's own session update there).
template <class F>
void poll(F&& fn) {
    const Session s = session();
    if (!s.ready()) return;
    static std::uint8_t buf[0x6400];  // as large as any message, so none is skipped
    for_each_peer(s, [&](std::uint8_t* route) {
        for (int i = 0; i < 32; ++i) {
            const auto n = static_cast<std::int32_t>(call(kPopPeer, u(s.wsom), u(route), kType, u(buf), sizeof buf));
            if (n <= 0) break;
            Header h;
            if (static_cast<std::size_t>(n) < sizeof h) continue;
            std::memcpy(&h, buf, sizeof h);
            if (h.magic != kMagic || sizeof h + h.body_size > static_cast<std::size_t>(n)) continue;
            fn(h, buf + sizeof h);
        }
    });
}

}  // namespace chan

// ---- Following the host ------------------------------------------------------
// The host's lamp travel (WarpNextStage_Bonfire, 0x172e050: the lamps' and
// the Dream's headstones' travel, by ReturnPointParam row) goes to every
// guest, who travels to the same lamp: everyone loads the same map, and the
// session (keep_on_load) carries on there.
constexpr std::uint64_t kWarpBonfire = 0x172e050;
const std::uint8_t kWarpBonfirePrologue[14] = {0x55, 0x48, 0x89, 0xe5, 0x48, 0x63, 0xc6,
                                               0x48, 0x69, 0xc8, 0x89, 0xb5, 0xf8, 0x14};
std::int32_t g_follow_to = 0;  // a lamp the host went to, for this guest to follow
int g_following = 0;  // frames left in which this guest's own world transition is the follow

// SessionWorldTransition (0x17cde30) leaves the room with BeginLeaveSession
// unless GameStateMan (pointer at 0x5956678) +0x1592 is set - as it is on a
// host, whose transitions keep the room. A following guest's transition is
// made to keep it the same way: the byte is set for that call and put back
// at the next frame.
constexpr std::uint64_t kGameStateManPtr = 0x5956678;
std::uint8_t* g_keep_room = nullptr;  // the byte while set for a follow
std::uint8_t g_keep_room_was = 0;

int on_warp_bonfire(BbHookCtx* ctx, void*) {
    const auto rp = static_cast<std::int32_t>(ctx->arg[1]);
    const chan::Session s = chan::session();
    if (s.ready() && s.host()) {
        const int n = chan::send_all(chan::HostLampTravel, &rp, sizeof rp);
        g.log("seamless: the host travels to lamp %d; told %d guest%s", rp, n, n == 1 ? "" : "s");
    }
    return 0;
}

void on_message(const chan::Header& h, const std::uint8_t* body) {
    switch (h.kind) {
    case chan::Ping: {
        std::uint32_t count = 0;
        if (h.body_size >= sizeof count) std::memcpy(&count, body, sizeof count);
        g.log("seamless: ping %u from player %d (seq %u)", count, h.sender_no, h.seq);
        break;
    }
    case chan::HostLampTravel:
        if (g_follow && h.body_size >= sizeof g_follow_to && !chan::session().host()) {
            std::memcpy(&g_follow_to, body, sizeof g_follow_to);
            g.log("seamless: the host travels to lamp %d; following", g_follow_to);
        }
        break;
    default:
        break;
    }
}

// ---- Version 12: the party on screen, and who ends a session -----------------
// Banners for the room's events (on_session), a status line while in a party
// (overlay_text), and BBHOST_SEAMLESS_TRACE=1: every BeginLeaveSession
// (0x22d07a0) and SessionWorldTransition (0x17cde30) logged with the caller's
// Binary Ninja address - which of the game's ways out ended a session.
constexpr std::uint32_t kStatusText = 0x5ea10001;  // this plugin's overlay ids: 0x5ea1xxxx
constexpr std::uint64_t kBeginLeaveSession = 0x22d07a0;
const std::uint8_t kBeginLeavePrologue[17] = {0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41,
                                              0x55, 0x41, 0x54, 0x53, 0x48, 0x83, 0xec, 0x38};
constexpr std::uint64_t kSessionWorldTransition = 0x17cde30;
const std::uint8_t kWorldTransitionPrologue[14] = {0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41,
                                                   0x56, 0x41, 0x55, 0x41, 0x54, 0x53, 0x50};

std::uint64_t caller_bn(const BbHookCtx* ctx) {
    const std::uint64_t base = g.api->guest_addr(0x400000);
    return base && ctx->ret_addr >= base ? ctx->ret_addr - base + 0x400000 : ctx->ret_addr;
}

bool g_trace = false;

int on_world_transition(BbHookCtx* ctx, void*) {
    const chan::Session s = chan::session();
    if (g_following && s.mgr && s.role == 6) {
        if (std::uint8_t* gsm = chan::deref(kGameStateManPtr)) {
            g_keep_room = gsm + 0x1592;
            g_keep_room_was = *g_keep_room;
            *g_keep_room = 1;
            g_following = 0;
            g.log("seamless: following the host: this load keeps the room");
        }
    }
    if (g_trace)
        g.log("seamless: trace: SessionWorldTransition from 0x%llx (role %d, session state %d)",
              static_cast<unsigned long long>(caller_bn(ctx)), s.role, s.wsom ? chan::at<std::int32_t>(s.wsom, 0x84) : -1);
    return 0;
}

int trace_hook(BbHookCtx* ctx, void* what) {
    const chan::Session s = chan::session();
    g.log("seamless: trace: %s from 0x%llx (role %d, session state %d)", static_cast<const char*>(what),
          static_cast<unsigned long long>(caller_bn(ctx)), s.role, s.wsom ? chan::at<std::int32_t>(s.wsom, 0x84) : -1);
    return 0;
}

std::string g_host_name;  // the host's online id, while a guest in a party
int g_party = 0;          // other members in the room

void on_session(const BbSessionEvent* ev, void*) {
    const std::string who = ev->online_id && ev->online_id[0] ? ev->online_id : "A hunter";
    switch (ev->kind) {
    case BB_SESSION_CREATED:
        g_party = 0;
        break;
    case BB_SESSION_JOINED:
        g_party = 0;
        break;
    case BB_SESSION_MEMBER_JOINED:
        ++g_party;
        if (ev->member_id == ev->owner_member_id) g_host_name = who;
        g.message(ev->member_id == ev->owner_member_id ? "You are in " + who + "'s world" : who + " joined the party");
        break;
    case BB_SESSION_MEMBER_LEFT:
        if (g_party > 0) --g_party;
        g.message(who + " left the party");
        break;
    case BB_SESSION_LEFT:
    case BB_SESSION_CLOSED:
    case BB_SESSION_KICKED:
        if (g_party > 0) g.message("The party has ended");
        g_party = 0;
        g_host_name.clear();
        break;
    default:
        break;
    }
}

// The status line, refreshed each second while there is a party.
void status_line() {
    static std::uint32_t frame;
    if (++frame % 60 != 0) return;
    if (g_party <= 0) return;
    const chan::Session s = chan::session();
    char line[128];
    if (s.host())
        std::snprintf(line, sizeof line, "Seamless: your world, %d %s", g_party, g_party == 1 ? "guest" : "guests");
    else
        std::snprintf(line, sizeof line, "Seamless: in %s's world", g_host_name.empty() ? "the host" : g_host_name.c_str());
    g.api->overlay_text(kStatusText, line, 0.01f, 0.01f, 0xd8c8a0ffu, 2.0f, 0);
}

void read_options() {
    g_free_bells = option("free_bells", true);
    g_anywhere = option("anywhere", true);
    g_stay_after_boss = option("stay_after_boss", true);
    g_keep_on_load = option("keep_on_load", true);
    g_follow = option("follow_host", true);
}

}  // namespace

extern "C" BB_PLUGIN_EXPORT int bb_plugin_image(const BbHostApi* api) {
    if (!g.attach(api, 10)) return 1;
    if (!api->eboot_is_109()) return 1;
    read_options();
    g.log("seamless: free bells %s, bells anywhere %s, stay after a boss %s, keep the session through loads %s",
          g_free_bells ? "on" : "off", g_anywhere ? "on" : "off", g_stay_after_boss ? "on" : "off", g_keep_on_load ? "on" : "off");
    // Its own rules: seamless players meet only each other, and with the same
    // switches (a guest whose host keeps the session through loads must too).
    std::string rules = "seamless;v=1";
    if (g_free_bells) rules += ";free";
    if (g_anywhere) rules += ";anywhere";
    if (g_stay_after_boss) rules += ";boss";
    if (g_keep_on_load) rules += ";keep";
    api->set_rules("seamless", rules.c_str());
    if (g_anywhere) apply("bells anywhere", kAnywhere);
    if (g_stay_after_boss) apply("stay after a boss", kStayAfterBoss);
    if (g_keep_on_load) apply("keep the session through loads", kKeepOnLoad);
    // The Beckoning Bell's Insight is its goods row's consumeHeroPoint (the
    // Souls games' humanity): 0 once the table has loaded.
    if (g_free_bells) {
        g.every_frame([] {
            static bool done;
            if (done) return;
            if (g.api->param_set("EquipParamGoods", kBeckoningBell, "consumeHeroPoint", "0") == 0) {
                done = true;
                g.log("seamless: the Beckoning Bell costs no Insight");
            }
        });
    }
    // The channel: what arrives each frame, and a lamp for a guest to follow.
    // BBHOST_SEAMLESS_PING=1 sends a numbered ping every two seconds (the
    // channel's test).
    static const bool ping = [] {
        const char* e = std::getenv("BBHOST_SEAMLESS_PING");
        return e && e[0] == '1';
    }();
    g.every_frame([] {
        if (g_keep_room) {
            *g_keep_room = g_keep_room_was;
            g_keep_room = nullptr;
        }
        if (g_following > 0) --g_following;
        chan::poll(on_message);
        if (g_follow_to) {
            const std::int32_t rp = g_follow_to;
            g_follow_to = 0;
            const int r = g.api->lamp_warp(rp);
            if (r == 0) g_following = 300;
            g.log("seamless: lamp travel to %d after the host: %s", rp, r == 0 ? "queued" : "refused");
        }
        if (g.api->version >= 12) status_line();
        static std::uint32_t frame, pings;
        if (ping && ++frame % 120 == 0) {
            ++pings;
            const int n = chan::send_all(chan::Ping, &pings, sizeof pings);
            if (n) g.log("seamless: ping %u sent to %d", pings, n);
        }
    });
    if (api->version >= 12) {
        api->on_session(on_session, nullptr);
        const char* t = std::getenv("BBHOST_SEAMLESS_TRACE");
        g_trace = t && t[0] == '1';
        if (g_trace && api->hook2(kBeginLeaveSession, kBeginLeavePrologue, sizeof kBeginLeavePrologue, trace_hook,
                                  const_cast<char*>("BeginLeaveSession")) != 0)
            g.log("seamless: trace: BeginLeaveSession has other bytes");
    }
    if ((g_follow || g_trace) && api->hook2(kSessionWorldTransition, kWorldTransitionPrologue, sizeof kWorldTransitionPrologue,
                                            on_world_transition, nullptr) != 0)
        g.log("seamless: the world transition at 0x%llx has other bytes; a following guest leaves the room",
              static_cast<unsigned long long>(kSessionWorldTransition));
    if (g_follow && api->hook2(kWarpBonfire, kWarpBonfirePrologue, sizeof kWarpBonfirePrologue, on_warp_bonfire, nullptr) != 0)
        g.log("seamless: the lamp travel at 0x%llx has other bytes; guests will not follow",
              static_cast<unsigned long long>(kWarpBonfire));
    return 0;
}
