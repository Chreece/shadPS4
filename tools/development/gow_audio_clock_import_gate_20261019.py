#!/usr/bin/env python3
"""GoW no-build import gate for opt-in Audio3D silent output clock candidate.

Scan only game executables/modules for direct NID references. A missing
GetLastOutputTime import with positive AudioOut imports rejects this
specific hypothesis before spending CPU time on an emulator build.
Does not read game data assets, modify saves, launch shadPS4 or change services.
"""
import argparse
import json
from pathlib import Path

NIDS = {
    "GetLastOutputTime": b"Ptlts326pds",
    "Output": b"QOQtbeDqsT4",
    "Outputs": b"w3PdaSTSwGE",
    "Audio3dOutOutput": b"7NYEzJ9SJbM",
    "Audio3dPortPush": b"VEVhZ9qd4ZY",
}
LIMIT=512*1024*1024

def inspect(paths):
    result={key:[] for key in NIDS}
    checked=[]
    for p in sorted(set(paths)):
        if p.is_symlink() or not p.is_file() or p.stat().st_size>LIMIT:
            continue
        try:
            contents=p.read_bytes()
        except OSError:
            continue
        checked.append(str(p))
        for name,nid in NIDS.items():
            if nid in contents:
                result[name].append(str(p))
    return checked,result

def selftest():
    found = lambda payload: {name: bool(value in payload) for name,value in NIDS.items()}
    assert found(b"xxxPtlts326pdsxxxQOQtbeDqsT4")["GetLastOutputTime"]
    assert found(b"xxxPtlts326pdsxxxQOQtbeDqsT4")["Output"]
    assert not found(b"QOQtbeDqsT4")["GetLastOutputTime"]
    print("GOW_AUDIO_IMPORT_GATE_NATIVE_SELFTEST=PASS cases=3")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,
      default=Path("/mnt/roms-all/ps4-games/CUSA34384"))
    ap.add_argument("--out",type=Path)
    ap.add_argument("--self-test",action="store_true")
    a=ap.parse_args()
    if a.self_test:return selftest()
    if not a.root.is_dir():
        raise SystemExit("GAME_LOCATION_MISSING_NO_BUILD="+str(a.root))
    files=[]
    for p in a.root.rglob("*"):
        if p.name=="eboot.bin" or p.suffix.lower() in (
                ".elf",".self",".prx",".sprx",".bin"):
            # Limit to executable-related dirs; never scan game WAD/asset packs.
            if p.name!="eboot.bin" and len(p.relative_to(a.root).parts)>5:
                continue
            if p.name!="eboot.bin" and p.suffix.lower()==".bin" and p.parent.name not in ("sce_module","exec"):
                continue
            files.append(p)
    checked,found=inspect(files)
    control=any(found[key] for key in ("Output","Outputs","Audio3dOutOutput","Audio3dPortPush"))
    clock=bool(found["GetLastOutputTime"])
    decision=("POSSIBLE_API_DEPENDENCY_SOURCE_FIX_TEST_WORTH_BUILDING" if clock else
              "NO_DIRECT_CLOCK_IMPORT_DO_NOT_BUILD_THIS_CANDIDATE" if control else
              "UNRESOLVED_NID_IMPORTS_DO_NOT_BUILD_WITHOUT_FURTHER_EVIDENCE")
    report=dict(game="CUSA34384",checked_files=checked,detected_imports=found,
                positive_audio_import_control=control,
                direct_getlastoutputtime_import=clock,
                decision=decision,game_files_changed=False,build_started=False)
    if a.out:
        a.out.parent.mkdir(parents=True,exist_ok=True)
        a.out.write_text(json.dumps(report,indent=2)+"\n")
    print("GAME_EXECUTABLES_CHECKED="+str(len(checked)))
    for key,matches in found.items():
        print("IMPORT_"+key.upper()+"="+str(bool(matches))+
              (" paths="+",".join(matches[:4]) if matches else ""))
    print("AUDIO_CLOCK_IMPORT_DECISION="+decision)
    print("NO_BUILD_NO_GAME_LAUNCH_NO_OLLAMA_OR_SUNSHINE_CHANGES=YES")

if __name__=="__main__":
    main()
