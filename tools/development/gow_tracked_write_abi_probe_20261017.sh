#!/usr/bin/env bash
set -Eeuo pipefail
root="$HOME/projects/shadps4/GodOfWarRagnarok/tests"
original="$root/native-rendered-gfx-20261016-152535-3014679/source"
isolated="$root/gow-renderer-log-hotpath-20261017/source"
logs="$HOME/projects/shadps4/logs"
stamp="$(date +%Y%m%d-%H%M%S)"
out="$logs/gow-tracked-write-abi-$stamp"
mkdir -p "$out"
trap 'rc=$?; tar -czf "$out.tar.gz" -C "$logs" "$(basename "$out")"; echo "ARCHIVE=$out.tar.gz"; echo "NO_BUILD=YES NO_SOURCE_CHANGES=YES SSH=OPEN"; exit "$rc"' EXIT
python3 - "$original" "$isolated" "$out" <<'PY'
from pathlib import Path
import hashlib,sys,json,re
orig,iso,out=map(Path,sys.argv[1:])
rel=Path("src/video_core/renderer_vulkan/vk_rasterizer.cpp")
hdr=Path("src/video_core/buffer_cache/buffer_cache.h")
result={}
for name,root in (("original",orig),("isolated",iso)):
    for label,relative in (("renderer",rel),("buffer_cache_header",hdr)):
        p=root/relative
        key=f"{name}_{label}"
        if not p.is_file():
            result[key]={"error":"missing"}
            continue
        source=p.read_text(errors="replace")
        lines=source.splitlines()
        result[key]={"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"lines":len(lines)}
        terms=("tracked_write_size","ObtainBuffer(","ReadMemory(","ObtainBufferFlags","IsWritten","IsTexelBuffer","InvalidateTextureCache","InvalidateMemoryOnGpuThread")
        hits=[i for i,line in enumerate(lines) if any(term in line for term in terms)]
        intervals=[]
        for i in hits:
            lo=max(0,i-12);hi=min(len(lines),i+15)
            if intervals and lo<=intervals[-1][1]:intervals[-1]=(intervals[-1][0],max(hi,intervals[-1][1]))
            else:intervals.append((lo,hi))
        evidence="\n\n".join(f"LINES {lo+1}-{hi}\n"+"\n".join(f"{j+1}: {lines[j]}" for j in range(lo,hi)) for lo,hi in intervals)
        (out/f"{key}.txt").write_text(evidence)
(out/"summary.json").write_text(json.dumps(result,indent=2)+"\n")
print("READ_ONLY_ABI_PROBE=PASS")
for k,v in result.items(): print(k,v)
PY
