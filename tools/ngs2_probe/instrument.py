#!/usr/bin/env python3
"""Generate a diagnostic translation unit; never edit the supplied source file."""
from pathlib import Path
import argparse
import re

HOOKS = {
    "sceNgs2SystemCreateWithAllocator": 'SHAD_NGS2_PROBE("system_create_options", 0, 0, option, option ? sizeof(*option) : 0);',
    "sceNgs2SystemSetGrainSamples": 'SHAD_NGS2_PROBE("grain", systemHandle, numSamples, nullptr, 0);',
    "sceNgs2RackCreateWithAllocator": 'SHAD_NGS2_PROBE("rack_options", systemHandle, rackId, option, option ? sizeof(*option) : 0);',
    "sceNgs2RackGetVoiceHandle": 'SHAD_NGS2_PROBE("voice_handle_request", rackHandle, voiceIndex, outHandle, outHandle ? sizeof(*outHandle) : 0);',
    "sceNgs2ParseWaveformData": 'Ngs2Probe::Waveform(data, dataSize);',
    "sceNgs2CalcWaveformBlock": 'SHAD_NGS2_PROBE("calc_format", samplePos, numSamples, format, format ? sizeof(*format) : 0);',
    "sceNgs2VoiceControl": 'Ngs2Probe::Control(voiceHandle, paramList);',
    "sceNgs2VoiceGetState": 'SHAD_NGS2_PROBE("state_before", voiceHandle, stateSize, outState, stateSize);',
    "sceNgs2VoiceGetStateFlags": 'SHAD_NGS2_PROBE("flags_before", voiceHandle, 4, outStateFlags, outStateFlags ? sizeof(*outStateFlags) : 0);',
    "sceNgs2SystemRender": 'Ngs2Probe::Render(systemHandle, aBufferInfo, numBufferInfo);',
}
SPAM = [
    'LOG_ERROR(Lib_Ngs2, "samplePos = {}, numSamples = {}", samplePos, numSamples);',
    'LOG_ERROR(Lib_Ngs2, "dataSize = {}", dataSize);',
    'LOG_ERROR(Lib_Ngs2, "stateSize = {}", stateSize);',
]

def instrument(source: str) -> str:
    if 'tools/ngs2_probe/probe.h' in source:
        raise ValueError("Source already instrumented; refusing to duplicate hooks")
    result = '#include "tools/ngs2_probe/probe.h"\n' + source
    for name, hook in HOOKS.items():
        pattern = re.compile(r'\bs32\s+PS4_SYSV_ABI\s+' + re.escape(name) + r'\s*\([^;{}]*\)\s*\{')
        matches = list(pattern.finditer(result))
        if len(matches) != 1:
            raise ValueError(f"Expected one definition for {name}, found {len(matches)}")
        pos = matches[0].end()
        result = result[:pos] + '\n    ' + hook + result[pos:]
    for text in SPAM:
        if result.count(text) != 1:
            raise ValueError(f"Stub log anchor mismatch: {text}")
        result = result.replace(text, 'if (!Ngs2Probe::Enabled()) ' + text)
    for name in ('sceNgs2VoiceControl', 'sceNgs2VoiceGetStateFlags'):
        pattern = re.compile(r'(s32 PS4_SYSV_ABI ' + name + r'\([^{}]*\)\s*\{.*?)(    LOG_ERROR\(Lib_Ngs2, "called"\);)', re.S)
        result, count = pattern.subn(r'\1    if (!Ngs2Probe::Enabled()) LOG_ERROR(Lib_Ngs2, "called");', result, count=1)
        if count != 1:
            raise ValueError(f"Could not identify entry log for {name}")
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error('Output must not overwrite source')
    try:
        patched = instrument(args.source.read_text(encoding='utf-8'))
        args.output.write_text(patched, encoding='utf-8')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'NGS2_INSTRUMENTATION=FAIL: {exc}\n')
    print(f'NGS2_INSTRUMENTATION=PASS hooks={len(HOOKS)}')
