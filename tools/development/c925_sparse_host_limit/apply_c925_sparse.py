#!/usr/bin/env python3
"""Adapt existing PR109 to retained C925; never truncate guest memory.
Donor methods and planner are verified against the immutable shared patch.
"""
from __future__ import annotations
import difflib
import hashlib
import json
from pathlib import Path
import re
import donor_fragments as donor

CPP='src/video_core/buffer_cache/buffer_cache.cpp'
HDR='src/video_core/buffer_cache/buffer_cache.h'
BUF='src/video_core/buffer_cache/buffer.cpp'
INC='src/video_core/renderer_vulkan/vk_instance.cpp'
INH='src/video_core/renderer_vulkan/vk_instance.h'
NEW='src/video_core/buffer_cache/sparse_arena_window.h'
FILES=(CPP,HDR,BUF,INC,INH)
EXPECTED={CPP:'9e3f5d8a54ae8dd7496beaf4a4c9a9ba7362349daf18a9d46b89205ee4caaa5e',
 HDR:'6fd573cf0f6fb2dccd819a752ee34c1b8ec924831cf93c5b60d5d8d417599c9d',
 BUF:'b88b470318b5352edd2f63a097b9b60baf239c6e4fc2abf0904ef38854e70466',
 INC:'b26369863680ab25b8df235271d6fe3da613c32ecb8e41e0bb7850f61a3071c9'}
PATCH_BLOB='078127d4c82dd44631f4437267656ad504fc2cc6'
FIXTURE_BLOB='b069b44591ca557c20c0728e8fddcdb404f538e9'

def blob(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def sha(data):return hashlib.sha256(data).hexdigest()
def once(text,old,new):
 if text.count(old)!=1:raise ValueError('CONTEXT_MISMATCH:'+old[:90])
 return text.replace(old,new,1)
def function(text,signature):
 if text.count(signature)!=1:raise ValueError('METHOD_CONTEXT:'+signature)
 a=text.index(signature);b=text.index('{',a);depth=1;i=b+1
 while i<len(text) and depth:
  depth+=(text[i]=='{')-(text[i]=='}');i+=1
 if depth:raise ValueError('METHOD_BRACES:'+signature)
 return text[a:i]
def side(patch,path,new):
 active=False;in_hunk=False;parts=[]
 for line in patch.splitlines():
  if line.startswith('--- '):active=False;in_hunk=False
  elif line.startswith('+++ '):active=line[4:]==('b/'+path);in_hunk=False
  elif active and line.startswith('@@'):
   in_hunk=True;parts.append('\n')
  elif active and in_hunk and line and line[0] in (' ', '+' if new else '-'):
   parts.append(line[1:]+'\n')
 return ''.join(parts)
def norm(s):return '\n'.join(x.rstrip() for x in s.strip().splitlines())
def verify_donor(patch):
 if blob(patch)!=PATCH_BLOB:raise ValueError('SHARED_PR109_PATCH_HASH_MISMATCH')
 text=patch.decode();after=side(text,CPP,True)
 for signature,value in [('const Buffer* BufferCache::GetArena(',donor.GET),
   ('void BufferCache::BindBackingToArena(',donor.BIND),
   ('void BufferCache::SynchronizeSparseAliases()',donor.SYNC)]:
  if norm(function(after,signature))!=norm(value):raise ValueError('DONOR_METHOD_CHANGED:'+signature)
 if norm(side(text,NEW,True))!=norm(donor.PLAN):raise ValueError('DONOR_PLAN_CHANGED')
 return True

def transform(original):
 for p,want in EXPECTED.items():
  if sha(original[p])!=want:raise ValueError('SOURCE_PREIMAGE_CHANGED:'+p)
 out={p:v.decode() for p,v in original.items()}
 c=out[CPP]
 c=once(c,'#include "video_core/buffer_cache/region_definitions.h"',
  '#include "video_core/buffer_cache/region_definitions.h"\n#include "video_core/buffer_cache/sparse_arena_window.h"')
 c=once(c,'    const vk::BufferCreateInfo probe_ci = {',
  '    max_buffer_size = instance.MaxBufferSize();\n'
  '    ASSERT_MSG(max_buffer_size != 0, "Device reports no supported VkBuffer size");\n'
  '    vk::BufferCreateInfo probe_ci = {')
 c=once(c,'        .size = ARENA_PAGE_SIZE,','        .size = std::min(ARENA_PAGE_SIZE, max_buffer_size),')
 c=once(c,'    const vk::DeviceBufferMemoryRequirements req_info = {',
  '    if (instance.IsSparseResidencyAliasedSupported()) {\n'
  '        probe_ci.flags |= vk::BufferCreateFlagBits::eSparseAliased;\n    }\n'
  '    const vk::DeviceBufferMemoryRequirements req_info = {')
 anchor='    blocks_per_arena_page_shift = ARENA_PAGE_BITS - block_shift;'
 c=once(c,anchor,anchor+'\n    ASSERT_MSG(max_buffer_size >= block_size, "Sparse block exceeds device buffer size limit");\n'
  '    LOG_INFO(Render_Vulkan, "SparseArenaLimits: max_buffer_size={} block_size={} aliased={}",\n'
  '             max_buffer_size, block_size, instance.IsSparseResidencyAliasedSupported());')
 c=once(c,'void BufferCache::SynchronizeDmaBuffers() {\n    fault_process_pending = true;',
  'void BufferCache::SynchronizeDmaBuffers() {\n    fault_process_pending = true;\n    SynchronizeSparseAliases();')
 old_get=function(c,'const Buffer* BufferCache::GetArena(')
 c=once(c,old_get,donor.BIND+'\n\n'+donor.SYNC+'\n\n'+donor.GET)
 c=once(c,'.allocationSize = resident_blocks << block_shift,','.allocationSize = u64{resident_blocks} << block_shift,')
 c=once(c,'    ArenaBinds* binds = BindsForArena(arena);\n','')
 c=once(c,'        backing.offset = memory_offset >> block_shift;',
  '        backing.offset = memory_offset >> block_shift;\n        backing.owner = arena;')
 c=once(c,'        const auto& bind = binds->binds.emplace_back(vk::SparseMemoryBind{',
  '        const vk::SparseMemoryBind bind{')
 c=once(c,'            .memoryOffset = memory_offset,\n        });\n        memory_offset += bind.size;',
  '            .memoryOffset = memory_offset,\n        };\n'
  '        for (const auto& view : arenas) {\n            BindBackingToArena(&view, backing);\n        }\n'
  '        memory_offset += bind.size;')
 c=once(c,'        for (u32 block = 0; block < bind.size; block += block_size) {',
  '        for (u64 block = 0; block < bind.size; block += block_size) {')
 out[CPP]=c
 h=out[HDR]
 h=once(h,'                const u64 page = block >> blocks_per_arena_page_shift;\n'
  '                ASSERT(page < address_space.size());\n                const auto* arena = address_space[page];',
  '                // C925 exposes this iterator to other DMA users too. Keep the\n'
  '                // original BDA owner rather than a replaceable page hint.\n'
  '                const auto* arena = range.owner;')
 h=once(h,'    std::array<const Buffer*, NUM_ARENA_PAGES> address_space{};',
  '    // Lookup hints only: a legal host view may be smaller than a guest page.\n'
  '    std::array<const Buffer*, NUM_ARENA_PAGES> address_space{};')
 h=once(h,'        u64 offset;\n        constexpr bool CanMergeWith',
  '        u64 offset;\n        const Buffer* owner{}; // Stable original BDA/view for this backing.\n        constexpr bool CanMergeWith')
 h=once(h,'            return memory == other.memory && offset + (end - start) == other.offset;',
  '            return memory == other.memory && owner == other.owner &&\n'
  '                   offset + (end - start) == other.offset;')
 h=once(h,'            return {{a, b}, memory, offset + (a - start)};',
  '            return {{a, b}, memory, offset + (a - start), owner};')
 h=once(h,'    IntervalList<Backing> resident_ranges;',
  '    IntervalList<Backing> resident_ranges;\n\n'
  '    void BindBackingToArena(const Buffer* arena, const Backing& backing);\n'
  '    void SynchronizeSparseAliases();\n    bool has_sparse_aliases{};\n    u64 max_buffer_size{};')
 out[HDR]=h
 out[BUF]=once(out[BUF],'    VmaAllocationInfo alloc_info{};\n    buffer.Create(buffer_ci, mem_type, &alloc_info);',
  '    if (mem_type == MemoryType::Sparse && instance.IsSparseResidencyAliasedSupported()) {\n'
  '        buffer_ci.flags |= vk::BufferCreateFlagBits::eSparseAliased;\n    }\n'
  '    VmaAllocationInfo alloc_info{};\n    buffer.Create(buffer_ci, mem_type, &alloc_info);')
 out[INC]=once(out[INC],'                .sparseResidencyBuffer = features.sparseResidencyBuffer,',
  '                .sparseResidencyBuffer = features.sparseResidencyBuffer,\n'
  '                .sparseResidencyAliased = features.sparseResidencyAliased,')
 # The full host header was not archived; require this exact reviewed method,
 # no replacement of the rest of that header or assumption of its full hash.
 getter='    vk::DeviceSize MaxMemoryAllocationSize() const {\n        return vk11_props.maxMemoryAllocationSize;\n    }'
 if 'MaxBufferSize()' in out[INH] or 'IsSparseResidencyAliasedSupported()' in out[INH]:
  raise ValueError('INSTANCE_ACCESSOR_ALREADY_PRESENT')
 out[INH]=once(out[INH],getter,getter+'\n\n'
  '    /// Maximum legal VkBuffer size, including sparse virtual buffers.\n'
  '    vk::DeviceSize MaxBufferSize() const noexcept {\n        return vk13_props.maxBufferSize;\n    }\n\n'
  '    bool IsSparseResidencyAliasedSupported() const noexcept {\n        return features.sparseResidencyAliased;\n    }')
 out[NEW]=donor.PLAN
 return {p:v.encode() for p,v in out.items()}

def fixture(original,corrected,template,baseline=False):
 if blob(template.encode())!=FIXTURE_BLOB:raise ValueError('SHARED_FIXTURE_HASH_MISMATCH')
 h=corrected[HDR].decode();c=corrected[CPP].decode()
 backing=function(h,'    struct Backing : public Interval')+';'
 iterator=function(h,'    template <typename Func>\n    void ForEachDmaBuffer')
 methods=[]
 for s in ['void BufferCache::SynchronizeDmaBuffers()', 'void BufferCache::BindBackingToArena(',
           'void BufferCache::SynchronizeSparseAliases()', 'const Buffer* BufferCache::GetArena(',
           'void BufferCache::EnsureResident(']:
  text=original[CPP].decode() if baseline and s.startswith('const Buffer*') else c
  methods.append(function(text,s))
 t=once(template,'/* EXACT_BACKING */',backing)
 t=once(t,'/* EXACT_METHODS */','\n\n'.join(methods))
 t=once(t,'#include <array>','#include <array>\n#include <atomic>')
 t=once(t,'namespace Common {template<class T>T AlignDownPow2(T a,T n){return a&~(n-1);}}',
  'namespace Common {template<class T>T AlignDownPow2(T a,T n){return a&~(n-1);}\n'
  'inline bool ShouldReportRepeatedEvent(std::atomic<u64>& reports,u64& event){event=reports.fetch_add(1);return event<4;} }')
 t=once(t,' u64 BufferDeviceAddress()const{return bda;}',
  ' u64 BufferDeviceAddress()const{return bda;}\n u64 CpuAddr()const{return cpu_addr;}\n'
  ' u64 SizeBytes()const{return size_bytes;}\n u64 Offset(u64 address)const{return address-cpu_addr;}')
 t=once(t,' void SynchronizeDmaBuffers();',' void SynchronizeDmaBuffers();\n'+iterator)
 return t

def prepare(root,evidence,patch):
 verify_donor(patch)
 original={p:(root/p).read_bytes() for p in FILES}
 if (root/NEW).exists():raise ValueError('NEW_HEADER_COLLISION')
 evidence.mkdir(parents=True,exist_ok=True)
 for rel,body in original.items():
  target=evidence/'source-before'/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
 corrected=transform(original)
 manifest={};diff=[]
 for p,data in corrected.items():
  for tag,body in [('source-before',original.get(p)),('source-after',data)]:
   if body is None:continue
   dst=evidence/tag/p;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(body)
  manifest[p]={'before':sha(original[p]) if p in original else None,'after':sha(data)}
  diff.extend(difflib.unified_diff(original.get(p,b'').decode().splitlines(True),data.decode().splitlines(True),'a/'+p if p in original else '/dev/null','b/'+p))
 (evidence/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 (evidence/'c925-pr109-adaptation.patch').write_text(''.join(diff))
 return original,corrected,manifest

def install(root,original,corrected):
 for p,data in original.items():
  if (root/p).read_bytes()!=data:raise ValueError('SOURCE_CHANGED_BEFORE_WRITE:'+p)
 if (root/NEW).exists():raise ValueError('NEW_HEADER_COLLISION')
 for p,data in corrected.items():
  dst=root/p;pending=dst.with_name(dst.name+'.c925-sparse-pending');pending.write_bytes(data);pending.replace(dst)
