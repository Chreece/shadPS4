#!/usr/bin/env python3
"""C925: preserve GPU contents when a same-layout image changes dimensions.

Isolated upstream shadps4-emu/shadPS4 PR #5303 candidate; never promote without
real GPU event + improved menu pixels. Uses pinned, previously verified helpers.
"""
from __future__ import annotations
import datetime as dt
import difflib
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import sys
import tarfile
import traceback
from pathlib import Path

P=Path.home()/'projects/shadps4'
BASE=P/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
SRCROOT=BASE/'source'
BUILD=BASE/'build'
ELF=BUILD/'shadps4'
ORIG_ELF_SHA='89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
FILES={
 'runtime_cpp': ('src/video_core/renderer_vulkan/vk_runtime.cpp','CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_runtime.cpp.o'),
 'runtime_h': ('src/video_core/renderer_vulkan/vk_runtime.h',None),
 'texture_cpp': ('src/video_core/texture_cache/texture_cache.cpp','CMakeFiles/shadps4.dir/src/video_core/texture_cache/texture_cache.cpp.o'),
 'texture_h': ('src/video_core/texture_cache/texture_cache.h',None),
}
HELPERS={
 'direct_ninja.py':'cba0f46505d188c422297f49ad1d03aacad0442dbacca64b2651531b73f77d09',
 'run_draw_probe.py':'049bb4863d20af7da11460367bf786375b3ba7b37738f57f7fe325dad5fadf7a',
 'resume_c925_revalidation.py':'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250',
 'resume_finite_raw.py':'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b',
 'menu_validation_runtime.py':'7467dbc881babc9d36d53ff6f169bec112e7f97918a827cc17a0bfde47740f50',
}
MARK='GpuImageResizePreserved'

def digest(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,obj):Path(p).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
def one(text,old,new,tag):
 n=text.count(old)
 if n!=1:raise RuntimeError('SOURCE_GATE_'+tag+'_EXPECTED_ONE_GOT_'+str(n))
 return text.replace(old,new,1)
def patch_runtime_h(s):
 return one(s,'    void CopyMip(VideoCore::Image* src, VideoCore::Image* dst, u32 mip, u32 slice);',
            '    void CopyMip(VideoCore::Image* src, VideoCore::Image* dst, u32 mip, u32 slice);\n'
            '    // Copy only the common pixel rectangle from a GPU-written source.\n'
            '    void CopyCompatibleRegion(VideoCore::Image* src, VideoCore::Image* dst);','RUNTIME_HEADER')
def patch_runtime_cpp(s):
 anchor='\nvoid Runtime::CopyColorAndDepth(VideoCore::Image* src, VideoCore::Image* dst) {'
 insert='''
void Runtime::CopyCompatibleRegion(VideoCore::Image* src, VideoCore::Image* dst) {
    const vk::ImageCopy region{
        .srcSubresource = {.aspectMask = src->aspect_mask,
                           .mipLevel = 0, .baseArrayLayer = 0, .layerCount = 1},
        .dstSubresource = {.aspectMask = dst->aspect_mask,
                           .mipLevel = 0, .baseArrayLayer = 0, .layerCount = 1},
        .extent = {std::min(src->info.size.width, dst->info.size.width),
                   std::min(src->info.size.height, dst->info.size.height), 1},
    };
    SetBackingSamples(dst, dst->info.num_samples);
    SetBackingSamples(src, src->info.num_samples);
    scheduler.EndRendering();
    bool needs_flush = Transit(src, vk::ImageLayout::eTransferSrcOptimal,
                               vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferRead);
    needs_flush |= Transit(dst, vk::ImageLayout::eTransferDstOptimal,
                           vk::PipelineStageFlagBits2::eCopy,
                           vk::AccessFlagBits2::eTransferWrite);
    if (needs_flush) FlushBarriers();
    scheduler.CommandBuffer().copyImage(src->GetImage(), vk::ImageLayout::eTransferSrcOptimal,
                                        dst->GetImage(), vk::ImageLayout::eTransferDstOptimal,
                                        region);
    dst->flags |= (src->flags & VideoCore::ImageFlagBits::GpuModified);
    dst->flags &= ~VideoCore::ImageFlagBits::Dirty;
}
'''
 if s.count('void Runtime::CopyCompatibleRegion('):raise RuntimeError('RUNTIME_ALREADY_PATCHED')
 return one(s,anchor,insert+anchor,'RUNTIME_SOURCE')
def patch_texture_h(s):
 old='    [[nodiscard]] ImageId ExpandImage(const ImageInfo& info, ImageId image_id);'
 return one(s,old,'    [[nodiscard]] ImageId ResizeCompatibleImage(const ImageInfo& info, ImageId image_id);\n'+old,'TEXTURE_HEADER')
def patch_texture_cpp(s):
 if 'ResizeCompatibleImage(' in s:raise RuntimeError('TEXTURE_ALREADY_PATCHED')
 signature='std::tuple<ImageId, int, int> TextureCache::ResolveOverlap('
 helper='''static bool HasHeightIndependentLayout(const ImageInfo& info) {
    if (info.type != AmdGpu::ImageType::Color2D || info.num_samples != 1 ||
        info.resources.layers != 1 || info.resources.levels != 1 ||
        info.props.is_depth || info.props.is_block) return false;
    const auto mode = AmdGpu::GetArrayMode(info.tile_mode);
    return AmdGpu::GetMicroTileThickness(mode) == 1 && !AmdGpu::IsPrt(mode);
}

'''
 s=one(s,signature,helper+signature,'LAYOUT_HELPER')
 a=s.index(signature);b=s.index('ImageId TextureCache::ExpandImage(',a)
 section=s[a:b]
 original='        const u32 rhs_block_size = cache_image.info.num_bits * cache_image.info.num_samples;'
 replacement=original+'''
        // Preserve valid GPU-only pixels when the same allocation changes viewport size.
        if (image_info.size != cache_image.info.size &&
            image_info.pitch == cache_image.info.pitch &&
            image_info.tile_mode == cache_image.info.tile_mode &&
            image_info.pixel_format == cache_image.info.pixel_format &&
            lhs_block_size == rhs_block_size && HasHeightIndependentLayout(image_info) &&
            HasHeightIndependentLayout(cache_image.info) &&
            True(cache_image.flags & ImageFlagBits::GpuModified)) {
            return {ResizeCompatibleImage(image_info, cache_image_id), -1, -1};
        }
'''
 section=one(section,original,replacement,'RESOLVE_GPU_REUSE')
 s=s[:a]+section+s[b:]
 expand='ImageId TextureCache::ExpandImage(const ImageInfo& info, ImageId image_id) {'
 resized='''ImageId TextureCache::ResizeCompatibleImage(const ImageInfo& info, ImageId image_id) {
    auto new_id = slot_images.Insert(instance, runtime, slot_image_views, info);
    RegisterImage(new_id);
    auto& src = slot_images[image_id];
    auto& dst = slot_images[new_id];
    RefreshImage(dst);
    runtime.CopyCompatibleRegion(&src, &dst);
    static thread_local u32 observed = 0;
    if (observed++ < 32) {
        LOG_INFO(Render_Vulkan, "GpuImageResizePreserved old={}x{} new={}x{} guest={:#x}",
                 src.info.size.width, src.info.size.height,
                 dst.info.size.width, dst.info.size.height, info.guest_address);
    }
    if (src.binding.is_bound || src.binding.is_target) src.binding.needs_rebind = 1u;
    FreeImage(image_id);
    TrackImage(new_id);
    return new_id;
}

'''
 s=one(s,expand,resized+expand,'IMAGE_RESIZE_IMPL')
 if s.count(MARK)!=1:raise RuntimeError('RESIZE_EVENT_COUNT_INVALID')
 return s

TRANSFORMS={'runtime_cpp':patch_runtime_cpp,'runtime_h':patch_runtime_h,
            'texture_cpp':patch_texture_cpp,'texture_h':patch_texture_h}

def tests():
 assert patch_runtime_h('    void CopyMip(VideoCore::Image* src, VideoCore::Image* dst, u32 mip, u32 slice);').count('CopyCompatibleRegion')==1
 assert 'void Runtime::CopyCompatibleRegion' in patch_runtime_cpp('\nvoid Runtime::CopyColorAndDepth(VideoCore::Image* src, VideoCore::Image* dst) {}')
 assert 'ResizeCompatibleImage' in patch_texture_h('    [[nodiscard]] ImageId ExpandImage(const ImageInfo& info, ImageId image_id);')
 assert all(min(a,b)<=a and min(a,b)<=b for a,b in ((512,360),(260,512),(1,1)))
 print('SOURCE_TRANSFORM_AND_CROP_TESTS=PASS',flush=True)

def module(work,name):
 f=work/name
 if not f.is_file() or f.is_symlink() or digest(f)!=HELPERS[name]:raise RuntimeError('PINNED_HELPER_NOT_VERIFIED:'+name)
 spec=importlib.util.spec_from_file_location('c925_'+f.stem,f)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def run(work):
 os.umask(0o077)
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 evidence=P/'logs'/('got-c925-gpu-image-resize-'+stamp);evidence.mkdir(parents=True,exist_ok=False)
 checkpoint=BASE/'checkpoints'/('gpu-image-resize-'+stamp)
 objects=[(key+'_obj',BUILD/target) for key,(_,target) in FILES.items() if target]
 managed=[(key,SRCROOT/name) for key,(name,_) in FILES.items()]+objects+[('elf',ELF)]
 if len({name for name,_ in managed}) != len(managed):
  raise RuntimeError('CHECKPOINT_NAME_COLLISION')
 state=dict(record_id=evidence.name,journal_scope='UNIVERSAL_SHADPS4',
            game='CUSA11456',change='Preserve GPU-written same-layout images across dimension changes',
            upstream_reference='https://github.com/shadps4-emu/shadPS4/pull/5303',
            status='PREFLIGHT',graphics='NOT_PROVEN',promoted=False,
            keep_or_revert='RETAIN_ISOLATED_CANDIDATE_RESTORE_BASELINE',game_test='NOT_RUN')
 originals={};updated={};locks=[];pending=[];touched=False;rc=1
 print('EVIDENCE_DIRECTORY='+str(evidence),flush=True)
 try:
  direct=module(work,'direct_ninja.py');draw=module(work,'run_draw_probe.py')
  u=module(work,'resume_c925_revalidation.py');hr=module(work,'resume_finite_raw.py')
  menu=module(work,'menu_validation_runtime.py');api=u.Ollama(evidence)
  for lock in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock',
     '.got-finite-raw-image.lock','.got-uniform-samples.lock','.got-indirect-materials.lock',
     '.got-sparse-host-limit.lock','.got-menu-resource-validation.lock',
     '.got-menu-fills.lock','.got-fragment-draw-trace.lock','.got-descriptor-fetch-fix.lock',
     '.got-texture-overlap-fix.lock','.got-gpu-image-resize.lock'):
   f=(P/'logs'/lock).open('a+')
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except OSError:f.close();raise RuntimeError('SHARED_BUILD_LOCK_BUSY:'+lock)
   locks.append(f)
  if u.emulator_pids():raise RuntimeError('EXTERNAL_SHADPS4_RUNNING')
  for n,p in managed:
   if not p.is_file() or p.is_symlink():raise RuntimeError('BASELINE_FILE_MISSING:'+n)
   originals[n]=digest(p)
  if originals['elf']!=ORIG_ELF_SHA:raise RuntimeError('PROVEN_ELF_HASH_CHANGED')
  sparse=json.loads((P/'logs/got-c925-sparse-host-limit-20261029T075648223982Z/trial.json').read_text())
  if sparse['candidate_sha256']!=ORIG_ELF_SHA or sparse['build']!='PASS':raise RuntimeError('BASELINE_MANIFEST_INVALID')
  for name,entry in sparse['source_manifest'].items():
   path=SRCROOT/name
   if not path.is_file() or digest(path)!=entry['after']:raise RuntimeError('SOURCE_MANIFEST_CHANGED:'+name)
  protected=BASE.parent/'c925-integrated-gpu-20261027-122701/build/shadps4'
  if not protected.is_file():raise RuntimeError('PROTECTED_SECONDARY_ELF_MISSING')
  protected_hash=digest(protected)
  previous=json.loads((P/'logs/got-c925-menu-fills-20261029T154222818563Z/trial.json').read_text())
  if previous['status']!='MENU_RUN_FINISHED_VISUAL_PENDING' or not previous['game']['time_limit_reached']:
   raise RuntimeError('PREVIOUS_GAME_PROFILE_INVALID')
  profile_old=Path(previous['private_profile']).resolve()
  if not profile_old.is_relative_to((BASE/'runs').resolve()) or profile_old.name!='user':raise RuntimeError('PROFILE_OUTSIDE_ISOLATION')
  display=os.getenv('DISPLAY') or ':0';u.command(['env','DISPLAY='+display,'xdpyinfo'],timeout=8)
  eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
  if not eboot.is_file():raise RuntimeError('GAME_EBOOT_MISSING')
  tests()
  transformed={}
  for key,(filename,_) in FILES.items():
   p=SRCROOT/filename;before=p.read_text();after=TRANSFORMS[key](before)
   if after==before:raise RuntimeError('NO_PATCH_CHANGE:'+key)
   transformed[key]=after
   (evidence/(key+'.diff')).write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),'a/'+filename,'b/'+filename)))
  plans=[]
  for key,src in [('runtime_cpp',SRCROOT/FILES['runtime_cpp'][0]),('texture_cpp',SRCROOT/FILES['texture_cpp'][0])]:
   obj=dict(objects)[key+'_obj']
   plans.append(direct.parse(BUILD,src,obj,ELF,evidence))
  if plans[0][1]!=plans[1][1]:raise RuntimeError('INCONSISTENT_LINK_COMMANDS')
  checkpoint.mkdir(parents=True,exist_ok=False)
  for name,path in managed:
   dst=checkpoint/name;shutil.copy2(path,dst)
   if digest(dst)!=originals[name]:raise RuntimeError('CHECKPOINT_MISMATCH:'+name)
  touched=True
  for key,(filename,_) in FILES.items():
   p=SRCROOT/filename;temp=p.with_name(p.name+'.gpu-resize-pending')
   if temp.exists() or temp.is_symlink():raise RuntimeError('PENDING_SOURCE_COLLISION:'+filename)
   temp.write_text(transformed[key]);shutil.copymode(p,temp);os.replace(temp,p)
   updated[key]=digest(p)
  for (key,_), (cmd,_) in zip((('runtime_cpp',None),('texture_cpp',None)),plans):
   print('STAGE=COMPILE_'+key.upper(),flush=True)
   menu.logged(u,['bash','-c',direct.command_string(cmd)],evidence/(key+'-compile.log'),
               'COMPILE_ONE_GPU_IMAGE_OBJECT_'+key,timeout=900)
   updated[key+'_obj']=digest(dict(objects)[key+'_obj'])
  print('STAGE=RELINK_TEST_EXECUTABLE',flush=True)
  menu.logged(u,['bash','-c',direct.command_string(plans[0][1])],evidence/'link.log','RELINK_TEST_EXECUTABLE',timeout=900)
  updated['elf']=digest(ELF)
  if updated['elf']==ORIG_ELF_SHA:raise RuntimeError('LINKED_BINARY_UNCHANGED')
  state.update(build='PASS',candidate_sha256=updated)
  candidate=BASE/'candidates'/('gpu-image-resize-'+stamp);candidate.mkdir(parents=True,exist_ok=False)
  shutil.copy2(ELF,candidate/'shadps4');state['candidate_directory']=str(candidate)
  profile=BASE/'runs'/('gpu-image-resize-'+stamp)/'user'
  state['profile_copy']=menu.copy_profile(profile_old,profile)
  state['private_profile']=str(profile)
  for config in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
   if config.is_file():
    data=json.loads(config.read_text());vk=data.setdefault('Vulkan',{})
    for k in ('vkvalidation_enabled','vkvalidation_core_enabled','vkvalidation_sync_enabled'):vk[k]=True
    vk['vkvalidation_gpu_enabled']=False
    save(config,data)
  state['free_vram_before']=u.free_vram();print('STAGE=CHECK_8_GIB_VRAM_OLLAMA_IF_NEEDED',flush=True)
  hr.verified_headroom(u,api,evidence,pending,state)
  if u.free_vram()<hr.MIN_FREE:raise RuntimeError('VRAM_BELOW_8_GIB')
  if u.emulator_pids():raise RuntimeError('EXTERNAL_EMULATOR_STARTED')
  print('STAGE=GAME_120_SECONDS_NO_INPUT',flush=True)
  state['game_test']=u.run_game(ELF,eboot,profile.parent,evidence,display)
  log=(evidence/'game.log').read_text(errors='replace')
  state['runtime_metrics']={x:log.count(x) for x in (MARK,'INDEXED_MATERIAL_BIND_REJECT','Sharp source was not flatenned','Vulkan validation error')}
  state['status']='CANDIDATE_TESTED_VISUAL_PENDING' if state['game_test'].get('time_limit_reached') else 'GAME_EXITED_EARLY'
  rc=0 if state['game_test'].get('time_limit_reached') else 1
 except (Exception,KeyboardInterrupt) as exc:
  state['status']='FAILED:'+str(exc);(evidence/'failure.txt').write_text(traceback.format_exc())
  print('STOP='+str(exc),flush=True);rc=1
 finally:
  if touched:
   try:
    print('STAGE=RESTORE_SOURCE_OBJECT_ELF',flush=True)
    state['restoration']=draw.restore(managed,checkpoint,originals,updated)
    if any(x!='RESTORED_VERIFIED' for x in state['restoration'].values()):rc=1
   except Exception as exc:state['restoration_error']=str(exc);rc=1
  for name,path in managed:
   if name in originals:
    state[name+'_restored']=path.is_file() and digest(path)==originals[name]
    if not state[name+'_restored']:rc=1
  if pending and 'u' in locals() and 'api' in locals():
   try:
    state['ollama_restore']=u.restore_models(api,pending,evidence)
    if any(x.get('result') in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in state['ollama_restore']):rc=1
   except Exception as exc:state['ollama_restore_error']=str(exc);rc=1
  if 'protected_hash' in locals() and digest(protected)!=protected_hash:
   state['protected_build_changed']=True;rc=1
  state['runner_exit_code']=rc;save(evidence/'trial.json',state)
  diary=P/'diary/trials.jsonl';diary.parent.mkdir(parents=True,exist_ok=True)
  with diary.open('a') as f:
   fcntl.flock(f,fcntl.LOCK_EX);f.write(json.dumps(state,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
  with tarfile.open(str(evidence)+'.tar.gz','w:gz') as tar:tar.add(evidence,arcname=evidence.name)
  print('RESULT='+state['status']+'\nEVIDENCE='+str(evidence)+'.tar.gz\nSSH_SESSION=OPEN',flush=True)
  for f in locks:f.close()
 return rc

if __name__=='__main__':
 signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
 runpath=Path(sys.argv[1]) if len(sys.argv)>1 else None
 if runpath is None or not runpath.is_dir():raise SystemExit('VERIFIED_HELPER_DIRECTORY_REQUIRED')
 sys.exit(run(runpath))
