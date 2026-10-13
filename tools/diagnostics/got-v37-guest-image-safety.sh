#!/usr/bin/env bash
# GoT v37: preserve v31/v33 and fix incorrect null image-array shape.
# Only one existing Vulkan TU is compiled; v33 SRT is linked unchanged.
set -Eeuo pipefail
umask 077
R="$HOME/projects/shadps4"; P="$R/GhostOfTsushima"; T="$P/tests"
A="$T/got-v31-nptoolkit-mspace-20261013"
B="$T/got-v33-gow-srt-correctness-20261013"
C="$T/got-v37-guest-image-safety-20261013"
VK=src/video_core/renderer_vulkan/vk_rasterizer.cpp
SRT=src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
L33="$P/scripts/got-v33-gow-srt-visual.sh"
L37="$P/scripts/got-v37-descriptor-visual.sh"
AHASH=1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f
BHASH=9e6aff9d6d83310cdb4937c2488d64be836e7903a55b67fca08264cdd6a8ab8a
VKSHA=937a0a7e7e63bbaec84abe4d0372385f0f5dc874506dfd3a8dd7fae67f798a69
FIXSHA=02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d
SRTHASH=5bbdf054553361bda16921a813fc81499994ea62e28340fc90cb5f06034ad1ca
PATCHHASH=873f1269b21c52fae190ddef85ec3f9cb88be47f4c2a9b06d7576363ee8d2160
PIN=3b7197f57c2a44048f6365e2568050df7324aa33
fail(){ echo "STOP: $*" >&2; exit 2; }
sha(){ sha256sum "$1" | cut -d ' ' -f1; }
if [[ -f "$L37" && ! -L "$L37" ]]; then
 echo 'V37_ALREADY_SCHEDULED; NO_REBUILD_OR_REPEAT'
 bash "$L37" status
 exit 0
fi
[[ ! -e "$L37" && ! -L "$L37" ]] || fail 'unsafe v37 launcher'
for x in git ninja python3 base64 gzip sha256sum bash cmp; do
 command -v "$x" >/dev/null || fail "missing $x"
done
for p in "$T" "$P/scripts" "$R/logs" "$A" "$B" "$A/build" "$B/build"; do
 [[ -d "$p" && ! -L "$p" ]] || fail "missing/unsafe directory $p"
done
for p in "$A/build/shadps4" "$B/build/shadps4" "$A/build/build.ninja" "$B/build/link-v33.sh" "$B/$VK" "$B/$SRT" "$L33"; do
 [[ -f "$p" && ! -L "$p" ]] || fail "missing/unsafe source $p"
done
[[ "$(sha "$A/build/shadps4")" == "$AHASH" ]] || fail 'v31 binary hash mismatch'
[[ "$(sha "$B/build/shadps4")" == "$BHASH" ]] || fail 'v33 binary hash mismatch'
[[ "$(sha "$B/$VK")" == "$VKSHA" && "$(sha "$A/$VK")" == "$VKSHA" ]] || fail 'rasterizer source modified'
[[ "$(sha "$B/$SRT")" == "$SRTHASH" ]] || fail 'v33 SRT patch changed'
[[ "$(git -C "$A" rev-parse HEAD)" == "$PIN" && "$(git -C "$B" rev-parse HEAD)" == "$PIN" ]] || fail 'git source pin changed'
HDR=src/video_core/renderer_vulkan/vk_rasterizer.h
cmp -s "$A/$HDR" "$B/$HDR" || fail 'rasterizer ABI headers differ'
grep -Fq 'Shader::NUM_IMAGES * 16' "$B/$HDR" || fail 'expanded image binding capacity missing'
[[ -s "$B/build/CMakeFiles/shadps4.dir/$SRT.o" ]] || fail 'v33 SRT object missing'
[[ "$(df -Pk "$T" | awk 'END {print $4}')" -gt 524288 ]] || fail 'less than 512MiB free'
if [[ ! -e "$C" && ! -L "$C" ]]; then
 echo STAGE=CREATE_ISOLATED_SOURCE
 git -C "$B" worktree add --detach "$C" "$PIN"
 for f in "$VK" "$SRT" src/shader_recompiler/resource.h src/shader_recompiler/info.h src/shader_recompiler/ir/passes/resource_discover_pass.cpp src/shader_recompiler/ir/passes/resource_patching_pass.cpp src/shader_recompiler/backend/spirv/emit_spirv_image.cpp src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp; do
  cp -p "$B/$f" "$C/$f"
 done
 for f in libc_internal_memory.cpp libc_internal_threads.cpp mspace_allocator.h cxa_guard_registry.h; do
  p=src/core/libraries/libc_internal/$f
  [[ -s "$B/$p" ]] || fail "missing preserved HLE source $p"
  cp -p "$B/$p" "$C/$p"
 done
else
 [[ -d "$C" && ! -L "$C" && -f "$C/.git" ]] || fail 'unsafe existing v37 source'
 [[ "$(git -C "$C" rev-parse HEAD)" == "$PIN" ]] || fail 'existing v37 git revision mismatch'
fi
mkdir -p "$C/build"
PATCH="$C/build/got37.patch"
if [[ ! -f "$PATCH" ]]; then
 base64 -d <<'PATCH64' | gzip -dc > "$PATCH"
H4sIAAAAAAAC/9VZa1PjOBb9zq9QM1VsUnEMgaYfDmGanaaz1AA9RRh2qyjKpdhKosWvkuyEbA//fe+VbEd+hKZ7P60/ED+kK+nec4+OLv1+n9B9Kbz9JfdZ7HqxYPuCRT4TTLjLLHik0f7y0RVUpkzw/zBhe0my0+v1yPTHu336RPof3g2swSHp4e/xgHz6tEPw8uJIpiQ7OiQzLmTq8pDOmcv9JzIi+X00i6UtwVinO9Sd9vfJbUxYmAU0ZUSmseDRnKTw6ikJuMdTEvKEBGzJAmmRacYDn1DiM+kJnkBrQoWga7Li6YIw6i02zW09wDSOZeo4MLmU8ogJx5EhDQJ3yTzofgLTtciH03yCG7uusuviXCVMVdvCtUVZqFcmvz0Pd3rFa91fMBlnwsOV+uwJG6h+M5hnR7uHZmm8Z4xGHFg0PNjaZpd80zarDq3aLf1Zfd3r5cNt+uJoJJULKpKyE45qj1k6wbcdNXh3uBkUAvI7YwlJFxCOBQU4/E2Cv72ACuYbju9rx0OThKkFTmMIwZIGHAIU+RWDdCpZlOrxpU0mjzxJMMzaBHwSnEkwxWepJBAcgmAQ5E6BkMggTqXd5haMxRTWDrZkdX3XWfj3/ENziWeTyfnNrXs1GXcqFk7JAdnb27Qzrkq7k2Ko4k0OadI30GG12tm9ZU9pJlgTwezJY8yXZJNvehCSD6JyA553zZUYUcZmbpQBsnW3EbnfezDRVAJRwRW+Hwzh56SytCHp9Xi33gsvjdv7IqcttYAHUvfE/cYBvd7DsGmnMAA9y/wxLxW9dJ2wajy5dFeCpymLWt1auX4ld8hovwGhOU7u79+AGuApx8Qt2HeciXbo9w06rzSYf6qt6rn6+BLR2EkmF+6Ueo8VYJoRf66maj4+OSIcMgeI9wmSVA1hkeuvtySOGIlnZLwAEoQ8lkCMETk+6mPOrTXyzNQCi5fxnHBIQ8FoQOYZA3wZWJUsoQKSM1iTmYhDxRLjP/40m/hZmBgm+YzoDLSTeeguqFyQEWDv6QN7//bI+8D+vLyEpKsz3Ak5bMAQrKTcgx/fcWgah9xD9j4lQTyfM99Va29gCoc3G9gzlnoLl/p+Z2BpWyELY7F2YwFcB5QaUGjWhRm8bU0EPTw6rtw7BF21YllP2KVSMpF2MMLxrHMW+uMkc5wLDFIXfZF/ACvd7nDLgDBHL1ljG9unKe10LbKned36Xv/Lr2P3n2c31xfX486N2t9dTaztDEV2x19vj967Xy7+df7Zvbg6G5/n+8Dom/PL03MZqdG354Kb8HZ3izXwNPSQeWe1P2DzkCYJU3e4HncWpngPqJ/C/PKnbSaRHvC7DqXMwpCK9Usd/NVBPr6/GpR3h+Xdkb7b3eKRKnytGlatwgt2FvEZh/zr1Vu0m9Xxs8+0gzCk+Zs7dBI8t3fTcO2fXkjVDsB0RdV22mkaPNhmBKBbNAcp8BlC8CVMO13oUv1yrQKSf3uNKWTC0o5B4KqBqyJm5xHbZhCAfH/wYKnfQf57mP8ePXS30+vzTluWVhLOAvI7JXOkQ9dfRxRIxFBzFdYwJt/SHvRyFqXIEJW5A4HeMOorEWMwaKp3BrkhTUlDRhA3AnSoQZ8NcymdBgy60NSQZEThCuwp3Yt0Ha8iUE0R64NKyzwAIdgFRrOr9gzl831OVlr/48GhNTggvY9vQfMflVq/NklYTRIwv6+lhxY0VAIzo8iTauK5mFtytiJqfweRiPkOWsdrtenFYQLbF+hG3XwuGLPJH4IteZxJ2IBW4A0eQAO4FywJKC5bEUyrvRDpvw8KKARXiniVOw/nps4r+A62yDxUdtOGFltwngjU3IDbMTIjEhkkXmN3cGKrovBg8RyIjxW5jlvg5mWZweTNaLsmfYEJWkwhGWy3ROcAbAbhcBz86+bHJWlYqvDE/2rJ5JVXeAl63AFwFLlUVaHCGm6jO1vl24acXrbxqmkYE8ewtzLlsGkHCeXNBjUN3mhJ0OpmgACp87tCh6XT9PjYOvwIafrxvXX4rpKmz8XptXZgKPZdYq6ijHD7URIDWu/SvnBccGO+SDLkr7+MoUekyBg1skpOx9G/F5FKZvRVv6mg208cdu180f8J6T2obzG1o1UjwAhxHmVsuMXpKvg/tm2Dl6pjvNmeWIU3sdNL7fLgdRvge0Ehbk2q3Rv2b9i58OzBdZzI7S+kIvYsknDY7kGYWUC1fqrvdl+wqVeigq/aqhkXj7vWd1O8KaXUDMonNQur0OUenLTVCWLjwReGaHQq3Tn8P4enzu+JEhaOc8WT/GD8BUQMjngV+wzLau4sf+GG+KZyPm98Ntb2s/UaY4qqbqEYaGvhgvd6DWCbJTfJAsAr86vjblV2O+SV16+tavKeP7zagpPDc6iYfHBw+E4prsHBu2NrcLydy3+uklDa6eW6KBdmWF5heOyfgbxER28qtkPyiBVB9kRBqmpxB7Jrni6kEnEskiCYSnsym824x1G4SS1e88rVpkqI6lj7SRmYxUEQr1SNS+lIUZQkjN2wWUCGY9Ymt+BBC9rSQt7oZNSqU0xzHk2ox9N124mrrV6HXi0Kcgy8sqCZBGDtGiXtCYhvWFgCR3/0pWB9jECxZiXmVww6ZwnqCjguzLDemUdp58VSnbZRFup+uEDHHwweUGIWFFARoZFZfxv9WB2ttvm/KWaCs1TAHgzea2AP9P8P2s4SxRXwJRMJzK5/Kthc2sDPUw/wLA1FW+c5WdS5C0kx0VionOyeW3XN8tHNgUNqQjI/DgDvw8JV2Vy368i8+rJ9plYBZ9SYPkvSxZYidAu2T16NUBOl+8UizIpdHtomTut5wEJ1htKssfGIRe5+d6/hSOj+4+z68+W5Bd7KDziXdB1noNfYmEVM0MC0XLBuqgq3CK3yXt6Xt8V/LkxQlh9tX6YThmqzOoNtbYti6Khekan8Z6TS5QzZ7DxgIdIV5tnOfwGuZ+W1TxsAAA==
PATCH64
fi
[[ "$(sha "$PATCH")" == "$PATCHHASH" ]] || fail 'source patch integrity failure'
if [[ "$(sha "$C/$VK")" == "$VKSHA" ]]; then
 (cd "$C" && git apply --check "$PATCH" && git apply "$PATCH")
elif [[ "$(sha "$C/$VK")" == "$FIXSHA" ]]; then
 echo STAGE=RESUME_ALREADY_PATCHED_SOURCE
else
 fail 'unrecognized source state in v37'
fi
[[ "$(sha "$C/$VK")" == "$FIXSHA" && "$(sha "$C/$SRT")" == "$SRTHASH" ]] || fail 'cumulative source validation failed'
python3 - "$A" "$B" "$C" "$VK" <<'PY'
from pathlib import Path
import re,subprocess,sys
A,B,C,VK=sys.argv[1:]
a,b,c=Path(A),Path(B),Path(C)
obj='CMakeFiles/shadps4.dir/'+VK+'.o'
out=str(c/'build'/obj)
commands=subprocess.run(['ninja','-C',str(a/'build'),'-t','commands','shadps4'],
 capture_output=True,text=True,check=True).stdout.splitlines()
cc=[s for s in commands if VK in s and re.search(r'\s-c\s',s)]
if len(cc)!=1 or obj not in cc[0] or str(a/VK) not in cc[0]:
 raise SystemExit('STOP: unique rasterizer compile command not found')
cmd=cc[0].replace(str(a/VK),str(c/VK))
cmd=cmd.replace(obj+'.d','__DEPF__').replace(obj,'__OBJF__')
cmd=cmd.replace('__DEPF__',out+'.d').replace('__OBJF__',out)
if str(a/VK) in cmd or '-o '+obj in cmd:
 raise SystemExit('STOP: unsafe compiler output')
link=(b/'build/link-v33.sh').read_text()
if ' && ' in link or ';' in link or link.count(obj)!=1 or link.count(str(b/'build/shadps4'))!=1:
 raise SystemExit('STOP: v33 linker format unexpected')
newlink=link.replace(obj,out).replace(str(b/'build/shadps4'),str(c/'build/shadps4'))
newlink=newlink.replace(str(b/'build/link.d'),str(c/'build/link.d'))
if str(b/'build/shadps4') in newlink or out not in newlink:
 raise SystemExit('STOP: unsafe linked binary or rasterizer path')
for name,body in [('compile-one.sh','#!/usr/bin/env bash\nset -Eeuo pipefail\n'+cmd+'\n'),('link-v37.sh',newlink)]:
 p=c/'build'/name
 if p.exists() and p.read_text()!=body:raise SystemExit('STOP: prior build command differs: '+name)
 p.write_text(body)
 p.chmod(0o700)
print('V37_COMPILE=ONE_VULKAN_TU; LINK=V33_SRT_PLUS_V37_VK')
PY
mkdir -p "$C/build/CMakeFiles/shadps4.dir/$(dirname "$VK")"
bash -n "$C/build/compile-one.sh" || fail 'compile command invalid'
bash -n "$C/build/link-v37.sh" || fail 'link command invalid'
PROOF="$C/build/.got37-binary-sha256"
if [[ -e "$C/build/shadps4" || -L "$C/build/shadps4" ]]; then
 [[ -f "$PROOF" && ! -L "$PROOF" ]] || fail 'unknown existing executable; refusing overwrite'
 [[ "$(sha "$C/build/shadps4")" == "$(cat "$PROOF")" ]] || fail 'binary proof mismatch'
 echo STAGE=RESUME_VERIFIED_BINARY
else
 LOG="$R/logs/got37-vulkan-build-$(date +%Y%m%d-%H%M%S).log"
 echo STAGE=COMPILE_ISOLATED_VULKAN
 (cd "$A/build" && bash "$C/build/compile-one.sh") > "$LOG" 2>&1 || { tail -65 "$LOG"; fail "compile failed: $LOG"; }
 echo STAGE=LINK_ISOLATED_EMULATOR
 (cd "$A/build" && bash "$C/build/link-v37.sh") >> "$LOG" 2>&1 || { tail -65 "$LOG"; fail "link failed: $LOG"; }
 [[ -x "$C/build/shadps4" && ! -L "$C/build/shadps4" ]] || fail 'v37 executable missing'
 [[ "$(sha "$C/build/shadps4")" != "$BHASH" ]] || fail 'v37 binary not changed'
 sha "$C/build/shadps4" > "$PROOF"
 echo "BUILD_LOG=$LOG"
fi
[[ "$(sha "$A/build/shadps4")" == "$AHASH" && "$(sha "$B/build/shadps4")" == "$BHASH" ]] || fail 'baseline binaries changed'
[[ "$(sha "$B/$VK")" == "$VKSHA" && "$(sha "$B/$SRT")" == "$SRTHASH" ]] || fail 'v33 source changed'
CHASH="$(sha "$C/build/shadps4")"
python3 - "$L33" "$L37" "$BHASH" "$CHASH" <<'PY'
from pathlib import Path
import sys
src,dest,sha0,sha1=sys.argv[1:]
s=Path(src).read_text()
for old in ('got-v33-gow-srt-correctness-20261013','got-v33-gow-srt-visual',sha0):
 if old not in s: raise SystemExit('STOP: pinned screenshot runner mismatch: '+old)
s=s.replace('got-v33-gow-srt-correctness-20261013','got-v37-guest-image-safety-20261013')
s=s.replace('got-v33-gow-srt-visual','got-v37-descriptor-visual')
s=s.replace(sha0,sha1)
p=Path(dest)
if p.exists() or p.is_symlink():raise SystemExit('STOP: v37 runner already exists')
p.write_text(s);p.chmod(0o700)
PY
bash -n "$L37" || fail 'v37 visual launcher invalid'
printf 'BIN_SHA256=%s\nPRIOR_BINARIES=PRESERVED\n' "$CHASH"
echo STAGE=ONE_DETACHED_VISUAL_GAMEPLAY_TEST
bash "$L37" start
echo "GET_REPORT=bash $L37 status"
