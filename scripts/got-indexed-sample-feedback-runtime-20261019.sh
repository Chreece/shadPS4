#!/usr/bin/env bash
# Bounded executed texture-index and sampled-value feedback, with scene GPU queries.
# No rebuild, global graphics config changes, or active executable writes.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
LOGS="$ROOT/logs"
BIN="$WORK/build/shadps4"
PIN="aa775cd9965118910d44e675708e7fc0cc2a62a1"
CANDIDATE="$WORK/bin-backups/shadps4-got-indexed-sample-feedback-$PIN"
HOST_REPORT="${GOT_INDEXED_SAMPLE_FEEDBACK_HOST_REPORT:-$LOGS/got-indexed-sample-feedback-host-not-built.tar.gz}"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
API="http://127.0.0.1:7869"
MIN_VRAM=$((8*1024*1024*1024))
SECONDS_TO_RUN=300
RUN="$GOT/tests/runs/got-indexed-sample-feedback-runtime-$(date +%Y%m%d-%H%M%S)"
REPORT="$LOGS/$(basename "$RUN").tar.gz"
STATUS=PRECHECK
GAME_EXIT=NOT_RUN
OLLAMA_ACTION=UNCHANGED
OLLAMA_RESTORE=NOT_NEEDED
OLLAMA_TOUCHED=0
OLLAMA_ID=""
TEST_PGID=""
BIN_SHA=""
GAME_LAUNCHED=NO
START_ISO="$(date -Is)"
mkdir -p "$RUN/info" "$RUN/screenshots" "$RUN/profile/user/shader/got-menu-probes" "$LOGS"
touch "$RUN/info/console.log" "$RUN/game.log"
exec > >(tee -a "$RUN/info/console.log") 2>&1

cleanup() {
  rc=$?
  trap - EXIT INT TERM HUP
  set +e
  if [ -n "$TEST_PGID" ] && kill -0 "$TEST_PGID" 2>/dev/null; then
    kill -TERM -- "-$TEST_PGID" 2>/dev/null || true
    for i in 1 2 3 4 5 6; do
      kill -0 "$TEST_PGID" 2>/dev/null || break
      sleep 1
    done
    kill -KILL -- "-$TEST_PGID" 2>/dev/null || true
    wait "$TEST_PGID" 2>/dev/null || true
  fi
  if [ "$OLLAMA_TOUCHED" = 1 ]; then
    if [ "$(docker inspect -f '{{.State.Running}}' "$OLLAMA_ID" 2>/dev/null)" != true ]; then
      docker start "$OLLAMA_ID" >"$RUN/info/ollama-start.log" 2>&1
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$OLLAMA_ID" 2>/dev/null)" = true ] &&
       python3 "$RUN/info/ollama.py" restore "$API" "$RUN/info/ollama-before.json" \
         > "$RUN/info/ollama-reload.log" 2>&1; then
      OLLAMA_RESTORE=PASS
    else
      OLLAMA_RESTORE=FAILED
      rc=3
    fi
  fi
  if [ -n "$BIN_SHA" ]; then
    AFTER="$(sha256sum "$BIN" 2>/dev/null | awk '{print $1}')"
    if [ "$AFTER" != "$BIN_SHA" ]; then rc=3; fi
    echo "ACTIVE_BINARY_UNCHANGED=$([ "$AFTER" = "$BIN_SHA" ] && echo PASS || echo FAIL)"
  fi
  grep -aE 'TextureCacheSlots:|TextureSlotPressure:|SlotVector pool=|Assertion Failed!' \
    "$RUN/game.log" > "$RUN/info/texture-cache-slots.log" || true
  grep -aE 'BoundedPointerImage:|GuestStridedSourceProof:|GuestStridedPermutationProof:|GuestTsharpIncompleteProgram:|RealIndexedTSharp(Enabled|Bound|UnsupportedSize):|ResourceNullBuffer:|GPUVM fault detected|GPU hang detected' \
    "$RUN/game.log" > "$RUN/info/graphics-evidence.log" || true
  grep -aE 'IndexedImageFeedback(Submitted)?:' "$RUN/game.log" \
    > "$RUN/info/indexed-image-feedback.log" || true
  python3 - "$RUN/info/indexed-image-feedback.log" "$RUN/info/indexed-image-feedback-summary.txt" <<'PYFEEDBACK'
import re,sys
from pathlib import Path
raw=re.sub(r"\x1b\[[0-9;]*[mK]", "", Path(sys.argv[1]).read_text(errors="replace"))
rows=[x.split("IndexedImageFeedback:",1)[1].strip() for x in raw.splitlines()
      if "IndexedImageFeedback:" in x and "timeline_complete=YES" in x]
def value(row,key):
    m=re.search(r"\b"+key+r"=(\d+)",row)
    return int(m[1]) if m else 0
out=["PURPOSE=OBSERVE_EXECUTED_GPU_TEXTURE_IDS_AND_SAMPLE_VALUES",
     "DRAW_BINDINGS=1,32,256 EACH_CAPTURE_RESET=YES",
     "NONZERO_COUNTS_INCLUDE_ALPHA_AND_DO_NOT_PROVE_CORRECT_COLOR=YES",
     "ZERO_SAMPLES_DO_NOT_PROVE_INVALID_DESCRIPTORS=YES",
     f"COMPLETED_RESOURCE_ROWS={len(rows)}"]
for key in ["samples","out_of_range","nonzero_samples","nonfinite_samples"]:
    out.append(key.upper()+"="+str(sum(value(r,key) for r in rows)))
for shader in sorted(set(re.search(r"shader=(\S+)", row)[1] for row in rows)):
    matching=[r for r in rows if re.search(r"shader=(\S+)",r)[1]==shader]
    out.append("SHADER="+shader+" COMPLETED_ROWS="+str(len(matching))+" "+
               " ".join(k.upper()+"="+str(sum(value(r,k) for r in matching))
                        for k in ["samples","out_of_range","nonzero_samples","nonfinite_samples"]))
out.extend(rows)
if not rows: out.append("RESULT=NO_COMPLETED_GPU_CAPTURE")
elif not sum(value(r,"samples") for r in rows): out.append("RESULT=NO_EXECUTED_SAMPLES_IN_CAPTURED_DRAWS")
else: out.append("RESULT=EXECUTED_SAMPLE_EVIDENCE_CAPTURED_NOT_A_GRAPHICS_FIX")
Path(sys.argv[2]).write_text("\n".join(out)+"\n")
print("\n".join(out))
PYFEEDBACK
  # Extract resource-selection records independently of
  # large texture binding successes. Preserve the full game log as well.
  grep -aE 'BoundedPointerImage:|SrtGlobalDependencyResult:|SrtHostBarrier:|SrtBarrierShape:|SrtHostBarrierShape:|SrtHostBarrierSummary:|SrtHostBarrierGroup:|SrtNativeAudit:|SrtSourceAudit:|SrtPlanSkip:|SrtPlanCollision:|SrtInlineCandidate:|SrtInlineEligibility:|InvalidTSharpSource:|InvalidVSharpSource:|SharpMaskPreserved:|ResourceAuditCompile:|RealIndexedTSharpBound:|GuestAttributeTsharpIndexed:|IndexedTSharpSource:' \
    "$RUN/game.log" >"$RUN/info/menu-provenance-all.log" || true
  # Correlate submitted guest shader draws and actual video-out sources.
  # Every event uses steady_clock monotonic milliseconds across threads.
  grep -aE 'GotMenuDrawTrace:|GotMenuPassWindow:|GotMenuPassTop:|GotMenuPresentTrace:|GotMenuBlankPresent:|GotMenuFastClear:|GotMenuSourceEdge:|GotMenuMrtTargetTrace:|GotMenuDepthTargetTrace:|GotMenuDepthAlias:|GotMenuDepthImageRequest:|GotMenuDepthImageResolved:|GotMenuComputeImageWriter:|GotMenuComputeBufferWriter:|GotMenuComputeImageCopy:|GotMenuComputeImageClear:' \
    "$RUN/game.log" > "$RUN/info/presented-color-events.log" || true
  python3 - "$RUN/info/presented-color-events.log" "$RUN/info/presented-color-summary.txt" <<'PYMENUCOLOUR'
from collections import Counter
from pathlib import Path
import re,sys
log=Path(sys.argv[1]).read_text(errors="replace")
log=re.sub(r"\x1b\[[0-9;]*[mK]","",log)
lines=log.splitlines()
sources=[];draws=[];windows=[];clears=[]
for line in lines:
    def item(name):
        m=re.search(r"\b"+name+r"=([^\s]+)",line)
        return m.group(1) if m else ""
    if "GotMenuPresentTrace:" in line:
        sources.append((item("mono_ms"),item("source_addr"),
                        item("source"),item("frame")))
    elif "GotMenuPassTop:" in line:
        windows.append((item("mono_ms"),item("fs"),item("color0"),
                        int(item("draws") or "0"),item("fb")))
    elif "GotMenuDrawTrace:" in line:
        draws.append((item("mono_ms"),item("fs"),item("color0"),item("fb")))
    elif "GotMenuFastClear:" in line:
        clears.append((item("mono_ms"),item("target"),item("clear0")))
addresses=Counter(addr for t,addr,size,frame in sources)
most=Counter()
on_present=Counter()
for t,shader,addr,count,fb in windows:
    most[(shader,addr)]+=count
    if addr in addresses:on_present[(shader,addr)]+=count
out=[
 "PURPOSE=MAP_WHITE_MENU_TO_REAL_PRESENTED_FRAMEBUFFER",
 f"PRESENT_SAMPLES={len(sources)} DISTINCT_PRESENT_ADDRS={len(addresses)}",
 f"TOP_DRAW_EVENTS={len(windows)} DRAW_FIRST_EVENTS={len(draws)} FAST_CLEARS={len(clears)}",
]
out.extend(f"PRESENT_ADDRESS={addr} count={n}" for addr,n in addresses.most_common(15))
out.extend(f"DRAW_SHADER={shader} TARGET={addr} WINDOW_DRAWS={n} DIRECTLY_PRESENTED={addr in addresses}"
           for (shader,addr),n in most.most_common(30))
out.extend(f"DIRECT_PRESENT_SHADER={shader} TARGET={addr} WINDOW_DRAWS={n}"
           for (shader,addr),n in on_present.most_common(20))
out.extend(f"FAST_CLEAR time={t} target={addr} clear0={clr}"
           for t,addr,clr in clears[:25])
if not sources:out.append("PRESENT_TRACE_MISSING_NO_RENDERING_CLAIM")
if not draws:out.append("DRAW_TRACE_MISSING_NO_SHADER_CLAIM")
Path(sys.argv[2]).write_text("\n".join(out)+"\n")
print("\n".join(out))
PYMENUCOLOUR
  # Find the screen's actual fullscreen source, its guest address, and the
  # exact shader whose output was subsequently read from that address.
  # Correlate actual colour attachments 0..7, including the 1920x1080
  # source 0x1504a08000 that the earlier color0-only trace could NOT see.
  # Always save summary, even if some shader producer is not in this capture.
  python3 - "$RUN/info/presented-color-events.log" "$RUN/info/texture-source-graph.txt" <<'PYTEXGRAPH'
import re,sys
from pathlib import Path
from collections import Counter,defaultdict
raw=Path(sys.argv[1]).read_text(errors="replace")
raw=re.sub(r"\x1b\[[0-9;]*[mK]","",raw)
presented=Counter()
writers=defaultdict(set)
writer_counts=Counter()
writer_masks={}
edges=defaultdict(Counter)
properties={}
mrt_events=0
for line in raw.splitlines():
    def item(key):
        match=re.search(r"\b"+re.escape(key)+r"=([^\s]+)",line)
        return match.group(1) if match else ""
    if "GotMenuPresentTrace:" in line:
        presented[item("source_addr")]+=1
    elif "GotMenuMrtTargetTrace:" in line:
        mrt_events+=1
        addr=item("address")
        shader=item("fs")
        if addr not in ("","0x0") and item("can_write")=="true":
            writers[addr].add(shader)
            writer_counts[(addr,shader)]+=1
            writer_masks[(addr,shader)]=(item("attachment"),
                                          item("guest_write_mask"),
                                          item("vk_write_mask"))
    elif "GotMenuDrawTrace:" in line:
        if item("output")=="true":
            writers[item("color0")].add(item("fs"))
    elif "GotMenuSourceEdge:" in line:
        key=(item("fs"),item("target"),item("resource"))
        address=item("source_addr")
        edges[key][address]+=1
        properties[(key,address)]=(item("kind"),item("fetched"),
                                    item("dimensions"),item("data_format"),
                                    item("number_format"))
out=["PURPOSE=ALL_MRT_SHADER_TO_TEXTURE_TO_FINAL_FRAMEBUFFER_PROOF",
     f"PRESENT_SAMPLES={sum(presented.values())} DISTINCT_PRESENT_TARGETS={len(presented)}",
     f"MRT_TARGET_EVENTS={mrt_events} DISTINCT_MRT_WRITTEN_ADDRESSES={len(writers)}",
     f"DISTINCT_SAMPLING_EDGES={len(edges)}",
     "KNOWN_WHITE_MENU=YES_CAPTURE_SCREENSHOTS_FOR_VISUAL_VALIDATION"]
out.extend(f"PRESENTED={addr} frames={n}"
           for addr,n in presented.most_common(8))
out.append("FINAL_SCREEN_FS=0x96aa3f2e")
out.append("FINAL_GUEST_SOURCE=0x1504210000")
out.append("TONEMAP_INPUT=0x1504a08000")
for addr in ("0x1504210000","0x1525478000","0x1504a08000","0x15203f8000"):
    stage_writers=sorted(writers.get(addr,()))
    out.append(f"KEY_TARGET={addr} WRITER_SHADERS={','.join(stage_writers) or 'NONE_OBSERVED'}")
    for fs in stage_writers:
        extra=writer_masks.get((addr,fs),("", "", ""))
        out.append(f"  MRT_WRITER_FS={fs} TARGET={addr} samples={writer_counts[(addr,fs)]} "
                   f"attachment={extra[0]} guest_mask={extra[1]} vk_mask={extra[2]}")
for (fs,target,res), sources in sorted(edges.items()):
    if target not in presented and target not in (
        "0x1504210000","0x1525478000","0x1504a08000"):
        continue
    for source,n in sources.most_common(4):
        kind,fetched,dims,fmt,nfmt=properties[((fs,target,res),source)]
        producers=','.join(sorted(writers.get(source,()))) or "NO_MRT_WRITER_IN_CAPTURE"
        out.append(f"SOURCE_EDGE fs={fs} target={target} resource={res} "
                   f"source={source} samples={n} kind={kind} fetched={fetched} "
                   f"dimensions={dims} format={fmt} number_format={nfmt} "
                   f"producer_fs={producers}")
for root in ("0x1504a08000","0x1525478000","0x1504210000"):
    for (fs,target,res), sources in sorted(edges.items()):
        if root not in sources: continue
        out.append(f"DOWNSTREAM_OF_{root}={fs}@{target} slot={res}")
if mrt_events==0:
    out.append("MRT_TRACE_MISSING=YES_NOT_EVIDENCE_OF_NO_SCENE_DRAW")
if "0x1504a08000" not in writers:
    out.append("SCENE_COLOR_WRITER=NOT_YET_CAPTURED_CHECK_OTHER_OUTPUT_PATHS")
path=Path(sys.argv[2])
path.write_text("\n".join(out)+"\n")
print("\n".join(out[:100]))
PYTEXGRAPH

  # The guest surface is a 1920x1080 R32_FLOAT image. Absence of
  # color-MRT writers is not evidence of corruption if a depth target
  # writes it. Correlate real depth draws, caches, compute writes, HLE copies.
  python3 - "$RUN/info/presented-color-events.log" \
      "$RUN/info/depth-source-provenance.txt" <<'PYDEPTHSOURCE'
import sys,re
from collections import Counter,defaultdict
from pathlib import Path
raw=Path(sys.argv[1]).read_text(errors="replace")
raw=re.sub(r"\x1b\[[0-9;]*[mK]","",raw)
watch="0x1504a08000"
depth=Counter()
aliases=Counter()
requests=Counter()
resolutions=Counter()
computes=Counter()
compute_buffer=Counter()
copies=Counter()
clears=Counter()
scene_color=Counter()
details=[]
for line in raw.splitlines():
    def attr(name):
        m=re.search(r"\b"+name+r"=([^\s]+)",line)
        return m.group(1) if m else ""
    if "GotMenuDepthTargetTrace:" in line:
        address=attr("depth_address");write=attr("depth_write_address")
        if address==watch or write==watch:
            shader=attr("fs")
            depth[shader]+=1
            if len(details)<60:
                details.append(f"DEPTH_WRITER shader={shader} address={address} "
                               f"write_address={write} depth_enable={attr('depth_enable')} "
                               f"depth_write={attr('depth_write')} clear={attr('depth_clear')} "
                               f"zformat={attr('zformat')} color0={attr('color0')} "
                               f"indices={attr('indices')}")
    elif "GotMenuDepthAlias:" in line:
        aliases[attr("phase")]+=1
        if len(details)<75:details.append("DEPTH_ALIAS "+line.split("GotMenuDepthAlias:",1)[-1].strip())
    elif "GotMenuDepthImageRequest:" in line:
        requests[(attr("binding"),attr("depth"),attr("vk_format"))]+=1
        if len(details)<85:details.append("DEPTH_REQUEST "+line.split("GotMenuDepthImageRequest:",1)[-1].strip())
    elif "GotMenuDepthImageResolved:" in line:
        resolutions[(attr("requested_depth"),attr("cached_depth"),
                     attr("req_fmt"),attr("cached_fmt"))]+=1
        if len(details)<95:details.append("DEPTH_RESOLVED "+line.split("GotMenuDepthImageResolved:",1)[-1].strip())
    elif "GotMenuComputeImageWriter:" in line:
        computes[attr("cs")]+=1
    elif "GotMenuComputeBufferWriter:" in line:
        compute_buffer[attr("cs")]+=1
    elif "GotMenuComputeImageCopy:" in line:
        copies[(attr("cs"),attr("src"),attr("dst"))]+=1
    elif "GotMenuComputeImageClear:" in line:
        clears[attr("cs")]+=1
    elif "GotMenuMrtTargetTrace:" in line and attr("address")=="0x15203f8000" and attr("can_write")=="true":
        scene_color[(attr("fs"),attr("attachment"))]+=1
out=[
"FOCUS=WHY_GOT_MENU_SCENE_IS_WHITE",
"WATCHED_R32_FLOAT_SOURCE=0x1504a08000",
"SCENE_GEOMETRY_RGBA16_FLOAT=0x15203f8000",
"ABSENCE_OF_MRT_WRITER_DOES_NOT_PROVE_MISSING_DEPTH=YES",
f"WATCHED_DEPTH_DRAW_SAMPLES={sum(depth.values())}",
f"WATCHED_DEPTH_SHADER_COUNT={len(depth)}",
f"WATCHED_DEPTH_ALIASES={sum(aliases.values())}",
f"WATCHED_IMAGE_REQUESTS={sum(requests.values())}",
f"WATCHED_IMAGE_RESOLUTIONS={sum(resolutions.values())}",
f"WATCHED_COMPUTE_STORAGE_IMAGE_WRITES={sum(computes.values())}",
f"WATCHED_COMPUTE_STORAGE_BUFFER_WRITES={sum(compute_buffer.values())}",
f"WATCHED_HLE_IMAGE_COPIES={sum(copies.values())}",
f"WATCHED_HLE_IMAGE_CLEARS={sum(clears.values())}",
f"SCENE_RGBA16_GEOMETRY_WRITER_SAMPLES={sum(scene_color.values())}",
]
out.extend(f"DEPTH_FS={shader} traces={n}" for shader,n in depth.most_common())
out.extend(f"SCENE_RGBA_FS={shader} mrt={slot} trace_samples={n}"
           for (shader,slot),n in scene_color.most_common(24))
out.extend(f"DEPTH_ALIAS_PHASE={name} traces={n}" for name,n in aliases.most_common())
out.extend(f"IMAGE_REQUEST binding={b} depth={d} format={f} samples={n}"
           for (b,d,f),n in requests.most_common(12))
out.extend(f"IMAGE_RESOLVED requested_depth={d} cached_depth={cd} "
           f"requested_format={f} cached_format={cf} samples={n}"
           for (d,cd,f,cf),n in resolutions.most_common(12))
out.extend(f"COMPUTE_IMAGE_CS={h} observations={n}" for h,n in computes.most_common())
out.extend(f"COMPUTE_BUFFER_CS={h} observations={n}" for h,n in compute_buffer.most_common())
out.extend(f"COMPUTE_HLE_COPY={k} observations={n}" for k,n in copies.most_common())
out.extend(f"COMPUTE_HLE_CLEAR={h} observations={n}" for h,n in clears.most_common())
if not depth and not computes and not compute_buffer and not copies and not clears:
    out.append("PRODUCER_NOT_OBSERVED=YES_OUT_OF_BAND_OR_OTHER_SOURCE")
if not requests:
    out.append("WATCHED_CACHE_REQUESTS_NOT_SEEN=YES_INSUFFICIENT_ALIAS_EVIDENCE")
out+=details
Path(sys.argv[2]).write_text("\n".join(out)+"\n")
print("\n".join(out[:105]))
PYDEPTHSOURCE

  grep -aE 'shader=0x7eaf40ad' "$RUN/info/menu-provenance-all.log" \
    >"$RUN/info/menu-provenance-target.log" || true
  python3 - "$RUN/info/menu-provenance-target.log" "$RUN/info/menu-provenance-summary.txt" <<'PYMENU'
from pathlib import Path
import re,sys
log=Path(sys.argv[1]).read_text(errors="replace")
log=re.sub(r"\x1b\[[0-9;]*m","",log)
lines=log.splitlines()
families=("SrtHostBarrier","SrtHostBarrierGroup","SrtNativeAudit","SrtSourceAudit",
          "SrtInlineCandidate","SrtInlineEligibility","SrtPlanSkip","SrtPlanCollision",
          "InvalidTSharpSource","InvalidVSharpSource","SharpMaskPreserved",
          "ResourceAuditCompile","RealIndexedTSharpBound","GuestAttributeTsharpIndexed","IndexedTSharpSource")
result=["MENU_SHADER=0x96aa3f2e","MODE=R32_FLOAT_DEPTH_VS_COLOR_PROVENANCE"]
for family in families:
    rows=[line for line in lines if family+":" in line]
    result.append(f"{family.upper()}_ROWS={len(rows)}")
    if family=="InvalidTSharpSource":
        result.extend("INVALID_IMAGE="+line.split("InvalidTSharpSource:",1)[1].strip()
                      for line in rows[:20])
    if family=="SrtHostBarrierGroup":
        result.extend("HOST_BLOCKER="+line.split("SrtHostBarrierGroup:",1)[1].strip()
                      for line in rows[:30])
if not any("InvalidTSharpSource:" in v for v in lines):
    result.append("INVALID_IMAGE_PROVENANCE=NOT_CAPTURED_CHECK_COMPILE_AND_CACHE")
if not any("SrtNativeAudit:" in v or "SrtHostBarrier:" in v for v in lines):
    result.append("SRT_PROVENANCE=NOT_CAPTURED_CHECK_SHADER_COMPILATION")
if not any("GuestAttributeTsharpIndexed:" in v for v in lines):
    result.append("GUEST_ATTRIBUTE_INDEXED=NOT_SEEN_NO_RECOVERY_CLAIM")
if any("InvalidTSharpSource:" in v for v in lines):
    result.append("INVALID_IMAGE_REMAINS=YES")
result.append("TARGETED_LOG_PATH=info/menu-provenance-target.log")
Path(sys.argv[2]).write_text("\n".join(result)+"\n")
print("\n".join(result))
PYMENU

  python3 - "$RUN/info/graphics-evidence.log" "$RUN/info/shader-summary.txt" <<'PY'
import pathlib,re,collections,sys
lines=pathlib.Path(sys.argv[1]).read_text(errors="replace").splitlines()
out=[]
for h in ("0x55d17c16","0x5c4c2b6f","0x7eaf40ad"):
    rows=[s for s in lines if "shader="+h in s.lower()]
    proofs=[s for s in rows if "GuestStridedSourceProof:" in s]
    reasons=collections.Counter(re.search(r"reason=([A-Z_]+)",s).group(1)
        for s in proofs if re.search(r"reason=([A-Z_]+)",s))
    out.append(f"SHADER={h} PROOFS={len(proofs)} INCOMPLETE={sum('GuestTsharpIncompleteProgram:' in s for s in rows)} BOUND={sum('RealIndexedTSharpBound:' in s for s in rows)}")
    out += [f"  REASON={name} COUNT={n}" for name,n in sorted(reasons.items())]
pathlib.Path(sys.argv[2]).write_text("\n".join(out)+"\n")
print("\n".join(out))
PY
  if command -v journalctl >/dev/null; then
    journalctl -k -b --since "$START_ISO" -o short-iso --no-pager \
      > "$RUN/info/kernel-since-test.log" 2> "$RUN/info/kernel-permission.txt" ||
    sudo -n journalctl -k -b --since "$START_ISO" -o short-iso --no-pager \
      > "$RUN/info/kernel-since-test.log" 2>> "$RUN/info/kernel-permission.txt" || true
    grep -Ei 'amdgpu.*(reset|timeout|fault)|GPUVM fault|soft lockup|MES failed|init_user_pages' \
      "$RUN/info/kernel-since-test.log" >"$RUN/info/kernel-gpu-errors.log" || true
    echo "KERNEL_GPU_ERRORS=$(wc -l < "$RUN/info/kernel-gpu-errors.log")"
  fi
  # Capture the actually active 5-MRT renderer shaders. Earlier Z-clip
  # candidates dumped unrelated shader hashes and therefore never tested
  # the pipelines dominating the white-menu frame.
  mkdir -p "$RUN/info/active-five-mrt-shaders"
  find "$RUN/profile/user/shader/dumps" -maxdepth 1 -type f \
    \( -iname '*c1123f0f*' -o -iname '*c8caf263*' \
       -o -iname '*5116e459*' -o -iname '*ae1cc7b7*' \) \
    -size -6M -print0 2>/dev/null |
    while IFS= read -r -d '' f; do
      cp -p -- "$f" "$RUN/info/active-five-mrt-shaders/$(basename "$f")" || true
    done
  find "$RUN/info/active-five-mrt-shaders" -maxdepth 1 -type f \
    -printf '%f bytes=%s\n' >"$RUN/info/active-five-mrt-shader-files.txt"

  # Preserve the real compiled SPIR-V of the *final* fullscreen fragment
  # shader for math/format review. The targeted shader-dump support is
  # already in this source base; no global dumps or patches are enabled.
  mkdir -p "$RUN/info/final-fragment-shader"
  find "$RUN/profile/user/shader/dumps" -maxdepth 1 -type f \
    -iname '*96aa3f2e*' -size -6M -print0 2>/dev/null |
    while IFS= read -r -d '' shader; do
      cp -p -- "$shader" "$RUN/info/final-fragment-shader/$(basename "$shader")" || true
    done
  find "$RUN/info/final-fragment-shader" -maxdepth 1 -type f \
    -printf '%f bytes=%s\n' >"$RUN/info/final-shader-files.txt"

  # Preserve the exact instrumented shader and its resource-selection IR.
  mkdir -p "$RUN/info/unresolved-image-shader"
  find "$RUN/profile/user/shader/dumps" -maxdepth 1 -type f \
    -iname '*7eaf40ad*' -size -6M -print0 2>/dev/null |
    while IFS= read -r -d '' shader; do
      cp -p -- "$shader" "$RUN/info/unresolved-image-shader/$(basename "$shader")" || true
    done
  find "$RUN/info/unresolved-image-shader" -maxdepth 1 -type f \
    -printf '%f bytes=%s\n' >"$RUN/info/unresolved-image-shader-files.txt"

  mkdir -p "$RUN/info/material-table-shaders"
  find "$RUN/profile/user/shader/dumps" -maxdepth 1 -type f \
    \( -iname '*4950cf6*' -o -iname '*55d17c16*' -o -iname '*52b6e003*' \
       -o -iname '*836253ca*' -o -iname '*22d378cd*' -o -iname '*5116e459*' \
       -o -iname '*5c4c2b6f*' -o -iname '*b5c898c7*' \) \
    -size -6M -print0 2>/dev/null |
    while IFS= read -r -d '' shader; do
      cp -p -- "$shader" "$RUN/info/material-table-shaders/$(basename "$shader")" || true
    done
  find "$RUN/info/material-table-shaders" -maxdepth 1 -type f \
    -printf '%f bytes=%s\n' > "$RUN/info/material-table-shader-files.txt"
  grep -aE 'LiveMaterialPermutation|GuestStridedSourceProof:|GuestTsharpIncompleteProgram:|RealIndexedTSharpEnabled:|RealIndexedTSharpBound:|StridedSamplerBound:|InvalidTSharpSource:|InvalidVSharpSource:' \
    "$RUN/game.log" > "$RUN/info/indexed-table-transitions.log" || true

  if command -v spirv-val >/dev/null 2>&1; then
    validation_files=0
    validation_failures=0
    while IFS= read -r -d '' shader; do
      validation_files=$((validation_files+1))
      echo "VALIDATING=$shader" >> "$RUN/info/indexed-shader-validation.log"
      if ! spirv-val --target-env vulkan1.3 "$shader" >> "$RUN/info/indexed-shader-validation.log" 2>&1; then
        validation_failures=$((validation_failures+1))
      fi
    done < <(find "$RUN/info/unresolved-image-shader" "$RUN/info/material-table-shaders" \
      -type f -name '*.spv' -print0)
    echo "SPIRV_FILES=$validation_files FAILURES=$validation_failures" >> "$RUN/info/indexed-shader-validation.log"
    if [ "$validation_failures" -gt 0 ]; then
      STATUS=INDEXED_SHADER_SPIRV_VALIDATION_FAILED
      rc=2
    fi
  else
    echo "SPIRV_VALIDATION=NOT_RUN_TOOL_UNAVAILABLE" > "$RUN/info/indexed-shader-validation.log"
  fi

  # Counters are values returned by the device's GPU queries, not
  # guessed from CPU-side draw submissions or shader compilation logs.
  grep -aE 'GotSceneGpuQuerySetup:|GotSceneLiveQueryCheckpoint:|GotSceneGpuQuerySelected:|GotSceneGpuInvocations:' \
    "$RUN/game.log" >"$RUN/info/scene-gpu-query-events.log" || true


  grep -aE 'GotSceneIndirectGpuArgs:|GotSceneIndirectGpuCommand:' \
    "$RUN/game.log" >"$RUN/info/scene-indirect-gpu-arguments.log" || true
  python3 - "$RUN/info/scene-indirect-gpu-arguments.log" \
      "$RUN/info/scene-indirect-gpu-argument-summary.txt" <<'PYINDIRECTGPU'
from pathlib import Path
from collections import Counter
import re,sys
src=Path(sys.argv[1]).read_text(errors="replace")
src=re.sub(r"\x1b\[[0-9;]*[mK]","",src)
reads=[];copies=[];args=[]
for line in src.splitlines():
    if "GotSceneIndirectGpuArgs:" in line:
        event=line.split("GotSceneIndirectGpuArgs:",1)[1].strip()
        if "status=GPU_READBACK_PASS" in event:reads.append(event)
        elif "status=COPY_SUBMITTED" in event:copies.append(event)
        else:args.append(event)
    elif "GotSceneIndirectGpuCommand:" in line:
        args.append(line.split("GotSceneIndirectGpuCommand:",1)[1].strip())
def prop(text,name):
    m=re.search(r"\b"+re.escape(name)+r"=([^\s]+)",text)
    return m.group(1) if m else "missing"
out=["FOCUS=WHY_TWO_INDEXED_INDIRECT_DRAW_CALLS_RAN_ZERO_GPU_VERTICES",
     "SOURCE=ACTUAL_GPU_COPY_OF_VULKAN_INDIRECT_BUFFER_NOT_GUEST_CPU_MIRROR",
     f"GPU_COPY_SUBMITTED={len(copies)}",
     f"GPU_COPY_COMPLETED={len(reads)}",
     f"ARGUMENT_RECORDS={len(args)}"]
counts=Counter()
for event in reads:
    name=prop(event,"diagnosis")
    counts[name]+=1
    out.append("GPU_ARGUMENT_CLASSIFICATION="+event)
for name,n in counts.most_common():
    out.append(f"GPU_ARGUMENT_DIAGNOSIS={name} OCCURRENCES={n}")
for line in copies[:8]:out.append("ARGUMENT_COPY_REQUEST="+line)
for line in args[:70]:out.append("GPU_ARGUMENT_VALUE="+line)
if not reads:
    out.append("GPU_READBACK_NOT_COMPLETED=YES_NO_INDIRECT_ARGUMENT_CLAIM")
elif any("EXECUTABLE_GPU_COMMAND_PRESENT" in x for x in counts):
    out.append("FOLLOWUP=CHECK_GPU_INDIRECT_ARGUMENT_OFFSET_BUFFER_SYNC_AND_COUNT_COMMAND_DECODE")
elif all("NO_DRAWS_SELECTED" in x or "ZERO_INDEX_OR_INSTANCE_COUNT" in x for x in counts):
    out.append("FOLLOWUP=TRACE_GPU_WRITER_OF_ZERO_INDIRECT_COMMANDS_OR_INTENTIONAL_GAME_CULLING")
else:
    out.append("FOLLOWUP=MORE_GPU_ARGUMENT_SAMPLES_NEEDED")
Path(sys.argv[2]).write_text("\n".join(out)+"\n")
print("\n".join(out[:35]))
PYINDIRECTGPU

  python3 - "$RUN/info/scene-gpu-query-events.log" \
    "$RUN/info/scene-gpu-invocation-summary.txt" <<'PYGOTQUERY'
import re,sys
from pathlib import Path
src=Path(sys.argv[1]).read_text(errors="replace")
src=re.sub(r"\x1b\[[0-9;]*[mK]","",src)
picked=[];completed=[];supports=None
for line in src.splitlines():
    if "GotSceneGpuQuerySetup:" in line:
        m=re.search(r"pipeline_statistics_enabled=(\w+)",line)
        if m:supports=m.group(1)
    if "GotSceneGpuQuerySelected:" in line:
        picked.append(line.split("GotSceneGpuQuerySelected:",1)[1].strip())
    if "GotSceneGpuInvocations:" in line:
        completed.append(line.split("GotSceneGpuInvocations:",1)[1].strip())
def val(line,name):
    m=re.search(r"\b"+name+r"=([^\s]+)",line)
    return m.group(1) if m else ""
out=["PURPOSE=PROVE_WHETHER_GOT_SCENE_GCN_GEOMETRY_REACHES_REAL_GPU_FRAGMENTS",
     f"PIPELINE_STATISTICS_FEATURE={supports or 'UNKNOWN'}",
     f"QUERY_SELECTED={len(picked)}",
     f"QUERY_RESULT={len(completed)}",
     "STAT_ORDER=IA_VERTICES,VS_INVOCATIONS,CLIP_INVOCATIONS,CLIP_PRIMITIVES,FS_INVOCATIONS",
     "SCOPE=EXACT_0x15203f8000_COLOR_AND_0x1504a08000_DEPTH_SCENE_DRAWS"]
for p in picked[:16]:out.append("SELECTED="+p)
nvalid=0
for p in completed[:16]:
    out.append("RESULT="+p)
    status=val(p,"occlusion_status")
    stats_status=val(p,"stats_status")
    try:
        passed=int(val(p,"passed_samples"))
        vs=int(val(p,"vs_invocations"))
        clp=int(val(p,"clip_primitives"))
        fs=int(val(p,"fs_invocations"))
    except ValueError:
        out.append("CLASSIFICATION=INVALID_QUERY_NUMBERS")
        continue
    if status!="Success":
        out.append("CLASSIFICATION=OCCLUSION_NOT_READY_NO_COVERAGE_CLAIM")
    elif stats_status!="Success":
        out.append(f"CLASSIFICATION=OCCLUSION_ONLY_PASSED_SAMPLES_{passed}")
    elif vs==0:
        out.append("CLASSIFICATION=ZERO_VERTEX_SHADER_INVOCATIONS_FETCH_OR_PIPELINE")
        nvalid+=1
    elif clp==0:
        out.append("CLASSIFICATION=VERTEX_EXECUTED_BUT_ZERO_PRIMITIVES_SURVIVED_CLIPPING")
        nvalid+=1
    elif fs==0:
        out.append("CLASSIFICATION=PRIMITIVES_CLIPPED_IN_BUT_NO_FRAGMENT_INVOCATIONS")
        nvalid+=1
    elif passed==0:
        out.append("CLASSIFICATION=FRAGMENTS_EXECUTE_BUT_NO_SAMPLES_SURVIVE")
        nvalid+=1
    else:
        out.append("CLASSIFICATION=FRAGMENT_AND_SAMPLES_PRESENT_CHECK_OUTPUT_BUFFER")
        nvalid+=1
out.append(f"CLASSIFIED_COMPLETE_COUNTERS={nvalid}")
if not completed:out.append("NO_COUNTER_RESULTS=YES_RUN_INVALID_FOR_GPU_COVERAGE")
Path(sys.argv[2]).write_text("\n".join(out)+"\n")
print("\n".join(out))
PYGOTQUERY

  python3 - "$RUN/info/scene-gpu-query-events.log" \
    "$RUN/info/scene-indirect-gpu-arguments.log" \
    "$RUN/info/active-menu-hardware-summary.txt" <<'PYACTIVESCENE'
from pathlib import Path
import re,sys
results=Path(sys.argv[1]).read_text(errors="replace")
args=Path(sys.argv[2]).read_text(errors="replace")
def clean(text):
    return re.sub(r"\x1b\[[0-9;]*[mK]","",text)
results=clean(results)
args=clean(args)
def field(line,key):
    match=re.search(r"\b"+re.escape(key)+r"=([^\s]+)",line)
    return match.group(1) if match else ""
out=["EXPERIMENT=GPU_COUNTERS_ON_ACTUAL_HI_VOLUME_FIVE_MRT_MENU_DRAWS",
     "NATIVE_RENDERING_DEFAULT=YES",
     "CPU_REGISTER_NUM_INDICES_NOT_A_GPU_INDIRECT_COUNT=YES",
     "TARGET_COLOR=0x15203f8000",
     "TARGET_DEPTH=0x1504a08000",
     "ACTIVE_PAIR_A=FS_0x5116e459_VS_0xae1cc7b7",
     "ACTIVE_PAIR_B=FS_0xc1123f0f_VS_0xc8caf263",
     "EXPECTED_CHECKPOINTS=4096,4097,8192,8193"]
main=[]
controls=[]
selected=[]
for line in results.splitlines():
    if "GotSceneGpuQuerySelected:" in line:
        selected.append(line.split("GotSceneGpuQuerySelected:",1)[1].strip())
    if "GotSceneGpuInvocations:" not in line:
        continue
    raw=line.split("GotSceneGpuInvocations:",1)[1].strip()
    if field(raw,"high_volume")=="true":
        main.append(raw)
    else:
        controls.append(raw)
out.append(f"HARDWARE_QUERIES_TOTAL={len(main)+len(controls)}")
out.append(f"ACTIVE_INDIRECT_HARDWARE_RESULTS={len(main)}")
out.append(f"DIRECT_CONTROL_RESULTS={len(controls)}")
completed=0
last_checkpoints=0
for item in main:
    fs,vs=field(item,"fs"),field(item,"vs")
    ordinal=field(item,"pair_ordinal")
    status=field(item,"stats_status")
    occ=field(item,"occlusion_status")
    out.append("ACTIVE_INDIRECT_GPU_RESULT="+item)
    if ordinal=="8192":
        last_checkpoints+=1
    if status!="Success" or occ!="Success":
        out.append("DIAGNOSIS=QUERY_NOT_READY_NO_RENDERING_INFERENCE")
        continue
    completed+=1
    def n(key):
        try:return int(field(item,key))
        except ValueError:return -1
    ia=n("ia_vertices")
    vs_n=n("vs_invocations")
    cp=n("clip_primitives")
    frag=n("fs_invocations")
    passed=n("passed_samples")
    if ia==0 and vs_n==0:
        diagnosis="GPU_INDIRECT_DRAW_EXECUTES_NO_GEOMETRY_CHECK_GPU_ARGS"
    elif vs_n>0 and cp==0:
        diagnosis="VERTEX_OUTPUT_DISCARDED_BEFORE_PRIMITIVE_RASTERIZATION"
    elif cp>0 and frag==0:
        diagnosis="CLIPPED_PRIMITIVES_BUT_NO_FRAGMENT_COVERAGE"
    elif frag>0 and passed==0:
        diagnosis="FRAGMENT_SHADER_EXECUTES_WITH_ZERO_VISIBLE_SAMPLES"
    elif frag>0 and passed>0:
        diagnosis="REAL_SCENE_FRAGMENT_AND_COVERAGE_WORK_CONFIRMED"
    else:
        diagnosis="COUNTERS_INCONCLUSIVE"
    out.append(f"ACTIVE_GPU_CLASSIFICATION fs={fs} vs={vs} ordinal={ordinal} "
               f"ia={ia} vs_invocations={vs_n} clipped_primitives={cp} "
               f"fragments={frag} passed_samples={passed} RESULT={diagnosis}")
out.append(f"ACTIVE_COMPLETE_COUNTERS={completed}")
out.append(f"ACTIVE_8192_CHECKPOINT_RESULTS={last_checkpoints}")
for item in controls:
    out.append("DIRECT_REFERENCE_RESULT="+item)
arg_lines=[l for l in args.splitlines() if "GotSceneIndirectGpuArgs:" in l and
           "status=GPU_READBACK_PASS" in l]
out.append(f"INDIRECT_GPU_ARG_SNAPSHOTS={len(arg_lines)}")
for line in arg_lines[:12]:
    out.append("GPU_ARGS="+line.split("GotSceneIndirectGpuArgs:",1)[1].strip())
if not main:
    out.append("RESULT=ACTIVE_MENU_QUERY_NEVER_ACTIVATED_INVALID_TEST")
elif not last_checkpoints:
    out.append("RESULT=ACTIVE_MENU_8192_NOT_REACHED_PARTIAL_EVIDENCE")
else:
    out.append("RESULT=ACTIVE_MENU_LATE_HARDWARE_WORK_MEASURED")
Path(sys.argv[3]).write_text("\n".join(out)+"\n")
print("\n".join(out[:55]))
PYACTIVESCENE

  grep -a 'GotSceneRasterCoverage:' "$RUN/game.log" \
    > "$RUN/info/scene-raster-viewport-scissor.log" || true
  # Snapshot actual Vulkan raster state for the same scene draws.
  python3 - "$RUN/info/scene-raster-viewport-scissor.log" \
    "$RUN/info/scene-raster-viewport-summary.txt" <<'PYRASTERCOVERAGE'
from pathlib import Path
from collections import Counter
import re,sys
src=Path(sys.argv[1]).read_text(errors="replace")
src=re.sub(r"\x1b\[[0-9;]*m","",src)
rows=[x for x in src.splitlines() if "GotSceneRasterCoverage:" in x]
summ=["DIAGNOSTIC=LIVE_GCN_SCENE_VIEWPORT_AND_SCISSOR",
      f"SCENE_COVERAGE_EVENTS={len(rows)}"]
empty=[];culls=Counter();scissors=Counter();depth=Counter()
for line in rows:
    def get(k):
        m=re.search(r"\b"+re.escape(k)+r"=([^\s]+)",line)
        return m.group(1) if m else "missing"
    try:
        w,h=int(get("scissor_w")),int(get("scissor_h"))
    except ValueError:
        continue
    if w==0 or h==0:empty.append(line)
    culls[get("cull")]+=1
    scissors[(get("scissor_x"),get("scissor_y"),w,h)]+=1
    depth[(get("viewport_zmin"),get("viewport_zmax"),
           get("depth_test"),get("depth_write"),get("depth_compare"),
           get("stencil_test"))]+=1
summ.append(f"ZERO_AREA_SCISSORS={len(empty)}")
summ.extend(f"CULL_MODE={k} OBSERVATIONS={n}" for k,n in culls.most_common(6))
summ.extend(f"SCISSOR={k} OBSERVATIONS={n}" for k,n in scissors.most_common(12))
summ.extend(f"DEPTH_STENCIL_STATE={k} OBSERVATIONS={n}" for k,n in depth.most_common(12))
summ.extend("ZERO_AREA="+x for x in empty[:12])
if not rows:summ.append("SCENE_COVERAGE_LOG_MISSING=YES_DO_NOT_CLAIM_RASTER_FIX")
Path(sys.argv[2]).write_text("\n".join(summ)+"\n")
print("\n".join(summ))
PYRASTERCOVERAGE

  # Convert the *actual* GPU readbacks to small PNG previews without
  # any Python packages. Preserve raw data for exact IEEE-754 inspection.
  python3 - "$RUN/profile/user/shader/got-menu-probes" "$RUN/info" <<'PYSTAGEPIXELS'
from pathlib import Path
import sys,struct,math,hashlib,zlib,binascii
folder=Path(sys.argv[1]); dest=Path(sys.argv[2])
summary=["GOT_MENU_GPU_PIXEL_CHAIN=CAPTURE_OF_REAL_VULKAN_IMAGES",
         "NO_SHADER_OUTPUT_OVERRIDES=YES",
         "EXPECTED_STAGE_ADDRESSES=0x1522020000,0x15203f8000,0x1504a08000,0x1525478000,0x1504210000,0x1505578000,0x1505d70000"]
def save_rgb_png(path,width,height,pixels):
    def chunk(name,payload):
        return (struct.pack(">I",len(payload))+name+payload+
                struct.pack(">I",binascii.crc32(name+payload)&0xffffffff))
    rows=bytearray()
    length=width*3
    for i in range(height):
        rows.append(0)
        rows.extend(pixels[i*length:(i+1)*length])
    data=(b"\x89PNG\r\n\x1a\n" +
          chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))+
          chunk(b"IDAT",zlib.compress(bytes(rows),6))+chunk(b"IEND",b""))
    path.write_bytes(data)
def srgb(x):
    if not math.isfinite(x): return 0
    x=max(0.0,min(1.0,x))
    return int(min(255,max(0,255*(12.92*x if x<=0.0031308 else
                           1.055*x**(1/2.4)-0.055))+0.5))
def get_float(data,off,code):
    return struct.unpack_from(code,data,off)
count=0
for name,expected_bpp,kind in [
    ("scene_rgba16f",8,"rgba16f"),
    ("scene_rgba8",4,"rgba8"),
    ("scene_depth32",4,"f32"),
    ("depth_blur32",4,"f32"),
    ("composed_rgba8",4,"rgba8"),
    ("video_frame_a",4,"rgba8"),
    ("video_frame_b",4,"rgba8"),
    ("host_postprocess",4,"rgba8")]:
    path=folder/(name+".raw")
    metadata=folder/(name+".raw.txt")
    if not path.is_file() or not metadata.is_file():
        summary.append(f"STAGE={name} CAPTURE=MISSING")
        continue
    kv={}
    for line in metadata.read_text().splitlines():
        if "=" in line:
            key,value=line.split("=",1)
            kv[key]=value
    width=int(kv.get("width","0"))
    height=int(kv.get("height","0"))
    length=width*height*expected_bpp
    raw=path.read_bytes()
    full_depth_stencil_staging=(name=="scene_depth32" and
                                len(raw)==length*2)
    if width!=1920 or height!=1080 or (
        len(raw)!=length and not full_depth_stencil_staging):
        summary.append(f"STAGE={name} CAPTURE=INVALID expected={length} "
                       f"actual={len(raw)}")
        continue
    if full_depth_stencil_staging:
        summary.append("D32S8_FULL_STAGING_PRESERVED=YES depth_preview_uses_first_4_bytes_per_pixel")
    sha=hashlib.sha256(raw).hexdigest()
    step=8
    outwidth=(width+step-1)//step
    outheight=(height+step-1)//step
    values=[]
    colored=[]
    finite_count=0
    near_white=0
    near_black=0
    anomalies=0
    for y in range(0,height,step):
        for x in range(0,width,step):
            pos=(y*width+x)*expected_bpp
            if kind=="rgba16f":
                rgba=get_float(raw,pos,"<eeee")
                channels=rgba[:3]
            elif kind=="rgba8":
                rgba=raw[pos:pos+4]
                # Vulkan eB8G8R8A8Unorm/Srgb store B,G,R,A.
                if kv.get("format") in ("44","50"):
                    channels=(rgba[2]/255,rgba[1]/255,rgba[0]/255)
                else:
                    channels=tuple(v/255 for v in rgba[:3])
            else:
                channels=(get_float(raw,pos,"<f")[0],)
            valid=all(math.isfinite(v) for v in channels)
            finite_count+=int(valid)
            anomalies+=int(not valid)
            if valid:
                values.extend(channels)
                black=all(v<0.03 for v in channels)
                white=all(v>0.97 for v in channels)
                near_black+=int(black)
                near_white+=int(white)
            colored.append((channels,valid))
    n=len(colored)
    pixels=bytearray()
    lo=min(values) if values else 0.0
    hi=max(values) if values else 0.0
    if kind=="f32":
        ordered=sorted(values)
        qlo=ordered[int(len(ordered)*0.01)] if ordered else 0
        qhi=ordered[min(len(ordered)-1,int(len(ordered)*0.99))] if ordered else 1
        if qhi-qlo<1e-7:qlo,qhi=0,1
    for channels,valid in colored:
        if not valid:
            rgb=(255,0,255)
        elif kind=="rgba16f":
            rgb=tuple(srgb(max(0.0,v)/(1+max(0.0,v))) for v in channels)
        elif kind=="rgba8":
            rgb=tuple(int(v*255+0.5) for v in channels)
        else:
            value=max(0,min(1,(channels[0]-qlo)/(qhi-qlo)))
            grey=int(value*255+0.5)
            rgb=(grey,grey,grey)
        pixels.extend(rgb)
    png=dest/(name+"-preview.png")
    save_rgb_png(png,outwidth,outheight,pixels)
    summary.append(f"STAGE={name} CAPTURE=PASS IMAGE={png.name} "
                   f"guest=0x{kv.get('guest_address','unknown')} "
                   f"vulkan_format={kv.get('format','unknown')} "
                   f"sha256={sha} samples={n} "
                   f"min={lo:.7g} max={hi:.7g} finite={finite_count}/{n} "
                   f"near_white={near_white}/{n} near_black={near_black}/{n} "
                   f"preview_tonemapped={kind=='rgba16f'}")
    count+=1
# Test whether the enormous white-menu brightness gain happens in the
# GUEST's final fs_0x96aa3f2e or after it in the HOST postprocess path.
composed=folder/"composed_rgba8.raw"
for last in ("video_frame_a","video_frame_b"):
    other=folder/(last+".raw")
    if not composed.exists() or not other.exists():
        summary.append(f"POSTCOMPOSITOR_COMPARE_{last}=MISSING_REQUIRED_RAW")
        continue
    a=composed.read_bytes(); b=other.read_bytes()
    if len(a)!=1920*1080*4 or len(b)!=len(a):
        summary.append(f"POSTCOMPOSITOR_COMPARE_{last}=WRONG_SIZE")
        continue
    pairs=[]; clamped=0; examined=0
    for y in range(680,1080,16):
        for x in range(1100,1900,16):
            pos=(y*1920+x)*4+1
            x0=a[pos]; y0=b[pos]
            examined+=1
            clamped+=int(y0>=250)
            if 20<x0<245 and 10<y0<250:
                pairs.append((x0,y0))
    if len(pairs)<50:
        summary.append(f"POSTCOMPOSITOR_COMPARE_{last}=TOO_FEW_UNCLAMPED_SAMPLES "
                       f"total={examined} eligible={len(pairs)} saturated={clamped}")
        continue
    scale=sum(x*y for x,y in pairs)/max(1,sum(x*x for x,y in pairs))
    intercept=(sum(y for _,y in pairs)-scale*sum(x for x,_ in pairs))/len(pairs)
    ratios=sorted(y/x for x,y in pairs)
    median=ratios[len(ratios)//2]
    summary.append(f"POSTCOMPOSITOR_COMPARE_{last}=PASS "
                   f"uncapped_samples={len(pairs)} total_samples={examined} "
                   f"median_output_to_composed_gain={median:.4f} "
                   f"zero_intercept_gain={scale:.4f} fitted_intercept={intercept:.2f} "
                   f"saturated_fraction={clamped/examined:.4f}")

# Synchronized, one-frame brightness test. Compare the PP shader's *actual*
# output with the already-presented game's frame, not with X11 screenshots
# from a different time (the previous error).
host=folder/"host_postprocess.raw"
hostmeta=folder/"host_postprocess.raw.txt"
if host.exists() and hostmeta.exists():
    pp={}
    for line in hostmeta.read_text().splitlines():
        if "=" in line:
            key,val=line.split("=",1);pp[key]=val
    summary.append("HOST_PP_GAMMA="+pp.get("gamma","unknown"))
    summary.append("HOST_PP_HDR="+pp.get("hdr","unknown"))
    summary.append("HOST_PP_SRGB_INPUT="+pp.get("srgb_input","unknown"))
    h=host.read_bytes()
    if len(h)==1920*1080*4:
        for stage in ("video_frame_a","video_frame_b"):
            inp=folder/(stage+".raw")
            if not inp.exists() or inp.stat().st_size!=len(h):
                summary.append(f"HOST_PP_GAIN_FROM_{stage}=MISSING_INPUT")
                continue
            b=inp.read_bytes();ratios=[];hits=0;clipped=0
            for y in range(600,1000,16):
                for x in range(1000,1900,16):
                    idx=(y*1920+x)*4+1
                    x0=b[idx];y0=h[idx]
                    hits+=1
                    clipped+=int(y0>=250)
                    if 16<x0<245 and 5<y0<250:ratios.append(y0/x0)
            ratios.sort()
            if ratios:
                summary.append(
                    f"HOST_PP_GAIN_FROM_{stage}=PASS "
                    f"median_ratio={ratios[len(ratios)//2]:.5f} "
                    f"clipped={clipped}/{hits} compared_pixels={len(ratios)}")
            else:summary.append(f"HOST_PP_GAIN_FROM_{stage}=NO_VALID_SAMPLES")
    else:summary.append("HOST_PP_RAW_SIZE=INVALID")
else:summary.append("HOST_PP_CAPTURE=NOT_AVAILABLE")

summary.append("STAGE_CAPTURED_COUNT="+str(count))
if not count:summary.append("NO_GPU_STAGE_READBACK_AVAILABLE=YES")
(dest/"stage-pixel-summary.txt").write_text("\n".join(summary)+"\n")
print("\n".join(summary))
PYSTAGEPIXELS

  {
    echo "STATUS=$STATUS"
    echo "GAME_EXIT=$GAME_EXIT"
    echo "GAME_LAUNCHED=$GAME_LAUNCHED"
    echo "OLLAMA_ACTION=$OLLAMA_ACTION"
    echo "OLLAMA_RESTORE=$OLLAMA_RESTORE"
    echo "ACTIVE_BINARY_UNCHANGED=$([ -z "$BIN_SHA" ] || [ "$(sha256sum "$BIN" 2>/dev/null | awk '{print $1}')" = "$BIN_SHA" ] && echo YES || echo NO)"
    echo "ISOLATED_SHADPS4_RUN=YES"
    echo "CANDIDATE_SOURCE=$PIN"
    echo "CAPTURE_DURATION_SECONDS=$SECONDS_TO_RUN"
    echo "PACKED_AND_STRIDED_TABLE_SIZE_SPECIALIZATION=ENABLED"
    echo "BOUNDED_POINTER_IMAGE_LOWERINGS=$(grep -ac 'BoundedPointerImage:' "$RUN/game.log" || true)"
    echo "BOUNDED_POINTER_IMAGE_SHADER=0x5c4c2b6f_EXPECTED_WHEN_COUNT_4"
    echo "BOUNDED_POINTER_COUNT_SPECIALIZATION=ENABLED"
    echo "INDEXED_FEEDBACK_SHADERS=0x7eaf40ad,0x4950cf6,0x55d17c16,0x52b6e003,0x5c4c2b6f"
    echo "INDEXED_FEEDBACK_CAPTURE_BINDINGS=1,32,256"
    echo "INDEXED_FEEDBACK_COMPLETED_ROWS=$(grep -ac 'IndexedImageFeedback:.*timeline_complete=YES' "$RUN/game.log" || true)"
    echo "INDEXED_TSHARP_BINDS=$(grep -ac 'RealIndexedTSharpBound:' "$RUN/game.log" || true)"
    echo "MENU_INVALID_IMAGE_EVENTS=$(grep -ac 'InvalidTSharpSource: shader=0x7eaf40ad' "$RUN/game.log" || true)"
    echo "GPU_QUERY_RESULTS=$(grep -ac 'GotSceneGpuInvocations:' "$RUN/game.log" || true)"
    echo "ACTIVE_MENU_LATE_QUERIES=$(grep -ac 'GotSceneGpuInvocations:.*high_volume=true' "$RUN/game.log" || true)"
    echo "GPU_INDIRECT_ARG_READBACKS=$(grep -ac 'GotSceneIndirectGpuArgs:.*GPU_READBACK_PASS' "$RUN/game.log" || true)"
    echo "VERIFIED_LDS_PHI_AND_SYNCHRONIZATION_FIXES=RETAINED"
    echo "GDS_DMA_DEPTH_AND_RASTER_EXPERIMENTS=DISABLED"
    echo "DIAGNOSTIC_SHADER_INSTRUMENTATION=SEPARATE_BUFFER_ONLY"
    echo "SHADER_DISK_CACHE=DISABLED_FOR_DIAGNOSTIC_RUN"
    echo "WHITE_MENU_FIX=NOT_CLAIMED_REVIEW_SCREENSHOTS_AND_SCENE_QUERIES"
    echo "TEXTURE_POOL_EXHAUSTION_EVENTS=$(grep -ac 'SlotVector pool=.*exhausted' "$RUN/game.log" || true)"
    echo "GPUVM_FAULT_EVENTS=$(grep -aic 'GPUVM fault detected' "$RUN/game.log" || true)"
    echo "EXIT=$rc"
    echo "SSH_SESSION=OPEN"
  } > "$RUN/info/final.txt"
  tar -czf "$REPORT" -C "$RUN" info screenshots game.log profile/user/shader/got-menu-probes 2>/dev/null || {
    echo "ARCHIVE_ERROR keep=$RUN"; exit 3;
  }
  echo "STATUS=$STATUS GAME_EXIT=$GAME_EXIT EXIT=$rc"
  echo "OLLAMA_RESTORE=$OLLAMA_RESTORE"
  echo "REPORT=$REPORT"
  echo "SSH_SESSION=OPEN"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

for command in git tar sha256sum flock timeout setsid xdpyinfo python3 docker; do
  command -v "$command" >/dev/null || { STATUS="MISSING_$command"; exit 2; }
done
exec 9>"$WORK/.build.lock"
flock -n 9 || { STATUS=BUILD_LOCK_HELD; exit 2; }
exec 8>"$GOT/tests/.got-runtime.lock"
flock -n 8 || { STATUS=RUNTIME_LOCK_HELD; exit 2; }
if python3 - <<'PY'
import os,pathlib
uid=os.getuid()
for entry in pathlib.Path("/proc").iterdir():
    if not entry.name.isdigit(): continue
    try:
        if entry.stat().st_uid!=uid: continue
        exe=os.readlink(entry/"exe")
        if pathlib.Path(exe).name.lower().startswith("shadps4"):
            print("ACTIVE_SHADPS4="+entry.name); raise SystemExit(2)
    except OSError: pass
PY
then :; else STATUS=ANOTHER_EMULATOR_RUNNING; exit 2; fi
[ -x "$BIN" ] && [ -x "$CANDIDATE" ] &&
[ -f "$HOST_REPORT" ] && [ -f "$GAME" ] && [ -d "$PROFILE/user" ] || {
  STATUS=MISSING_REQUIRED_INPUT; exit 2;
}
BIN_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
find "$WORK/bin-backups" -maxdepth 1 -type f -name 'shadps4*' \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS bytes=%s %f\n' | sort \
  > "$RUN/info/available-checkpoints.txt"
EXPECTED="$(tar -xOzf "$HOST_REPORT" info/candidate-preserved.sha256 2>/dev/null | awk 'NR==1 {print $1}')"
ACTUAL="$(sha256sum "$CANDIDATE" | awk '{print $1}')"
if [[ ! "$EXPECTED" =~ ^[0-9a-f]{64}$ ]] || [ "$EXPECTED" != "$ACTUAL" ]; then
  STATUS=CANDIDATE_SHA_MISMATCH
  echo "CHECKSUM_FAILED expected=$EXPECTED actual=$ACTUAL"
  exit 2
fi
echo "PINNED_CANDIDATE=PASS sha256=$ACTUAL"
test "$(df -Pk "$WORK" | awk 'NR==2 {print $4}')" -gt 2097152 || {
  STATUS=LOW_DISK_SPACE; exit 2;
}
DISPLAY_OK=""
for value in "${DISPLAY:-}" :0 :1 :2; do
  [ -n "$value" ] || continue
  if DISPLAY="$value" timeout 4s xdpyinfo -display "$value" >/dev/null 2>&1; then
    DISPLAY_OK="$value"; break
  fi
done
[ -n "$DISPLAY_OK" ] || { STATUS=NO_X11_DISPLAY; exit 2; }
GPU=""
for f in /sys/class/drm/card[0-9]*/device/vendor; do
  if [ -f "$f" ] && [ "$(cat "$f")" = 0x1002 ]; then GPU="$(dirname "$f")"; break; fi
done
[ -n "$GPU" ] && [ -r "$GPU/mem_info_vram_total" ] &&
[ -r "$GPU/mem_info_vram_used" ] || { STATUS=GPU_TELEMETRY_MISSING; exit 2; }
TOTAL="$(cat "$GPU/mem_info_vram_total")"
USED="$(cat "$GPU/mem_info_vram_used")"
FREE=$((TOTAL-USED))
echo "FREE_VRAM_BEFORE=$FREE REQUIRED=$MIN_VRAM"

if [ "$FREE" -lt "$MIN_VRAM" ]; then
  STATUS=OLLAMA_LOW_VRAM_PRECHECK
  docker ps --format '{{json .}}' >"$RUN/info/containers.jsonl"
  python3 - "$RUN/info/containers.jsonl" "$RUN/info/ollama-id.txt" <<'PY'
import json,pathlib,sys
rows=[json.loads(s) for s in pathlib.Path(sys.argv[1]).read_text().splitlines() if s]
match=[r for r in rows if r.get("Names")=="ollama" and str(r.get("Image","")).startswith("ollama/ollama:")]
if len(match)!=1: raise SystemExit("AMBIGUOUS_OLLAMA_CONTAINER="+str(len(match)))
pathlib.Path(sys.argv[2]).write_text(str(match[0]["ID"])+"\n")
print("OLLAMA_CONTAINER="+str(match[0].get("Names","")))
PY
  OLLAMA_ID="$(cat "$RUN/info/ollama-id.txt")"
  cat > "$RUN/info/ollama.py" <<'PY'
import json,sys,pathlib,time,urllib.request
mode,url,filename=sys.argv[1:4]
p=pathlib.Path(filename)
def req(path,payload=None,timeout=30):
    data=json.dumps(payload).encode() if payload is not None else None
    u=urllib.request.Request(url+path,data=data,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(u,timeout=timeout) as f:return json.load(f)
def names(d):
    if not isinstance(d,dict) or not isinstance(d.get("models"),list):
        raise RuntimeError("OLLAMA_PS_INVALID")
    return list(dict.fromkeys(x.get("name") or x.get("model") for x in d["models"]
                              if isinstance(x,dict) and (x.get("name") or x.get("model"))))
if mode=="snapshot":
    d=req("/api/ps")
    p.write_text(json.dumps(d,indent=2)+"\n")
    print("MODEL_NAMES_BEFORE="+str(names(d)))
elif mode=="restore":
    original=names(json.loads(p.read_text()))
    for _ in range(30):
        try:req("/api/ps",timeout=3);break
        except Exception:time.sleep(2)
    else:raise RuntimeError("OLLAMA_API_UNAVAILABLE")
    for name in original:
        req("/api/generate",{"model":name,"prompt":"","stream":False,"keep_alive":"30m"},timeout=160)
        print("MODEL_RELOADED="+name,flush=True)
    if not set(original).issubset(set(names(req("/api/ps")))):
        raise RuntimeError("MODEL_RESTORE_INCOMPLETE")
else:raise ValueError(mode)
PY
  python3 "$RUN/info/ollama.py" snapshot "$API" "$RUN/info/ollama-before.json"
  OLLAMA_TOUCHED=1
  docker stop --time 20 "$OLLAMA_ID" > "$RUN/info/docker-stop.log"
  OLLAMA_ACTION=STOPPED_LOW_VRAM
  sleep 2
  FREE=$((TOTAL-$(cat "$GPU/mem_info_vram_used")))
  echo "FREE_VRAM_AFTER_OLLAMA_STOP=$FREE"
  if [ "$FREE" -lt "$MIN_VRAM" ]; then
    STATUS=STILL_LOW_VRAM_NO_GAME
    exit 2
  fi
else
  OLLAMA_ACTION=KEPT_RUNNING_SUFFICIENT_VRAM
fi

STATUS=ISOLATED_PROFILE
tar -C "$PROFILE" --exclude='user/log' --exclude='user/logs' \
  --exclude='user/shader/dumps' -cf - user | tar -C "$RUN/profile" -xf -
python3 - "$RUN/profile/user/config.json" <<'PY'
import pathlib,json,sys
p=pathlib.Path(sys.argv[1])
d=json.loads(p.read_text()) if p.exists() else {}
assert isinstance(d,dict)
assert isinstance(d.setdefault("Log",{}),dict)
d["Log"].update(filter="*:critical Input:info Config:info Render.Recompiler:info Render.Vulkan:info",
                sync=False,skip_duplicate=True)
# Compile the instrumented shader in the isolated portable profile.
assert isinstance(d.setdefault("Vulkan",{}),dict)
d["Vulkan"]["pipeline_cache_enabled"]=False
# Ensure prior diagnostic shader overrides cannot contaminate the presented
# output trace. Both settings live inside the isolated copied profile.
assert isinstance(d.setdefault("GPU",{}),dict)
d["GPU"]["patch_shaders"]=False
game_file=p.parent/"custom_configs"/"CUSA11456.json"
if game_file.is_file():
    game_cfg=json.loads(game_file.read_text())
    if not isinstance(game_cfg,dict):
        raise SystemExit("INVALID_ISOLATED_GAME_OVERRIDE")
    if "GPU" in game_cfg and not isinstance(game_cfg["GPU"],dict):
        raise SystemExit("INVALID_ISOLATED_GAME_GPU_OVERRIDE")
    game_cfg.setdefault("GPU",{})["patch_shaders"]=False
    game_file.write_text(json.dumps(game_cfg,indent=2)+"\n")

p.write_text(json.dumps(d,indent=2)+"\n")
print("ISOLATED_CONFIG=PASS; PIPELINE_CACHE=OFF_TEST_ONLY")
PY

# Isolated portable directory only. No probing files on the primary profile.
mkdir -p "$RUN/profile/user/shader/got-menu-probes"
STATUS=GAME_RUNNING
echo "GAME_START duration=${SECONDS_TO_RUN}s DISPLAY=$DISPLAY_OK"
echo "ENTER_GAMEPLAY_WITH_YOUR_CONTROLLER; capture stops automatically after five minutes"
(
  cd "$RUN/profile"
  test -d "$RUN/profile/user"
  export DISPLAY="$DISPLAY_OK"
  export SHADPS4_STRIDED_SOURCE_PROOF=1
  export SHADPS4_STRIDED_SOURCE_PROOF_HASHES="0x4950cf6,0x55d17c16,0x52b6e003,0x5c4c2b6f"
  export SHADPS4_STRIDED_GUEST_TSHARP=1
  export SHADPS4_GUEST_TSHARP_LARGE_ARRAY=1
  export SHADPS4_GUEST_TSHARP_CACHE=0
  export SHADPS4_TEXTURE_SLOT_PRESSURE=0
  export SHADPS4_GPUVM_SPARSE_MAP=0
  export SHADPS4_RESOURCE_AUDIT=1
  export SHADPS4_RESOURCE_AUDIT_HASH="0x7eaf40ad,0x55d17c16,0x5c4c2b6f"
  export SHADPS4_INDEXED_IMAGE_FEEDBACK_HASHES="0x7eaf40ad,0x4950cf6,0x55d17c16,0x52b6e003,0x5c4c2b6f"
  export SHADPS4_INDEXED_IMAGE_AUDIT=1
  export SHADPS4_INDEXED_IMAGE_AUDIT_HASHES="0x7eaf40ad"
  export SHADPS4_MENU_PASS_TRACE=1
  export SHADPS4_GOT_MENU_STAGE_PIXELS=1
  export SHADPS4_GOT_SCENE_GPU_QUERY=1
  export SHADPS4_INDIRECT_WAR_AUDIT=0
  # Keep the previous indirect-producer and GDS/DMA experiments disabled.
  export SHADPS4_GOT_INDIRECT_PRODUCERS=0
  export SHADPS4_GOT_DMA_WORD_PROBE=0
  export SHADPS4_GOT_GDS_PRODUCER_PROBE=0
  export SHADPS4_GOT_INDIRECT_GPU_ARGUMENTS=1
  # Preserve native depth and raster state for the indexed-sample capture.
  export SHADPS4_GOT_SCENE_DEPTH_AB=0
  export SHADPS4_GOT_SCENE_RASTER_AB=0
  export SHADPS4_GOT_MENU_CAPTURE_DELAY_SEC="${GOT_CAPTURE_DELAY_SEC:-85}"
  export SHADPS4_TARGET_SHADER_DUMP_HASHES="0x96aa3f2e"
  export SHADPS4_MENU_WATCH_SOURCE=0x1504a08000
  export SHADPS4_RENDER_AUDIT=1
  export SHADPS4_TARGET_SHADER_DUMP_HASHES="0xc1123f0f,0xc8caf263,0x5116e459,0xae1cc7b7,0x96aa3f2e,0xc0cac3b6,0xb1ec4568,0x7eaf40ad,0xb5c898c7,0x4950cf6,0x55d17c16,0x52b6e003,0x5c4c2b6f,0x836253ca,0x22d378cd,0x5116e459"
  export SHADPS4_INVALID_SHARP_TRACE_HASHES="0x7eaf40ad,0xb5c898c7,0x4950cf6,0x55d17c16,0x52b6e003,0x5c4c2b6f,0x836253ca,0x22d378cd,0x5116e459"
  export SHADPS4_SRT_SOURCE_AUDIT=1
  export SHADPS4_SRT_INLINE_AUDIT=1
  export SHADPS4_SRT_TRACE_HASHES="0x7eaf40ad,0xb5c898c7,0x4950cf6,0x55d17c16,0x52b6e003,0x5c4c2b6f,0x836253ca,0x22d378cd,0x5116e459"
  export SHADPS4_SHARP_MASK_AUDIT=1
  export SHADPS4_SHARP_MASK_AUDIT_HASHES="0x7eaf40ad"
  unset RADV_DEBUG VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
  exec setsid timeout -s TERM -k 8s "${SECONDS_TO_RUN}s" "$CANDIDATE" \
    --game "$GAME" --fullscreen true --show-fps
) > "$RUN/game.log" 2>&1 &
TEST_PGID=$!
GAME_LAUNCHED=YES
start="$(date +%s)"
last=-20
next_screenshot=10
echo "elapsed,vram_used_bytes,vram_free_bytes,gpu_busy" > "$RUN/info/gpu-samples.csv"
while kill -0 "$TEST_PGID" 2>/dev/null; do
  elapsed=$(( $(date +%s)-start ))
  used="$(cat "$GPU/mem_info_vram_used" 2>/dev/null || echo 0)"
  busy="$(cat "$GPU/gpu_busy_percent" 2>/dev/null || echo -1)"
  if [[ "$used" =~ ^[0-9]+$ ]] && [ "$used" -le "$TOTAL" ]; then
    available=$((TOTAL-used))
  else available=-1; fi
  printf '%s,%s,%s,%s\n' "$elapsed" "$used" "$available" "$busy" >> "$RUN/info/gpu-samples.csv"
  # Stop on the FIRST GPUVM fault or device loss. The prior run reached a
  # RADV fault and context loss; continuing after that is unsafe on this host.
  # Only consult the currently running game's fresh log.
  if tail -n 140 "$RUN/game.log" 2>/dev/null | grep -Eiq     'GPUVM fault detected|device lost|VK_ERROR_DEVICE_LOST|GPU hang detected|amdgpu.*ring.*timeout'; then
    STATUS=GPU_FAULT_ABORT
    echo "GPU_FAULT_DETECTED=YES STOPPING_GAME_EARLY"
    kill -TERM -- "-$TEST_PGID" 2>/dev/null || true
    sleep 2
    break
  fi
  # Extra host-safety guard: do not let the test continue into severe VRAM
  # depletion even if its launch-time headroom was sufficient.
  if [ "$available" -ge 0 ] && [ "$available" -lt $((2*1024*1024*1024)) ]; then
    echo "RUNTIME_VRAM_CRITICAL_ABORT free_bytes=$available"
    STATUS=RUNTIME_VRAM_CRITICAL_ABORT
    kill -TERM -- "-$TEST_PGID" 2>/dev/null || true
    sleep 2
    break
  fi
  if [ $((elapsed-last)) -ge 10 ]; then
    echo "GOT_RUNNING elapsed=${elapsed}s/${SECONDS_TO_RUN}s free_vram=$available gpu_busy=$busy"
    last="$elapsed"
  fi
  if [ "$elapsed" -ge "$next_screenshot" ] && command -v import >/dev/null; then
    timeout 6s import -display "$DISPLAY_OK" -window root \
      "$RUN/screenshots/$elapsed.png" >/dev/null 2>&1 || true
    next_screenshot=$((elapsed+10))
  fi
  sleep 3
done
set +e
wait "$TEST_PGID"
GAME_EXIT=$?
set -e
TEST_PGID=""
echo "GAME_EXIT=$GAME_EXIT"
# Preserve the actual GPU-fault or low-VRAM abort as the root cause. Missing
# later shader/query evidence is expected after an intentionally early stop.
if [ "$STATUS" = GPU_FAULT_ABORT ] || [ "$STATUS" = RUNTIME_VRAM_CRITICAL_ABORT ]; then
  exit 2
fi
if ! grep -aq 'IndexedImageFeedback: shader=0x7eaf40ad.*timeline_complete=YES' "$RUN/game.log"; then
  echo "INDEXED_SAMPLE_FEEDBACK=NOT_CAPTURED; no executed-index or sample-value claim"
fi
if ! grep -aq 'GotSceneGpuInvocations:.*high_volume=true' "$RUN/game.log"; then
  echo "ACTIVE_MENU_GPU_QUERY=NOT_CAPTURED; gameplay may use different pipelines"
fi
if ! grep -aq 'GotSceneIndirectGpuArgs:.*GPU_READBACK_PASS' "$RUN/game.log"; then
  echo "GPU_INDIRECT_ARGUMENTS=NOT_CAPTURED; no geometry-count claim"
fi
echo "NATIVE_GUEST_RASTER_AND_DEPTH_STATE=UNCHANGED"
if [ "$GAME_EXIT" -eq 0 ] || [ "$GAME_EXIT" -eq 124 ]; then
  STATUS=CAPTURE_COMPLETE
else
  STATUS=GAME_ABNORMAL_EXIT
  exit 2
fi
echo "SCREENSHOTS=$(find "$RUN/screenshots" -name '*.png' | wc -l)"
