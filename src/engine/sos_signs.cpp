#include "engine/sos_signs.h"

#include "core/config.h"
#include "core/elf.h"
#include "core/memory.h"
#include "engine/addr.h"
#include "log.h"

#include <cstdint>
#include <cstring>

namespace {
constexpr std::uint64_t kSignTimeout = 0x4d27a5c;  // Binary Ninja (guest VA): 180.0f
constexpr float kGameTimeout = 180.0f;
// The ss.info parser's default for <SummonDataGetListInterval>:
// `mov dword ptr [rax + 0x8a0], 60`; the last four bytes are the seconds.
constexpr std::uint64_t kPollDefault = 0x22b814e;  // Binary Ninja (guest VA)
constexpr std::uint8_t kPollExpect[] = {0xc7, 0x80, 0xa0, 0x08, 0x00, 0x00, 0x3c, 0x00, 0x00, 0x00};
constexpr int kMinPoll = 5;  // a sign list a second from every player is the server's load, not a feature

void summon_poll_install(GuestMemory& mem) {
    int seconds = config().summon_poll_seconds;
    if (seconds <= 0 || seconds == 60) return;
    if (seconds < kMinPoll) seconds = kMinPoll;
    const std::uint64_t at = mem.slide + (kPollDefault - kPreferredGuestSlide);
    auto* p = static_cast<std::uint8_t*>(guest_ptr(mem, at));
    if (std::memcmp(p, kPollExpect, sizeof(kPollExpect)) != 0) {
        host_log("sos signs: poll not patched: unexpected bytes at 0x%llx",
                 static_cast<unsigned long long>(kPollDefault));
        return;
    }
    const std::uint64_t page = at & ~0xfffull;  // the store's ten bytes are in one page
    if (!guest_protect_rwx(&mem, page, 0x1000)) {
        host_log("sos signs: cannot unprotect 0x%llx", static_cast<unsigned long long>(kPollDefault));
        return;
    }
    const auto v = static_cast<std::uint32_t>(seconds);
    std::memcpy(p + 6, &v, sizeof(v));
    guest_protect_rx(&mem, page, 0x1000);
    host_log("sos signs: the game asks for signs every %d s unless ss.info says (was 60)", seconds);
}
}  // namespace

void sos_signs_install(ElfImage* image) {
    if (!image || image->sha256 != kEboot109Sha256) return;
    GuestMemory& mem = image->mem;
    summon_poll_install(mem);
    const int seconds = config().sign_timeout_seconds;
    if (seconds <= 0 || seconds == 180) return;
    const std::uint64_t at = mem.slide + (kSignTimeout - kPreferredGuestSlide);
    auto* p = static_cast<float*>(guest_ptr(mem, at));
    if (std::memcmp(p, &kGameTimeout, sizeof(float)) != 0) {
        host_log("sos signs: not patched: 0x%llx is not 180.0", static_cast<unsigned long long>(kSignTimeout));
        return;
    }
    if (!guest_protect_rw(&mem, at & ~0xfffull, 0x1000)) {
        host_log("sos signs: cannot unprotect 0x%llx", static_cast<unsigned long long>(kSignTimeout));
        return;
    }
    *p = static_cast<float>(seconds);  // the page stays writable, as frame_rate.cpp leaves .rodata it rewrites
    host_log("sos signs: a sign entry times out after %d s (was 180)", seconds);
}
