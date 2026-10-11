// How long a summon sign the player is answering stays in the game's list of
// sign entries before it gives up on it.
//
// The SOS sign manager's update (Binary Ninja 0x1c72360) ages each entry by
// the frame's seconds (its +0x4) and drops it - "the summon failed" message,
// the entry freed - once it is 180 s old, or 55 s old when it never got an
// answer (+0xc clear). The 180 is one float in .rodata (0x4d27a5c) read by
// that comparison alone. Waiting three minutes on a sign that is not coming
// keeps the player from touching another; online.sign_timeout_seconds
// replaces it (default 30, as the community patch "SosSignEntry Cooldown 30s"
// does; 0 keeps the game's 180).
//
// How often the game asks the server for signs: the API timers' tick
// (0x22946e0, the GetList sender - its play-log step is
// HostSendGetListRequest, 50) waits for the countdown at +0x100 of its
// object, then reloads it from the server status info, ss.info's
// <SummonDataGetListInterval{i}> (+0x8a0, seconds, an integer: 0x229597c,
// 0x2297af6). The debug menu shows the countdown as "Get summon messengers
// API Next". When ss.info has no such tag the parser (0x22b65e0) leaves the
// 60 it stores just before looking (`mov dword [rax+0x8a0], 60` at
// 0x22b814e). online.summon_poll_seconds replaces that 60 (0 keeps it; at
// least 5): a sign put down is found within that many seconds instead of a
// minute. A server whose ss.info sends the tag still decides.

struct ElfImage;

void sos_signs_install(ElfImage* image);
