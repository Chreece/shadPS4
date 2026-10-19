#!/usr/bin/env python3
"""Source-exact surgical Audio3D PortAdvance synchronization repair.

Never replace entire audio3d.cpp; retain all pre-existing tested audio
HLE implementation. Only insert a single std::scoped_lock into Advance
after the auto& port line and before any parameter/queue/PCM mutation.
Verify that reversing the change reproduces original bytes exactly.
"""
import argparse
import difflib
import hashlib
from pathlib import Path

ADVANCE = "s32 PS4_SYSV_ABI sceAudio3dPortAdvance("
END = "s32 PS4_SYSV_ABI sceAudio3dPortClose("
NEEDLE = """    auto& port = state->ports[port_id];

    if (port.parameters.buffer_mode == OrbisAudio3dBufferMode::ORBIS_AUDIO3D_BUFFER_NO_ADVANCE) {"""
REPLACE = """    auto& port = state->ports[port_id];
    // The Advance queue-depth check and ProcessMixQueue both mutate the
    // PCM and mixed-frame queues used by BedWrite/ObjectSetAttributes/Push.
    // Synchronize those operations with their existing per-port mutex.
    std::scoped_lock lock{port.mutex};

    if (port.parameters.buffer_mode == OrbisAudio3dBufferMode::ORBIS_AUDIO3D_BUFFER_NO_ADVANCE) {"""


def transform(raw: bytes):
    text = raw.decode("utf-8")
    if text.count(ADVANCE) != 1 or text.count(END) != 1:
        raise ValueError("ADVANCE_OR_CLOSE_FUNCTION_MISSING_OR_DUPLICATED")
    before = text.index(ADVANCE)
    after = text.index(END, before)
    body = text[before:after]
    if body.count(NEEDLE) != 1:
        raise ValueError("PORT_ADVANCE_QUEUE_ANCHOR_CHANGED_OR_ALREADY_LOCKED")
    new_body = body.replace(NEEDLE, REPLACE, 1)
    if new_body.count("std::scoped_lock lock{port.mutex};") != 1:
        raise ValueError("PORT_MUTEX_MISSING_OR_DUPLICATED_IN_ADVANCE")
    if new_body.index("std::scoped_lock lock{port.mutex};") >= new_body.index("ProcessMixQueue(port)"):
        raise ValueError("LOCK_AFTER_PCM_CONSUMPTION")
    candidate = text[:before]+new_body+text[after:]
    reverted = candidate[:before]+new_body.replace(REPLACE, NEEDLE,1)+candidate[before+len(new_body):]
    if reverted.encode()!=raw:
        raise ValueError("SOURCE_REVERSE_RECONSTRUCTION_NOT_EXACT")
    return candidate.encode("utf-8")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("source",type=Path)
    p.add_argument("diff",type=Path)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    if not a.source.is_file() or a.source.is_symlink():
        raise SystemExit("UNSAFE_AUDIO3D_SOURCE_NO_CHANGE")
    original=a.source.read_bytes()
    try:
        patched=transform(original)
    except (ValueError,UnicodeDecodeError) as exc:
        raise SystemExit("AUDIO3D_FIX_SOURCE_CONTRACT_FAILED="+str(exc))
    a.diff.parent.mkdir(parents=True,exist_ok=True)
    a.diff.write_text(
        "ORIGINAL_SHA256="+hashlib.sha256(original).hexdigest()+"\n"+
        "PATCHED_SHA256="+hashlib.sha256(patched).hexdigest()+"\n"+
        "".join(difflib.unified_diff(
            original.decode().splitlines(True),
            patched.decode().splitlines(True),
            fromfile="original/audio3d.cpp",tofile="patched/audio3d.cpp")))
    if a.apply:
        a.source.write_bytes(patched)
    print("AUDIO3D_ADVANCE_QUEUE_MUTEX_PATCH="+("APPLIED" if a.apply else "PREFLIGHT_ONLY"))
    print("EXACT_REVERSE_TRANSFORM=PASS; UNRELATED_AUDIO_SOURCE_PRESERVED=YES")


if __name__=="__main__":
    main()
