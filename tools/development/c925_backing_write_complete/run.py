#!/usr/bin/env python3
"""Reversible C925 GPU readback backing integrity: complete physical coverage.

Corrects MemoryManager::TryWriteBacking partial-success/incorrect-source-offset
and prevents BufferCache from forgetting GPU-dirty ranges when a writeback
cannot be fully committed. Independent of any game/shader ID.
"""
from __future__ import annotations
import datetime as dt
import difflib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import sys
import tarfile
import traceback
from pathlib import Path

P=Path.home()/'projects/shadps4'
BASE=P/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
ROOT=BASE/'source'
BUILD=BASE/'build'
ELF=BUILD/'shadps4'
BASE_SHA='89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
PATHS={
 'memory_src':ROOT/'src/core/memory.cpp',
 'buffer_src':ROOT/'src/video_core/buffer_cache/buffer_cache.cpp',
 'memory_obj':BUILD/'CMakeFiles/shadps4.dir/src/core/memory.cpp.o',
 'buffer_obj':BUILD/'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o',
 'elf':ELF,
}
PINS={
 'direct_ninja.py':'cba0f46505d188c422297f49ad1d03aacad0442dbacca64b2651531b73f77d09',
 'run_draw_probe.py':'049bb4863d20af7da11460367bf786375b3ba7b37738f57f7fe325dad5fadf7a',
 'resume_c925_revalidation.py':'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250',
 'resume_finite_raw.py':'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b',
 'menu_validation_runtime.py':'7467dbc881babc9d36d53ff6f169bec112e7f97918a827cc17a0bfde47740f50',
}
LOCKS=(
 '.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock',
 '.got-finite-raw-image.lock','.got-uniform-samples.lock','.got-indirect-materials.lock',
 '.got-sparse-host-limit.lock','.got-menu-resource-validation.lock','.got-menu-fills.lock',
 '.got-fragment-draw-trace.lock','.got-descriptor-fetch-fix.lock',
 '.got-texture-overlap-fix.lock','.got-gpu-image-resize.lock',
 '.got-gpu-backing-complete.lock')
MARK='BackingWriteCompleteVerified'

def digest(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
def require(cond,what):
 if not cond:raise RuntimeError(what)
def module(work,name):
 file=work/name
 require(file.is_file() and not file.is_symlink() and digest(file)==PINS[name],
         'PINNED_HELPER_CHANGED:'+name)
 spec=importlib.util.spec_from_file_location('verified_'+file.stem,file)
 require(spec is not None and spec.loader is not None,'UNIMPORTABLE_HELPER:'+name)
 result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
def function_range(src,signature):
 require(src.count(signature)==1,'FUNCTION_SIGNATURE_NOT_UNIQUE:'+signature)
 begin=src.index(signature);opening=src.find('{',begin)
 require(opening>=0,'FUNCTION_BODY_MISSING')
 depth=0
 for i in range(opening,len(src)):
  if src[i]=='{':depth+=1
  if src[i]=='}':
   depth-=1
   if depth==0:return begin,i+1
 raise RuntimeError('FUNCTION_BRACE_MISMATCH')

def patch_memory(src):
 signature='bool MemoryManager::TryWriteBacking(void* address, const void* data, u64 size) {'
 a,b=function_range(src,signature)
 original=src[a:b]
 if MARK in original:raise RuntimeError('COMPLETE_BACKING_WRITE_ALREADY_PRESENT')
 for evidence in ('TryWriteBacking','phys_areas','memcpy','HasPhysicalBacking'):
  require(evidence in original,'MEMORY_SOURCE_NOT_RECOGNIZED:'+evidence)
 new=r'''bool MemoryManager::TryWriteBacking(void* address, const void* data, u64 size) {
    // Full-span physical coverage is mandatory: never report a partial GPU readback as saved.
    const VAddr virtual_addr = std::bit_cast<VAddr>(address);
    std::shared_lock lk{mutex};
    ASSERT_MSG(IsValidMapping(virtual_addr, size),
               "Attempted to access invalid address {:#x}", virtual_addr);
    if (size == 0) return true;
    if (virtual_addr + size < virtual_addr) return false;

    // BACKING_WRITE_COMPLETE_V1: same checked traversal for validation and copies.
    // The preliminary pass does not touch guest memory; failure is atomic.
    const auto visit = [&](auto&& sink) -> bool {
        VAddr pos = virtual_addr;
        const VAddr end = virtual_addr + size;
        auto vma_it = FindVMA(pos);
        while (pos < end) {
            if (vma_it == vma_map.end()) return false;
            const auto& vma = vma_it->second;
            const VAddr vma_end = vma.base + vma.size;
            if (pos < vma.base || pos >= vma_end ||
                !(vma.type == VMAType::Direct || vma.type == VMAType::Flexible ||
                  vma.type == VMAType::Pooled)) return false;
            const VAddr stop = std::min(end, vma_end);
            while (pos < stop) {
                const u64 offset = pos - vma.base;
                auto physical = vma.phys_areas.upper_bound(offset);
                if (physical == vma.phys_areas.begin()) return false;
                --physical;
                if (offset < physical->first ||
                    offset - physical->first >= physical->second.size) return false;
                const u64 available =
                    physical->second.size - (offset - physical->first);
                const u64 count = std::min<u64>(stop - pos, available);
                if (!count) return false;
                sink(impl.BackingBase() + physical->second.base +
                         (offset - physical->first), pos - virtual_addr, count);
                pos += count;
            }
            ++vma_it;
        }
        return true;
    };

    u32 segments = 0;
    const bool complete = visit([&](u8*, u64, u64){ ++segments; });
    if (!complete) {
        static std::atomic<u32> rejected{};
        const u32 count = rejected.fetch_add(1, std::memory_order_relaxed) + 1;
        if (count <= 8 || (count & (count - 1)) == 0) {
            LOG_WARNING(Kernel_Vmm, "BackingWriteIncomplete event={} addr={:#x} bytes={}",
                        count, virtual_addr, size);
        }
        return false;
    }
    const auto* const source = static_cast<const u8*>(data);
    const bool copied = visit([&](u8* dest, u64 source_offset, u64 count) {
        std::memcpy(dest, source + source_offset, count);
    });
    ASSERT_MSG(copied, "Physical backing changed under the memory lock");
    static std::atomic<u32> successful{};
    const u32 count = successful.fetch_add(1, std::memory_order_relaxed) + 1;
    if (count <= 8 || (count & (count - 1)) == 0) {
        LOG_INFO(Kernel_Vmm, "BackingWriteCompleteVerified event={} segments={} bytes={}",
                 count, segments, size);
    }
    return copied;
}'''
 result=src[:a]+new+src[b:]
 for header in ('#include <algorithm>','#include <atomic>','#include <cstring>'):
  if header not in result:
   pos=result.find('#include <')
   require(pos>=0,'MEMORY_INCLUDE_ANCHOR_MISSING')
   result=result[:pos]+header+'\n'+result[pos:]
 require(result.count(MARK)==1 and result.count('BackingWriteIncomplete')==1,
         'MEMORY_PATCH_RESULT_INVALID')
 return result

def patch_buffer(src):
 signature='void BufferCache::DownloadMemory('
 a,b=function_range(src,signature)
 section=src[a:b]
 if 'BackingReadbackDirtyRetained' in section:
  raise RuntimeError('BUFFER_COHERENCE_ALREADY_FIXED')
 if section.count('memory->TryWriteBacking(')!=1:
  raise RuntimeError('BUFFER_TRY_WRITE_CALL_CHANGED')
 if section.count('memory_tracker->UnmarkRegionAsGpuModified(device_addr, size, false);')!=1:
  raise RuntimeError('BUFFER_DIRTY_CLEAR_CHANGED')
 if section.count('gpu_modified_ranges.Subtract(address, size);')!=1:
  raise RuntimeError('BUFFER_GPU_DIRTY_EXTRACTION_CHANGED')
 old=r'''        for (const auto& copy : copies) {
            auto* dst_addr = std::bit_cast<u8*>(arena_base + copy.srcOffset);
            memory->TryWriteBacking(dst_addr, download.mapped + (copy.dstOffset - download.offset),
                                    copy.size);
        }
        memory_tracker->UnmarkRegionAsGpuModified(device_addr, size, false);'''
 new=r'''        // A failed physical readback must not be acknowledged as CPU-coherent:
        // retain those original GPU-dirty segments for a later valid mapping.
        boost::container::small_vector<std::pair<VAddr, u64>, 8> failed_writes;
        for (const auto& copy : copies) {
            const VAddr address = arena_base + copy.srcOffset;
            auto* dst_addr = std::bit_cast<u8*>(address);
            if (!memory->TryWriteBacking(
                    dst_addr, download.mapped + (copy.dstOffset - download.offset), copy.size)) {
                failed_writes.emplace_back(address, copy.size);
            }
        }
        memory_tracker->UnmarkRegionAsGpuModified(device_addr, size, false);
        for (const auto& [address, bytes] : failed_writes) {
            gpu_modified_ranges.Add(address, bytes);
            memory_tracker->MarkRegionAsGpuModified(address, bytes);
        }
        if (!failed_writes.empty()) {
            static std::atomic<u32> retained{};
            const u32 count = retained.fetch_add(1, std::memory_order_relaxed) + 1;
            if (count <= 8 || (count & (count - 1)) == 0) {
                LOG_WARNING(Render_Vulkan,
                            "BackingReadbackDirtyRetained event={} failed_copies={} request_bytes={}",
                            count, failed_writes.size(), size);
            }
        }'''
 require(section.count(old)==1,'BUFFER_READBACK_WRITE_CONTEXT_CHANGED')
 section=section.replace(old,new,1)
 result=src[:a]+section+src[b:]
 if '#include <atomic>' not in result:
  pos=result.find('#include <')
  require(pos>=0,'BUFFER_INCLUDE_ANCHOR_MISSING')
  result=result[:pos]+'#include <atomic>\n'+result[pos:]
 require(result.count('BackingReadbackDirtyRetained')==1,
         'BUFFER_PATCH_RESULT_INVALID')
 return result

def tests():
 # Simulated two-pass backing semantics: unbacked segment fails atomically,
 # contiguous multi-segment mappings transfer bytes at correct offsets.
 def fake_write(payload,segments):
  covers=0
  for start,length,_ in segments:
   if start!=covers:return None
   covers+=length
  if covers!=len(payload):return None
  return b''.join(payload[start:start+length] for start,length,_ in segments)
 data=bytes(range(64))
 require(fake_write(data,[(0,12,0),(12,20,100),(32,32,200)])==data,'MULTI_SEGMENT_TEST')
 require(fake_write(data,[(0,12,0),(13,51,100)]) is None,'HOLE_TEST')
 require(fake_write(data,[(0,12,0)]) is None,'PARTIAL_TRUE_TEST')
 require(fake_write(b'',[])==b'','EMPTY_TEST')
 # Verify visitor/dirty-state contract against source-like isolated fixtures.
 fixture_mem='''#include <shared_mutex>
bool MemoryManager::TryWriteBacking(void* address, const void* data, u64 size) {
    if (!HasPhysicalBacking(vma)) return false;
    auto it = vma.phys_areas.begin();
    memcpy(dst, data, size);
    return true;
}
'''
 fixture_buffer='''#include <atomic>
void BufferCache::DownloadMemory(const Buffer* arena, VAddr device_addr, u64 size, bool is_write, bool async) {
    gpu_modified_ranges.Subtract(address, size);
    const auto write_data = [this, arena_base, device_addr, size, download, is_write, async,
                             copies = std::move(copies)] {
        download.Invalidate();
        for (const auto& copy : copies) {
            auto* dst_addr = std::bit_cast<u8*>(arena_base + copy.srcOffset);
            memory->TryWriteBacking(dst_addr, download.mapped + (copy.dstOffset - download.offset),
                                    copy.size);
        }
        memory_tracker->UnmarkRegionAsGpuModified(device_addr, size, false);
        if (is_write) { memory_tracker->MarkRegionAsCpuModified(device_addr, size); }
    };
}
'''
 m=patch_memory(fixture_mem);b=patch_buffer(fixture_buffer)
 require('source + source_offset' in m and 'visit([&](u8*' in m,'OFFSET_FIX')
 require('memory_tracker->MarkRegionAsGpuModified(address, bytes)' in b,'RETAIN_DIRTY_FIX')
 require('memory->TryWriteBacking' in b,'WRITEBACK_NOT_LOST')
 print('BACKING_FULL_COVERAGE_AND_DIRTY_RETENTION_FIXTURES=PASS',flush=True)

def main(work):
 os.umask(0o077)
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evidence=P/'logs'/('got-c925-backing-complete-'+stamp)
 evidence.mkdir(parents=True,exist_ok=False)
 backup=BASE/'checkpoints'/('backing-complete-'+stamp)
 names=list(PATHS)
 require(len(names)==len(set(names)),'CHECKPOINT_NAME_COLLISION')
 before={};after={};locks=[];pending=[];touched=False;rc=1
 state=dict(record_id=evidence.name,journal_scope='UNIVERSAL_SHADPS4',
            game='CUSA11456',
            change='Complete physical readback coverage and retain GPU-dirty on failed writeback',
            build='NOT_RUN',game_test='NOT_RUN',graphics='NOT_PROVEN',
            keep_or_revert='KEEP_ISOLATED_CORRECTNESS_CANDIDATE_RESTORE_BASELINE',
            promoted=False,status='PREFLIGHT')
 print('EVIDENCE_DIRECTORY='+str(evidence),flush=True)
 try:
  direct=module(work,'direct_ninja.py')
  draw=module(work,'run_draw_probe.py')
  u=module(work,'resume_c925_revalidation.py')
  hr=module(work,'resume_finite_raw.py')
  menu=module(work,'menu_validation_runtime.py')
  api=u.Ollama(evidence)
  for name in LOCKS:
   lock=(P/'logs'/name).open('a+')
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except OSError:lock.close();raise RuntimeError('BUILD_LOCK_BUSY:'+name)
   locks.append(lock)
  require(not u.emulator_pids(),'ANOTHER_SHADPS4_ACTIVE')
  for n,p in PATHS.items():
   require(p.is_file() and not p.is_symlink(),'SOURCE_OBJECT_OR_ELF_MISSING:'+n)
   before[n]=digest(p)
  require(before['elf']==BASE_SHA,'PROVEN_EXECUTABLE_CHANGED')
  accepted=json.loads((P/'logs/got-c925-sparse-host-limit-20261029T075648223982Z/trial.json').read_text())
  require(accepted.get('build')=='PASS' and accepted.get('candidate_sha256')==BASE_SHA,
          'ACCEPTED_C925_MANIFEST_CHANGED')
  for name,entry in accepted['source_manifest'].items():
   file=ROOT/name
   require(file.is_file() and digest(file)==entry['after'],'PROVEN_FIXES_CHANGED:'+name)
  protected=BASE.parent/'c925-integrated-gpu-20261027-122701/build/shadps4'
  require(protected.is_file(),'PROTECTED_BACKUP_ELF_MISSING')
  protected_digest=digest(protected)
  previous=json.loads((P/'logs/got-c925-menu-fills-20261029T154222818563Z/trial.json').read_text())
  require(previous.get('status')=='MENU_RUN_FINISHED_VISUAL_PENDING' and
          previous.get('game',{}).get('time_limit_reached'), 'WORKING_MENU_PROFILE_NOT_VERIFIED')
  profile_old=Path(previous['private_profile']).resolve()
  require(profile_old.name=='user' and
          profile_old.is_relative_to((BASE/'runs').resolve()),'PROFILE_OUTSIDE_ISOLATION')
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  require(eboot.is_file(),'GAME_EBOOT_MISSING')
  display=os.environ.get('DISPLAY') or ':0'
  u.command(['env','DISPLAY='+display,'xdpyinfo'],timeout=8)
  tests()
  changed={
   'memory_src':patch_memory(PATHS['memory_src'].read_text()),
   'buffer_src':patch_buffer(PATHS['buffer_src'].read_text()),
  }
  for key,source in changed.items():
   filename=str(PATHS[key].relative_to(ROOT))
   original=PATHS[key].read_text()
   (evidence/(key+'.diff')).write_text(''.join(difflib.unified_diff(
    original.splitlines(True),source.splitlines(True),
    'a/'+filename,'b/'+filename)))
  plans=[
   direct.parse(BUILD,PATHS['memory_src'],PATHS['memory_obj'],ELF,evidence),
   direct.parse(BUILD,PATHS['buffer_src'],PATHS['buffer_obj'],ELF,evidence),
  ]
  require(plans[0][1]==plans[1][1],'INCONSISTENT_LINK_PLAN')
  backup.mkdir(parents=True,exist_ok=False)
  for n,p in PATHS.items():
   shutil.copy2(p,backup/n)
   require(digest(backup/n)==before[n],'CHECKPOINT_HASH_MISMATCH:'+n)
  touched=True
  for key in ('memory_src','buffer_src'):
   p=PATHS[key]
   temp=p.with_name(p.name+'.backing-complete-pending')
   require(not temp.exists() and not temp.is_symlink(),'TEMP_SOURCE_COLLISION')
   temp.write_text(changed[key]);shutil.copymode(p,temp);os.replace(temp,p)
   after[key]=digest(p)
  for key,plan in zip(('memory_obj','buffer_obj'),plans):
   print('STAGE=COMPILE_'+key.upper(),flush=True)
   menu.logged(u,['bash','-c',direct.command_string(plan[0])],
               evidence/(key+'-compile.log'),'COMPILE_ONE_'+key,timeout=900)
   after[key]=digest(PATHS[key])
  print('STAGE=RELINK_TWO_OBJECT_CANDIDATE',flush=True)
  menu.logged(u,['bash','-c',direct.command_string(plans[0][1])],
              evidence/'link.log','RELINK_C925_BACKING_FIX',timeout=900)
  after['elf']=digest(ELF)
  require(after['elf']!=BASE_SHA,'CANDIDATE_BINARY_UNCHANGED')
  state['build']='PASS'
  state['candidate_sha256']=after.copy()
  candidate=BASE/'candidates'/('backing-complete-'+stamp)
  candidate.mkdir(parents=True,exist_ok=False)
  shutil.copy2(ELF,candidate/'shadps4')
  state['candidate_directory']=str(candidate)
  profile=BASE/'runs'/('backing-complete-'+stamp)/'user'
  state['profile_copy']=menu.copy_profile(profile_old,profile)
  state['private_profile']=str(profile)
  for conf in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
   if conf.is_file():
    x=json.loads(conf.read_text());v=x.setdefault('Vulkan',{})
    for k in ('vkvalidation_enabled','vkvalidation_core_enabled','vkvalidation_sync_enabled','vkvalidation_gpu_enabled'):
     v[k]=False
    save(conf,x)
  state['vram_before']=u.free_vram()
  print('STAGE=8GIB_VRAM_CHECK_OLLAMA_CONDITIONAL_UNLOAD',flush=True)
  hr.verified_headroom(u,api,evidence,pending,state)
  require(u.free_vram()>=hr.MIN_FREE,'INSUFFICIENT_VRAM_FOR_GAME')
  require(not u.emulator_pids(),'EXTERNAL_EMULATOR_STARTED')
  print('STAGE=MENU_118_PLUS_2_SECONDS_NO_INPUT',flush=True)
  state['game_test']=u.run_game(ELF,eboot,profile.parent,evidence,display)
  log=(evidence/'game.log').read_text(errors='replace')
  state['runtime_counts']={k:log.count(k) for k in (
   'BackingWriteCompleteVerified','BackingWriteIncomplete',
   'BackingReadbackDirtyRetained','GpuDescriptorTableCpuCoherence',
   'INDEXED_MATERIAL_BIND_REJECT','Sharp source was not flatenned',
   'SYNC-HAZARD','ErrorDeviceLost')}
  state['status']='CANDIDATE_TESTED_VISUAL_PENDING' if state['game_test'].get('time_limit_reached') else 'GAME_EXITED_EARLY'
  rc=0 if state['game_test'].get('time_limit_reached') else 1
 except (Exception,KeyboardInterrupt) as exc:
  state['status']='FAILED:'+str(exc)
  (evidence/'failure.txt').write_text(traceback.format_exc())
  print('STOP='+str(exc),flush=True)
 finally:
  if touched:
   try:
    print('STAGE=RESTORE_SOURCE_OBJECT_ELF',flush=True)
    state['restoration']=draw.restore(tuple(PATHS.items()),backup,before,after)
    if any(x!='RESTORED_VERIFIED' for x in state['restoration'].values()):rc=1
   except Exception as exc:state['restoration_error']=str(exc);rc=1
  for n,p in PATHS.items():
   if n in before:
    state[n+'_restored']=p.is_file() and digest(p)==before[n]
    if not state[n+'_restored']:rc=1
  if pending and 'api' in locals():
   try:
    state['ollama_restore']=u.restore_models(api,pending,evidence)
    if any(x.get('result') in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in state['ollama_restore']):rc=1
   except Exception as exc:state['ollama_restore_error']=str(exc);rc=1
  if 'protected_digest' in locals() and digest(protected)!=protected_digest:
   state['protected_binary_changed']=True;rc=1
  state['exit_code']=rc;save(evidence/'trial.json',state)
  diary=P/'diary/trials.jsonl';diary.parent.mkdir(parents=True,exist_ok=True)
  with diary.open('a') as file:
   fcntl.flock(file,fcntl.LOCK_EX);file.write(json.dumps(state,ensure_ascii=False)+'\n')
   file.flush();os.fsync(file.fileno())
  with tarfile.open(str(evidence)+'.tar.gz','w:gz') as tar:
   tar.add(evidence,arcname=evidence.name)
  print('RESULT='+state['status']+'\nUPLOAD_THIS='+str(evidence)+'.tar.gz\nSSH_SESSION=OPEN',flush=True)
  for lock in locks:lock.close()
 return rc

if __name__=='__main__':
 signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
 if len(sys.argv)!=2:raise SystemExit('PINNED_HELPER_DIRECTORY_REQUIRED')
 sys.exit(main(Path(sys.argv[1])))
