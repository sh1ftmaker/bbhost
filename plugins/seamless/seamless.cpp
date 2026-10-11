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
//
// Every player in a session needs it: it announces itself in the session's
// rules (X-BBHost-Ruleset), so the server only matches seamless players with
// each other.
#include "bbhost/sdk.hpp"

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
    {BB_OPT_END}};

namespace {

bb::Plugin g;

constexpr std::uint32_t kBeckoningBell = 200;  // EquipParamGoods

bool g_free_bells = true;
bool g_anywhere = true;
bool g_stay_after_boss = true;
bool g_keep_on_load = true;

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

void read_options() {
    g_free_bells = option("free_bells", true);
    g_anywhere = option("anywhere", true);
    g_stay_after_boss = option("stay_after_boss", true);
    g_keep_on_load = option("keep_on_load", true);
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
    return 0;
}
