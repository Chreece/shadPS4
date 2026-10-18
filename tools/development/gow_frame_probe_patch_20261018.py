#!/usr/bin/env python3
"""Insert optional flip cadence telemetry into isolated shadPS4 videoout only."""
import argparse
from pathlib import Path
import hashlib
import sys


def replace_once(source: str, original: str, replacement: str) -> str:
    n = source.count(original)
    if n != 1:
        raise RuntimeError(f'FRAME_PROBE_ANCHOR_NOT_UNIQUE count={n}: {original[:90]!r}')
    return source.replace(original, replacement, 1)


def patch(text: str) -> str:
    if 'VIDEOOUT_FLIP_TIMING' in text or 'SHADPS4_FRAME_AUDIO_PROBE' in text:
        raise RuntimeError('FRAME_TELEMETRY_ALREADY_PRESENT_NO_DOUBLE_PATCH')
    text = replace_once(text,
        '#include "common/assert.h"',
        '#include <algorithm>\n#include <chrono>\n#include <cstdlib>\n#include "common/assert.h"')
    text = replace_once(text,
        '    Common::AccurateTimer timer{vblank_period};\n\n    const auto receive_request =',
        """    Common::AccurateTimer timer{vblank_period};

    // Read-only opt-in: measure actual accepted guest flips, not repeated
    // presentation of the last frame. Normal emulation is unchanged.
    const char* frame_probe_env = std::getenv("SHADPS4_FRAME_AUDIO_PROBE");
    const bool frame_probe_enabled = frame_probe_env && frame_probe_env[0] == '1' &&
                                     frame_probe_env[1] == '\\0';
    auto frame_last_report = std::chrono::steady_clock::now();
    auto frame_last_flip = std::chrono::steady_clock::time_point{};
    u64 frame_presented = 0;
    u64 frame_vblanks_without_request = 0;
    u64 frame_max_gap_us = 0;
    u64 frame_max_present_us = 0;

    const auto receive_request =""")
    text = replace_once(text,
        """    while (!token.stop_requested()) {
        timer.Start();

        if (DebugState.IsGuestThreadsPaused()) {""",
        """    while (!token.stop_requested()) {
        timer.Start();

        if (frame_probe_enabled) {
            const auto now = std::chrono::steady_clock::now();
            const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                now - frame_last_report).count();
            if (ms >= 5000) {
                LOG_WARNING(Lib_VideoOut,
                            "VIDEOOUT_FLIP_TIMING window_ms={} game_flips={} fps_x1000={} "
                            "max_flip_gap_us={} max_present_us={} empty_vblanks={} port_open={}",
                            ms, frame_presented, ms ? frame_presented * 1000000ULL / ms : 0,
                            frame_max_gap_us, frame_max_present_us,
                            frame_vblanks_without_request, main_port.is_open ? 1 : 0);
                frame_last_report = now;
                frame_presented = 0;
                frame_max_gap_us = 0;
                frame_max_present_us = 0;
                frame_vblanks_without_request = 0;
            }
        }

        if (DebugState.IsGuestThreadsPaused()) {""")
    text = replace_once(text,
        """            if (!request) {
                if (timer.GetTotalWait().count() < 0) {""",
        """            if (!request) {
                if (frame_probe_enabled) {
                    ++frame_vblanks_without_request;
                }
                if (timer.GetTotalWait().count() < 0) {""")
    text = replace_once(text,
        """            } else {
                Flip(request);
                FRAME_END;
            }""",
        """            } else {
                if (frame_probe_enabled) {
                    const auto begin = std::chrono::steady_clock::now();
                    if (frame_last_flip != std::chrono::steady_clock::time_point{}) {
                        const u64 gap_us = std::chrono::duration_cast<
                            std::chrono::microseconds>(begin - frame_last_flip).count();
                        frame_max_gap_us = std::max(frame_max_gap_us, gap_us);
                    }
                    frame_last_flip = begin;
                    Flip(request);
                    const u64 present_us = std::chrono::duration_cast<
                        std::chrono::microseconds>(
                            std::chrono::steady_clock::now() - begin).count();
                    frame_max_present_us = std::max(frame_max_present_us, present_us);
                    ++frame_presented;
                } else {
                    Flip(request);
                }
                FRAME_END;
            }""")
    assert text.count('VIDEOOUT_FLIP_TIMING') == 1
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('target', type=Path)
    parser.add_argument('diagnostics', type=Path)
    args = parser.parse_args()
    source = args.target.read_text()
    modified = patch(source)
    before = hashlib.sha256(source.encode()).hexdigest()
    after = hashlib.sha256(modified.encode()).hexdigest()
    args.target.write_text(modified)
    args.diagnostics.write_text(
        f'FRAME_PROBE_PRE_SHA256={before}\nFRAME_PROBE_POST_SHA256={after}\n'
        'FRAME_PROBE_RUNTIME_DEFAULT_OFF=YES\n'
        'VIDEOOUT_NON_PROBE_BRANCH_UNCHANGED=YES\n')
    print('GOW_FRAME_PROBE_PATCH=PASS; ONLY_VIDEOOUT_PRESENT_THREAD_CHANGES')
    return 0


if __name__ == '__main__':
    sys.exit(main())
