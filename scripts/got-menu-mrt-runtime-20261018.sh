#!/usr/bin/env bash
# GoT fullscreen shader source-to-color-target graph tracer.
# No rebuild, global graphics config changes, or active executable writes.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
LOGS="$ROOT/logs"
BIN="$WORK/build/shadps4"
PIN="8987779e92bb66e8ad3813a2fe10b61bbdc13840"
CANDIDATE="$WORK/bin-backups/shadps4-got-menu-mrt-$PIN"
HOST_REPORT="${GOT_MENU_MRT_HOST_REPORT:-$LOGS/got-menu-mrt-host-not-built.tar.gz}"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
API="http://127.0.0.1:7869"
MIN_VRAM=$((8*1024*1024*1024))
SECONDS_TO_RUN=65
RUN="$GOT/tests/runs/got-menu-mrt-runtime-$(date +%Y%m%d-%H%M%S)"
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
mkdir -p "$RUN/info" "$RUN/screenshots" "$RUN/profile" "$LOGS"
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
  grep -aE 'GuestStridedSourceProof:|GuestStridedPermutationProof:|GuestTsharpIncompleteProgram:|RealIndexedTSharp(Enabled|Bound|UnsupportedSize):|ResourceNullBuffer:|GPUVM fault detected|GPU hang detected' \
    "$RUN/game.log" > "$RUN/info/graphics-evidence.log" || true
  # Extract source failures of the white MENU shader independently of
  # large texture binding successes. Preserve the full game log as well.
  grep -aE 'SrtGlobalDependencyResult:|SrtHostBarrier:|SrtBarrierShape:|SrtHostBarrierShape:|SrtHostBarrierSummary:|SrtHostBarrierGroup:|SrtNativeAudit:|SrtSourceAudit:|SrtPlanSkip:|SrtPlanCollision:|SrtInlineCandidate:|SrtInlineEligibility:|InvalidTSharpSource:|InvalidVSharpSource:|SharpMaskPreserved:|ResourceAuditCompile:|RealIndexedTSharpBound:|GuestAttributeTsharpIndexed:|IndexedTSharpSource:' \
    "$RUN/game.log" >"$RUN/info/menu-provenance-all.log" || true
  # Correlate submitted guest shader draws and actual video-out sources.
  # Every event uses steady_clock monotonic milliseconds across threads.
  grep -aE 'GotMenuDrawTrace:|GotMenuPassWindow:|GotMenuPassTop:|GotMenuPresentTrace:|GotMenuBlankPresent:|GotMenuFastClear:|GotMenuSourceEdge:|GotMenuMrtTargetTrace:' \
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
if not any("0x1504a08000" in (x for x in writer_addresses)
           for writer_addresses in [writers.keys()]):
    out.append("SCENE_COLOR_WRITER=NOT_YET_CAPTURED_CHECK_OTHER_OUTPUT_PATHS")
path=Path(sys.argv[2])
path.write_text("\n".join(out)+"\n")
print("\n".join(out[:100]))
PYTEXGRAPH
import re,sys
from collections import Counter,defaultdict
from pathlib import Path
content=Path(sys.argv[1]).read_text(errors="replace")
content=re.sub(r"\x1b\[[0-9;]*[mK]","",content)
out=[]
presents=Counter()
draws=defaultdict(set)
sources=defaultdict(Counter)
info={}
for line in content.splitlines():
    def val(name):
        m=re.search(r"\b"+name+r"=([^\s]+)",line)
        return m.group(1) if m else ""
    if "GotMenuPresentTrace:" in line:
        presents[val("source_addr")]+=1
    elif "GotMenuDrawTrace:" in line:
        draws[val("color0")].add(val("fs"))
    elif "GotMenuSourceEdge:" in line:
        key=(val("fs"),val("target"),val("resource"))
        source=val("source_addr")
        sources[key][source]+=1
        info[key,source]=(val("kind"),val("fetched"),
                          val("dimensions"),val("data_format"),
                          val("number_format"),val("image_type"))
out.append("PURPOSE=REAL_MENU_FINAL_SHADER_TEXTURE_TO_RENDER_TARGET_GRAPH")
out.append("PRESENTED_BUFFERS="+",".join(
    f"{addr}:{n}" for addr,n in presents.most_common(8)))
out.append("DISTINCT_SHADER_SOURCE_EDGES="+str(len(sources)))
out.append("REAL_FINAL_FRAGMENT=0x96aa3f2e")
present_edges=[]
for (fs,target,res), srcs in sorted(sources.items()):
    if target not in presents: continue
    for source,num in srcs.most_common(15):
        kind,fetched,dims,fmt,nfmt,typ=info[fs,target,res,source]
        producer=",".join(sorted(draws[source])) or "NOT_SEEN_AS_GUEST_DRAW_TARGET"
        present_edges.append((fs,target,res,source,num,fetched,dims,kind,producer))
        out.append(f"PRESENT_SOURCE fs={fs} target={target} slot={res} "
                   f"source={source} observations={num} "
                   f"fetched={fetched} dimensions={dims} type={kind} "
                   f"source_producer_fs={producer}")
if not present_edges:
    out.append("NO_PRESENT_SOURCE_TRACES=YES_RESULT_INCOMPLETE")
for (fs,target,res),srcs in sorted(sources.items()):
    if fs=="0x96aa3f2e" and target in presents:continue
    if target=="0x0":continue
    for source,num in srcs.most_common(2):
        target_is_presentation=target in presents
        source_is_draw=source in draws
        if target_is_presentation or source_is_draw:
            kind,fetched,dims,fmt,nfmt,typ=info[fs,target,res,source]
            out.append(f"OFFSCREEN_SOURCE fs={fs} target={target} "
                       f"slot={res} source={source} "
                       f"source_drawn_by={','.join(sorted(draws[source])) or 'NOT_SEEN'} "
                       f"fetched={fetched} type={kind} "
                       f"dimensions={dims} fmt={fmt} nfmt={nfmt}")
out.append("ANY_DIRECT_FINAL_TO_GUEST_DRAW_EDGE="+str(any(
    source in draws for fs,target,res,source,num,*rest in present_edges)))
Path(sys.argv[2]).write_text("\n".join(out)+"\n")
print("\n".join(out[:80]))
PYTEXGRAPH

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
result=["MENU_SHADER=0x96aa3f2e","MODE=LIVE_SAMPLED_TEXTURE_SOURCE_GRAPH"]
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
  {
    echo "STATUS=$STATUS"
    echo "GAME_EXIT=$GAME_EXIT"
    echo "GAME_LAUNCHED=$GAME_LAUNCHED"
    echo "OLLAMA_ACTION=$OLLAMA_ACTION"
    echo "OLLAMA_RESTORE=$OLLAMA_RESTORE"
    echo "ACTIVE_BINARY_UNCHANGED=$([ -z "$BIN_SHA" ] || [ "$(sha256sum "$BIN" 2>/dev/null | awk '{print $1}')" = "$BIN_SHA" ] && echo YES || echo NO)"
    echo "ISOLATED_SHADPS4_RUN=YES"
    echo "MATERIAL_SOURCE_PROOFS=$(grep -ac 'GuestStridedSourceProof:' "$RUN/game.log" || true)"
    echo "INDEXED_TSHARP_BINDS=$(grep -ac 'RealIndexedTSharpBound:' "$RUN/game.log" || true)"
    echo "MENU_GPU_ATTRIBUTE_MATCHES=$(grep -ac 'GuestAttributeTsharpIndexed:' "$RUN/game.log" || true)"
    echo "MENU_INVALID_IMAGE_EVENTS=$(grep -ac 'InvalidTSharpSource: shader=0x7eaf40ad' "$RUN/game.log" || true)"
    echo "MENU_PRESENT_TRACE_ENABLED=YES"
    echo "IN_GAME_VRAM_ABORT_THRESHOLD_GIB=2"
    echo "SOURCE_GRAPH_DIAGNOSTIC_ONLY=YES"
    echo "ACTUAL_COLOR_DRAW_EVENTS=$(grep -ac 'GotMenuDrawTrace:' "$RUN/game.log" || true)"
    echo "ACTUAL_COLOR_PASS_WINDOWS=$(grep -ac 'GotMenuPassWindow:' "$RUN/game.log" || true)"
    echo "ACTUAL_PRESENT_FRAMES=$(grep -ac 'GotMenuPresentTrace:' "$RUN/game.log" || true)"
    echo "ACTUAL_TEXTURE_SOURCE_EDGES=$(grep -ac 'GotMenuSourceEdge:' "$RUN/game.log" || true)"
    echo "ALL_MRT_TARGET_TRACES=$(grep -ac 'GotMenuMrtTargetTrace:' "$RUN/game.log" || true)"
    echo "NO_SHADER_PATCHES=YES"
    echo "GPUVM_FAULT_EVENTS=$(grep -aic 'GPUVM fault detected' "$RUN/game.log" || true)"
    echo "EXIT=$rc"
    echo "SSH_SESSION=OPEN"
  } > "$RUN/info/final.txt"
  tar -czf "$REPORT" -C "$RUN" info screenshots game.log 2>/dev/null || {
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
# This test is looking for *compile-time* invalid T# and native SRT causes.
# Cached SPIR-V would hide them; disable cache ONLY for isolated profile.
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

STATUS=GAME_RUNNING
echo "GAME_START duration=${SECONDS_TO_RUN}s DISPLAY=$DISPLAY_OK"
(
  cd "$RUN/profile"
  export HOME="$RUN/profile" DISPLAY="$DISPLAY_OK"
  export SHADPS4_STRIDED_SOURCE_PROOF=1
  export SHADPS4_STRIDED_SOURCE_PROOF_HASHES="0x55d17c16,0x5c4c2b6f"
  export SHADPS4_STRIDED_GUEST_TSHARP=1
  export SHADPS4_GUEST_TSHARP_LARGE_ARRAY=1
  export SHADPS4_GUEST_TSHARP_CACHE=0
  export SHADPS4_TEXTURE_SLOT_PRESSURE=0
  export SHADPS4_GPUVM_SPARSE_MAP=0
  export SHADPS4_RESOURCE_AUDIT=1
  export SHADPS4_RESOURCE_AUDIT_HASH="0x7eaf40ad,0x55d17c16,0x5c4c2b6f"
  export SHADPS4_INDEXED_IMAGE_AUDIT=1
  export SHADPS4_INDEXED_IMAGE_AUDIT_HASHES="0x7eaf40ad"
  export SHADPS4_MENU_PASS_TRACE=1
  export SHADPS4_RENDER_AUDIT=1
  unset SHADPS4_TARGET_SHADER_DUMP_HASHES
  export SHADPS4_INVALID_SHARP_TRACE_HASHES="0x7eaf40ad"
  export SHADPS4_SRT_SOURCE_AUDIT=1
  export SHADPS4_SRT_INLINE_AUDIT=1
  export SHADPS4_SRT_TRACE_HASHES="0x7eaf40ad"
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
  for mark in 20 40 60; do
    if [ "$elapsed" -ge "$mark" ] && [ "$elapsed" -lt $((mark+4)) ] &&
       [ ! -f "$RUN/screenshots/$mark.png" ] && command -v import >/dev/null; then
      timeout 6s import -display "$DISPLAY_OK" -window root \
        "$RUN/screenshots/$mark.png" >/dev/null 2>&1 || true
    fi
  done
  sleep 3
done
set +e
wait "$TEST_PGID"
GAME_EXIT=$?
set -e
TEST_PGID=""
echo "GAME_EXIT=$GAME_EXIT"
if [ "$STATUS" = RUNTIME_VRAM_CRITICAL_ABORT ] || [ "$STATUS" = GPU_FAULT_ABORT ]; then
  exit 2
elif [ "$GAME_EXIT" -eq 0 ] || [ "$GAME_EXIT" -eq 124 ]; then
  STATUS=CAPTURE_COMPLETE
else
  STATUS=GAME_ABNORMAL_EXIT
  exit 2
fi
echo "SCREENSHOTS=$(find "$RUN/screenshots" -name '*.png' | wc -l)"
