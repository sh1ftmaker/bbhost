/* coop_lamp: a test-only bbhost plugin for coop-test\pair.ps1.
 *
 * 1. The test save loads in the Hunter's Dream, where the bells do nothing.
 *    Once the player stands in the Dream (map block 0x15xxxxxx, m21) for
 *    COOP_LAMP_DELAY seconds (default 6), it travels to the lamp whose
 *    ReturnPointParam row is COOP_LAMP_RP (default 2412951, Central
 *    Yharnam's lamp) through bbhost's lamp_warp - the travel the Dream's
 *    headstones do. Needs BBHOST_TEST_WARP=1. COOP_LAMP_RP=0 turns it off.
 * 2. Diagnostics for the bells: logs the bells' EquipParamGoods rows (200..)
 *    once, and every change in which SpEffects 9000..9099 the player has.
 * 3. COOP_BELL=<goods id> with COOP_BELL_AT=<s>: that many seconds after the
 *    player is in a world outside the Dream, applies the goods row's refId
 *    SpEffect to the player (what using the item does), as an alternative
 *    to BBHOST_NP_TEST's Lua events. COOP_BELL_SPEFFECT=<id> applies that
 *    SpEffect instead.
 *
 * 4. COOP_RELOAD_AFTER=<s>: that many seconds after another player first
 *    shows up in the world, travels to the lamp COOP_LAMP_RP again (a full
 *    map load, as the host's lamp travel would make), once; with the
 *    count of other players logged as it changes, to see whether the
 *    session survives the load.
 *
 * 5. COOP_BELL_GATE=<file>: the bell waits until that file exists (pair.ps1
 *    makes it when the guest's sign is on the server, so the host's first
 *    search finds it instead of waiting a minute for the next).
 * The session's role and member count (SprjSessionManager) are logged as
 * they change; after a reload a RESULT line says whether it survived.
 *
 * Build (llvm-mingw):
 *   clang --target=x86_64-w64-mingw32 -shared -O2 -I<bbhost-src>/include -o coop_lamp.dll coop_lamp.c
 */
#include "bbhost_plugin.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static const BbHostApi* api;
static int32_t g_rp = 2412951;
static double g_delay = 6.0;
static double g_in_dream_since = 0;
static double g_last_try = 0;
static int g_tries = 0;
static double g_world_since = 0;
static int g_bell_goods = 0, g_bell_sp = 0, g_bell_done = 0;
static double g_bell_at = 30.0;
static double g_last_scan = 0;
static char g_effects[1024];
static int g_goods_logged = 0;
static double g_reload_after = 0, g_others_since = 0, g_reload_at = 0;
static int g_reload_done = 0, g_others = -1, g_result_done = 0;
static char g_gate[512];
static double g_hold = 75;
static char g_session[64];

/* The session as SprjSessionManager (pointer at 0x5940290) holds it: its
 * role (+0x124: 3 host, 6 client) and its member routes (+0xf8..+0x100, 0x20
 * each). 0 when there is none. */
static int session_members(int* role) {
    static int warned;
    const uint64_t slot = api->guest_addr(0x5940290);
    *role = 0;
    if (!slot) {
        if (!warned++) api->log("coop_lamp: guest_addr(0x5940290) is 0");
        return 0;
    }
    const uint8_t* mgr = *(uint8_t* const*)(uintptr_t)slot;
    if (!mgr) return 0;
    *role = *(const int32_t*)(mgr + 0x124);
    const uint64_t begin = *(const uint64_t*)(mgr + 0xf8), end = *(const uint64_t*)(mgr + 0x100);
    return end > begin ? (int)((end - begin) / 0x20) : 0;
}


static double now_s(void) {
    struct timespec ts;
    timespec_get(&ts, TIME_UTC);
    return (double)ts.tv_sec + ts.tv_nsec / 1e9;
}

static void* player_ins(void) {
    BbChr chrs[64];
    size_t n = 0;
    if (api->chr_list(chrs, 64, &n) != 0) return 0;
    for (size_t i = 0; i < n && i < 64; ++i)
        if (chrs[i].is_player) return chrs[i].ins;
    return 0;
}

static void log_goods(void) {
    char ref[32], typ[32], hero[32], cat[32];
    for (uint32_t id = 200; id <= 215; ++id) {
        if (api->param_get("EquipParamGoods", id, "refId", ref, sizeof ref) != 0) {
            api->log("coop_lamp: goods %u: no row", id);
            continue;
        }
        typ[0] = hero[0] = cat[0] = 0;
        api->param_get("EquipParamGoods", id, "goodsType", typ, sizeof typ);
        api->param_get("EquipParamGoods", id, "consumeHeroPoint", hero, sizeof hero);
        api->param_get("EquipParamGoods", id, "refCategory", cat, sizeof cat);
        api->log("coop_lamp: goods %u refId %s refCategory %s goodsType %s consumeHeroPoint %s", id, ref, cat, typ, hero);
    }
}

static void scan_effects(void* chr) {
    char buf[1024];
    size_t at = 0;
    buf[0] = 0;
    for (int id = 9000; id < 9100; ++id) {
        if (api->sp_effect_has(chr, id) == 1 && at < sizeof buf - 8) at += (size_t)snprintf(buf + at, sizeof buf - at, " %d", id);
    }
    if (strcmp(buf, g_effects) != 0) {
        api->log("coop_lamp: player SpEffects 9000-9099 now:%s", buf[0] ? buf : " none");
        strcpy(g_effects, buf);
    }
}

static void on_frame(void* user) {
    (void)user;
    uint32_t block = 0;
    double t = now_s();
    if (api->player_block(&block) != 0) {
        g_in_dream_since = 0;
        g_world_since = 0;
        g_others = -1;
        return;
    }
    if ((block >> 24) != 0x15) { /* a world outside the Hunter's Dream */
        g_in_dream_since = 0;
        if (!g_world_since) {
            g_world_since = t;
            /* A load after another member was here (this side's reload, or
             * following the host's): watch whether the session is still on. */
            if (g_others_since && !g_reload_done) {
                g_reload_done = 1;
                g_reload_at = t;
                api->log("coop_lamp: a load with the session on; watching it");
            }
            api->log("coop_lamp: in block %08x", (unsigned)block);
            if (!g_goods_logged) {
                g_goods_logged = 1;
                log_goods();
            }
        }
        void* chr = 0;
        if (t - g_last_scan >= 1.0) {
            g_last_scan = t;
            chr = player_ins();
            if (chr) scan_effects(chr);
            int role = 0;
            const int members = session_members(&role);
            char sess[64];
            snprintf(sess, sizeof sess, "role %d, %d members", role, members);
            if (strcmp(sess, g_session) != 0) {
                api->log("coop_lamp: session: %s (block %08x)", sess, (unsigned)block);
                strcpy(g_session, sess);
            }
            /* After the load: the session must stay up COOP_HOLD seconds
             * (default 75 - past the game's later checks and its 60 s join
             * sync); losing it at any point after 10 s is a failure. */
            if (g_reload_done && !g_result_done && t - g_reload_at > 10 && members < 2) {
                g_result_done = 1;
                api->log("coop_lamp: RESULT the session did not survive the load: %s, %.0f s after it", sess, t - g_reload_at);
            }
            if (g_reload_done && !g_result_done && t - g_reload_at > g_hold) {
                if (members >= 2) {
                    g_result_done = 1;
                    api->log("coop_lamp: RESULT the session survived the load: %s, %.0f s after the reload", sess,
                             t - g_reload_at);
                }
            }
            const int others = members > 1 ? members - 1 : 0;
            if (others != g_others) {
                api->log("coop_lamp: other session members: %d (block %08x)", others, (unsigned)block);
                g_others = others;
            }
            if (others > 0 && !g_others_since) g_others_since = t;
            if (g_reload_after > 0 && !g_reload_done && g_others_since && t - g_others_since >= g_reload_after) {
                g_reload_done = 1;
                g_reload_at = t;
                int r = api->lamp_warp(g_rp);
                api->log("coop_lamp: reload: lamp_warp(%d) with %d other players -> %s", (int)g_rp, others,
                         r == 0 ? "queued" : "refused");
            }
            else if (!g_effects[0]) { api->log("coop_lamp: no player in chr_list yet"); strcpy(g_effects, "?"); }
        }
        FILE* gate = 0;
        const int gate_open = !g_gate[0] || ((gate = fopen(g_gate, "rb")) != 0);
        if (gate) fclose(gate);
        if (!g_bell_done && (g_bell_goods || g_bell_sp) && t - g_world_since >= g_bell_at && gate_open) {
            g_bell_done = 1;
            int sp = g_bell_sp;
            if (!sp) {
                char ref[32] = "";
                if (api->param_get("EquipParamGoods", (uint32_t)g_bell_goods, "refId", ref, sizeof ref) == 0) sp = atoi(ref);
            }
            if (!chr) chr = player_ins();
            int r = (chr && sp > 0) ? api->sp_effect_apply(chr, sp) : -1;
            api->log("coop_lamp: bell: goods %d -> SpEffect %d applied to the player: %s", g_bell_goods, sp,
                     r == 0 ? "ok" : "failed");
        }
        return;
    }
    if (!g_rp) return;
    if (!g_in_dream_since) {
        g_in_dream_since = t;
        api->log("coop_lamp: in the Hunter's Dream (block %08x); lamp travel to return point %d in %.0f s",
                 (unsigned)block, (int)g_rp, g_delay);
        return;
    }
    if (t - g_in_dream_since < g_delay || t - g_last_try < 15.0 || g_tries >= 4) return;
    g_last_try = t;
    ++g_tries;
    int r = api->lamp_warp(g_rp);
    api->log("coop_lamp: lamp_warp(%d) -> %s (try %d)", (int)g_rp, r == 0 ? "queued" : "refused (BBHOST_TEST_WARP=1?)",
             g_tries);
}

BB_PLUGIN_EXPORT const BbPluginInfo bb_plugin_info = {
    BB_PLUGIN_API_VERSION, 0, "coop_lamp", "Co-op test: leave the Dream", "1.1.0", "coop-test",
    "Test only: travels from the Hunter's Dream to a lamp (COOP_LAMP_RP) and logs the bells' SpEffects."};

BB_PLUGIN_EXPORT int bb_plugin_image(const BbHostApi* a) {
    if (a->version < 8 || !a->eboot_is_109()) return 1;
    api = a;
    const char* e;
    if ((e = getenv("COOP_LAMP_RP")) && *e) g_rp = (int32_t)atol(e);
    if ((e = getenv("COOP_LAMP_DELAY")) && *e) g_delay = atof(e);
    if ((e = getenv("COOP_BELL")) && *e) g_bell_goods = atoi(e);
    if ((e = getenv("COOP_BELL_SPEFFECT")) && *e) g_bell_sp = atoi(e);
    if ((e = getenv("COOP_BELL_AT")) && *e) g_bell_at = atof(e);
    if ((e = getenv("COOP_RELOAD_AFTER")) && *e) g_reload_after = atof(e);
    if ((e = getenv("COOP_HOLD")) && *e) g_hold = atof(e);
    if ((e = getenv("COOP_BELL_GATE")) && *e) snprintf(g_gate, sizeof g_gate, "%s", e);
    a->on_frame(on_frame, 0);
    a->log("coop_lamp: loaded (return point %d, bell goods %d speffect %d at %.0f s)", (int)g_rp, g_bell_goods,
           g_bell_sp, g_bell_at);
    return 0;
}
