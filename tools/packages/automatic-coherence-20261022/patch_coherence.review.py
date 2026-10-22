#!/usr/bin/env python3
"""Enable the existing demand-driven coherence path as a shared correctness policy.

No new rendering implementation and no game selection. Historical JSON values
remain parseable, but cannot disable correct visibility of GPU-written memory.
"""
from __future__ import annotations
import hashlib

SETTINGS = 'src/core/emulator_settings.h'
PAGES = 'src/video_core/page_manager.cpp'
LOG = 'src/emulator.cpp'
EXPECTED = {
    SETTINGS: '231cbc7147a7deea906358612e0a0d0ff0edaa47',
    PAGES: 'c8e463cf0b2117ad62227fcf8b0cdee99631af17',
    LOG: '355458a7858f87f078dcceceb80ffe4b33193066',
}

NEW_ACCESSORS = '''    // CPU reads of GPU-written guest memory require coherence for every title.
    // Precise is the legacy enum name for the existing demand/page-fault path:
    // it does not download every buffer on every frame. Old JSON values remain
    // readable, but no global/per-game setting may disable this correctness path.
    u32 GetReadbacksMode() const {
        return GpuReadbacksMode::Precise;
    }
    // Retain the source API for older frontends. The mode is no longer selectable.
    void SetReadbacksMode(const u32&, bool = false) {
    }'''

OLD_BACKEND = '''    if (EmulatorSettings.IsUserfaultfdTracking()) {
        try {'''
NEW_BACKEND = '''    // The current UFFD backend registers write-protect faults only; it cannot
    // intercept CPU reads of GPU-dirty pages. Use the read-capable signal backend
    // whenever demand coherence is required, even with a legacy UFFD preference.
    const bool needs_read_faults =
        EmulatorSettings.GetReadbacksMode() == GpuReadbacksMode::Precise;
    if (EmulatorSettings.IsUserfaultfdTracking() && needs_read_faults) {
        LOG_INFO(Config,
                 "Memory tracking: signals required for automatic GPU/CPU coherence; "
                 "write-only userfaultfd preference ignored");
    }
    if (EmulatorSettings.IsUserfaultfdTracking() && !needs_read_faults) {
        try {'''


def blob(data: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError('SOURCE_ANCHOR_MISMATCH='+repr(old[:100]))
    return text.replace(old, new, 1)


def transform_text(path: str, text: str) -> str:
    if path == SETTINGS:
        text = once(text,
            '    Setting<u32> readbacks_mode{GpuReadbacksMode::Disabled};',
            '    // Legacy serialized field; the effective policy is always demand-driven.\n'
            '    Setting<u32> readbacks_mode{GpuReadbacksMode::Precise};')
        text = once(text,
            '            make_override<GPUSettings>("readbacks_mode", &GPUSettings::readbacks_mode),\n',
            '')
        return once(text,
            '    SETTING_FORWARD(m_gpu, ReadbacksMode, readbacks_mode)', NEW_ACCESSORS)
    if path == PAGES:
        return once(text, OLD_BACKEND, NEW_BACKEND)
    if path == LOG:
        return once(text,
            '    LOG_INFO(Config, "GPU readbacksMode: {}", EmulatorSettings.GetReadbacksMode());',
            '    LOG_INFO(Config, "GPU readbacksMode: {} (automatic demand coherence; legacy option ignored)",\n'
            '             EmulatorSettings.GetReadbacksMode());')
    raise ValueError('UNEXPECTED_SOURCE_PATH='+path)


def transform(path: str, data: bytes) -> bytes:
    if path not in EXPECTED or blob(data) != EXPECTED[path]:
        raise ValueError('SOURCE_PREIMAGE_MISMATCH='+path)
    return transform_text(path, data.decode()).encode()
