#!/usr/bin/env bash
# GoT white-menu SPIR-V output A/B probe using already successful GPU candidate.
# No rebuild, global graphics config changes, or active executable writes.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
LOGS="$ROOT/logs"
BIN="$WORK/build/shadps4"
PIN="59d806d2543741111f4a2c2616b357775a221b1a"
CANDIDATE="$WORK/bin-backups/shadps4-got-menu-gpu-$PIN"
HOST_REPORT="${GOT_MENU_GPU_HOST_REPORT:-$LOGS/got-menu-gpu-host-20261018-173042.tar.gz}"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
API="http://127.0.0.1:7869"
MIN_VRAM=$((8*1024*1024*1024))
SECONDS_TO_RUN=63
RUN="$GOT/tests/runs/got-menu-stage-isolation-$(date +%Y%m%d-%H%M%S)"
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
result=["MENU_SHADER=0x7eaf40ad","MODE=GPU_INDEXED_TSHARP_CANDIDATE_VISUAL_UNVERIFIED"]
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
    echo "ISOLATED_SHADPS4_RUN=$GAME_LAUNCHED"
    echo "MATERIAL_SOURCE_PROOFS=$(grep -ac 'GuestStridedSourceProof:' "$RUN/game.log" || true)"
    echo "INDEXED_TSHARP_BINDS=$(grep -ac 'RealIndexedTSharpBound:' "$RUN/game.log" || true)"
    echo "MENU_GPU_ATTRIBUTE_MATCHES=$(grep -ac 'GuestAttributeTsharpIndexed:' "$RUN/game.log" || true)"
    echo "MENU_INVALID_IMAGE_EVENTS=$(grep -ac 'InvalidTSharpSource: shader=0x7eaf40ad' "$RUN/game.log" || true)"
    echo "MAGENTA_PATCH_LOADED=$(grep -ac 'Loaded patch for fs shader 0x7eaf40ad' "$RUN/game.log" || true)"
    echo "MAGENTA_STAGE_ISOLATION_TEST=VISUAL_ONLY_NOT_A_GAMEPLAY_FIX"
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
# Report the actual missing input instead of an opaque failure.
# These guards are read-only; never silently select another game's binary.
inputs_ok=1
if [ ! -x "$BIN" ]; then echo "MISSING_ACTIVE_EXECUTABLE=$BIN"; inputs_ok=0; fi
if [ ! -x "$CANDIDATE" ]; then echo "MISSING_PINNED_CANDIDATE=$CANDIDATE"; inputs_ok=0; fi
if [ ! -f "$HOST_REPORT" ]; then echo "MISSING_HOST_BUILD_ARCHIVE=$HOST_REPORT"; inputs_ok=0; fi
if [ ! -f "$GAME" ]; then echo "MISSING_GOT_EBOOT=$GAME"; inputs_ok=0; fi
if [ ! -d "$PROFILE/user" ]; then echo "MISSING_SOURCE_PROFILE=$PROFILE/user"; inputs_ok=0; fi
if [ "$inputs_ok" -ne 1 ]; then STATUS=MISSING_REQUIRED_INPUT; exit 2; fi
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
assert isinstance(d.setdefault("GPU",{}),dict)
d["GPU"]["patch_shaders"]=True
p.write_text(json.dumps(d,indent=2)+"\n")
print("ISOLATED_CONFIG=PASS; PIPELINE_CACHE=OFF_TEST_ONLY; GPU_SHADER_PATCH=ON_TEST_ONLY")
PY

# One isolated fragment SPIR-V patch. It changes ONLY fs_0x7eaf40ad's
# final RGBA stores to magenta. If the white menu changes, this FS owns it;
# if not, another pass owns the missing background. Not a graphics fix.
# SHA-verify the exact previously observed SPIR-V and all four OpStore
# destination/value pairs. Never patch an unknown shader binary.
SPV_ARCHIVE="$LOGS/got-menu-spv-lookup-proof-20261018-174210.tar.gz"
SPV_PATCH="$RUN/profile/user/shader/patch/fs_0x000000007eaf40ad_0.spv"
if [ ! -f "$SPV_ARCHIVE" ]; then
    STATUS=CAPTURED_SHADER_ARCHIVE_MISSING
    exit 2
fi
python3 - "$SPV_ARCHIVE" "$SPV_PATCH" "$RUN/info/menu-stage-patch-proof.txt" <<'PYSPV'
import hashlib,pathlib,struct,tarfile,sys
archive=pathlib.Path(sys.argv[1]); dest=pathlib.Path(sys.argv[2])
report=pathlib.Path(sys.argv[3])
member="info/shader-binaries/fs_0x000000007eaf40ad_0.spv"
with tarfile.open(archive,"r:gz") as tf:
    fp=tf.extractfile(member)
    if fp is None:raise SystemExit("MISSING_PINNED_SPV")
    data=fp.read()
sha=hashlib.sha256(data).hexdigest()
if sha!="67041d08553fa1235e36069b82dad3530992eac55242d06345ff64bd24b450b7":
    raise SystemExit("PINNED_SPV_SHA_MISMATCH sha="+sha)
if len(data)<1024 or len(data)%4: raise SystemExit("TRUNCATED_SPV")
words=list(struct.unpack("<"+"I"*(len(data)//4),data))
if words[0]!=0x07230203 or words[3]!=1903:
    raise SystemExit("SPV_HEADER_OR_ID_BOUND_MISMATCH")
source={1896:1892,1897:1890,1898:1891,1899:1476}
target={1896:31,1897:32,1898:31,1899:31}
stores={};float_constants={}
i=5
while i<len(words):
    n=words[i]>>16;op=words[i]&0xffff
    if n<=0 or i+n>len(words):
        raise SystemExit("INVALID_SPV_INSTRUCTION_STREAM")
    if op==43 and n==4 and words[i+1]==8:
        float_constants[words[i+2]]=words[i+3]
    if op==62 and n==3 and words[i+1] in source:
        ptr=words[i+1]
        if ptr in stores or words[i+2]!=source[ptr]:
            raise SystemExit("UNEXPECTED_MENU_OUTPUT_STORE="+str(ptr))
        stores[ptr]=i+2
    i+=n
if set(stores)!=set(source):
    raise SystemExit("INCOMPLETE_MENU_OUTPUT_COMPONENTS")
if float_constants.get(31)!=0x3f800000 or float_constants.get(32)!=0:
    raise SystemExit("MISSING_PINNED_COLOUR_CONSTANTS")
for ptr,offset in stores.items():words[offset]=target[ptr]
patched=struct.pack("<"+"I"*len(words),*words)
dest.parent.mkdir(parents=True,exist_ok=True)
dest.write_bytes(patched)
actual=hashlib.sha256(patched).hexdigest()
if actual!="18f30314b842530d98fef4d80053adb64ffa13f288533e6a22851fc4e43f83c7":
    raise SystemExit("PATCH_OUTPUT_SHA_MISMATCH")
msg=(f"PINNED_INPUT_SHA256={sha}\nPATCH_SHA256={actual}\n"
     "ONLY_FRAGMENT=0x7eaf40ad\nCHANGED_OUTPUT_STORES=4\n"
     "EXPECTED_COLOUR=MAGENTA_RGBA_1_0_1_1\n"
     "HOST_GAME_BINARY=UNCHANGED\n")
report.write_text(msg)
print("MAGENTA_STAGE_ISOLATION_PATCH=PASS")
print(msg)
PYSPV
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
  if [ "$available" -ge 0 ] && [ "$available" -lt $((1024*1024*1024)) ]; then
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
  for mark in 20 45 59; do
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
if ! grep -aq 'Loaded patch for fs shader 0x7eaf40ad' "$RUN/game.log"; then
  STATUS=TARGETED_SPV_PATCH_NOT_LOADED
  echo "TARGETED_SPV_PATCH_NOT_LOADED; visual comparison invalid"
  exit 2
fi
if [ "$STATUS" = RUNTIME_VRAM_CRITICAL_ABORT ] || [ "$STATUS" = GPU_FAULT_ABORT ]; then
  exit 2
elif [ "$GAME_EXIT" -eq 0 ] || [ "$GAME_EXIT" -eq 124 ]; then
  STATUS=CAPTURE_COMPLETE
else
  STATUS=GAME_ABNORMAL_EXIT
  exit 2
fi
echo "SCREENSHOTS=$(find "$RUN/screenshots" -name '*.png' | wc -l)"
