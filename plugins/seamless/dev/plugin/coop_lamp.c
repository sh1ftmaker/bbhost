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
static double g_reload_after = 0, g_others_since = 0;
static int g_reload_done = 0, g_others = -1;

/* Other players in the world: characters with no NpcParam row that are not
 * the main player. */
static int count_others(void) {
    BbChr chrs[256];
    size_t n = 0;
    if (api->chr_list(chrs, 256, &n) != 0) return -1;
    int others = 0;
    for (size_t i = 0; i < n && i < 256; ++i)
        if (!chrs[i].is_player && chrs[i].npc_param == -1) ++others;
    return others;
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
            const int others = count_others();
            if (others != g_others) {
                api->log("coop_lamp: other players in the world: %d (block %08x)", others, (unsigned)block);
                g_others = others;
            }
            if (others > 0 && !g_others_since) g_others_since = t;
            if (g_reload_after > 0 && !g_reload_done && g_others_since && t - g_others_since >= g_reload_after) {
                g_reload_done = 1;
                int r = api->lamp_warp(g_rp);
                api->log("coop_lamp: reload: lamp_warp(%d) with %d other players -> %s", (int)g_rp, others,
                         r == 0 ? "queued" : "refused");
            }
            else if (!g_effects[0]) { api->log("coop_lamp: no player in chr_list yet"); strcpy(g_effects, "?"); }
        }
        if (!g_bell_done && (g_bell_goods || g_bell_sp) && t - g_world_since >= g_bell_at) {
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
    a->on_frame(on_frame, 0);
    a->log("coop_lamp: loaded (return point %d, bell goods %d speffect %d at %.0f s)", (int)g_rp, g_bell_goods,
           g_bell_sp, g_bell_at);
    return 0;
}
