#!/usr/bin/env python3
"""Read-only GoW Audio3D queue-fix relevance and source-contract gate.

No build, game run, screenshots, signal, Ollama or source modification.
Checks direct NID imports and proves PortAdvance serializes the actual
shared mixed_queue and object PCM operations with the same per-port mutex
already used by PortPush and PCM/attribute writers.
"""
import argparse
import hashlib
import json
from pathlib import Path

# NIDs are exported symbols registered in src/core/libraries/audio3d/audio3d.cpp
NIDS = {
    "PortAdvance": b"lw0qrdSjZt8",
    "PortPush": b"VEVhZ9qd4ZY",
    "PortFlush": b"ZOGrxWLgQzE",
    "PortGetQueueLevel": b"YaaDbDwKpFM",
    "Audio3dAudioOutOutput": b"7NYEzJ9SJbM",
    "AudioOutOutput": b"QOQtbeDqsT4",
}
EXPECTED_PATCHED_AUDIO3D_BLOB = "45320f60d1565ace053b794335bd48c2e1a10edf"
MAX_EXEC = 512 * 1024 * 1024


def source_contract(source: str):
    prefix="s32 PS4_SYSV_ABI sceAudio3dPortAdvance("
    end="s32 PS4_SYSV_ABI sceAudio3dPortClose("
    if source.count(prefix)!=1 or source.count(end)!=1:
        raise ValueError("AUDIO3D_ADVANCE_FUNCTION_MISSING")
    body=source.split(prefix,1)[1].split(end,1)[0]
    markers={
        "port_mutex": "std::scoped_lock lock{port.mutex};",
        "mixed_queue_guard": "port.mixed_queue.size()",
        "process_mix": "ProcessMixQueue(port);",
    }
    if any(body.count(needle)!=1 for needle in markers.values()):
        raise ValueError("AUDIO3D_QUEUE_GUARD_OR_SHARED_MIX_OPERATION_MISSING")
    positions={k:body.index(v) for k,v in markers.items()}
    if positions["port_mutex"] >= min(positions["mixed_queue_guard"],
                                     positions["process_mix"]):
        raise ValueError("AUDIO3D_QUEUE_CONSUMED_BEFORE_LOCK")
    writer=source.split("s32 PS4_SYSV_ABI sceAudio3dObjectSetAttributes(",1)[1].split(
        "s32 PS4_SYSV_ABI sceAudio3dObjectUnreserve(",1)[0]
    if "std::scoped_lock lock{port.mutex};" not in writer:
        raise ValueError("PCM_ATTRIBUTE_PRODUCER_LOCK_MISSING")
    consumer=source.split("s32 PS4_SYSV_ABI sceAudio3dPortPush(",1)[1].split(
        "s32 PS4_SYSV_ABI sceAudio3dPortQueryDebug(",1)[0]
    if "std::scoped_lock lock{port.mutex};" not in consumer:
        raise ValueError("AUDIO3D_PORT_PUSH_QUEUE_LOCK_MISSING")
    return dict(port_advance_uses_same_port_mutex=True,
                PCM_attribute_producer_locked=True,
                port_push_consumer_locked=True,
                no_new_audio_out_calls_in_advance=("sceAudioOutOutput(" not in body))


def game_files(root:Path):
    found=[]
    for p in root.rglob("*"):
        if not p.is_file() or p.is_symlink():continue
        if p.name=="eboot.bin" or (p.suffix.lower() in (".prx",".sprx",".self",".elf") and
                                   len(p.relative_to(root).parts) <= 5):
            if p.stat().st_size<=MAX_EXEC:found.append(p)
    return sorted(found)


def scan_imports(root):
    matches={name:[] for name in NIDS}
    checked=[]
    for p in game_files(root):
        try:data=p.read_bytes()
        except OSError:continue
        checked.append(str(p))
        for name,nid in NIDS.items():
            if nid in data:matches[name].append(str(p))
    return checked,matches


def selftest():
    example=("s32 PS4_SYSV_ABI sceAudio3dPortAdvance("
             "port_id){ auto& port=state->ports[port_id];"
             " std::scoped_lock lock{port.mutex};"
             " if(port.mixed_queue.size()) return ProcessMixQueue(port); }"
             "s32 PS4_SYSV_ABI sceAudio3dPortClose("
             "s32 PS4_SYSV_ABI sceAudio3dObjectSetAttributes("
             " std::scoped_lock lock{port.mutex};"
             "s32 PS4_SYSV_ABI sceAudio3dObjectUnreserve("
             "s32 PS4_SYSV_ABI sceAudio3dPortPush("
             " std::scoped_lock lock{port.mutex};"
             "s32 PS4_SYSV_ABI sceAudio3dPortQueryDebug(")
    assert source_contract(example)["port_advance_uses_same_port_mutex"]
    try:source_contract(example.replace("std::scoped_lock lock{port.mutex};","",1))
    except ValueError:pass
    else:raise AssertionError("MISSING_PORT_LOCK_WAS_NOT_REJECTED")
    assert NIDS["PortPush"] in b"prefixVEVhZ9qd4ZYsuffix"
    print("AUDIO3D_QUEUE_SOURCE_CONTRACT_SELFTEST=PASS cases=3")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--self-test",action="store_true")
    p.add_argument("--game-root",type=Path,
                   default=Path("/mnt/roms-all/ps4-games/CUSA34384"))
    p.add_argument("--source",type=Path)
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.self_test:return selftest()
    if not a.source or not a.out:
        p.error("--source and --out required")
    if not a.game_root.is_dir() or not a.source.is_file() or a.source.is_symlink():
        raise SystemExit("GAME_OR_PINNED_SOURCE_MISSING_NO_BUILD")
    raw=a.source.read_bytes()
    # git blob hash rather than SHA-256: EXACT GitHub source blob.
    import subprocess
    # Avoid shell and subprocess completely: Git blob is SHA-1 over prefix+content.
    blob=hashlib.sha1((f"blob {len(raw)}\0").encode()+raw).hexdigest()
    if blob!=EXPECTED_PATCHED_AUDIO3D_BLOB:
        raise SystemExit("AUDIO3D_PINNED_PATCH_SOURCE_HASH_MISMATCH="+blob)
    contract=source_contract(raw.decode())
    checked,imports=scan_imports(a.game_root)
    direct_advance=bool(imports["PortAdvance"])
    decision=("ADVANCE_IMPORTED_PATCH_BUILD_CANDIDATE_JUSTIFIED" if direct_advance else
              "NO_DIRECT_ADVANCE_IMPORT_DO_NOT_BUILD_FOR_GOW")
    report=dict(game="CUSA34384",decision=decision,
                scanned_executable_modules=checked,
                direct_symbol_references=imports,
                queue_patch_contract=contract,
                source_git_blob=blob,
                game_files_modified=False,emulator_build_started=False,
                causal_audio_stutter_fix_proven=False)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,indent=2)+"\n")
    print("GAME_EXECUTABLE_FILES_CHECKED="+str(len(checked)))
    for name,files in imports.items():
        print("DIRECT_NID_"+name.upper()+"="+str(bool(files)))
    print("PROTECTED_AUDIO3D_QUEUE_SOURCE_CONTRACT=PASS")
    print("NEXT_DECISION="+decision)
    print("NO_GAME_RUN_NO_COMPILATION_NO_OLLAMA_OR_SUNSHINE_CHANGES=YES")
    print("UPLOAD_REPORT="+str(a.out))


if __name__=="__main__":
    main()
