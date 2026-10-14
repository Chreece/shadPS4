#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
P="$HOME/projects/shadps4/GhostOfTsushima"
TESTS="$P/tests"
LOGROOT="$HOME/projects/shadps4/logs"
V53="$TESTS/got-v53-invalid-image-fallback-20261014"
V31="$TESTS/got-v31-nptoolkit-mspace-20261013"
V55="$TESTS/got-v55-empty-dynamic-slot-null-20261014"
SRC='src/shader_recompiler/ir/passes/resource_patching_pass.cpp'
SHA53='600d2ac9c48353310f16bd8365d9ab0b14ba8acc34e0a3e33256e1e62578aab4'
SRC53='58241b84918f2bb6d9f7a1db3ec49a6a021369f5d460ff9eaa9e6000e7999941'
SHA31='1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f'
DRV='87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
ICD="$P/tools/radv-nuw-complete-25.0.7/radeon_icd.json"
DRIVER="$P/tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so"
GAME='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'
sha(){ sha256sum "$1" | cut -d' ' -f1; }
fail(){ echo "STOP: $*" >&2; exit 2; }

[[ "$(id -u)" != 0 ]] || fail 'run without sudo'
[[ -x "$V53/build/shadps4" && "$(sha "$V53/build/shadps4")" == "$SHA53" ]] || fail 'v53 binary changed/missing'
[[ -f "$V53/$SRC" && "$(sha "$V53/$SRC")" == "$SRC53" ]] || fail 'v53 source changed/missing'
[[ -x "$V31/build/shadps4" && "$(sha "$V31/build/shadps4")" == "$SHA31" ]] || fail 'v31 binary changed/missing'
[[ -f "$DRIVER" && "$(sha "$DRIVER")" == "$DRV" ]] || fail 'private RADV changed/missing'
[[ -f "$ICD" && -f "$GAME" ]] || fail 'ICD or GoT eboot missing'
if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then fail 'another shadPS4 is running'; fi
mkdir -p "$V55/$(dirname "$SRC")" "$V55/build" "$P/play" "$LOGROOT"

python3 - "$V53/$SRC" "$V55/$SRC" <<'PY'
from pathlib import Path
import sys
src,dst=map(Path,sys.argv[1:])
t=src.read_text()
a=t.index('std::optional<AmdGpu::Image> GhostPrototypeImage(')
b=t.index('\nstatic bool GhostBindlessEnabled()',a)
prototype=r'''std::optional<AmdGpu::Image> GhostPrototypeImage(const Info& info, u32 slot) {
    if (info.user_data.size() < 2 || slot >= 7)
        return {};
    const u64 root = u64(info.user_data[0]) | (u64(info.user_data[1]) << 32);
    if (!root || root > 0x00007fffffffffffull - 0x180 - sizeof(AmdGpu::Buffer))
        return {};
    const int fd = ::open("/proc/self/mem", O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return {};
    AmdGpu::Buffer table{};
    const ssize_t n = ::pread(fd, &table, sizeof(table), static_cast<off_t>(root + 0x180));
    if (n != sizeof(table) || !table.base_address || table.GetStride() != 340 ||
        table.GetSize() != 18020 || table.base_address >
        0x00007fffffffffffull - (u64(52) * 340 + u64(slot) * 32 + sizeof(AmdGpu::Image))) {
        ::close(fd);
        return {};
    }
    for (u32 row = 0; row < 53; ++row) {
        AmdGpu::Image image{};
        const u64 address = table.base_address + u64(row) * 340 + u64(slot) * 32;
        const ssize_t got = ::pread(fd, &image, sizeof(image), static_cast<off_t>(address));
        if (got == sizeof(image) && image.Valid() && image.Address() &&
            image.GetType() == AmdGpu::ImageType::Color2D) {
            ::close(fd);
            LOG_WARNING(Render_Recompiler,
                        "GOT55_GHOST_PROTOTYPE shader={:#x} slot={} row={} address={:#x} data_format={} num_format={}",
                        info.pgm_hash, slot, row, image.Address(),
                        u32(image.GetDataFmt()), u32(image.GetNumberFmt()));
            return image;
        }
    }
    ::close(fd);
    return {};
}
'''
t=t[:a]+prototype+t[b:]
old='''        const auto prototype = GhostPrototypeImage(info, index->slot);\n        ASSERT_MSG(prototype.has_value(), "Ghost indexed T# slot {} is not a valid 2D texture", index->slot);\n        image_res.ghost_dynamic_image_count = 53;\n        image_res.ghost_dynamic_image_slot = index->slot;\n        image_res.ghost_prototype_image = *prototype;\n        LOG_WARNING(Render_Recompiler,\n                    "GHOST_BINDLESS_SOURCE shader={:#x} records=53 slot={} format={} "\n                    "image_type={} record_offset={} stride=340",\n                    info.pgm_hash, index->slot, u32(prototype->GetDataFmt()),\n                    u32(prototype->GetType()), index->slot * 32);\n'''
new='''        const auto prototype = GhostPrototypeImage(info, index->slot);\n        if (!prototype.has_value()) {\n            LOG_WARNING(Render_Recompiler,\n                        "GOT55_GHOST_EMPTY_DYNAMIC_SLOT shader={:#x} slot={} rows_scanned=53 action=null_sampled_image",\n                        info.pgm_hash, index->slot);\n        } else {\n            image_res.ghost_dynamic_image_count = 53;\n            image_res.ghost_dynamic_image_slot = index->slot;\n            image_res.ghost_prototype_image = *prototype;\n            LOG_WARNING(Render_Recompiler,\n                        "GHOST_BINDLESS_SOURCE shader={:#x} records=53 slot={} format={} "\n                        "image_type={} record_offset={} stride=340",\n                        info.pgm_hash, index->slot, u32(prototype->GetDataFmt()),\n                        u32(prototype->GetType()), index->slot * 32);\n        }\n'''
if t.count(old)!=1: raise SystemExit('STOP: Ghost assertion block changed')
t=t.replace(old,new,1)
if t.count('GOT53_NULL_INVALID_IMAGE')!=1: raise SystemExit('STOP: v53 fallback missing')
if t.count('GOT55_GHOST_EMPTY_DYNAMIC_SLOT')!=1 or t.count('GOT55_GHOST_PROTOTYPE')!=1:
    raise SystemExit('STOP: v55 patch verification failed')
dst.parent.mkdir(parents=True,exist_ok=True)
if dst.exists() and dst.read_text()!=t: raise SystemExit('STOP: existing v55 source differs')
dst.write_text(t)
PY

python3 - "$V53" "$V31" "$V55" "$SRC" <<'PY'
from pathlib import Path
import json,subprocess,sys,os
v53,v31,v55=map(Path,sys.argv[1:4]); rel=sys.argv[4]; build=v55/'build'
cc=json.loads((v53/'build/compile-argv.json').read_text())
oldsrc=str(v53/rel); newsrc=str(v55/rel)
if cc.count(oldsrc)!=1 or cc.count('-o')!=1: raise SystemExit('STOP: v53 compile manifest changed')
oldobj=cc[cc.index('-o')+1]; newobj=str(build/'resource_patching_pass.cpp.o')
cc=[newsrc if x==oldsrc else x for x in cc]; cc[cc.index('-o')+1]=newobj
if '-MF' in cc: cc[cc.index('-MF')+1]=newobj+'.d'
if '-MT' in cc: cc[cc.index('-MT')+1]=newobj
ld=json.loads((v53/'build/link-argv.json').read_text())
hits=[i for i,x in enumerate(ld) if x==oldobj or x.endswith('/'+Path(oldobj).name)]
if len(hits)!=1 or ld.count('-o')!=1: raise SystemExit('STOP: v53 link manifest changed')
pending=str(build/'shadps4.pending'); ld[hits[0]]=newobj; ld[ld.index('-o')+1]=pending
ld=[x.replace(str(v53/'build/link.d'),str(build/'link.d')) for x in ld]
(build/'compile-argv.json').write_text(json.dumps(cc,indent=2)+'\n')
(build/'link-argv.json').write_text(json.dumps(ld,indent=2)+'\n')
for p in (Path(newobj),Path(newobj+'.d'),Path(pending)):
    if p.is_symlink(): raise SystemExit('STOP: unsafe build output '+str(p))
    if p.exists(): p.unlink()
print('STAGE=COMPILE_RESOURCE_PATCHING_ONLY',flush=True)
with (build/'compile.log').open('wb') as f:
    r=subprocess.run(cc,cwd=v31/'build',stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT)
if r.returncode: raise SystemExit('STOP: compile failed; '+str(build/'compile.log'))
print('STAGE=RELINK_V53_WITH_V55_RESOURCE_OBJECT',flush=True)
with (build/'link.log').open('wb') as f:
    r=subprocess.run(ld,cwd=v31/'build',stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT)
if r.returncode: raise SystemExit('STOP: link failed; '+str(build/'link.log'))
out=build/'shadps4'
os.replace(pending,out); out.chmod(0o700)
PY

SHA55="$(sha "$V55/build/shadps4")"
[[ "$SHA55" != "$SHA53" ]] || fail 'v55 binary unchanged'
echo "BUILD=V55_READY SHA=$SHA55"

RUN="$P/play/manual-v55-direct"
if [[ ! -d "$RUN" ]]; then
  SRC_RUN=''
  for x in "$P/play/manual-v54-direct" "$P/play/manual-v53-direct" "$P/play/manual-v53" "$P/play/manual-v52" "$P/play/manual-v51"; do
    if [[ -d "$x/user" && ! -L "$x" ]]; then SRC_RUN="$x"; break; fi
  done
  [[ -n "$SRC_RUN" ]] || fail 'no preserved manual GoT profile found'
  if find "$SRC_RUN" -type l -print -quit | grep -q .; then fail 'symlink in preserved manual profile'; fi
  TMP="$(mktemp -d "$P/play/.manual-v55-XXXXXXXX")"
  cp -a "$SRC_RUN/." "$TMP/"
  rm -f "$TMP/shadps4"
  cp "$V55/build/shadps4" "$TMP/shadps4"
  chmod 700 "$TMP/shadps4"
  printf 'source_profile=%s\nbinary_sha256=%s\nautomatic_input=false\n' "$SRC_RUN" "$SHA55" > "$TMP/v55-runtime.txt"
  mv "$TMP" "$RUN"
fi
[[ "$(sha "$RUN/shadps4")" == "$SHA55" ]] || fail 'v55 runtime binary mismatch'
if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then fail 'another shadPS4 is running'; fi

STAMP="$(date +%Y%m%d-%H%M%S)"
REPORT="$LOGROOT/got-v55-manual-$STAMP"
mkdir -p "$REPORT"/{info,logs,screenshots,radv-home,spirv-failures}
RUNNER="$REPORT/info/worker.sh"
cat > "$RUNNER" <<RUN
#!/usr/bin/env bash
set -uo pipefail
cd $(printf '%q' "$RUN")
export HOME=$(printf '%q' "$REPORT/radv-home")
export VK_DRIVER_FILES=$(printf '%q' "$ICD")
export VK_ICD_FILENAMES=$(printf '%q' "$ICD")
export RADV_DEBUG=hang,noumr
export MESA_SHADER_CACHE_DISABLE=true
export MESA_SPIRV_FAIL_DUMP_PATH=$(printf '%q' "$REPORT/spirv-failures")
export SHADPS4_GHOST_BINDLESS_EXPERIMENT=1
export SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1
./shadps4 --cpu-id-mode=native --game $(printf '%q' "$GAME") --fullscreen true >$(printf '%q' "$REPORT/logs/game-stdout.log") 2>&1 &
game=\$!
( last=0; for target in 20 45 75 120 180 300 480; do delay=\$((target-last)); sleep "\$delay"; last=\$target; kill -0 "\$game" 2>/dev/null || break; wid=\$(xdotool search --onlyvisible --pid "\$game" 2>/dev/null | tail -n1 || true); if [[ -n "\$wid" ]]; then import -window "\$wid" $(printf '%q' "$REPORT/screenshots")/\${target}s.png 2>/dev/null || true; fi; done ) & shot=\$!
wait "\$game"; rc=\$?
kill "\$shot" 2>/dev/null || true; wait "\$shot" 2>/dev/null || true
echo "\$rc" >$(printf '%q' "$REPORT/info/exit-code.txt")
tar -czf $(printf '%q' "$REPORT.tar.gz") -C $(printf '%q' "$REPORT") info logs screenshots 2>/dev/null || true
exit "\$rc"
RUN
chmod 700 "$RUNNER"

ENVARGS=()
for k in DISPLAY XAUTHORITY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS PULSE_SERVER PIPEWIRE_REMOTE SDL_GAMECONTROLLERCONFIG SDL_GAMECONTROLLERCONFIG_FILE; do
  [[ -n "${!k:-}" ]] && ENVARGS+=("--setenv=$k=${!k}")
done
UNIT="got-v55-manual-$STAMP.service"
systemd-run --user --collect --no-block --service-type=exec --unit="$UNIT" \
  --description='GoT v55 manual gamepad' --property=TimeoutStopSec=30s --property=KillMode=control-group \
  --working-directory="$RUN" --setenv="HOME=$HOME" --setenv='PATH=/usr/local/bin:/usr/bin:/bin' \
  "${ENVARGS[@]}" /usr/bin/bash "$RUNNER"
printf 'REPORT=%s\nARCHIVE=%s\nUNIT=%s\n' "$REPORT" "$REPORT.tar.gz" "$UNIT" > "$LOGROOT/.got-v55-manual-latest"
echo 'V55_STARTED=YES'
echo 'INPUT=MANUAL_GAMEPAD'
echo 'AUTOMATIC_INPUT=NONE'
echo "REPORT=$REPORT"
echo "WHEN_FINISHED_UPLOAD=$REPORT.tar.gz"
