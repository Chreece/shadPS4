import os, re, json, time, tarfile, signal, shutil, hashlib, fcntl, tempfile, subprocess, base64, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

H=Path.home()
ROOT=H/'Applications/shadps4-ghost'
ORIGINAL=ROOT/'fence-readback-candidate/shadps4'
ORIGINAL_SHA='e294cc6b5fabb7c41b7f7caca20ad14e47993bd0e9f1a774f341b35846fa2fae'
SRC=H/'.cache/shadps4-ghost-fullstack-20261008-131621/source'
BUILD=H/'.cache/shadps4-ghost-isolated/build'
PIN='89af13f6d306ebc24396b4e8e207688537cdc28b'
FILES={
 'src/video_core/renderer_vulkan/vk_runtime.cpp':('d870176003d773e742df1d16adc08fd2ca69e931a3a777af4674a071850d5198','30a25a11ef8e506b8e42eac24daaec95590617f259b5164fafa8a1423852e6b6'),
 'src/core/libraries/videoout/driver.cpp':('b33eebf943320b00493d86094a96e0f37b70a8f24076c12a6a51e40323d7da77','66685848ca208733b00b060544f9e3d887bb4216a9a1f22a303c9e39cbc7603d'),
 'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp':('bfcfb28400afddc0e8ac4aad85d41419c302d9807e53f4b736bc497dfcc252db','2b1c9af5e8610cf003512bf927dde295c1f557323f4464bf02d3ab064055d9cb'),
 'src/video_core/amdgpu/pm4_cmds.h':('83d7139ce43313d8c91f06fd5b4e92c24a425fcd5c7f0e8ef534f63d04cd05ae','67bc5d894751b52574c11f67dbc75a314d2ee9d4e5c5a8fd95c47035b40e0a91'),
 'src/core/libraries/gnmdriver/gnmdriver.cpp':('3914c166b32ef166d6087265a7d80e79f9c701bbb359aa5e24590b9edc44ada4','53baceea70cb1d76d30ca498042e4c31dfd68a3a09cfaf117cf238d1d2c22a02'),
 'src/core/libraries/kernel/equeue.cpp':('28d0c510bbfe83b683ad08d1bf05af59d8511a6d374259cad4199e46e9e2193a','7e2db2c5d293fc4621fff5caf7457e0ac42add96cc3b0d9c121e4fdb530d1782'),
 'src/video_core/amdgpu/liverpool.cpp':('8b2af6af124bc2bd6fbe7311336e2761a1deadc85b45a189d78cf9b3da2aeb83','34f5e90caec4de3a19b3e6d4ea4ac1cd381ee4383895de10416d5ad81e395593')
}
BLOBS={
 'mip':'6b0142c3aa6e2a9cf09a58cd93388e4ec35a68a2',
 'reverse':'ff6448d070e5cc3d66a565f6be1bdc0a18719028',
 'fallback':'ae5e58b97c710f6b05d052fe7daa1fb7eb6c519e',
 'pm4':'fde20169fa98b4f290931b7506ce57e8d67a6012',
 'irq':'d08f78ca69001469f45579df3d4c9bda12c878e9',
 'trace':'52bd38e436980b3c3ff901b458a1938b249fda0e',
}

(H/'.cache').mkdir(exist_ok=True)
W=Path(tempfile.mkdtemp(prefix='ghost-rebuilt-timeline-',dir=H/'.cache'))
OUT=H/f'ghost-bindless-prototype-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'
S={'goal':'NEW opt-in runtime 53-texture GPU indexing prototype on proven Ghost source; original shaders; restore all',
   'started':datetime.now().isoformat(),'source':str(SRC),'build':str(BUILD)}
P=PIDFD=LOCK=None
PSTART=None
BACKUP={}
BINARY_COPY=None
PROTOTYPE_BASE='105514765f7c69dbd65a5041f089ca2076e50f0a'
PROTOTYPE_HEAD='cb00062980b4a7467aa2f9de92081f713158944c'
PROTOTYPE_PATHS=(
 'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp',
 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h',
 'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp',
 'src/shader_recompiler/ir/ir_emitter.cpp',
 'src/shader_recompiler/ir/ir_emitter.h',
 'src/shader_recompiler/ir/opcodes.inc',
 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp',
 'src/shader_recompiler/resource.h',
 'src/video_core/renderer_vulkan/vk_instance.cpp',
 'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
)
SOURCES_STAGED=False
BUILD_OBJECTS_RESTORED=False
BUILD_RESTORE_ATTEMPTED=False

def sha_bytes(b):return hashlib.sha256(b).hexdigest()
def sha_file(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def get_output(cmd,cwd=None,timeout=20):
 return subprocess.run(cmd,cwd=cwd,text=True,capture_output=True,timeout=timeout,stdin=subprocess.DEVNULL)
def log_run(cmd,log,timeout=1200,cwd=None):
 with (W/log).open('ab') as f:
  f.write(('RUN: '+repr(cmd)+'\n').encode());f.flush()
  try:
   return subprocess.run(cmd,cwd=cwd,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,timeout=timeout).returncode
  except subprocess.TimeoutExpired:
   f.write(b'COMMAND TIMED OUT\n');return 124

def ensure_one(s,old,new,tag):
 if s.count(old)!=1:raise RuntimeError(f'{tag}: anchor count {s.count(old)}')
 return s.replace(old,new,1)

def get_blob(blob):
 # Prefer original local Git object; fall back to the exact object in the user's fork.
 probe=get_output(['git','cat-file','blob',blob],cwd=SRC,timeout=15)
 if probe.returncode==0:
  raw=probe.stdout.encode('utf-8')
 else:
  url='https://api.github.com/repos/Chreece/shadPS4/git/blobs/'+blob
  req=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'GhostSourceProvenanceProbe/1.0'})
  with urllib.request.urlopen(req,timeout=25) as response:
   item=json.load(response)
  if item.get('encoding')!='base64':raise RuntimeError('Unsupported blob encoding for '+blob)
  raw=base64.b64decode(item['content'])
 digest=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
 if digest!=blob:raise RuntimeError('Git blob checksum mismatch: '+blob)
 return raw

def module_from_blob(name,sha):
 raw=get_blob(sha)
 (W/f'patcher-{name}.sha.txt').write_text(f'{sha} bytes={len(raw)}\n')
 ns={'__name__':'archived_'+name,'__file__':f'<verified-blob-{sha}>'}
 exec(compile(raw,f'<verified-blob-{sha}>','exec'),ns)
 if 'selftest' in ns:ns['selftest']()
 return ns

def validate_candidate_manifest():
    # The archived manifest is the authority for all seven source-file hashes.
    # Validate it before reconstructing or touching the checkout, so a copied
    # SHA constant cannot accidentally invalidate a correct source build.
    archive=H/'ghost-c6-fence-readback-20261009-234617.tar.gz'
    with tarfile.open(archive,'r:gz') as tf:
        entry=tf.getmember('candidate-build.json')
        if not entry.isfile() or entry.size!=2506:
            raise RuntimeError('Unexpected original candidate-build manifest size')
        raw=tf.extractfile(entry).read()
    manifest_sha='80e8c8007e2048915a161e4fa7078c5511300548626ffc8530bcedd454dde6d0'
    if sha_bytes(raw)!=manifest_sha:
        raise RuntimeError('Original candidate-build manifest SHA256 mismatch')
    manifest=json.loads(raw)
    original={name:pair[0] for name,pair in FILES.items()}
    staged={name:pair[1] for name,pair in FILES.items()}
    if (manifest.get('source_head')!=PIN or
        manifest.get('candidate_sha256')!=ORIGINAL_SHA or
        manifest.get('original_source_hashes')!=original or
        manifest.get('staged_source_hashes')!=staged):
        raise RuntimeError('Controller constants differ from exact archived Ghost manifest')
    S['candidate_manifest_sha256']=manifest_sha
    S['candidate_manifest_verified']=True

def reconstruct():
 # Hard stop unless the original local Git commit and all SEVEN originals match.
 if not ORIGINAL.is_file() or sha_file(ORIGINAL)!=ORIGINAL_SHA:
  raise RuntimeError('Verified Ghost executable SHA changed')
 validate_candidate_manifest()
 if not SRC.is_dir() or not (BUILD/'CMakeCache.txt').is_file() or not (BUILD/'shadps4').is_file():
  raise RuntimeError('Archived source/build paths no longer exist')
 g=get_output(['git','rev-parse','HEAD'],cwd=SRC)
 S['source_head']=g.stdout.strip()
 if g.returncode or S['source_head']!=PIN:
  raise RuntimeError('Source revision does not match archived exact commit')
 m=re.search(r'^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$',(BUILD/'CMakeCache.txt').read_text(errors='replace'),re.M)
 if not m or Path(m.group(1)).resolve()!=SRC.resolve():
  raise RuntimeError('CMake cache points at different source checkout')
 for path,(original,staged) in FILES.items():
  p=SRC/path
  if not p.is_file() or sha_file(p)!=original:
   raise RuntimeError('Original source differs from archive: '+path)
  BACKUP[path]=(p.read_bytes(),p.stat().st_atime_ns,p.stat().st_mtime_ns)
 for p in ('src/video_core/renderer_vulkan/vk_scheduler.h','src/video_core/renderer_vulkan/vk_scheduler.cpp'):
  if not (SRC/p).is_file():raise RuntimeError('Missing scheduler source: '+p)
  BACKUP[p]=((SRC/p).read_bytes(),(SRC/p).stat().st_atime_ns,(SRC/p).stat().st_mtime_ns)
 S['all_seven_original_shas']='PASS'
 original={n:BACKUP[n][0] for n in FILES}
 mods={k:module_from_blob(k,v) for k,v in BLOBS.items()}
 # EXACT sequence from the archived candidate-build manifest and patcher self-tests.
 result=dict(original)
 a='src/video_core/renderer_vulkan/vk_runtime.cpp'
 result[a]=mods['mip']['validate'](result[a])
 result[a]=mods['reverse']['modify'](result[a])
 result[a]=mods['fallback']['update'](result[a])
 a='src/video_core/amdgpu/pm4_cmds.h'
 result[a]=mods['pm4']['transform'](result[a])
 three={k:result[k] for k in (mods['irq']['GNM'],mods['irq']['EQUEUE'],mods['irq']['LIVERPOOL'])}
 result.update(mods['irq']['transform'](three))
 a='src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'
 result[a]=mods['trace']['patch_flatten'](result[a].decode()).encode()
 a='src/core/libraries/videoout/driver.cpp'
 result[a]=mods['trace']['patch_videoout'](result[a].decode()).encode()
 a='src/video_core/amdgpu/liverpool.cpp'
 # The final fence-readback patch lives in the user's original evidence archive.
 archive=H/'ghost-c6-fence-readback-20261009-234617.tar.gz'
 if not archive.is_file():
  raise RuntimeError('Original fence patch archive missing: '+str(archive))
 with tarfile.open(archive,'r:gz') as z:
  m=z.getmember('ghost-c6-fence-readback-patch-20261009.py')
  raw=z.extractfile(m).read()
  if not m.isfile() or len(raw)!=3985:raise RuntimeError('Unexpected fence patcher length')
 fence={'__name__':'archived_fence','__file__':'<original-fence-archive>'}
 exec(compile(raw,'<original-fence-archive>','exec'),fence)
 fence['selftest']()
 result[a]=fence['transform'](result[a])
 got={name:sha_bytes(value) for name,value in result.items()}
 mismatches=[name for name in FILES if got[name]!=FILES[name][1]]
 S['reconstruction_hashes']=got
 if mismatches:raise RuntimeError('Reconstructed staged source did not match archived hashes: '+repr(mismatches))
 S['seven_staged_hashes']='PASS'
 return result

def instrument(result):
 # The exact 7-file source is independently verified before we add the read-only probe.
 sched_h='src/video_core/renderer_vulkan/vk_scheduler.h'
 sched_c='src/video_core/renderer_vulkan/vk_scheduler.cpp'
 liv='src/video_core/amdgpu/liverpool.cpp'
 h=BACKUP[sched_h][0].decode()
 cpp=BACKUP[sched_c][0].decode()
 code=result[liv].decode()
 if '#include <atomic>' not in h:
  h=ensure_one(h,'#include <condition_variable>','#include <atomic>\n#include <condition_variable>','header atomic')
 h=ensure_one(h,'    ~Scheduler();', '''    ~Scheduler();

    struct GhostGpuTimeline {
        u64 recording_tick;
        u64 submitted_tick;
        u64 completed_tick;
    };
    [[nodiscard]] GhostGpuTimeline SampleGhostGpuTimelineNonblocking() {
        const u64 submitted = ghost_submitted_tick.load(std::memory_order_acquire);
        if (submitted) (void)IsFree(submitted);
        return {CurrentTick(), submitted, work_semaphore.KnownGpuTick()};
    }''','scheduler method')
 h=ensure_one(h,'    Semaphore work_semaphore;', '    Semaphore work_semaphore;\n    std::atomic<u64> ghost_submitted_tick{0};','scheduler field')
 cpp=ensure_one(cpp,'#include "common/debug.h"','#include <cstdlib>\n#include <cstring>\n#include "common/debug.h"\n#include "common/logging/log.h"','scheduler include')
 cpp=ensure_one(cpp,'    work_semaphore.Refresh();\n    BeginSession();','''    if (submit_result == vk::Result::eSuccess) {
        ghost_submitted_tick.store(signal_value, std::memory_order_release);
    }
    work_semaphore.Refresh();
    static const bool ghost_probe = [] {
        const char* flag = std::getenv("SHADPS4_GHOST_TIMELINE_PROBE");
        return flag && std::strcmp(flag, "1") == 0;
    }();
    if (ghost_probe && (signal_value <= 32 || signal_value % 16 == 0)) {
        LOG_WARNING(Render_Vulkan,
                    "GHOST_TIMELINE_SUBMIT tick={} completed={} buffers={}",
                    signal_value, work_semaphore.KnownGpuTick(), cmd_buffers.size());
    }
    BeginSession();''','scheduler submit')
 code=ensure_one(code,'#include <boost/preprocessor/stringize.hpp>','#include <atomic>\n#include <cstdlib>\n#include <cstring>\n#include <boost/preprocessor/stringize.hpp>','liverpool include')
 code=ensure_one(code,'namespace AmdGpu {\n','namespace AmdGpu {\n\nstatic std::atomic<u64> ghost_timeline_seq{0};\n','liverpool namespace')
 needle='            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);'
 snippet='''            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);
            static const bool ghost_timeline_on = [] {
                const char* flag = std::getenv("SHADPS4_GHOST_TIMELINE_PROBE");
                return flag && std::strcmp(flag, "1") == 0;
            }();
            if (ghost_timeline_on && queue.pipe_id == 6 && rasterizer) {
                const u64 seq = ghost_timeline_seq.fetch_add(1, std::memory_order_relaxed) + 1;
                if (seq <= 128 || seq % 16 == 0) {
                    const auto tick = rasterizer->GetScheduler().SampleGhostGpuTimelineNonblocking();
                    LOG_WARNING(Render,
                                "GHOST_TIMELINE_RELEASE seq={} pipe={} recording={} submitted={} "
                                "completed={} pending={} int_sel={} data_sel={}",
                                seq, queue.pipe_id, tick.recording_tick, tick.submitted_tick,
                                tick.completed_tick, tick.submitted_tick > tick.completed_tick,
                                u32(release_mem->int_sel.Value()), u32(release_mem->data_sel.Value()));
                }
            }'''
 code=ensure_one(code,needle,snippet,'compute RELEASE_MEM')
 if 'GHOST_C6_RELEASE seq=' not in code or 'GHOST_C6_SLOT_AFTER' not in code:
  raise RuntimeError('Lost proven Ghost fence/IRQ source instrumentation')
 result[liv]=code.encode()
 result[sched_h]=h.encode()
 result[sched_c]=cpp.encode()
 return result

def restore_source():
 for rel,(data,atime,mtime) in BACKUP.items():
  p=SRC/rel
  try:
   p.write_bytes(data)
   os.utime(p,ns=(atime,mtime))
  except OSError as e:
   S.setdefault('restore_failures',[]).append(f'{rel}: {e}')
 if BACKUP:
  S['source_restored']=all(sha_bytes(v[0])==sha_file(SRC/k) for k,v in BACKUP.items())

def backup_binary():
 global BINARY_COPY
 BINARY_COPY=W/'original_build_executable'
 shutil.copy2(BUILD/'shadps4',BINARY_COPY)
 S['build_sha_before']=sha_file(BINARY_COPY)

def restore_binary():
 if BINARY_COPY is not None and BINARY_COPY.is_file():
  shutil.copy2(BINARY_COPY,BUILD/'shadps4')
  S['build_binary_restored']=sha_file(BUILD/'shadps4')==S.get('build_sha_before')

def restore_build_state():
 global SOURCES_STAGED, BUILD_OBJECTS_RESTORED, BUILD_RESTORE_ATTEMPTED
 # Restoring only the build executable leaves instrumented .o files in CMake's
 # build tree. Force a rebuild from the pristine sources before restoring its
 # original executable. Report any failure instead of claiming a clean build.
 if SOURCES_STAGED:
  restore_source()
  if BUILD_RESTORE_ATTEMPTED:
   restore_binary()
   raise RuntimeError('Build object restoration already attempted; see restore-build.log')
  BUILD_RESTORE_ATTEMPTED=True
  for name in BACKUP:
   os.utime(SRC/name,None)
  code=log_run(['cmake','--build',str(BUILD),'--target','shadps4',
                '--parallel','4'],'restore-build.log',1200)
  BUILD_OBJECTS_RESTORED=(code==0)
  S['build_objects_restored']=BUILD_OBJECTS_RESTORED
  restore_source()
  if code:
   raise RuntimeError('Restore build from pristine sources failed; inspect restore-build.log')
  SOURCES_STAGED=False
 else:
  restore_source()
 restore_binary()


def prepare_dynamic_patch():
 # Unmodified 7-file source provenance must pass before this call.
 # Download only the immutable two Git commits to generate their exact source
 # diff; verify changed filenames and ensure it applies BEFORE editing anything.
 repo=W/'bindless-ref'
 repo.mkdir()
 if log_run(['git','init','-q',str(repo)],'fetch.log',40):
  raise RuntimeError('Failed to initialize pinned prototype diff checkout')
 args=['git','-C',str(repo),'fetch','-q','--no-tags','--depth=1',
       'https://github.com/Chreece/shadPS4.git',
       PROTOTYPE_BASE+':refs/heads/exact-base',
       PROTOTYPE_HEAD+':refs/heads/exact-prototype']
 if log_run(args,'fetch.log',240):
  raise RuntimeError('Could not retrieve immutable Ghost implementation commits')
 base=get_output(['git','rev-parse','exact-base'],cwd=repo)
 head=get_output(['git','rev-parse','exact-prototype'],cwd=repo)
 if base.returncode or head.returncode or base.stdout.strip()!=PROTOTYPE_BASE or head.stdout.strip()!=PROTOTYPE_HEAD:
  raise RuntimeError('Source diff commit IDs are not exactly pinned')
 names=get_output(['git','diff','--name-only','exact-base','exact-prototype'],cwd=repo)
 if names.returncode or set(names.stdout.splitlines())!=set(PROTOTYPE_PATHS):
  raise RuntimeError('Prototype diff changed unexpected source paths: '+repr(names.stdout))
 patch=W/'ghost-gpu-indexing.patch'
 with patch.open('wb') as out:
  status=subprocess.run(['git','diff','--binary','exact-base','exact-prototype',
                         '--',*PROTOTYPE_PATHS],cwd=repo,stdout=out,
                         stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,timeout=60)
 if status.returncode or not patch.is_file() or patch.stat().st_size<100:
  raise RuntimeError('Pinned prototype diff generation failed')
 check=get_output(['git','apply','--check',str(patch)],cwd=SRC,timeout=60)
 (W/'prototype-preflight.log').write_text(check.stdout+'\n'+check.stderr)
 if check.returncode:
  raise RuntimeError('Prototype patch incompatible with exact Ghost source; no source touched')
 for path in PROTOTYPE_PATHS:
  f=SRC/path
  if path in BACKUP or not f.is_file():
   raise RuntimeError('Prototype path unexpectedly overwritten or missing: '+path)
  BACKUP[path]=(f.read_bytes(),f.stat().st_atime_ns,f.stat().st_mtime_ns)
 S['prototype_base']=PROTOTYPE_BASE
 S['prototype_head']=PROTOTYPE_HEAD
 S['prototype_paths']=len(PROTOTYPE_PATHS)
 S['prototype_diff_sha256']=sha_file(patch)
 return patch

def build_test_executable():
 global BINARY_COPY,SOURCES_STAGED
 stage=reconstruct()
 patch=prepare_dynamic_patch()
 patched=instrument(stage)
 backup_binary()
 # Verify every change is confined to all backed-up paths.
 if any(name not in BACKUP for name in patched):
  raise RuntimeError('Attempt to stage unbacked file')
 SOURCES_STAGED=True
 for name,text in patched.items():
  (SRC/name).write_bytes(text)
 if log_run(['git','apply','--check',str(patch)],'apply.log',80,cwd=SRC):
  raise RuntimeError('Pinned prototype unexpectedly changed before stage; refusing build')
 if log_run(['git','apply',str(patch)],'apply.log',80,cwd=SRC):
  raise RuntimeError('Failed applying pinned C++ prototype to verified Ghost source')
 S['prototype_staged']=True
 if log_run(['cmake','--build',str(BUILD),'--target','shadps4','--parallel','4'],'build.log',1200):
  raise RuntimeError('Compilation failed; original sources and build will be restored')
 S['built_sha']=sha_file(BUILD/'shadps4')
 candidate=W/'instrumented_shadps4'
 shutil.copy2(BUILD/'shadps4',candidate)
 # The seven archived source hashes were verified before instrumentation.
 restore_build_state()
 S['probe_compiled']=True
 return candidate

def pstate(pid):
 try:
  a=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()
  return a[0],a[19]
 except (OSError,IndexError):return None

def exe(pid):
 try:return os.readlink(f'/proc/{pid}/exe').removesuffix(' (deleted)')
 except OSError:return ''

def active():
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   if p.stat().st_uid!=os.getuid():continue
   q=pstate(p.name)
   if q and q[0]!='Z' and ((p/'comm').read_text().strip().lower().startswith('shadps4') or Path(exe(p.name)).name.lower()=='shadps4'):
    return p.name
  except OSError:pass
 return None

def desktop(env):
 if env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'):return True
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   if p.stat().st_uid!=os.getuid() or Path(exe(p.name)).name not in ('sunshine','es-de'):continue
   e=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
   for k in ('DISPLAY','XAUTHORITY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS'):
    if k.encode() in e:env[k]=e[k.encode()].decode(errors='replace')
   if env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'):return True
  except OSError:pass
 return False

def overlay():
 run=W/'run';run.mkdir()
 user=run/'user';user.mkdir()
 for p in ROOT.iterdir():
  if p.name!='user':(run/p.name).symlink_to(p,target_is_directory=p.is_dir())
 for p in (ROOT/'user').iterdir():
  if p.name not in ('config.json','custom_configs','shader','home','log','screenshots','cache'):
   (user/p.name).symlink_to(p,target_is_directory=p.is_dir())
 for k in ('log','screenshots','cache'):(user/k).mkdir()
 (user/'shader/patch').mkdir(parents=True)
 (user/'shader/dumps').mkdir()
 global_cfg=ROOT/'user/config.json'
 cfg=json.loads(global_cfg.read_text())
 cfg.setdefault('GPU',{}).update(direct_memory_access_enabled=True,patch_shaders=False,dump_shaders=False,userfaultfd=False)
 (user/'config.json').write_text(json.dumps(cfg,indent=2))
 cc=ROOT/'user/custom_configs';(user/'custom_configs').mkdir()
 if cc.is_dir():
  for p in cc.iterdir():
   if p.name!='CUSA11456.json':(user/'custom_configs'/p.name).symlink_to(p,target_is_directory=p.is_dir())
 gp=cc/'CUSA11456.json';gc=json.loads(gp.read_text()) if gp.is_file() else {}
 gc.setdefault('GPU',{}).update(direct_memory_access_enabled=True,patch_shaders=False,dump_shaders=False,userfaultfd=False)
 (user/'custom_configs/CUSA11456.json').write_text(json.dumps(gc,indent=2))
 if (ROOT/'user/home').is_dir():
  if any(x.is_symlink() for x in (ROOT/'user/home').rglob('*')):raise RuntimeError('Save directory contains symlinks')
  shutil.copytree(ROOT/'user/home',user/'home')
 else:(user/'home').mkdir()
 S['config_before']={'global':sha_file(global_cfg),'game':sha_file(gp) if gp.is_file() else None}
 return run

def stop():
 if P is None:return 'not-started'
 if P.poll() is not None:return 'exited:'+str(P.returncode)
 for sig,secs in ((signal.SIGTERM,5),(signal.SIGKILL,8)):
  try:
   if PIDFD is not None:signal.pidfd_send_signal(PIDFD,sig,None,0)
   elif pstate(P.pid) and pstate(P.pid)[1]==PSTART and exe(P.pid)==str(W/'run/shadps4'):
    os.kill(P.pid,sig)
   else:return 'REFUSED: PID identity changed'
   P.wait(timeout=secs);return 'reaped-'+sig.name
  except subprocess.TimeoutExpired:continue
  except ProcessLookupError:return 'already-exited'
  except OSError as e:return repr(e)
 return 'SIGKILL-not-reaped'

def playtest(candidate):
 global P,PIDFD,PSTART
 env=os.environ.copy()
 if not desktop(env):raise RuntimeError('No GUI session')
 icd=next((p for p in (Path('/usr/share/vulkan/icd.d/radeon_icd.json'),Path('/usr/share/vulkan/icd.d/radeon_icd.x86_64.json')) if p.is_file()),None)
 if not icd:raise RuntimeError('RADV ICD unavailable')
 for k in ('VK_DRIVER_FILES','VK_ICD_FILENAMES','RADV_DEBUG','RADV_PERFTEST','ACO_DEBUG'):env.pop(k,None)
 env.update(VK_DRIVER_FILES=str(icd),VK_ICD_FILENAMES=str(icd),RADV_DEBUG='hang,nocache,noumr',RADV_PERFTEST='pswave32',SHADPS4_GHOST_BINDLESS_EXPERIMENT='1')
 run=overlay();prior={str(x) for x in H.glob('radv_dumps_*')}
 # Keep the test binary beside the isolated run/user directory, so relative
 # executable assets resolve as in the proven Ghost launchers.
 trial_exe=run/'shadps4'
 if trial_exe.is_symlink():trial_exe.unlink()
 elif trial_exe.exists():raise RuntimeError('Isolated run already contains a regular shadps4 binary')
 shutil.copy2(candidate,trial_exe)
 log=W/'runtime.log'
 with log.open('wb',buffering=0) as f:
  P=subprocess.Popen([str(trial_exe),'--game','CUSA11456','--fullscreen','true'],cwd=run,env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
  S['pid']=P.pid;PSTART=(pstate(P.pid) or (None,None))[1]
  try:PIDFD=os.pidfd_open(P.pid)
  except (OSError,AttributeError):PIDFD=None
  print('GHOST_OWNED_PID='+str(P.pid),flush=True)
  start=time.monotonic();last_change=start;last_flips=-1;first_hang=None
  while time.monotonic()-start<135:
   now=time.monotonic();text=log.read_text(errors='replace')
   if P.poll() is not None:S['result']='game-exited';break
   if now-start>=45 and 'GHOST_C6_RELEASE' not in text:
    S['result']='original-Ghost-trace-absent';break
   if now-start>=45 and 'GHOST_BINDLESS_SOURCE' not in text:
    S['result']='dynamic-index-shader-source-not-recognized';break
   if now-start>=45 and 'GHOST_BINDLESS_TABLE' not in text:
    S['result']='gpu-dynamic-descriptor-array-not-bound';break
   ftrace=re.findall(r'GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)',text)
   if ftrace:
    vb,n,p,q=map(int,ftrace[-1]);S['guest_flips']=n
    if n!=last_flips:last_flips=n;last_change=now
    if n>=500 and p==q==0 and now-last_change>13:S['result']='frame-plateau';break
   reports=any(str(p) not in prior for p in H.glob(f'radv_dumps_{P.pid}_*'))
   if reports or 'radv: GPU hang detected' in text:
    if first_hang is None:first_hang=now
    if 'radv: GPU hang report saved successfully' in text or now-first_hang>9:
     S['result']='gpu-hang';break
   time.sleep(.7)
  else:S['result']='135s-without-hang-or-plateau'
 text=log.read_text(errors='replace')
 ticks=re.findall(r'GHOST_TIMELINE_RELEASE seq=(\d+) pipe=(\d+) recording=(\d+) submitted=(\d+) completed=(\d+) pending=(\w+)',text)
 S['bindless_source_compiles']=len(re.findall(r'GHOST_BINDLESS_SOURCE shader=',text))
 S['bindless_table_binds']=len(re.findall(r'GHOST_BINDLESS_TABLE shader=',text))
 S['timeline_releases']=len(ticks)
 S['pending_submissions_at_release']=sum(int(x[3])>int(x[4]) for x in ticks)
 S['first_timeline_records']=ticks[:8]
 S['last_timeline_records']=ticks[-8:]
 S['original_release_count']=len(re.findall(r'GHOST_C6_RELEASE seq=',text))
 S['original_irq_forward_count']=len(re.findall(r'GHOST_C6_IRQ_FORWARD seq=',text))
 S['gpu_submit_sample_count']=text.count('GHOST_TIMELINE_SUBMIT tick=')
 S['runtime_tail']=text.splitlines()[-18:]
 for p in H.glob(f'radv_dumps_{P.pid}_*'):
  if p.is_dir() and str(p) not in prior:
   shutil.copytree(p,W/'radv_reports'/p.name,dirs_exist_ok=True)

try:
 print('GHOST_GPU_DYNAMIC_IMAGE_EXPERIMENT=START',flush=True)
 if active():raise RuntimeError('Another shadPS4 running; refused')
 LOCK=os.open(H/'.cache/ghost-bindless-prototype.lock',os.O_CREAT|os.O_RDWR,0o600)
 fcntl.flock(LOCK,fcntl.LOCK_EX|fcntl.LOCK_NB)
 candidate=build_test_executable()
 print('GHOST_ARCHIVED_7_FILE_RECONSTRUCTION=PASS',flush=True)
 print('GHOST_GPU_BINDLESS_PROTOTYPE_BUILD=PASS',flush=True)
 playtest(candidate)
except BaseException as e:
 S['result']=S.get('result','preflight-build-or-runtime-error');S['exception']=repr(e)
finally:
 S['cleanup']=stop()
 if PIDFD is not None:os.close(PIDFD)
 try:restore_build_state()
 except Exception as e:
  S['restore_exception']=repr(e)
  try:restore_source();restore_binary()
  except Exception as final_err:S['additional_restore_error']=repr(final_err)
 if LOCK is not None:
  fcntl.flock(LOCK,fcntl.LOCK_UN);os.close(LOCK)
 S['installed_binary_untouched']=ORIGINAL.is_file() and sha_file(ORIGINAL)==ORIGINAL_SHA
 if 'config_before' in S:
  gp=ROOT/'user/custom_configs/CUSA11456.json'
  S['configs_untouched']=sha_file(ROOT/'user/config.json')==S['config_before']['global'] and (sha_file(gp) if gp.is_file() else None)==S['config_before']['game']
 S['finished']=datetime.now().isoformat()
 (W/'status.json').write_text(json.dumps(S,indent=2))
 with tarfile.open(OUT,'w:gz',compresslevel=5) as tf:
  for p in W.rglob('*'):
   if p.is_file() and not any(x in ('run',) for x in p.relative_to(W).parts) and p.name not in ('instrumented_shadps4','original_build_executable'):
    tf.add(p,arcname=str(p.relative_to(W)))
 print('GHOST_RESULT='+str(S.get('result')),flush=True)
 print('GHOST_PROVENANCE='+str(S.get('seven_staged_hashes')),flush=True)
 print('GHOST_BINDLESS_COMPILES='+str(S.get('bindless_source_compiles')),flush=True)
 print('GHOST_BINDLESS_TABLE_BINDS='+str(S.get('bindless_table_binds')),flush=True)
 print('GHOST_GUEST_FLIPS='+str(S.get('guest_flips')),flush=True)
 print('GHOST_SOURCE_RESTORED='+str(S.get('source_restored')),flush=True)
 print('GHOST_BUILD_RESTORED='+str(S.get('build_binary_restored')),flush=True)
 print('GHOST_CLEANUP='+str(S['cleanup']),flush=True)
 print('UPLOAD_THIS_ARCHIVE='+str(OUT),flush=True)
 if P is None or P.poll() is not None:shutil.rmtree(W,ignore_errors=True)
