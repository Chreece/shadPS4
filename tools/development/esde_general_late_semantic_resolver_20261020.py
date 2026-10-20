#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact-source reviewed later general GPU/SRT semantic reconciliations.

Each source change is checked against the 2026-10-20 05:45 uploaded host
conflict preview and a known output SHA. Never select a whole branch side.
Do not introduce a title/shader hash conditional or enable a test-only flag.
"""
from __future__ import annotations
import hashlib
import re
import tarfile
from pathlib import Path

# Format: commit12:relative_source_path -> (uploaded preview SHA256, reviewed output SHA256).
# A changed preimage is NOT an invitation to guess another resolution.
CASES = {
"16ab0edbd70e:src/video_core/renderer_vulkan/vk_rasterizer.h":("497c5b940d179de8bee91ab7c473d66ab93cacaada264701da361bc65ad1a94c","49e4f0b900715e66bd67ca5ff266eeff750a03454e5602c048b6b2bd84071558"),
"b913874ce5a9:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":("03c98ce7cf0b47b73e3c2759c6ae4381b5ad3f16272286a449a91233a9eaf3b5","e6e85d81780c86f7894404244330967c53cf8c2d17500d9ef58971863b508667"),
"baceadf44d0a:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":("8d6385febc89a89c0afcc3903c54d4205bda85882db9aca3d59eb086ccca7bd7","8d012e66ab41de6fad1735d06627d7cf7e16170047f3ccedc53eda0a6d78171f"),
"c8121ad97140:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":("a2e209f8fbf404bef859bb186b43c4f2ce55a15f3bfd1ef5fd6d4f8671ff6d08","37ea3cc833dee0197484a4ac9eb20a8f33172487dec315476b9eb4c917249dda"),
"aa775cd99651:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":("3d8b0aaf999676ba4795f0949028092dc8b087f6ea12b250f4ce72d342c504d1","c9f4159a238452c10b598afe134f2b04826026d73f40a0ef25c7e9ffef53489b"),
"84117bbd1e35:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":("b2ac68c3524e288c06627b50c3e3f2b8e9864f5d0d0bd92626c16e83d3f7432d","8d012e66ab41de6fad1735d06627d7cf7e16170047f3ccedc53eda0a6d78171f"),
"1b1ca11bfea7:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":("8ad315c122c8c3edcb8576edc80c400c722048b4190050fe503d91ecd9cb5b53","8d012e66ab41de6fad1735d06627d7cf7e16170047f3ccedc53eda0a6d78171f"),
"e255fa8f608c:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":("6d5c167967610d47990b612dfffd56cacad9650720631e83d1f8f0b15b964e52","8d012e66ab41de6fad1735d06627d7cf7e16170047f3ccedc53eda0a6d78171f"),
"71faef4a8d76:src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":("0b27668bf6bf12ff9062ddb9e37630eadb613daea4685a0662561a929b8b9dbf","fe339d5e281b37c0b07e83a2e766a4e0747a8052bffd52b6f0e7c077561070ac"),
"baceadf44d0a:src/shader_recompiler/ir/passes/phi_simplification_pass.cpp":("a3cfe68229eae07a793818034c08107fd6757d508703db5cbf624317cbb67ccc","54085f55a76f4607ce281ec2af502ee90255b9c109bbbd6abb488f878723b23a"),
"baceadf44d0a:src/shader_recompiler/ir/passes/shared_memory_barrier_pass.cpp":("b284b9e8e8cd4b3b91ff7844cde110baf7d09d23394e0e569d3765cf2c5e1b9e","433f878a54f9ee22ff68f43d859d1cfd4cf3c374ef5cab0346a4361ce822de9e"),
"c8121ad97140:src/shader_recompiler/specialization.h":("e4e640fcb69e96e59f5140ea5d56c5a5eaed5a3d83d79dc9ea8787e7137eea05","ff1f3438f63d21907ed3b5371e54d16d708a31b1e070cbcc671081ba52dda929"),
"aa775cd99651:src/shader_recompiler/specialization.h":("70fb3db91ce3c9e65dc9b778cfba062ab01c05edb99d9291870cf42e1239c044","06a3f703a0082135599b0f0e637817f8ad0f0aa0e99104d3a72e2ea21e932657"),
"c7ac9ac753b7:src/video_core/renderer_vulkan/vk_rasterizer.h":("8b8821467223ff8f576b87fca3a6ace4c90c6f837414a2c4dfdd4436d892a628","5fc26d7f77e07bf33ce886f78a93b64c5aa499e9810ee478428e5ec622f04298"),
"e8063a04b9f0:src/video_core/renderer_vulkan/vk_rasterizer.h":("176404791f0f50e24a1e17512ac04da5f2def8b80afcedcbd9460ec373345984","967f23f468979e02a7841ad9a0c8dac991b3dab43c3e45316e5344e3188c9bc8"),
"20ec7bfbb7a1:src/shader_recompiler/ir/passes/resource_discover_pass.cpp":("bce99902e19fa7c04e2b8bbbae5e5fd7b902024e9d311f9e9f737bb80d17e903","3689549901c44120ddadaa956a9e6b010ab088dddd3077097815a9d85bc267f0"),
"1b1ca11bfea7:src/shader_recompiler/ir/passes/resource_discover_pass.cpp":("b1bf010aad1b146653fd3681141051007972e95c27c8080acbf969f07b584e79","9d0ca9f9f6ce3d5e2f0c8ff576873ab69c6ea76cd519069c331b13b6ea4d9599"),
"71faef4a8d76:src/shader_recompiler/ir/passes/resource_patching_pass.cpp":("4594bb812bdbdc643eb1e449c2bef61c690ba041f4acb866424f8e66da81ac93","4be050220279e0049ebf468d930661bcbe58b0dfe31406309b71e6f52bf60b95"),
"71faef4a8d76:src/video_core/renderer_vulkan/vk_rasterizer.cpp":("68785613144d109d8fbbd085b18974e8c19b63d71dc0f6e136df7b832dc6fa1c","d746d2cef76be2602d171aa95e94b313573927212b798e3e8517030f255e97f3"),
"c036336bf4b9:src/video_core/renderer_vulkan/vk_rasterizer.cpp":("0eb7a0564a59a10bcbe4af05764f3950e550929c4da3dcc8d730fdf3cc0a16ee","a8afbbbd946677d530e9288f53746bf22239b7c9a9a46d33bfc87171aa33391d"),
"1b1ca11bfea7:src/video_core/renderer_vulkan/vk_rasterizer.cpp":("c7a34bf850e4ba0e044eeaea9c411666aea1a8172d4c4d7826c31ec37defc818","99c3802db848823f27d7f259a4f4e298c7280be3721d155f48a29b44032e20b5"),
}
PATTERN = re.compile(
    r"(?ms)^<<<<<<< current-GoW-upstream\n(.*?)^=======\n(.*?)^>>>>>>> generic-GoT\n"
)

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def resolve(commit12: str, rel: str, preview: bytes) -> bytes | None:
    key = commit12 + ":" + rel
    pins = CASES.get(key)
    if not pins or sha(preview) != pins[0]:
        return None
    seen = [0]
    def choose(match):
        seen[0] += 1
        ours, theirs = match.group(1), match.group(2)
        if rel.endswith("vk_rasterizer.cpp") and commit12 in (
                "c036336bf4b9", "1b1ca11bfea7"):
            if commit12 == "c036336bf4b9":
                assert seen[0] == 1
                assert "vk_occlusion_query.h" in ours
                assert "vk_buffer_hazard_rules.h" in theirs
                # Real draw-indirect hazard rules, without Ghost-only GPU
                # screenshots or scene-specific diagnostic include paths.
                return ('#include "video_core/renderer_vulkan/vk_buffer_hazard_rules.h"\\n'.replace('\\n','\n') + ours)
            if seen[0] == 1:
                assert not ours.strip()
                assert "indexed_image_feedback.h" in theirs
                assert "shader_recompiler/resource.h" in theirs
                return '#include "shader_recompiler/resource.h"\\n'.replace('\\n','\n')
            assert seen[0] == 2
            assert "vk_occlusion_query.h" in ours
            assert "vk_buffer_hazard_rules.h" in theirs
            return ('#include "video_core/renderer_vulkan/vk_buffer_hazard_rules.h"\\n'.replace('\\n','\n') + ours)
        if commit12 == "16ab0edbd70e":
            assert seen[0] == 1 and "#include <functional>" in ours
            assert "#include <vector>" in theirs
            return ours + theirs
        if rel.endswith("vk_pipeline_serialization.cpp"):
            if "ShaderBinaryVersion" in ours:
                assert "ShaderBinaryVersion = 43u" in ours
                assert "ShaderMetaVersion = 21u" in ours
                assert "PipelineKeyVersion = 5u" in theirs
                # Retain old cache revision while merging. One consolidated
                # version bump follows after final resource ABI reconciliation.
                return ours
            assert not ours.strip() and (
                "spec.Write(" in theirs or "spec.Read(" in theirs)
            return theirs
        if commit12 == "baceadf44d0a" and rel.endswith("phi_simplification_pass.cpp"):
            assert not ours.strip() and "GetUserData" in theirs
            return theirs
        if commit12 == "baceadf44d0a" and rel.endswith("shared_memory_barrier_pass.cpp"):
            assert "workgroup_size[component] > 1" in ours
            assert "IsVaryingLocalInvocationComponent" in theirs
            # The already reviewed Oct19 official upstream computes the same
            # singleton-LDS predicate without a second incompatible helper.
            return ours
        if rel.endswith("/specialization.h"):
            assert not ours.strip()
            assert "indexed_image_entries" in theirs or "bounded_image_counts" in theirs
            return theirs
        if rel.endswith("/vk_rasterizer.h"):
            assert "std::array<ImageBinding, Shader::NUM_IMAGES>" in ours
            assert "std::vector<ImageBinding> image_bindings" in theirs
            assert "image_uids;" in theirs
            return theirs
        if rel.endswith("/resource_discover_pass.cpp"):
            if commit12 == "20ec7bfbb7a1":
                assert not ours.strip() and "AnalyzeIndexedSamplerTable" in theirs
                # Remove unused experimental SHADPS4_STRIDED_GUEST_TSHARP
                # toggle; retain real same-record sampler provenance.
                marker = "// The strided material-table lowering remains opt-in until game-facing"
                assert theirs.count(marker) == 1
                assert theirs.count("StridedGuestTsharpEnabled()") == 1
                return theirs.split(marker, 1)[0]
            if commit12 == "1b1ca11bfea7":
                assert "if (uniform)" in ours
                assert "uniform_sampler" in theirs and "AnalyzeIndexedSamplerTable" in theirs
                return theirs
        if commit12 == "71faef4a8d76" and rel.endswith("resource_patching_pass.cpp"):
            assert "FetchChecked(" in theirs
            return theirs
        if commit12 == "71faef4a8d76" and rel.endswith("vk_rasterizer.cpp"):
            assert "const bool complete" in theirs
            assert "static const bool dump_tables" in theirs
            predicate = theirs.split("            static const bool dump_tables = ", 1)[0]
            assert predicate.strip().endswith(";")
            # Keep the complete bounds predicate; exclude shader/hash-specific
            # texture-table dump instrumentation entirely.
            return predicate
        raise ValueError("Unreviewed source conflict hunk " + key)
    text = PATTERN.sub(choose, preview.decode("utf-8"))
    if seen[0] == 0 or any(x in text for x in ("<<<<<<<", "=======", ">>>>>>>")):
        raise ValueError("Unresolved reviewed later-source conflict " + key)
    output = text.encode("utf-8")
    if sha(output) != pins[1]:
        raise ValueError("Audited later-source postimage changed " + key)
    return output

def check_fixture(archive_path: Path) -> int:
    with tarfile.open(archive_path, "r:gz") as archive:
        for key in CASES:
            sha_key, path = key.split(":", 1)
            member = archive.extractfile("conflicts/later/" + sha_key + "/" + path)
            if member is None:
                raise ValueError("Missing uploaded later-source evidence " + key)
            output = resolve(sha_key, path, member.read())
            if output is None or sha(output) != CASES[key][1]:
                raise ValueError("Incomplete review of " + key)
    return len(CASES)
