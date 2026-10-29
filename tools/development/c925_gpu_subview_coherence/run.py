#!/usr/bin/env python3
"""C925: preserve GPU-produced same-base smaller image views before sampling.

General source-correctness experiment derived from upstream shadPS4 PRs #4770
and #4771; reuses the proven one-object direct-Ninja game runner and rollback.
Never fabricate missing T#/S# records or change guest-selected indices.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT=Path.home()/'projects/shadps4'
BASE=ROOT/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
SRC=BASE/'source/src/video_core/texture_cache/texture_cache.cpp'
PROVEN='89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
MATCHED='got-c925-image-resize-matched-ab-20261029T184438567451Z'
RESIZED='got-c925-gpu-image-resize-20261029T181927653139Z'
MARK='GpuRenderedSubviewRefresh'

def require(ok,reason):
    if not ok: raise RuntimeError(reason)

def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def replace_one(s,old,new,tag):
    count=s.count(old)
    require(count==1,'SOURCE_GATE_'+tag+'_COUNT_'+str(count))
    return s.replace(old,new,1)

def patch(src):
    if MARK in src or 'same_base_small_gpu_subview' in src:
        raise RuntimeError('SUBVIEW_FIX_ALREADY_PRESENT')
    begin='std::tuple<ImageId, int, int> TextureCache::ResolveOverlap('
    end='ImageId TextureCache::ExpandImage('
    find='ImageId TextureCache::FindImage(ImageDesc& desc, bool exact_fmt) {'
    end_find='ImageId TextureCache::FindImageFromRange('
    require(all(src.count(x)==1 for x in (begin,end,find,end_find)),
            'EXPECTED_TEXTURE_CACHE_FUNCTIONS_CHANGED')
    a=src.index(begin);b=src.index(end,a);c=src.index(find,b);d=src.index(end_find,c)
    overlap=src[a:b]
    lookup=src[c:d]
    anchor=('        const u32 rhs_block_size = '
            'cache_image.info.num_bits * cache_image.info.num_samples;')
    gate=('''        // Same-address, identical-row-layout subviews are independent Vulkan images.
        const bool same_base_small_gpu_subview =
            image_info.type == AmdGpu::ImageType::Color2D &&
            cache_image.info.type == AmdGpu::ImageType::Color2D &&
            image_info.num_samples == 1 && cache_image.info.num_samples == 1 &&
            image_info.resources.levels == 1 && cache_image.info.resources.levels == 1 &&
            image_info.resources.layers == 1 && cache_image.info.resources.layers == 1 &&
            !image_info.props.is_block && !cache_image.info.props.is_block &&
            !image_info.props.is_depth && !cache_image.info.props.is_depth &&
            image_info.pitch == cache_image.info.pitch &&
            image_info.tile_mode == cache_image.info.tile_mode &&
            image_info.pixel_format == cache_image.info.pixel_format &&
            lhs_block_size == rhs_block_size;
''')
    overlap=replace_one(overlap,anchor,anchor+'\n'+gate,'ROW_LAYOUT_PREDICATE')
    overlap=replace_one(
        overlap,
        'if (image_info.BlockDim() != cache_image.info.BlockDim() ||\n'
        '            lhs_block_size != rhs_block_size) {',
        'if (!same_base_small_gpu_subview &&\n'
        '            (image_info.BlockDim() != cache_image.info.BlockDim() ||\n'
        '             lhs_block_size != rhs_block_size)) {',
        'ROW_LAYOUT_EARLY_REFUSAL')
    branch='''        // A smaller sampled view must keep its own dimensions. The GPU-produced
        // container stays resident; the separate view is refreshed on lookup.
        if (same_base_small_gpu_subview &&
            image_info.guest_size < cache_image.info.guest_size &&
            image_info.size.width <= cache_image.info.size.width &&
            image_info.size.height <= cache_image.info.size.height) {
            return {merged_image_id, -1, -1};
        }

'''
    anchor='        // Size and resources are less than or equal, use image view.'
    overlap=replace_one(overlap,anchor,branch+anchor,'PRESERVE_SMALLER_VIEW')
    grow='''        // A same-row-layout larger request can preserve the existing rendered
        // subset using the established ExpandImage/CopyImage path.
        if (same_base_small_gpu_subview &&
            image_info.guest_size > cache_image.info.guest_size &&
            image_info.size.width >= cache_image.info.size.width &&
            image_info.size.height >= cache_image.info.size.height) {
            return {ExpandImage(image_info, cache_image_id), -1, -1};
        }

'''
    anchor='        // Size is greater but resources are not, because the tiling mode is different.'
    overlap=replace_one(overlap,anchor,grow+anchor,'EXPAND_LARGER_VIEW')

    insert='''    // Recover GPU-only pixels of a distinct sampled sub-view before it is bound.
    // No upload from stale guest CPU memory and no guessed descriptor lengths.
    if (desc.type == BindingType::Texture) {
        auto& view = slot_images[image_id];
        if (view.info.type == AmdGpu::ImageType::Color2D &&
            !view.info.props.is_depth && !view.info.props.is_block &&
            view.info.num_samples == 1 && view.info.resources.levels == 1 &&
            view.info.resources.layers == 1 &&
            False(view.flags & ImageFlagBits::GpuModified)) {
            ImageId source{};
            ForEachImageInRegion(view.info.guest_address, 1,
                                 [&](ImageId cid, Image& cached) {
                if (source || cid == image_id || cached.backing == view.backing ||
                    cached.info.guest_address != view.info.guest_address ||
                    cached.info.guest_size <= view.info.guest_size ||
                    cached.info.type != view.info.type ||
                    cached.info.pixel_format != view.info.pixel_format ||
                    cached.info.tile_mode != view.info.tile_mode ||
                    cached.info.pitch != view.info.pitch ||
                    cached.info.num_samples != 1 ||
                    cached.info.resources.levels != 1 ||
                    cached.info.resources.layers != 1 ||
                    cached.info.props.is_block || cached.info.props.is_depth ||
                    cached.info.size.width < view.info.size.width ||
                    cached.info.size.height < view.info.size.height ||
                    False(cached.flags & ImageFlagBits::GpuModified) ||
                    True(cached.flags & ImageFlagBits::Dirty)) {
                    return;
                }
                source = cid;
            });
            if (source) {
                auto& container = slot_images[source];
                // CopyMip already crops to the common texel extent and applies
                // valid Vulkan transfer layouts/barriers. No new runtime API.
                runtime.CopyMip(&container, &view, 0, 0);
                // This is a derived sample view, not an independently authored
                // render target: refresh again after the container is redrawn.
                view.flags &= ~ImageFlagBits::GpuModified;
                view.flags &= ~ImageFlagBits::Dirty;
                static std::atomic<u32> visible{};
                const u32 n = visible.fetch_add(1, std::memory_order_relaxed) + 1;
                if (n <= 8 || std::has_single_bit(n)) {
                    LOG_INFO(Render_Vulkan,
                             "GpuRenderedSubviewRefresh event={} guest={:#x} "
                             "source={}x{} destination={}x{}",
                             n, view.info.guest_address,
                             container.info.size.width, container.info.size.height,
                             view.info.size.width, view.info.size.height);
                }
            }
        }
    }
'''
    target='''    Image& image = slot_images[image_id];
    image.tick_accessed_last = scheduler.CurrentTick();'''
    lookup=replace_one(lookup,target,insert+'\n'+target,'SAMPLED_VIEW_REFRESH')
    result=src[:a]+overlap+src[b:c]+lookup+src[d:]
    for h in ('#include <atomic>','#include <bit>'):
        if h not in result:
            result=replace_one(result,'#include <xxhash.h>',h+'\n#include <xxhash.h>','HEADER_'+h)
    require(result.count(MARK)==1 and result.count('same_base_small_gpu_subview')==5,
            'PATCH_COMPLETENESS_GATE')
    return result

def unit():
    source='''#include <xxhash.h>
std::tuple<ImageId, int, int> TextureCache::ResolveOverlap(
  const ImageInfo& image_info, BindingType binding, ImageId cache_image_id, ImageId merged_image_id) {
  const u32 lhs_block_size = image_info.num_bits * image_info.num_samples;
        const u32 rhs_block_size = cache_image.info.num_bits * cache_image.info.num_samples;
        if (image_info.BlockDim() != cache_image.info.BlockDim() ||
            lhs_block_size != rhs_block_size) { return {}; }
        // Size and resources are less than or equal, use image view.
        // Size is greater but resources are not, because the tiling mode is different.
}
ImageId TextureCache::ExpandImage(const ImageInfo& info, ImageId image_id) {}
ImageId TextureCache::FindImage(ImageDesc& desc, bool exact_fmt) {
    Image& image = slot_images[image_id];
    image.tick_accessed_last = scheduler.CurrentTick();
}
ImageId TextureCache::FindImageFromRange(VAddr a, size_t b) {}
'''
    out=patch(source)
    require('runtime.CopyMip(&container, &view, 0, 0);' in out and
            out.count('GpuRenderedSubviewRefresh event=')==1 and
            '#include <atomic>' in out and '#include <bit>' in out,
            'SOURCE_FIXTURE_RESULT_INVALID')
    try:patch(out)
    except RuntimeError as e:require('ALREADY_PRESENT' in str(e),'DOUBLE_PATCH_WRONG_REFUSAL')
    else:raise RuntimeError('DOUBLE_PATCH_ACCEPTED')
    print('SMALLER_VIEW_AND_GPU_CROP_SOURCE_FIXTURE=PASS',flush=True)

def main():
    unit()
    require(SRC.is_file() and not SRC.is_symlink(),'C925_SOURCE_MISSING')
    orig=SRC.read_text()
    changed=patch(orig)
    require(changed != orig,'NO_CORRECTION_APPLIED')
    latest=json.loads((ROOT/'logs'/MATCHED/'trial.json').read_text())
    require(latest.get('status')=='CANDIDATE_VISIBLE_WITH_ORIGINAL_VALIDATION_SETTINGS',
            'MATCHED_VULKAN_AB_NOT_VERIFIED')
    require(all(not latest['runs'][label].get('all_captures_tiny') and
                latest['runs'][label]['game'].get('time_limit_reached')
                for label in ('proven','resize_candidate')),
            'MATCHED_AB_PROFILE_OR_GAME_INVALID')
    cand=json.loads((ROOT/'logs'/RESIZED/'trial.json').read_text())
    protected=Path(cand['candidate_directory'])/'shadps4'
    require(protected.is_file() and not protected.is_symlink() and
            sha(protected)==cand['candidate_sha256']['elf'],
            'PR5303_RESIZE_CANDIDATE_CHANGED')
    verified=sha(protected)
    selected=ROOT/'scripts/got-texture-overlap.Xah6oZUt/run.py'
    require(selected.is_file() and not selected.is_symlink(),'KNOWN_REVERSIBLE_RUNNER_MISSING')
    s=selected.read_text()
    for needle in ('def run(work):','RETAIN_CANDIDATE_RESTORE_BASELINE',
                   'texture-overlap-fix-','PROVEN_ELF_HASH_CHANGED',
                   'def helper(directory,name):'):
        require(needle in s,'RUNNER_VERSION_CHANGED:'+needle)
    for old,new in (
        ('Componentwise mip/layer image-containment and incompatible-overlap correction',
         'Preserve same-base smaller sampled GPU-rendered image views'),
        ('https://github.com/shadps4-emu/shadPS4/pull/5457',
         'https://github.com/shadps4-emu/shadPS4/pull/4770'),
        ('texture-overlap-fix-','gpu-subview-coherence-')
    ):
        require(old in s,'RUNNER_METADATA_CHANGED:'+old)
        s=s.replace(old,new)
    ns={'__name__':'got_c925_gpu_subview','__file__':str(selected)}
    exec(compile(s,str(selected),'exec'),ns)
    require(ns['SRC'].resolve()==SRC.resolve() and ns['BASE_SHA']==PROVEN,
            'BASELINE_RUNNER_REPLACED')
    ns['patch']=patch;ns['unit']=unit
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    diary=ROOT/'diary/trials.jsonl';diary.parent.mkdir(parents=True,exist_ok=True)
    entry=dict(timestamp=now,journal_scope='UNIVERSAL_SHADPS4',test='GPU image resize matched Vulkan A/B',
               evidence=MATCHED+'.tar.gz',status='VISIBLE_BOTH_MATCHED_SETTINGS',
               candidate_resize_events=0,keep_or_revert='KEEP_ISOLATED_VALID_CORRECTION_NO_ELF_PROMOTION',
               note='Neither build rendered missing katana material; no freeze inferred from identical menu frames')
    with diary.open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX);f.write(json.dumps(entry)+'\n');f.flush();sys.stdout.flush()
    print('PRESERVED_RESIZE_CANDIDATE='+verified,flush=True)
    print('GPU_SUBVIEW_PATCH_PREFLIGHT=PASS',flush=True)
    print('BUILD=ONE_TEXTURE_CACHE_OBJECT_RELINK',flush=True)
    print('VRAM=MINIMUM_8_GIB_CONDITIONAL_OLLAMA_RESTORE',flush=True)
    print('GAME=GOT_MENU_118_PLUS_2_SECONDS_NO_INPUT',flush=True)
    print('RESTORE=ORIGINAL_SOURCE_OBJECT_ELF',flush=True)
    result=ns['run'](selected.parent)
    require(sha(protected)==verified,'PROTECTED_PR5303_CANDIDATE_MODIFIED')
    return result

if __name__=='__main__':sys.exit(main())
