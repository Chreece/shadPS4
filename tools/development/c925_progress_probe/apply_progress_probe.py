#!/usr/bin/env python3
"""Exact-source temporary telemetry; no shader/resource/render-state correction."""
from pathlib import Path
import difflib, hashlib, json, re, sys
NEW = 'src/common/execution_progress_probe.h'
EXPECTED = {
 'src/video_core/renderer_vulkan/vk_scheduler.cpp':'c3658281c244774e253d23661dba143db97af53051c97ea7a8912fb7c8ca7e3b',
 'src/video_core/renderer_vulkan/vk_semaphore.cpp':'137df18254306406df3162cf2b4ba5fd548cfb20fd7b2a02696ba61ba68fec14',
 'src/video_core/renderer_vulkan/vk_rasterizer.cpp':'ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0',
 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp':'12864f611064779142a1a8060382c561783d776406d9756a0516403691e44f89',
 'src/video_core/renderer_vulkan/vk_presenter.cpp':'bb5a5e2942430f2350c4851a1dddbc2884037b0037e77004703a30c40982fc4b',
 'src/core/libraries/videoout/driver.cpp':'ca11ca9879f765793c4c15ea5db6f4fa85bb7897e009182201b9917b1e45fdf1',
 'src/video_core/buffer_cache/buffer_cache.cpp':'9e3f5d8a54ae8dd7496beaf4a4c9a9ba7362349daf18a9d46b89205ee4caaa5e',
 'src/video_core/texture_cache/texture_cache.cpp':'4daf189a752d09f035a7ac4012db2fd8c2306c13faea9871cbe9a3957f733f58',
}
def sha(b): return hashlib.sha256(b).hexdigest()
def once(s,a,b):
 if s.count(a)!=1: raise ValueError('UNIQUE_CONTEXT_FAILED:'+a[:90])
 return s.replace(a,b,1)
def scope(s,method,phase,args=''):
 pattern=r'(^[^\n;{}]*\b'+re.escape(method)+r'\([^;{}]*?\)(?: const)?\s*\{)'
 matches=list(re.finditer(pattern,s,re.M))
 if len(matches)!=1: raise ValueError('FUNCTION_CONTEXT_FAILED:'+method+':'+str(len(matches)))
 m=matches[0]
 addition='\n    ExecutionProgress::Scope progress_scope{ExecutionProgress::Phase::'+phase+args+'};'
 return s[:m.end()]+addition+s[m.end():]
def transform(original):
 out={}
 for path,h in EXPECTED.items():
  b=original[path]
  if sha(b)!=h: raise ValueError('SOURCE_CHANGED_NO_WRITE:'+path)
  s=b.decode(); pos=s.index('#include')
  s=s[:pos]+'#include "common/execution_progress_probe.h"\n'+s[pos:]
  if path.endswith('vk_scheduler.cpp'):
   s=once(s,'    BeginSession();\n    priority_pending_ops_thread', '    ExecutionProgress::Start();\n    BeginSession();\n    priority_pending_ops_thread')
   s=scope(s,'Scheduler::SubmitExecution','Submit',', reinterpret_cast<std::uintptr_t>(&work_semaphore), CurrentTick()')
  elif path.endswith('vk_semaphore.cpp'):
   s=scope(s,'Semaphore::Wait','TimelineWait',', reinterpret_cast<std::uintptr_t>(this), tick, KnownGpuTick(), 0')
   a='''    while (instance.GetDevice().waitSemaphores(&wait_info, WAIT_TIMEOUT) != vk::Result::eSuccess) {
    }
    Refresh();'''
   b='''    // Diagnostic only: timeout polling observes actual progress; it never
    // signals the semaphore, reduces the target, or treats timeout as completion.
    const bool probing = ExecutionProgress::Enabled();
    for (;;) {
        const auto result = instance.GetDevice().waitSemaphores(
            &wait_info, probing ? 250000000ULL : WAIT_TIMEOUT);
        if (result == vk::Result::eSuccess) {
            break;
        }
        if (probing) {
            if (result == vk::Result::eTimeout) {
                Refresh(); // Real Vulkan counter query, not a synthesized tick.
            }
            progress_scope.Update(reinterpret_cast<std::uintptr_t>(this), tick,
                                  KnownGpuTick(), static_cast<std::uint64_t>(
                                      static_cast<std::int64_t>(result)));
        }
        // Preserve the existing non-success retry policy; no new recovery path.
    }
    Refresh();'''
   s=once(s,a,b)
  elif path.endswith('vk_rasterizer.cpp'):
   for name,phase in [('Draw','Draw'),('DrawIndirect','DrawIndirect'),('DispatchDirect','Dispatch'),('DispatchIndirect','DispatchIndirect')]:
    s=scope(s,'Rasterizer::'+name,phase)
  elif path.endswith('vk_pipeline_cache.cpp'):
   s=scope(s,'PipelineCache::GetProgram','ShaderLookup',', params.hash, static_cast<std::uint64_t>(sw_stage)')
   s=scope(s,'PipelineCache::GetComputePipeline','ComputePipeline')
   s=scope(s,'PipelineCache::GetGraphicsPipeline','GraphicsPipeline')
  elif path.endswith('vk_presenter.cpp'):
   s=scope(s,'Presenter::Present','Presenter',', reinterpret_cast<std::uintptr_t>(frame), is_reusing_frame, is_game_frame')
   s=scope(s,'Presenter::GetRenderFrame','RenderFrame')
  elif path.endswith('driver.cpp'):
   s=scope(s,'VideoOutDriver::SubmitFlip','FlipRequest',', reinterpret_cast<std::uintptr_t>(port), static_cast<std::uint64_t>(index), is_eop')
   s=scope(s,'VideoOutDriver::SubmitFlipInternal','FlipPrepare',', reinterpret_cast<std::uintptr_t>(port), static_cast<std::uint64_t>(index), is_eop')
   s=scope(s,'VideoOutDriver::Flip','FlipPresent',', reinterpret_cast<std::uintptr_t>(req.port), static_cast<std::uint64_t>(req.index)')
  elif path.endswith('buffer_cache.cpp'):
   s=scope(s,'BufferCache::DownloadMemory','BufferDownload',', device_addr, size, 0, async')
   s=once(s,'    runtime.CopyBuffer(arena, download.buffer, copies);','    progress_scope.Update(device_addr, size, total_size_bytes, async);\n    runtime.CopyBuffer(arena, download.buffer, copies);')
  elif path.endswith('texture_cache.cpp'):
   s=scope(s,'TextureCache::DownloadImageMemory','ImageDownload',', 0, 0, 0, static_cast<std::uint64_t>(flags)')
   s=once(s,'    tile_manager.TileImage(image, buffer_copies, download.buffer, download.offset);','    progress_scope.Update(image_addr, copy_size, download.offset, is_sync);\n    tile_manager.TileImage(image, buffer_copies, download.buffer, download.offset);')
  out[path]=s.encode()
 return out

def apply(root,evid):
 original={p:(root/p).read_bytes() for p in EXPECTED}
 if (root/NEW).exists(): raise ValueError('PROBE_HEADER_ALREADY_EXISTS_NO_WRITE')
 modified=transform(original)
 header=Path(__file__).with_name('execution_progress_probe.h').read_bytes()
 # Finish validating every old source before modifying any emulator file.
 manifest={};patch=[]
 for p,new in modified.items():
  target=evid/'source-before'/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(original[p])
  manifest[p]={'before':sha(original[p]),'after':sha(new)}
  patch.extend(difflib.unified_diff(original[p].decode().splitlines(True),new.decode().splitlines(True),'a/'+p,'b/'+p))
 manifest[NEW]={'before':None,'after':sha(header)}
 (evid/'probe.patch').write_text(''.join(patch))
 (evid/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 for p,data in {**modified,NEW:header}.items():
  target=root/p;target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_name(target.name+'.progress-pending');temp.write_bytes(data);temp.replace(target)
 return manifest
if __name__=='__main__':apply(Path(sys.argv[1]),Path(sys.argv[2]))
