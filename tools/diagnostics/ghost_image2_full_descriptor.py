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
OUT=H/f'ghost-image2-full-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'
S={'goal':'Correct full T# image2: first four dwords at selected_row*340+64, second four at +80; collect 53 rows once',
   'started':datetime.now().isoformat(),'source':str(SRC),'build':str(BUILD)}
P=PIDFD=LOCK=None
PSTART=None
BACKUP={}
BINARY_COPY=None
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
 p=SRC/'src/video_core/renderer_vulkan/vk_rasterizer.cpp'
 if not p.is_file():raise RuntimeError('Missing rasterizer source')
 data=p.read_bytes()
 if data.count(b'for (const auto* stage : pipeline->GetStages())')!=1 or data.count(b'void Rasterizer::BindTextures(')!=1:
  raise RuntimeError('Rasterizer source BindResources fingerprint mismatch')
 BACKUP['src/video_core/renderer_vulkan/vk_rasterizer.cpp']=(data,p.stat().st_atime_ns,p.stat().st_mtime_ns)
 if sha_bytes(data)!='8d73198df4f9114aa1a2e79892ed489a89a73fd01c1a982f616116fc08e7588d':
  raise RuntimeError('Original Ghost rasterizer source SHA differs from successful 17:26 audit')
 S['original_rasterizer_sha256']=sha_bytes(data)
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

 raster='src/video_core/renderer_vulkan/vk_rasterizer.cpp'
 src=BACKUP[raster][0].decode()
 src=ensure_one(src,'#include "common/debug.h"', '#include <algorithm>\n#include <atomic>\n#include <cerrno>\n#include <cstdlib>\n#include <cstring>\n#include <fcntl.h>\n#include <unistd.h>\n#include "common/debug.h"', 'resource audit C++ includes')
 audit=r'''    static const bool ghost_resource_audit_enabled = [] {
        const char* flag = std::getenv("SHADPS4_GHOST_RESOURCE_AUDIT");
        return flag && std::strcmp(flag, "1") == 0;
    }();
    static std::atomic<u64> ghost_resource_audit_count{0};
    if (ghost_resource_audit_enabled &&
        (stage->pgm_hash == 0x8e743c8eULL || stage->pgm_hash == 0x361a48f5ULL)) {
        const u64 n = ghost_resource_audit_count.fetch_add(
                          1, std::memory_order_relaxed) + 1;
        if (n <= 16 || (n & (n - 1)) == 0) {
            u32 invalid_buffers = 0, invalid_images = 0, invalid_samplers = 0;
            for (const auto& desc : stage->buffers) {
                if (!desc.IsSpecial() &&
                    desc.sharp_fetch.summary ==
                        decltype(desc.sharp_fetch.summary)::Invalid) {
                    ++invalid_buffers;
                }
            }
            for (const auto& desc : stage->images) {
                if (desc.sharp_fetch.summary ==
                    decltype(desc.sharp_fetch.summary)::Invalid) {
                    ++invalid_images;
                }
            }
            for (const auto& desc : stage->samplers) {
                if (desc.sharp_fetch.summary ==
                    decltype(desc.sharp_fetch.summary)::Invalid) {
                    ++invalid_samplers;
                }
            }
            LOG_WARNING(Render_Vulkan,
                "GHOST_RESOURCE_AUDIT shader={:#x} sample={} buffers={} "
                "invalid_buffers={} images={} invalid_images={} samplers={} "
                "invalid_samplers={} user_data_dw={} flatbuf_dw={} uses_dma={}",
                stage->pgm_hash, n, stage->buffers.size(), invalid_buffers,
                stage->images.size(), invalid_images, stage->samplers.size(),
                invalid_samplers, stage->user_data.size(),
                stage->flattened_ud_buf.size(), stage->uses_dma);
            for (u32 i = 0; i < stage->buffers.size(); ++i) {
                const auto& desc = stage->buffers[i];
                if (desc.IsSpecial()) {
                    LOG_WARNING(Render_Vulkan,
                        "GHOST_RESOURCE_BIND shader={:#x} sample={} type=buffer "
                        "idx={} special_type={}", stage->pgm_hash, n, i,
                        u32(desc.buffer_type));
                } else {
                    const auto sharp = desc.GetSharp(*stage);
                    LOG_WARNING(Render_Vulkan,
                        "GHOST_RESOURCE_BIND shader={:#x} sample={} type=buffer "
                        "idx={} fetch={} base={:#x} size={} stride={} valid={}",
                        stage->pgm_hash, n, i, u32(desc.sharp_fetch.summary),
                        u64(sharp.base_address), sharp.GetSize(),
                        sharp.GetStride(), sharp.Valid());
                }
            }
            for (u32 i = 0; i < stage->images.size(); ++i) {
                const auto& desc = stage->images[i];
                const auto sharp = desc.GetSharp(*stage);
                LOG_WARNING(Render_Vulkan,
                    "GHOST_RESOURCE_BIND shader={:#x} sample={} type=image "
                    "idx={} fetch={} addr={:#x} fmt={} valid={}",
                    stage->pgm_hash, n, i, u32(desc.sharp_fetch.summary),
                    sharp.Address(), u32(sharp.GetDataFmt()), sharp.Valid());
            }
            for (u32 i = 0; i < stage->samplers.size(); ++i) {
                const auto& desc = stage->samplers[i];
                const auto sharp = desc.GetSharp(*stage);
                LOG_WARNING(Render_Vulkan,
                    "GHOST_RESOURCE_BIND shader={:#x} sample={} type=sampler "
                    "idx={} fetch={} valid={}",
                    stage->pgm_hash, n, i, u32(desc.sharp_fetch.summary),
                    sharp.Valid());
            }
            for (u32 i = 0; i < stage->user_data.size() && i < 16; ++i) {
                LOG_WARNING(Render_Vulkan,
                    "GHOST_RESOURCE_USERDATA shader={:#x} sample={} sgpr={} value={:#x}",
                    stage->pgm_hash, n, i, stage->user_data[i]);
            }

            // Archived IR: first 4 texture words at record*340+64, last 4
            // at record*340+80; both form a single eight-dword ImageHandle.
            // Previously starting at +80 misclassified all 53 records.
            // Root SRT +0x180 supplies the verified 53x340 indirect buffer.
            if (stage->pgm_hash == 0x8e743c8eULL &&
                stage->buffers.size() > 1 && stage->images.size() > 2 &&
                stage->user_data.size() >= 2 && !stage->buffers[1].IsSpecial()) {
                const AmdGpu::Buffer table = stage->buffers[1].GetSharp(*stage);
                const u64 base = u64(table.base_address);
                const u32 stride = table.GetStride();
                const u32 bytes = table.GetSize();
                const bool shape = base != 0 && stride == 340 && bytes == 18020 &&
                    stage->images[2].sharp_fetch.summary ==
                        decltype(stage->images[2].sharp_fetch.summary)::Invalid;
                LOG_WARNING(Render_Vulkan,
                    "GHOST_INDIRECT_LAYOUT sample={} base={:#x} stride={} bytes={} shape={}",
                    n, base, stride, bytes, shape);
                static std::atomic<u64> last_base{0};
                if (shape && last_base.exchange(base, std::memory_order_relaxed) != base) {
                    const int fd = ::open("/proc/self/mem", O_RDONLY | O_CLOEXEC);
                    const int open_error = fd >= 0 ? 0 : errno;
                    const u64 root = u64(stage->user_data[0]) |
                        (u64(stage->user_data[1]) << 32);
                    std::array<u32, 4> root_descriptor{};
                    const u64 root_addr = root + 0x180;
                    ssize_t nroot = -1;
                    if (fd >= 0 && root > 0 &&
                        root <= 0x00007fffffffffffull - 0x180 - 16) {
                        nroot = ::pread(fd, root_descriptor.data(), 16,
                                        static_cast<off_t>(root_addr));
                    }
                    const bool source_match = nroot == 16 &&
                        sizeof(table) == 16 &&
                        std::memcmp(root_descriptor.data(), &table, 16) == 0;
                    LOG_WARNING(Render_Vulkan,
                        "GHOST_INDIRECT_SOURCE sample={} root={:#x} raw={:#x} "
                        "bytes={} bound_match={}",
                        n, root, root_addr, nroot, source_match);
                    u32 ok = 0, valid = 0, invalid = 0, zero = 0, errors = 0;
                    for (u32 row = 0; row < 53; ++row) {
                        const u64 displacement = u64(row) * 340 + 64;
                        if (displacement + 32 > bytes) break;
                        const bool safe = base > 0 &&
                            base <= 0x00007fffffffffffull - displacement - 32;
                        const u64 address = safe ? base + displacement : 0;
                        std::array<u32, 8> raw{};
                        ssize_t count = -1;
                        int err = open_error;
                        if (fd >= 0 && safe) {
                            errno = 0;
                            count = ::pread(fd, raw.data(), sizeof(raw),
                                            static_cast<off_t>(address));
                            err = count == sizeof(raw) ? 0 : errno;
                        }
                        if (count != sizeof(raw)) {
                            ++errors;
                            LOG_WARNING(Render_Vulkan,
                                "GHOST_INDIRECT_ROW sample={} row={} read=false "
                                "addr={:#x} bytes={} errno={}",
                                n, row, address, count, err);
                            continue;
                        }
                        ++ok;
                        const bool all_zero = std::all_of(raw.begin(), raw.end(),
                                                          [](u32 x){return x==0;});
                        if (all_zero) ++zero;
                        AmdGpu::Image image{};
                        std::memcpy(&image, raw.data(), sizeof(image));
                        const bool image_valid = image.Valid() && image.Address() != 0;
                        if (image_valid) ++valid;
                        else ++invalid;
                        LOG_WARNING(Render_Vulkan,
                            "GHOST_INDIRECT_ROW sample={} row={} read=true "
                            "valid={} zero={} image_addr={:#x} fmt={} "
                            "dw0={:#x} dw1={:#x} dw2={:#x} dw3={:#x} "
                            "dw4={:#x} dw5={:#x} dw6={:#x} dw7={:#x}",
                            n, row, image_valid, all_zero, image.Address(),
                            u32(image.data_format), raw[0], raw[1], raw[2],
                            raw[3], raw[4], raw[5], raw[6], raw[7]);
                    }
                    if (fd >= 0) ::close(fd);
                    LOG_WARNING(Render_Vulkan,
                        "GHOST_INDIRECT_SUMMARY sample={} base={:#x} rows=53 "
                        "readable={} valid={} invalid={} zero={} errors={} "
                        "source_match={}",
                        n, base, ok, valid, invalid, zero, errors, source_match);
                }
            }
        }
    }
'''
 target='''        if (!stage) {
            continue;
        }
        set_writes.resize('''
 src=ensure_one(src,target,
 '''        if (!stage) {
            continue;
        }
'''+audit+'''        set_writes.resize(''',
 'resource audit BindResources insertion')
 result[raster]=src.encode()
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

def build_test_executable():
 global BINARY_COPY,SOURCES_STAGED
 stage=reconstruct()
 patched=instrument(stage)
 backup_binary()
 # Verify every change is confined to the exact 9 backed-up paths.
 if any(name not in BACKUP for name in patched):
  raise RuntimeError('Attempt to stage unbacked file')
 SOURCES_STAGED=True
 for name,text in patched.items():
  (SRC/name).write_bytes(text)
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
 env.update(VK_DRIVER_FILES=str(icd),VK_ICD_FILENAMES=str(icd),RADV_DEBUG='hang,nocache,noumr',RADV_PERFTEST='pswave32',SHADPS4_GHOST_RESOURCE_AUDIT='1')
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
  while time.monotonic()-start<80:
   now=time.monotonic();text=log.read_text(errors='replace')
   if P.poll() is not None:S['result']='game-exited';break
   tables=re.findall(r'GHOST_INDIRECT_SUMMARY sample=\d+ base=0x([0-9a-f]+)',text,re.I)
   if len(set(tables))>=1:
    S['result']='first-complete-image2-table-captured';break
   if now-start>=45 and 'GHOST_C6_RELEASE' not in text:
    S['result']='original-Ghost-trace-absent';break
   if now-start>=45 and 'GHOST_RESOURCE_AUDIT' not in text:
    S['result']='pixel-resource-audit-not-executed';break
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
  else:S['result']='80s-without-full-table-or-hang'
 text=log.read_text(errors='replace')
 ticks=re.findall(r'GHOST_TIMELINE_RELEASE seq=(\d+) pipe=(\d+) recording=(\d+) submitted=(\d+) completed=(\d+) pending=(\w+)',text)
 samples=re.findall(r'GHOST_RESOURCE_AUDIT shader=0x([0-9a-f]+) sample=(\d+) buffers=(\d+) invalid_buffers=(\d+) images=(\d+) invalid_images=(\d+) samplers=(\d+) invalid_samplers=(\d+) user_data_dw=(\d+) flatbuf_dw=(\d+) uses_dma=(\w+)',text,re.I)
 S['resource_audit_samples']=len(samples)
 S['resource_audit_first']=samples[:6]
 S['resource_audit_last']=samples[-6:]
 S['resource_audit_invalid_samples']=sum(int(x[3])+int(x[5])+int(x[7])>0 for x in samples)
 S['srt_read_strategy']='pread:/proc/self/mem on shader-bound indirect buffer1, record*340+80; no guest writes'
 S['srt_source_samples']=len(re.findall(r'GHOST_SRT_SRC sample=',text))
 S['indirect_summaries']=re.findall(
  r'GHOST_INDIRECT_SUMMARY sample=(\d+) base=0x([0-9a-f]+) rows=(\d+) readable=(\d+) valid=(\d+) invalid=(\d+) zero=(\d+) errors=(\d+) source_match=(\w+)',text,re.I)
 S['indirect_tables']=len(set(x[1] for x in S['indirect_summaries']))
 S['indirect_readable']=sum(int(x[3]) for x in S['indirect_summaries'])
 S['indirect_invalid']=sum(int(x[5]) for x in S['indirect_summaries'])
 S['indirect_errors']=sum(int(x[7]) for x in S['indirect_summaries'])
 S['srt_raw_samples']=len(re.findall(r'GHOST_SRT_RAW sample=',text))
 S['srt_raw_image2_readable']=0 # Old root+0x80 probe deliberately disabled; correct indirect table is audited above
 S['srt_flat_samples']=len(re.findall(r'GHOST_SRT_FLAT sample=',text))
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
 print('GHOST_RECONSTRUCTED_SOURCE_TIMELINE=START',flush=True)
 if active():raise RuntimeError('Another shadPS4 running; refused')
 LOCK=os.open(H/'.cache/ghost-rebuilt-timeline.lock',os.O_CREAT|os.O_RDWR,0o600)
 fcntl.flock(LOCK,fcntl.LOCK_EX|fcntl.LOCK_NB)
 candidate=build_test_executable()
 print('GHOST_ARCHIVED_7_FILE_RECONSTRUCTION=PASS',flush=True)
 print('GHOST_TIMELINE_BUILD=PASS',flush=True)
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
 print('GHOST_RESOURCE_SAMPLES='+str(S.get('resource_audit_samples')),flush=True)
 print('GHOST_INVALID_RESOURCE_SAMPLES='+str(S.get('resource_audit_invalid_samples')),flush=True)
 print('GHOST_INDIRECT_TABLES='+str(S.get('indirect_tables')),flush=True)
 print('GHOST_INDIRECT_READABLE='+str(S.get('indirect_readable')),flush=True)
 print('GHOST_INDIRECT_VALID_IMAGES='+str(sum(int(x[4]) for x in S.get('indirect_summaries',[]))),flush=True)
 print('GHOST_SOURCE_RESTORED='+str(S.get('source_restored')),flush=True)
 print('GHOST_BUILD_RESTORED='+str(S.get('build_binary_restored')),flush=True)
 print('GHOST_CLEANUP='+str(S['cleanup']),flush=True)
 print('UPLOAD_THIS_ARCHIVE='+str(OUT),flush=True)
 if P is None or P.poll() is not None:shutil.rmtree(W,ignore_errors=True)
