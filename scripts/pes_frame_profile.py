# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded frame-path observation for the pinned PES diagnostic, not a game fix."""

APIS = (
    "sceGnmSubmitCommandBuffers", "sceGnmSubmitCommandBuffersForWorkload",
    "sceGnmSubmitAndFlipCommandBuffers", "sceGnmSubmitAndFlipCommandBuffersForWorkload",
    "sceGnmSubmitDone", "sceGnmDingDong", "sceGnmDingDongForWorkload",
    "sceVideoOutSubmitFlip", "sceVideoOutSubmitEopFlip",
    "sceVideoOutGetFlipStatus", "sceVideoOutGetVblankStatus",
    "VideoOutDriver::Flip", "VideoOutDriver::DrawBlankFrame",
    "VideoOutDriver::DrawLastFrame", "VideoOutDriver::SubmitFlipInternal",
)

SUPPORT = r'''
def capture_args(api):
    args = [register(r) for r in ("rdi", "rsi", "rdx", "rcx", "r8", "r9")]
    if api in ("sceGnmSubmitAndFlipCommandBuffers", "sceGnmSubmitAndFlipCommandBuffersForWorkload"):
        # PS4_SYSV_ABI: six integer/pointer registers, then slots above return RIP.
        count = 4 if api.endswith("ForWorkload") else 3
        stack = memory(register("rsp") + 8, count * 8)
        args += [number(stack, i * 8) for i in range(count)]
    return args


def return_kind(api):
    return "void" if api.startswith(("VideoOutDriver::", "sceGnmDingDong")) else "error_code"


def signed32(value):
    value &= 0xffffffff
    return value - (1 << 32) if value & (1 << 31) else value


def fields_before(api, args):
    if api.startswith("sceGnmSubmit") and api != "sceGnmSubmitDone":
        shift = int(api.endswith("ForWorkload"))
        count = args[shift] & 0xffffffff
        values = {"buffer_count": count}
        if count and args[shift + 1] and args[shift + 2]:
            length = min(count, 4)
            addresses = memory(args[shift + 1], length * 8)
            sizes = memory(args[shift + 2], length * 4)
            values["dcb_addresses"] = [hex(number(addresses, i * 8)) for i in range(length)]
            values["dcb_bytes"] = [number(sizes, i * 4, 4) for i in range(length)]
        if api.startswith("sceGnmSubmitAndFlip"):
            values.update(handle=signed32(args[shift + 5]),
                          buffer_index=signed32(args[shift + 6]),
                          flip_mode=signed32(args[shift + 7]), flip_arg=hex(args[shift + 8]))
        return values
    if api.startswith("sceGnmDingDong"):
        return {"queue": args[0] & 0xffffffff, "offset_dw": args[1] & 0xffffffff}
    if api in ("sceVideoOutSubmitFlip", "sceVideoOutSubmitEopFlip"):
        return {"handle": signed32(args[0]), "buffer_index": signed32(args[1]),
                "flip_mode": signed32(args[2]), "flip_arg": hex(args[3])}
    if api == "VideoOutDriver::SubmitFlipInternal":
        return {"buffer_index": signed32(args[2]), "flip_arg": hex(args[3]),
                "eop": bool(args[4] & 0xff)}
    if api == "VideoOutDriver::Flip":
        # Request in driver.h: frame*, port*, s64 flip_arg, s32 index, bool eop.
        request = memory(args[1], 32)
        return {"frame": hex(number(request, 0)), "flip_arg": hex(number(request, 16)),
                "buffer_index": signed32(number(request, 24, 4)), "eop": bool(request[28])}
    return {}


def fields_after(api, args, rc):
    if rc != 0:
        return {}
    if api == "sceVideoOutGetFlipStatus":
        data = memory(args[1], 64)
        return {"flip_count": number(data, 0), "flip_arg": hex(number(data, 24)),
                "gc_queue_num": signed32(number(data, 48, 4)),
                "flip_pending_num": signed32(number(data, 52, 4)),
                "current_buffer": signed32(number(data, 56, 4))}
    if api == "sceVideoOutGetVblankStatus":
        return {"vblank_count": number(memory(args[1], 40), 0)}
    return {}


def completion_snapshot(pc, key):
    # These addresses/signature come from the three supplied guest disassemblies.
    # Only interpret RBX after its key assignment, while inside that exact function.
    if not 0x2e81919 <= pc <= 0x2e819ae:
        return {"status": "outside_known_completion_check"}
    signature = bytes.fromhex("48 8b 48 08 48 8b 09 48 3b 08")
    if memory(0x2e81997, len(signature)) != signature:
        return {"status": "guest_signature_mismatch"}
    table = number(memory(0x5fff258, 8), 0)
    slot = table + 24 * (key & 1023)
    entry = memory(slot, 24)
    expected, pointer = number(entry, 0), number(entry, 8)
    observed = number(memory(pointer, 8), 0) if pointer else None
    return {"status": "sampled", "key": hex(key), "slot": hex(slot),
            "expected": hex(expected), "pointer": hex(pointer),
            "observed": hex(observed) if observed is not None else None,
            "slot_matches_key": expected == key, "equal_at_sample": observed == expected}


def profile_snapshot(phase):
    original = gdb.selected_thread()
    sample = {"phase": phase, "status": "game_thread_not_found"}
    try:
        for thread in gdb.selected_inferior().threads():
            if thread.name != "Game:Main":
                continue
            thread.switch()
            pc, key = register("rip"), register("rbx")
            sample.update(status="sampled", pc=hex(pc), rbx=hex(key),
                          completion=completion_snapshot(pc, key))
            sample["backtrace"] = gdb.execute("bt 12", to_string=True)[-12000:]
            break
    except Exception as error:
        sample["read_error"] = str(error)
    finally:
        if original is not None and original.is_valid():
            original.switch()
    result.setdefault("game_samples", []).append(sample)
'''

PROFILE = {
    "required_apis": APIS,
    "only_required": True,
    "symbol_queries": ("sceGnmSubmit", "sceGnmDingDong", "sceVideoOutSubmit",
                       "sceVideoOutGetFlipStatus", "sceVideoOutGetVblankStatus", "VideoOutDriver::"),
    "symbol_pattern": (r"^\s*(0x[0-9a-fA-F]+)\s+Libraries::"
                       r"(?:GnmDriver|VideoOut)::([A-Za-z0-9_:]+)\("),
    "profile_source": SUPPORT,
}

# The Docker-only target has the same ABI and layouts, with known sentinel values.
# No real renderer, emulator, game, or user data is mounted into that container.
FIXTURE_CPP = r'''
#define PROBE_API __attribute__((noinline, noipa, used))
namespace Libraries::GnmDriver {
PROBE_API int sceGnmSubmitCommandBuffers(unsigned, void**, unsigned*, void**, unsigned*) { return 0; }
PROBE_API int sceGnmSubmitCommandBuffersForWorkload(unsigned, unsigned, void**, unsigned*, void**, unsigned*) { return 0; }
PROBE_API int sceGnmSubmitAndFlipCommandBuffers(unsigned, void**, unsigned*, void**, unsigned*, unsigned, unsigned, unsigned, std::int64_t) { return 0; }
PROBE_API int sceGnmSubmitAndFlipCommandBuffersForWorkload(unsigned, unsigned, void**, unsigned*, void**, unsigned*, unsigned, unsigned, unsigned, std::int64_t) { return 0; }
PROBE_API int sceGnmSubmitDone() {
    static unsigned calls = 0;
    return ++calls % 5 == 0 ? static_cast<int>(0x80d11000u) : 0;
}
PROBE_API void sceGnmDingDong(unsigned, unsigned) {}
PROBE_API void sceGnmDingDongForWorkload(unsigned, unsigned, std::uint64_t) {}
}
namespace Libraries::VideoOut {
PROBE_API int sceVideoOutSubmitEopFlip(int, unsigned, unsigned, std::int64_t, void**) { return 0; }
// Force the same return-PC case as optimized emulator forwarding wrappers.
PROBE_API __attribute__((optimize("O2"))) int sceVideoOutSubmitFlip(int handle, int index, int mode, std::int64_t tag) {
    return sceVideoOutSubmitEopFlip(handle, index, mode, tag, nullptr);
}
PROBE_API int sceVideoOutGetFlipStatus(int, Bytes* data) {
    static std::uint64_t count = 0;
    put<std::uint64_t>(data, 0, ++count);
    put<std::uint64_t>(data, 24, 0x1122334455667788ULL);
    put<std::int32_t>(data, 48, 2);
    put<std::int32_t>(data, 52, 3);
    put<std::int32_t>(data, 56, -1);
    return 0;
}
PROBE_API int sceVideoOutGetVblankStatus(int, Bytes* data) {
    static std::uint64_t count = 100;
    put<std::uint64_t>(data, 0, ++count);
    return 0;
}
struct Request { void* frame; void* port; std::int64_t flip_arg; int index; bool eop; };
class VideoOutDriver {
public:
    PROBE_API void Flip(const Request&);
    PROBE_API void DrawBlankFrame();
    PROBE_API void DrawLastFrame();
    PROBE_API void SubmitFlipInternal(void*, int, std::int64_t, bool);
};
void VideoOutDriver::Flip(const Request&) {}
void VideoOutDriver::DrawBlankFrame() {}
void VideoOutDriver::DrawLastFrame() {}
void VideoOutDriver::SubmitFlipInternal(void*, int, std::int64_t, bool) {}
}
void frame_activity() {
    using namespace Libraries::GnmDriver;
    using namespace Libraries::VideoOut;
    void* addresses[] = {reinterpret_cast<void*>(0x12345678), reinterpret_cast<void*>(0x23456789)};
    unsigned sizes[] = {64, 128};
    constexpr std::int64_t tag = 0x1122334455667788LL;
    sceGnmSubmitCommandBuffers(2, addresses, sizes, nullptr, nullptr);
    sceGnmSubmitCommandBuffersForWorkload(99, 2, addresses, sizes, nullptr, nullptr);
    sceGnmSubmitAndFlipCommandBuffers(2, addresses, sizes, nullptr, nullptr, 7, -1, 1, tag);
    sceGnmSubmitAndFlipCommandBuffersForWorkload(99, 2, addresses, sizes, nullptr, nullptr, 7, -1, 1, tag);
    sceGnmSubmitDone();
    sceGnmDingDong(3, 128);
    sceGnmDingDongForWorkload(3, 128, 99);
    sceVideoOutSubmitFlip(7, -1, 1, tag);
    sceVideoOutSubmitEopFlip(7, -1, 1, tag, nullptr);
    Bytes status[64]{};
    sceVideoOutGetFlipStatus(7, status);
    sceVideoOutGetVblankStatus(7, status);
    VideoOutDriver driver;
    Request request{addresses[0], nullptr, tag, -1, true};
    driver.SubmitFlipInternal(nullptr, -1, tag, true);
    driver.Flip(request);
    driver.DrawLastFrame();
    driver.DrawBlankFrame();
}
'''


def validate_observations(mode, result, require):
    apis = result['apis']
    require(set(apis) == set(APIS), 'Frame symbols are missing or unexpected')
    if mode == 'idle':
        require(all(v['calls'] == 0 for v in apis.values()), 'Idle target recorded frame activity')
        return
    for api, stats in apis.items():
        require(stats['calls'] == stats['returns'] == 32 and stats['capped'],
                'Incorrect entry/return cap for ' + api + ': ' + str(stats))
        expected = {'0x80d11000': 6} if api == 'sceGnmSubmitDone' else {}
        require(stats['errors'] == expected, 'Wrong return classification for ' + api)
    records = result['records']
    require(not any('read_error' in r for r in records), 'Frame argument/output read failed')
    for record in records:
        api = record.get('api', '')
        if record['event'] == 'enter':
            if api.startswith('sceGnmSubmit') and api != 'sceGnmSubmitDone':
                require(record.get('buffer_count') == 2 and record.get('dcb_bytes') == [64, 128]
                        and record.get('dcb_addresses') == ['0x12345678', '0x23456789'],
                        'Wrong command buffer arguments: ' + str(record))
            if api.startswith('sceGnmSubmitAndFlip') or api in ('sceVideoOutSubmitFlip', 'sceVideoOutSubmitEopFlip'):
                require(record.get('handle') == 7 and record.get('buffer_index') == -1
                        and record.get('flip_mode') == 1 and record.get('flip_arg') == '0x1122334455667788',
                        'Wrong register/stack flip arguments: ' + str(record))
            if api.startswith('sceGnmDingDong'):
                require(record.get('queue') == 3 and record.get('offset_dw') == 128,
                        'Wrong compute queue arguments')
            if api in ('VideoOutDriver::Flip', 'VideoOutDriver::SubmitFlipInternal'):
                require(record.get('buffer_index') == -1 and record.get('eop') is True and
                        record.get('flip_arg') == '0x1122334455667788', 'Wrong driver request fields')
        elif api.startswith(('VideoOutDriver::', 'sceGnmDingDong')):
            require(record.get('rc') is None, 'Void function was interpreted as an error code')
        elif api == 'sceVideoOutGetFlipStatus':
            require(record.get('flip_count') == record['seq'] and record.get('current_buffer') == -1
                    and record.get('flip_pending_num') == 3 and record.get('gc_queue_num') == 2,
                    'Wrong flip status layout: ' + str(record))
        elif api == 'sceVideoOutGetVblankStatus':
            require(record.get('vblank_count') == 100 + record['seq'], 'Wrong vblank status layout')
