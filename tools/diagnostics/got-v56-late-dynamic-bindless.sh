#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

P="$HOME/projects/shadps4/GhostOfTsushima"
TESTS="$P/tests"
LOGROOT="$HOME/projects/shadps4/logs"
V31="$TESTS/got-v31-nptoolkit-mspace-20261013"
V51="$TESTS/got-v51-stencil-reference-20261014"
V55="$TESTS/got-v55-empty-dynamic-slot-null-20261014"
V56="$TESTS/got-v56-late-dynamic-bindless-20261014"
RES='src/shader_recompiler/ir/passes/resource_patching_pass.cpp'
RAST='src/video_core/renderer_vulkan/vk_rasterizer.cpp'
SHA31='1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f'
RAST51='814677f63524610ba23932fd4ebbe062be299d641ac4699c390d62fe1cc1b03b'
DRV='87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
DRIVER="$P/tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so"
ICD="$P/tools/radv-nuw-complete-25.0.7/radeon_icd.json"
GAME='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'

sha(){ sha256sum "$1" | cut -d' ' -f1; }
fail(){ echo "STOP: $*" >&2; exit 2; }

[[ "$(id -u)" != 0 ]] || fail 'run without sudo'
if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then fail 'another shadPS4 is running'; fi
[[ -x "$V31/build/shadps4" && "$(sha "$V31/build/shadps4")" == "$SHA31" ]] || fail 'v31 build baseline changed'
[[ -x "$V55/build/shadps4" && -f "$V55/$RES" ]] || fail 'v55 build/source missing'
[[ -f "$V51/$RAST" && "$(sha "$V51/$RAST")" == "$RAST51" ]] || fail 'v51 rasterizer source changed'
[[ -f "$DRIVER" && "$(sha "$DRIVER")" == "$DRV" && -f "$ICD" ]] || fail 'private RADV changed/missing'
[[ -f "$GAME" ]] || fail 'GoT eboot missing'
[[ -f "$V55/build/compile-argv.json" && -f "$V55/build/link-argv.json" && -f "$V51/build/compile-argv.json" ]] || fail 'stored build manifests missing'
mkdir -p "$V56/$(dirname "$RES")" "$V56/$(dirname "$RAST")" "$V56/build" "$P/play" "$LOGROOT"

python3 - "$V55/$RES" "$V56/$RES" "$V51/$RAST" "$V56/$RAST" <<'PY'
from pathlib import Path
import re,sys
res_src,res_dst,rast_src,rast_dst=map(Path,sys.argv[1:])

# Resource compile-time fix: v55 proved all 53 rows can be empty at shader compile
# time. Keep the dynamic array declaration anyway, using only a typed null image
# as the SPIR-V prototype. Actual descriptors are read on every BindTextures call.
r=res_src.read_text()
old='''        if (!prototype.has_value()) {\n            LOG_WARNING(Render_Recompiler,\n                        "GOT55_GHOST_EMPTY_DYNAMIC_SLOT shader={:#x} slot={} rows_scanned=53 action=null_sampled_image",\n                        info.pgm_hash, index->slot);\n        } else {\n            image_res.ghost_dynamic_image_count = 53;\n            image_res.ghost_dynamic_image_slot = index->slot;\n            image_res.ghost_prototype_image = *prototype;\n'''
new='''        if (!prototype.has_value()) {\n            // The descriptor table can be empty while this permutation is compiled and\n            // become populated later when gameplay starts. Preserve the 53-entry array\n            // shape; the null image supplies only sampled-image type information.\n            image_res.ghost_dynamic_image_count = 53;\n            image_res.ghost_dynamic_image_slot = index->slot;\n            image_res.ghost_prototype_image = AmdGpu::Image::Null(image_res.is_depth);\n            LOG_WARNING(Render_Recompiler,\n                        "GOT56_LATE_DYNAMIC_SLOT shader={:#x} slot={} rows_scanned=53 compile_prototype=typed_null runtime_binding=enabled",\n                        info.pgm_hash, index->slot);\n        } else {\n            image_res.ghost_dynamic_image_count = 53;\n            image_res.ghost_dynamic_image_slot = index->slot;\n            image_res.ghost_prototype_image = *prototype;\n'''
if r.count(old)!=1:
    raise SystemExit('STOP: v55 resource handoff block changed')
r=r.replace(old,new,1)
if r.count('GOT56_LATE_DYNAMIC_SLOT')!=1 or r.count('GOT53_NULL_INVALID_IMAGE')!=1:
    raise SystemExit('STOP: resource patch verification failed')
res_dst.parent.mkdir(parents=True,exist_ok=True)
if res_dst.exists() and res_dst.read_text()!=r: raise SystemExit('STOP: existing v56 resource source differs')
res_dst.write_text(r)

v=rast_src.read_text()
# If an older guard nulls an entire dynamic array solely because the compile-time
# prototype is null, remove that guard. Current-row validity is checked below.
guard=re.compile(r'''\n        if \(image_desc\.ghost_dynamic_image_count &&\n            \(!tsharp\.Valid\(\) \|\| tsharp\.Address\(\) == 0 \|\|\n             !memory->IsValidGpuMapping\(tsharp\.Address\(\), 0\)\)\) \{\n            // Do not read the indirect table or rebind a prototype that points\n            // at memory that is no longer resident\.\n            bind_null_image\(\);\n            continue;\n        \}\n''')
v,n=guard.subn('\n',v,count=1)
if n not in (0,1): raise SystemExit('STOP: unexpected dynamic prototype guard count')

# Runtime-row fallback must be allowed when the compile prototype is the typed
# null. Invalid rows stay null; valid later rows remain their real guest image.
old_assert='''                    ASSERT_MSG(tsharp.Valid() && tsharp.Address() != 0,\n                               "Ghost prototype descriptor is invalid");'''
new_assert='''                    if (tsharp.Address() == 0) {\n                        ghost_dynamic_images[row] = AmdGpu::Image::Null(image_desc.is_depth);\n                        continue;\n                    }\n                    ASSERT_MSG(tsharp.Valid(), "Ghost prototype descriptor is invalid");'''
if v.count(old_assert)!=1: raise SystemExit('STOP: Ghost runtime fallback assertion changed')
v=v.replace(old_assert,new_assert,1)

# Do not reject the whole 53-entry array just because the compile-only prototype
# has address zero. For fixed resources the old checks remain exact.
old1='''        if (tsharp.Address() == 0 || data_fmt == AmdGpu::DataFormat::FormatInvalid) {'''
new1='''        if (!image_desc.ghost_dynamic_image_count &&\n            (tsharp.Address() == 0 || data_fmt == AmdGpu::DataFormat::FormatInvalid)) {'''
if v.count(old1)!=1: raise SystemExit('STOP: fixed-image null check changed')
v=v.replace(old1,new1,1)
old2='''        if (!memory->IsValidGpuMapping(tsharp.Address(), 0) ||\n            !magic_enum::enum_contains(data_fmt) || !magic_enum::enum_contains(num_fmt)) {'''
new2='''        if ((!image_desc.ghost_dynamic_image_count &&\n             !memory->IsValidGpuMapping(tsharp.Address(), 0)) ||\n            !magic_enum::enum_contains(data_fmt) || !magic_enum::enum_contains(num_fmt)) {'''
if v.count(old2)!=1: raise SystemExit('STOP: fixed-image mapping check changed')
v=v.replace(old2,new2,1)

# A late-populated table can still contain empty rows. Bind those individual
# elements as null instead of feeding an address-zero image to TextureCache.
old3='''            auto& [image_id, desc] = image_bindings[num_images++];\n            std::construct_at(&desc, selected, image_desc);'''
new3='''            auto& [image_id, desc] = image_bindings[num_images++];\n            if (image_desc.ghost_dynamic_image_count &&\n                (selected.Address() == 0 || !selected.Valid() ||\n                 !memory->IsValidGpuMapping(selected.Address(), 0))) {\n                image_id = {};\n                desc.type = image_desc.is_written\n                                ? VideoCore::TextureCache::BindingType::Storage\n                                : VideoCore::TextureCache::BindingType::Texture;\n                continue;\n            }\n            if (image_desc.ghost_dynamic_image_count && tsharp.Address() == 0) {\n                static std::atomic<u32> late_rows_logged{};\n                if (late_rows_logged.fetch_add(1, std::memory_order_relaxed) < 24) {\n                    LOG_WARNING(Render_Vulkan,\n                                "GOT56_RUNTIME_DYNAMIC_ROW shader={:#x} slot={} row={} address={:#x} data_format={} num_format={}",\n                                stage.pgm_hash, image_desc.ghost_dynamic_image_slot, i,\n                                selected.Address(), u32(selected.GetDataFmt()),\n                                u32(selected.GetNumberFmt()));\n                }\n            }\n            std::construct_at(&desc, selected, image_desc);'''
if v.count(old3)!=1: raise SystemExit('STOP: Ghost selected-row bind block changed')
v=v.replace(old3,new3,1)

for marker in ('GOT56_RUNTIME_DYNAMIC_ROW','ghost_dynamic_image_count','candidate.GetViewType(image_desc.is_array) =='):
    if marker not in v: raise SystemExit('STOP: v56 rasterizer static check failed: '+marker)
rast_dst.parent.mkdir(parents=True,exist_ok=True)
if rast_dst.exists() and rast_dst.read_text()!=v: raise SystemExit('STOP: existing v56 rasterizer source differs')
rast_dst.write_text(v)
print('SOURCE_PREFLIGHT=PASS')
PY

python3 - "$V31" "$V51" "$V55" "$V56" "$RES" "$RAST" <<'PY'
from pathlib import Path
import json,os,subprocess,sys
v31,v51,v55,v56=map(Path,sys.argv[1:5]); res=sys.argv[5]; rast=sys.argv[6]; b=v56/'build'

def redirected(argv, oldsrc, newsrc, newobj):
    argv=list(argv)
    if argv.count(oldsrc)!=1 or argv.count('-o')!=1: raise SystemExit('STOP: compile manifest source/output differs')
    oldobj=argv[argv2ææFWrÖòr³Ð¢&wcÕ¶æWw7&2bÓÖöÆG7&2VÇ6Rf÷"â&weÐ¢&we&wbææFWrÖòr³ÓÖæWvö& ¢brÔÔbrâ&wc¢&we¶&wbææFWrÔÔbr³ÓÖæWvö&¢²ræBp¢brÔÕBrâ&wc¢&we¶&wbææFWrÔÕBr³ÓÖæWvö& ¢&WGW&â&wbÆöÆFö&  §&W5ö63Ö§6öâæÆöG2cSRòv'VÆBö6ö×ÆRÖ&wbæ§6öârç&VE÷FWB§&W5öö&£×7G""òw&W6÷W&6U÷F6æu÷72æ7æòr§&W5ö62ÆöÆE÷&W5öö&£×&VF&V7FVB&W5ö62Ç7G"cSR÷&W2Ç7G"cSb÷&W2Ç&W5öö&¢ §&7Eö63Ö§6öâæÆöG2cSòv'VÆBö6ö×ÆRÖ&wbæ§6öârç&VE÷FWB§&7Eöö&£×7G""òwfµ÷&7FW&¦W"æ7æòr§&7Eö62ÆöÆE÷&7Eöö&£×&VF&V7FVB&7Eö62Ç7G"cS÷&7BÇ7G"cSb÷&7BÇ&7Eöö&¢ ¦ÆCÖ§6öâæÆöG2cSRòv'VÆBöÆæ²Ö&wbæ§6öârç&VE÷FWB¦bÆBæ6÷VçBrÖòrÓ¢&6R77FVÔWBu5Dõ¢cSRÆæ²÷WGWBFffW'2r§&W5öG3Õ¶f÷"ÇâVçVÖW&FRÆBbÓÖöÆE÷&W5öö&¢÷"æVæG7vFrô4Ö¶TfÆW2÷6G3BæF"òr·&W2²ræòrÐ§&7EöG3Õ¶f÷"ÇâVçVÖW&FRÆBbÓÖöÆE÷&7Eöö&¢÷"æVæG7vFrô4Ö¶TfÆW2÷6G3BæF"òr·&7B²ræòrÐ¦bÆVâ&W5öG2Ó÷"ÆVâ&7EöG2Ó÷"&W5öG5³ÓÓ×&7EöG5³Ó ¢&6R77FVÔWBbu5Dõ¢cSRÆæ²ö&¦V7BÖF6FffW'2&W3×·&W5öG7Ò&7C×·&7EöG7Òr¦ÆE·&W5öG5³ÕÓ×&W5öö&£²ÆE·&7EöG5³ÕÓ×&7Eöö& §VæFæs×7G""òw6G3BçVæFærr²ÆE¶ÆBææFWrÖòr³Ó×VæFæp¦ÆCÕ·ç&WÆ6R7G"cSRòv'VÆBöÆæ²æBrÇ7G""òvÆæ²æBrf÷"âÆEÐ¦f÷"æÖRÆö&¢âw&W6÷W&6RrÅF&W5öö&¢Âw&7FW&¦W"rÅF&7Eöö&¢ ¢ö&¢ç&VçBæÖ¶F"&VçG3ÕG'VRÆW7Eöö³ÕG'VR¢f÷"âö&¢ÅF7G"ö&¢²ræBr ¢bæ5÷7ÖÆæ²¢&6R77FVÔWBu5Dõ¢Vç6fRr¶æÖR²r÷WGWBr¢bæW7G2¢çVæÆæ²¦bFVæFæræW7G2¢FVæFærçVæÆæ²¦f÷"æÖRÆ&wbâw&W6÷W&6RrÇ&W5ö62Âw&7FW&¦W"rÇ&7Eö62 ¢&çBu5DtSÔ4ôÕÄUòr¶æÖRçWW"ÆfÇW6ÕG'VR¢vF"òæÖR²rÖ6ö×ÆRæÆörræ÷Vâwv"r2c ¢#×7V'&ö6W72ç'Vâ&wbÆ7vC×c3òv'VÆBrÇ7FFã×7V'&ö6W72äDUdåTÄÂÇ7FF÷WCÖbÇ7FFW'#×7V'&ö6W72å5DDõUB¢b"ç&WGW&æ6öFS¢&6R77FVÔWBu5Dõ¢r¶æÖR²r6ö×ÆRfÆVC²r·7G""òæÖR²rÖ6ö×ÆRæÆörr§&çBu5DtSÕ$TÄäµõcSUõtDõcSeôEäÔ5ô$äDärrÆfÇW6ÕG'VR§vF"òvÆæ²æÆörræ÷Vâwv"r2c ¢#×7V'&ö6W72ç'VâÆBÆ7vC×c3òv'VÆBÇ7FFã×7V'&ö6W72äDUdåTÄÂÇ7FF÷WCÖbÇ7FFW'#×7V'&ö6W72å5DDõUB¦b"ç&WGW&æ6öFS¢&6R77FVÔWBu5Dõ¢Ææ²fÆVC²r·7G""òvÆæ²æÆör¦÷WCÖ"òw6G3Bs²÷2ç&WÆ6RVæFærÆ÷WB²÷WBæ6ÖöBós¢"òv6ö×ÆR×&W6÷W&6RÖ&wbæ§6öârçw&FU÷FWB§6öâæGV×2&W5ö62ÆæFVçCÓ"²uÆâr¢"òv6ö×ÆR×&7FW&¦W"Ö&wbæ§6öârçw&FU÷FWB§6öâæGV×2&7Eö62ÆæFVçCÓ"²uÆâr¢"òvÆæ²Ö&wbæ§6öârçw&FU÷FWB§6öâæGV×2ÆBÆæFVçCÓ"²uÆâr¥ ¥4ScÒ"B6"EcSbö'VÆB÷6G3B" ¦V6ò$%TÄCÕcSeõ$TE4ÒE4Sb  ¥%TãÒ"E÷ÆöÖçVÂ×cSbÖF&V7B ¦bµ²ÖB"E%Tâ"ÕÓ²FVà¢5$5õ%TãÒrp¢f÷"â"E÷ÆöÖçVÂ×cSRÖF&V7B""E÷ÆöÖçVÂ×cSBÖF&V7B""E÷ÆöÖçVÂ×cS2ÖF&V7B""E÷ÆöÖçVÂ×cS2""E÷ÆöÖçVÂ×cS#²Fð¢bµ²ÖB"G÷W6W""bbÔÂ"G"ÕÓ²FVâ5$5õ%TãÒ"G#²'&V³²f¢FöæP¢µ²Öâ"E5$5õ%Tâ"ÕÒÇÂfÂvæò&W6W'fVBÖçVÂvõB&öfÆRf÷VæBp¢bfæB"E5$5õ%Tâ"×GRÂ×&çB×VBÂw&W×ã²FVâfÂw7ÖÆæ²â&W6W'fVBÖçVÂ&öfÆRs²f¢DÕÒ"BÖ·FV×ÖB"E÷ÆòæÖçVÂ×cSbÕ" ¢7Ö"E5$5õ%Tâòâ""EDÕò ¢&ÒÖb"EDÕ÷6G3B ¢7"EcSbö'VÆB÷6G3B""EDÕ÷6G3B ¢6ÖöBs"EDÕ÷6G3B ¢&çFbw6÷W&6U÷&öfÆSÒW5Ææ&æ'÷6#ScÒW5ÆæWFöÖF5öçWCÖfÇ6UÆâr"E5$5õ%Tâ""E4Sb"â"EDÕ÷cSb×'VçFÖRçGB ¢×b"EDÕ""E%Tâ ¦f¥µ²"B6"E%Tâ÷6G3B""ÓÒ"E4Sb"ÕÒÇÂfÂwcSb'VçFÖR&æ'Ö6ÖF6p¦bw&W×R"BB×R"×6G3BâöFWböçVÆÃ²FVâfÂvæ÷FW"6E3B2'Vææærs²f ¥5DÕÒ"BFFR²UVÒVBÒTTÒU2 ¥$Uõ%CÒ"DÄôu$ôõBöv÷B×cSbÖÖçVÂÒE5DÕ ¦Ö¶F"×"E$Uõ%B"÷¶æfòÆÆöw2Ç67&VVç6÷G2Ç&GbÖöÖRÇ7'bÖfÇW&W7Ð¥%TääU#Ò"E$Uõ%Böæfò÷v÷&¶W"ç6 ¦6Bâ"E%TääU""ÃÅ%Tà¢2÷W7"ö&âöVçb&6§6WB×VòVfÀ¦6BB&çFbrWr"E%Tâ"¦W÷'BôÔSÒB&çFbrWr"E$Uõ%B÷&GbÖöÖR"¦W÷'BdµôE$dU%ôdÄU3ÒB&çFbrWr"D4B"¦W÷'Bdµô4EôdÄTäÔU3ÒB&çFbrWr"D4B"¦W÷'B$EeôDT%TsÖærÆæ÷V× ¦W÷'BÔU4õ4DU%ô44UôD4$ÄS×G'VP¦W÷'BÔU4õ5%eôdÅôETÕõDÒB&çFbrWr"E$Uõ%B÷7'bÖfÇW&W2"¦W÷'B4E3Eôtõ5Eô$äDÄU55ôUU$ÔTåCÓ¦W÷'B4DÅô¤õ5D4µôÄÄõuô$4´u$õTäEôUdTåE3Ó¢â÷6G3BÒÖ7RÖBÖÖöFSÖæFfRÒÖvÖRB&çFbrWr"DtÔR"ÒÖgVÆÇ67&VVâG'VRâB&çFbrWr"E$Uõ%BöÆöw2övÖR×7FF÷WBæÆör"#âc`¦vÖSÕÂB¢Æ7CÓ²f÷"F&vWBâ#sR##C3C#c²FòFVÆÕÂBF&vWBÖÆ7B²6ÆVW%ÂFFVÆ#²Æ7CÕÂGF&vWC²¶ÆÂÓ%ÂFvÖR"#âöFWböçVÆÂÇÂ'&V³²vCÕÂBF÷FööÂ6V&6ÒÖöæÇf6&ÆRÒ×B%ÂFvÖR"#âöFWböçVÆÂÂFÂÖãÇÂG'VR²bµ²Öâ%ÂGvB"ÕÓ²FVâ×÷'B×væF÷r%ÂGvB"B&çFbrWr"E$Uõ%B÷67&VVç6÷G2"õÂG·F&vWG×2çær#âöFWböçVÆÂÇÂG'VS²f²FöæRb6÷CÕÂB§vB%ÂFvÖR#²&3ÕÂCð¦¶ÆÂ%ÂG6÷B"#âöFWböçVÆÂÇÂG'VS²vB%ÂG6÷B"#âöFWböçVÆÂÇÂG'VP¦V6ò%ÂG&2"âB&çFbrWr"E$Uõ%BöæfòöWBÖ6öFRçGB"§F"Ö7¦bB&çFbrWr"E$Uõ%BçF"æw¢"Ô2B&çFbrWr"E$Uõ%B"æfòÆöw267&VVç6÷G2#âöFWböçVÆÂÇÂG'VP¦WB%ÂG&2 ¥%Tà¦6ÖöBs"E%TääU"  ¤Tåd$u3Ò¦f÷"²âD5ÄUDõ$EDuõ%TåDÔUôD"D%U5õ4U54ôåô%U5ôDE$U52TÅ4Uõ4U%dU"Ut$Uõ$TÔõDR4DÅôtÔT4ôåE$ôÄÄU$4ôädr4DÅôtÔT4ôåE$ôÄÄU$4ôäduôdÄS²Fð¢µ²Öâ"G²³¢×Ò"ÕÒbbTåd$u2³Ò"Ò×6WFVçcÒF³ÒG²·Ò"¦FöæP¥TäCÒ&v÷B×cSbÖÖçVÂÒE5DÕç6W'f6R §77FVÖB×'VâÒ×W6W"ÒÖ6öÆÆV7BÒÖæòÖ&Æö6²Ò×6W'f6R×GSÖWV2Ò×VæCÒ"ETäB"À¢ÒÖFW67&FöãÒtvõBcSbÆFRGæÖ2FWGW&R&æFærrÒ×&÷W'GÕFÖV÷WE7F÷6V3Ó32Ò×&÷W'GÔ¶ÆÄÖöFSÖ6öçG&öÂÖw&÷WÀ¢Ò×v÷&¶ærÖF&V7F÷'Ò"E%Tâ"Ò×6WFVçcÒ$ôÔSÒDôÔR"Ò×6WFVçcÒuDÒ÷W7"öÆö6Âö&ã¢÷W7"ö&ã¢ö&ârÀ¢"G´Tåd$u5´×Ò"÷W7"ö&âö&6"E%TääU" §&çFbu$Uõ%CÒW5Æä$4dSÒW5ÆåTäCÒW5Æâr"E$Uõ%B""E$Uõ%BçF"æw¢""ETäB"â"DÄôu$ôõBòæv÷B×cSbÖÖçVÂÖÆFW7B ¦V6òucSeõ5D%DTCÕU2p¦V6òtåUCÔÔåTÅôtÔUBp¦V6òtUDôÔD5ôåUCÔäôäRp¦V6ò%TC×77FVÖ7FÂÒ×W6W"7F÷ETäB ¦V6ò%tTåôdä4TEõUÄôCÒE$Uõ%BçF"æw¢ 