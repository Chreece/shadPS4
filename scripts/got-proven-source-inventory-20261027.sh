#!/usr/bin/env bash
# Read-only discovery of the source tree matching the historically menu-visible GoT build.
# No git checkout/fetch, compilation, game launch, cache mutation, or SSH interruption.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
OUT="$LOGS/got-proven-source-inventory-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
STATUS=STARTED
finish() {
  local rc=$?
  trap - EXIT
  python3 - "$OUT" "$LOGS" "$STATUS" "$rc" <<'PY' || true
import json,sys
from pathlib import Path
out,logs,status,rc=sys.argv[1:]
record={"schema_version":2,"record_type":"LOCAL_READ_ONLY_SOURCE_PROVENANCE",
 "game":"CUSA11456","source_target":"c925523b49ef39434024b16b2b014d120471ee1a",
 "binary_sha256":"260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d",
 "status":status,"runner_exit":int(rc),"source_mutations":"NONE", "game_launched":False}
line=json.dumps(record,separators=(',',':'))
(outdir:=Path(out)).joinpath('trial.jsonl').write_text(line+'\n')
with Path(logs,'emulator-trials-local.jsonl').open('a') as f:f.write(line+'\n')
PY
  printf '%s\n' "$rc" >"$OUT/runner-exit-code.txt"
  tar -czf "$OUT.tar.gz" -C "$OUT" . || true
  printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$OUT.tar.gz"
  exit "$rc"
}
trap finish EXIT
for exe in git python3 sha256sum tar; do
  command -v "$exe" >/dev/null 2>&1 || { STATUS=MISSING_TOOL; echo "Missing $exe" >&2; exit 2; }
done
STATUS=DISCOVERING
python3 - "$ROOT" "$OUT" <<'PY'
import hashlib,json,os,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]); out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
expected='260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d'
target='c925523b49ef39434024b16b2b014d120471ee1a'
known=root/'GhostOfTsushima'/'candidates'/(
 'c925523b49ef39434024b16b2b014d120471ee1a-strided-precise-image-readbacks-'+expected)/'shadps4'
result={'searched_root':str(root),'target_source_commit':target,'expected_binary_sha256':expected,
        'known_binary':str(known),'binary_present':known.is_file(),
        'binary_verified':False,'binary_actual_sha256':None,'sources':[],
        'directories_visited':0,'search_complete':True,'skipped_repo_entries':0}
if known.is_file():
    h=hashlib.sha256()
    with known.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    result['binary_actual_sha256']=h.hexdigest()
    result['binary_verified']=h.hexdigest()==expected
def git(path,*args):
    try:
        completed=subprocess.run(['git','-C',str(path),*args],text=True,
              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12,check=False,
              env={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0'})
        return completed.stdout.strip() if completed.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):return None
prune={'.git','build','build-debug','build-release','_build','externals','external',
       'logs','candidates','node_modules','.cache','_deps','.venv','venv',
       'shader_cache','cache','CMakeFiles','roms','archive','archives','downloads'}
max_dirs=55000
for base,dirs,files in os.walk(root,topdown=True,followlinks=False):
    result['directories_visited']+=1
    if result['directories_visited']>max_dirs:
        result['search_complete']=False;break
    dirs[:]=sorted(d for d in dirs if d not in prune and not d.startswith('.'))
    here=Path(base)
    if not ((here/'CMakeLists.txt').is_file() and
            (here/'src/video_core/amdgpu/resource.h').is_file() and
            (here/'src/video_core/renderer_vulkan/vk_rasterizer.cpp').is_file()):
        continue
    head=git(here,'rev-parse','HEAD')
    if not head:
        continue
    available=git(here,'cat-file','-t',target+'^{commit}')=='commit'
    branch=git(here,'branch','--show-current')
    source={'path':str(here),'head':head,'branch':branch or '(detached)',
      'head_matches_target':head==target,'has_target_object':available,
      'tracked_changes_present':None,'source_blob_at_target':None,
      'rasterizer_blob_at_target':None,
      'build_binary_present':(here/'build/shadps4').is_file() or
          (here.parent/'build/shadps4').is_file()}
    dirty=git(here,'status','--porcelain','--untracked-files=no')
    if dirty is not None: source['tracked_changes_present']=bool(dirty)
    if available:
        source['source_blob_at_target']=git(here,'rev-parse',target+':src/video_core/amdgpu/resource.h')
        source['rasterizer_blob_at_target']=git(here,'rev-parse',target+':src/video_core/renderer_vulkan/vk_rasterizer.cpp')
    result['sources'].append(source)
    if len(result['sources'])>=120:
        result['search_complete']=False;break
result['source_matches']=[x['path'] for x in result['sources']
          if x['head_matches_target'] or x['has_target_object']]
(out/'inventory.json').write_text(json.dumps(result,indent=2)+'\n')
print('BINARY_SHA_VERIFIED='+str(result['binary_verified']))
print('SOURCE_TREES_CHECKED='+str(len(result['sources'])))
print('SOURCE_MATCHES='+str(len(result['source_matches'])))
print('SOURCE_SEARCH_COMPLETE='+str(result['search_complete']))
for p in result['source_matches']:print('LOCAL_SOURCE='+p)
PY
STATUS=INVENTORY_RECORDED
