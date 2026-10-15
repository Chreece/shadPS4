#!/usr/bin/env python3
"""Fresh-upstream regression build for retained generic emulator corrections.

Captured images do not, by themselves, prove playable rendering or cross-title support.

The build source starts at an exact shadps4-emu/shadPS4 main commit.  It never
uses an old GoW executable/object/source as build input.  The Chreece Phase84
head is merged only because its runtime source changes are generic CPU/GPU
emulator fixes; all later fixes are reapplied semantically.  Source is audited
for game IDs, known shader hashes, and historical GoW behavioral controls before
compilation and again before gameplay.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import traceback
import types

HOME = Path.home()
ROOT = HOME / "projects/shadps4/GodOfWarRagnarok"
WORK = ROOT / "tests" / ("main-proof-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
SRC = WORK / "source"
BUILD = WORK / "build"
HELPERS = WORK / "helpers"
LOG_ROOT = HOME / "projects/shadps4/logs"

UPSTREAM_URL = "https://github.com/shadps4-emu/shadPS4.git"
FORK_URL = "https://github.com/Chreece/shadPS4.git"
UPSTREAM_MAIN = "b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
# CPU identity/affinity/memory fixes + generic Phase84 DS/wave/sparse/stream work.
GENERIC_STACK = "966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"

HELPER_URLS = {
    # The upstream-layout adapter includes the same general shader SRT,
    # DMA, texel alias, stream/coherency, and selective ownership fixes.
    # Unlike the old incremental helpers it uses current ObtainBufferFlags.
    "upstream.py": ("https://raw.githubusercontent.com/Chreece/shadPS4/"
                    "745506e3ef2fd4df87ef703e6a10ac06945cd2bf/"
                    "tools/gow110-main-transform.py"),
    "gow96.py": ("https://raw.githubusercontent.com/Chreece/shadPS4/"
                 "239be281dc524f4992cfc11bde48af6f876b659c/"
                 "tools/gow96-phi-buffer-load.py"),
    "auto.py": ("https://raw.githubusercontent.com/Chreece/shadPS4/"
               "343ae209e22391aff8ca513b536a858bb5ee4bfd/"
               "tools/gow-auto-gameplay.py"),
}

PHASE87_FILES = (
    "src/video_core/renderer_vulkan/vk_pipeline_cache.h",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
    "src/shader_recompiler/specialization.h",
)

FORBIDDEN = (
    r"SHADPS4_GOW_",
    r"CUSA34384",
    r"God of War",
    r"Ragnar",
    r"\bGOW(?:[0-9]+|_)",
    r"GOW_SRT_",
    r"0xe83356c8",
    r"0xad0b8f1f",
    r"0x04691bd5",
)

def note(s):
    print("MAIN_PROOF " + str(s), flush=True)

def run(args, log=None, cwd=None, timeout=None, check=True, env=None):
    args = [str(x) for x in args]
    e = os.environ.copy()
    if env:
        e.update(env)
    e["GIT_TERMINAL_PROMPT"] = "0"
    if args and args[0] == "git":
        args = ["git", "-c", "http.version=HTTP/1.1", *args[1:]]
    if log is None:
        cp = subprocess.run(args, cwd=cwd, env=e, text=True, capture_output=True,
                            timeout=timeout)
        if check and cp.returncode:
            raise RuntimeError("command failed: " + " ".join(args[:8]) +
                               "\n" + cp.stderr[-3000:] + "\n" + cp.stdout[-1000:])
        return cp
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as f:
        p = subprocess.Popen(args, cwd=cwd, env=e, stdin=subprocess.DEVNULL,
                             stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            raise
    if check and rc:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-80:])
        raise RuntimeError(f"command failed rc={rc}: {' '.join(args[:8])}\n{tail}")
    return rc

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def fetch(url, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".main-proof-", dir=path.parent)
    os.close(fd)
    try:
        run(["curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
             "--max-time", "120", url, "-o", tmp], timeout=380)
        raw = Path(tmp).read_bytes()
        if not raw.startswith(b"#!"):
            raise RuntimeError("unexpected helper contents: " + path.name)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def module(path, name):
    m = types.ModuleType(name)
    m.__file__ = str(path)
    exec(compile(Path(path).read_bytes(), str(path), "exec"), m.__dict__)
    return m

def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected one source match, got {n}")
    return text.replace(old, new, 1)

def function_span(text, name):
    # Function names used here are unique in their source files.
    masked = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"',
                    lambda m: "".join("\n" if x == "\n" else " " for x in m.group(0)),
                    text)
    starts = [m.start() for m in re.finditer(re.escape(name + "("), masked)]
    found = []
    for start in starts:
        paren = masked.find("(", start)
        depth = 0
        close = None
        for i in range(paren, len(masked)):
            depth += masked[i] == "("
            depth -= masked[i] == ")"
            if depth == 0:
                close = i
                break
        if close is None:
            continue
        brace = close + 1
        while brace < len(masked) and masked[brace].isspace():
            brace += 1
        if brace >= len(masked) or masked[brace] != "{":
            continue
        depth = 0
        end = None
        for i in range(brace, len(masked)):
            depth += masked[i] == "{"
            depth -= masked[i] == "}"
            if depth == 0:
                end = i + 1
                break
        if end:
            found.append((start, end))
    if len(found) != 1:
        raise RuntimeError(f"{name}: definition missing or ambiguous")
    return found[0]

def write_changed(rel, data):
    p = SRC / rel
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("missing/unsafe source: " + rel)
    p.write_bytes(data)

def apply_phase87():
    # Current upstream still has the exact permutation-zero metadata lifetime bug.
    hp = SRC / "src/video_core/renderer_vulkan/vk_pipeline_cache.h"
    text = hp.read_text()
    old = r'''struct Program {
    struct Module {
        vk::ShaderModule module;
        Shader::StageSpecialization spec;
    };
    static constexpr size_t MaxPermutations = 8;
    using ModuleList = boost::container::small_vector<Module, MaxPermutations>;

    Shader::Info info;
    ModuleList modules{};

    Program() = default;
    Program(Shader::HwStage stage, Shader::SwStage l_stage, Shader::ShaderParams params)
        : info{stage, l_stage, params} {}

    void AddPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec) {
        modules.emplace_back(module, std::move(spec));
    }

    void InsertPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,
                      size_t perm_idx) {
        modules.resize(std::max(modules.size(), perm_idx + 1)); // <-- beware of realloc
        modules[perm_idx] = {module, std::move(spec)};
    }
};'''
    new = r'''struct Program {
    struct Module {
        vk::ShaderModule module{};
        Shader::StageSpecialization spec;
        std::unique_ptr<Shader::Info> info;
    };
    static constexpr size_t MaxPermutations = 8;
    using ModuleList = boost::container::small_vector<Module, MaxPermutations>;

    ModuleList modules{};

    void AddPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,
                   std::unique_ptr<Shader::Info> info) {
        spec.info = info.get();
        modules.push_back(Module{module, std::move(spec), std::move(info)});
    }

    void InsertPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,
                      size_t perm_idx, std::unique_ptr<Shader::Info> info) {
        modules.resize(std::max(modules.size(), perm_idx + 1));
        spec.info = info.get();
        modules[perm_idx] = Module{module, std::move(spec), std::move(info)};
    }
};'''
    text = replace_once(text, old, new, "Program permutation metadata ownership")
    if "#include <memory>\n" not in text:
        text = replace_once(text, "#pragma once\n", "#pragma once\n\n#include <memory>\n",
                            "pipeline cache memory include")
    hp.write_text(text)

    cp = SRC / "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
    text = cp.read_text()
    a, b = function_span(text, "PipelineCache::GetProgram")
    old = text[a:b]
    new = r'''PipelineCache::Result PipelineCache::GetProgram(HwStage hw_stage, SwStage sw_stage,
                                                const Shader::ShaderParams& params,
                                                Shader::Backend::Bindings& binding) {
    const auto runtime_info = BuildRuntimeInfo(hw_stage, sw_stage);
    const auto start = binding;
    auto [it_pgm, new_program] = program_cache.try_emplace(params.hash);
    if (new_program) {
        it_pgm.value() = std::make_unique<Program>();
    }
    auto& program = *it_pgm.value();

    for (size_t perm_idx = 0; perm_idx < program.modules.size(); ++perm_idx) {
        auto& cached = program.modules[perm_idx];
        if (!cached.module || !cached.info || !cached.spec.Valid()) {
            continue;
        }
        auto& info = *cached.info;
        if (info.hw_stage != hw_stage || info.sw_stage != sw_stage) {
            continue;
        }
        info.pgm_base = params.Base();
        info.user_data = params.user_data;
        info.RefreshFlatBuf();
        const auto current = Shader::StageSpecialization(info, runtime_info, profile, start);
        if (!(cached.spec == current)) {
            continue;
        }
        info.AddBindings(binding);
        if (auto& fetch = cached.spec.fetch_shader_data; !fetch.Empty()) {
            fetch_shader = &fetch;
        }
        return std::make_tuple(cached.info.get(), cached.module,
                               HashCombine(params.hash, perm_idx));
    }

    const size_t perm_idx = program.modules.size();
    const auto perm_hash = HashCombine(params.hash, perm_idx);
    auto info = std::make_unique<Shader::Info>(hw_stage, sw_stage, params);
    auto compiled_runtime = runtime_info;
    const auto module = CompileModule(*info, compiled_runtime, params.code, perm_idx, binding);
    auto spec = Shader::StageSpecialization(*info, compiled_runtime, profile, start);

    RegisterShaderMeta(*info, spec.fetch_shader_data, spec, perm_hash, perm_idx);
    program.AddPermut(module, std::move(spec), std::move(info));
    auto& cached = program.modules.back();
    if (auto& fetch = cached.spec.fetch_shader_data; !fetch.Empty()) {
        fetch_shader = &fetch;
    }
    return std::make_tuple(cached.info.get(), module, perm_hash);
}'''
    text = text[:a] + new + text[b:]
    cp.write_text(text)

    sp = SRC / "src/shader_recompiler/specialization.h"
    text = sp.read_text()
    old = '''    bool operator==(const StageSpecialization& other) const {
        if (!Valid()) {
            return false;
        }
'''
    new = '''    bool operator==(const StageSpecialization& other) const {
        if (!Valid() || !other.Valid()) {
            return false;
        }
        if (buffers.size() != other.buffers.size() || images.size() != other.images.size() ||
            samplers.size() != other.samplers.size()) {
            return false;
        }
'''
    text = replace_once(text, old, new, "specialization layout guard")
    sp.write_text(text)

    pp = SRC / "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
    text = pp.read_text()
    a, b = function_span(text, "PipelineCache::LoadPipelineStage")
    new = r'''bool PipelineCache::LoadPipelineStage(Serialization::Archive& ar, size_t stage) {
    auto info = std::make_unique<Shader::Info>();
    Shader::StageSpecialization spec{};
    spec.info = info.get();
    size_t perm_idx{};
    if (!LoadShaderMeta(ar, *info, spec, perm_idx)) {
        return false;
    }

    std::vector<u32> spv{};
    Storage::DataBase::Instance().Load(Storage::BlobType::ShaderBinary,
                                       fmt::format("{:#018x}_{}", info->pgm_hash, perm_idx), spv);
    if (spv.empty()) {
        return false;
    }

    auto [it_pgm, new_program] = program_cache.try_emplace(info->pgm_hash);
    if (new_program) {
        it_pgm.value() = std::make_unique<Program>();
    }
    auto& program = *it_pgm.value();
    auto match = std::ranges::find(program.modules, spec, &Program::Module::spec);
    if (match != program.modules.end()) {
        const auto existing_idx =
            static_cast<size_t>(std::distance(program.modules.begin(), match));
        if (existing_idx != perm_idx || !match->module || !match->info) {
            LOG_WARNING(Render_Vulkan,
                        "Cached shader permutation index mismatch, skipping preload");
            return false;
        }
    } else {
        if (perm_idx < program.modules.size() && program.modules[perm_idx].module) {
            LOG_WARNING(Render_Vulkan,
                        "Conflicting cached shader permutation slot, skipping preload");
            return false;
        }
        const auto module = CompileSPV(spv, instance.GetDevice());
        program.InsertPermut(module, std::move(spec), perm_idx, std::move(info));
    }

    auto& cached = program.modules[perm_idx];
    infos[stage] = cached.info.get();
    modules[stage] = cached.module;
    if (auto& fetch = cached.spec.fetch_shader_data; !fetch.Empty()) {
        fetch_shader = &fetch;
    }
    return true;
}'''
    text = text[:a] + new + text[b:]
    for name in ("ShaderBinaryVersion", "ShaderMetaVersion"):
        m = re.search(rf"({name}\s*=\s*)(\d+)(u;)", text)
        if not m:
            raise RuntimeError("cache version missing: " + name)
        text = text[:m.start()] + m.group(1) + str(int(m.group(2)) + 1) + m.group(3) + text[m.end():]
    pp.write_text(text)

def adapt_generic_srt_order(data):
    text = data.decode()
    if "MakeSrtRootDependencyPlan" in text:
        raise RuntimeError("generic root planner already present")
    if "#include <unordered_set>\n" not in text:
        text = replace_once(text, "#include <unordered_map>\n",
                            "#include <unordered_map>\n#include <unordered_set>\n",
                            "unordered_set include")
    planner = r'''
struct SrtRootDependencyPlan {
    std::vector<u32> order;
    std::vector<std::pair<u32, u32>> edges;
    bool reordered{};
    bool cyclic{};
    bool ambiguous{};
    bool capped{};
};

static SrtRootDependencyPlan MakeSrtRootDependencyPlan(PassInfo& pass_info) {
    SrtRootDependencyPlan plan;
    using Root = std::pair<IR::ScalarReg, IR::Inst*>;
    std::vector<Root> roots;
    for (const auto& [sgpr, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr, root);
    }
    for (u32 i = 0; i < roots.size(); ++i) {
        plan.order.push_back(i);
    }
    constexpr size_t MaxRoots = 32;
    constexpr size_t MaxWalkNodes = 4096;
    constexpr size_t MaxIndexNodes = 4096;
    if (roots.size() > MaxRoots) {
        plan.capped = true;
        return plan;
    }

    std::unordered_map<IR::Inst*, u32> produced_by_root;
    std::vector<std::vector<IR::Value>> index_expressions(roots.size());
    size_t walked_nodes = 0;
    for (u32 root_id = 0; root_id < roots.size(); ++root_id) {
        std::vector<IR::Inst*> todo{roots[root_id].second};
        std::unordered_set<IR::Inst*> visited;
        for (size_t pos = 0; pos < todo.size(); ++pos) {
            if (++walked_nodes > MaxWalkNodes) {
                plan.capped = true;
                return plan;
            }
            auto* parent = todo[pos];
            if (!visited.insert(parent).second) {
                continue;
            }
            auto* children = pass_info.GetUsesAsPointer(parent);
            if (!children) {
                continue;
            }
            for (const auto& [src_offset, child] : *children) {
                index_expressions[root_id].push_back(src_offset);
                IR::Inst* canonical = pass_info.DeduplicateInstruction(child);
                for (IR::Inst* source : {child, canonical}) {
                    const auto [it, inserted] =
                        produced_by_root.try_emplace(source, root_id);
                    if (!inserted && it->second != root_id) {
                        plan.ambiguous = true;
                    }
                }
                if (pass_info.GetUsesAsPointer(child)) {
                    todo.push_back(child);
                }
            }
        }
    }
    if (plan.ambiguous) {
        return plan;
    }

    std::vector<std::vector<bool>> deps(
        roots.size(), std::vector<bool>(roots.size(), false));
    size_t index_nodes = 0;
    for (u32 consumer = 0; consumer < roots.size(); ++consumer) {
        std::vector<IR::Value> pending = index_expressions[consumer];
        std::unordered_set<IR::Inst*> visited;
        while (!pending.empty()) {
            if (++index_nodes > MaxIndexNodes) {
                plan.capped = true;
                return plan;
            }
            const IR::Value value = pending.back();
            pending.pop_back();
            if (value.IsImmediate()) {
                continue;
            }
            IR::Inst* inst = value.TryInst();
            if (!inst || !visited.insert(inst).second) {
                continue;
            }
            const auto op = inst->GetOpcode();
            if (op == IR::Opcode::ReadConst || op == IR::Opcode::ReadConstBuffer) {
                auto it = produced_by_root.find(inst);
                if (it == produced_by_root.end()) {
                    it = produced_by_root.find(pass_info.DeduplicateInstruction(inst));
                }
                if (it != produced_by_root.end() && it->second != consumer) {
                    deps[consumer][it->second] = true;
                }
                continue;
            }
            if (op == IR::Opcode::GetUserData || !IsAllowedOffsetInstruction(inst)) {
                continue;
            }
            for (u32 arg = 0; arg < inst->NumArgs(); ++arg) {
                IR::Value child = inst->Arg(arg);
                if (!child.IsImmediate()) {
                    pending.push_back(child);
                }
            }
        }
    }

    for (u32 consumer = 0; consumer < roots.size(); ++consumer) {
        for (u32 prerequisite = 0; prerequisite < roots.size(); ++prerequisite) {
            if (deps[consumer][prerequisite]) {
                plan.edges.emplace_back(consumer, prerequisite);
            }
        }
    }

    std::vector<u32> sorted;
    std::vector<bool> completed(roots.size(), false);
    while (sorted.size() < roots.size()) {
        bool advanced = false;
        for (u32 candidate = 0; candidate < roots.size(); ++candidate) {
            if (completed[candidate]) {
                continue;
            }
            bool ready = true;
            for (u32 dependency = 0; dependency < roots.size(); ++dependency) {
                if (deps[candidate][dependency] && !completed[dependency]) {
                    ready = false;
                    break;
                }
            }
            if (ready) {
                completed[candidate] = true;
                sorted.push_back(candidate);
                advanced = true;
                break;
            }
        }
        if (!advanced) {
            plan.cyclic = true;
            return plan;
        }
    }
    plan.reordered = sorted != plan.order;
    plan.order = std::move(sorted);
    return plan;
}

'''
    marker = "static void GenerateSrtProgram(Info& info, PassInfo& pass_info) {\n"
    text = replace_once(text, marker, planner + marker, "root planner insertion")
    old = '''    for (const auto& [sgpr_base, root] : pass_info.srt_roots) {
        VisitPointer(IR::Value(static_cast<u32>(sgpr_base)), root, pass_info, c);
    }
'''
    new = '''    std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
    roots.reserve(pass_info.srt_roots.size());
    for (const auto& [sgpr, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr, root);
    }
    const auto plan = MakeSrtRootDependencyPlan(pass_info);
    for (const u32 root_index : plan.order) {
        const auto [sgpr, root] = roots[root_index];
        VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
    }
'''
    text = replace_once(text, old, new, "root planner use")
    return text.encode()

def apply_generic_fixes():
    for name, url in HELPER_URLS.items():
        fetch(url, HELPERS / name)

    g96 = module(HELPERS/"gow96.py", "proof_g96")
    upstream = module(HELPERS/"upstream.py", "proof_upstream")

    # First retain the permutation metadata lifetime correction. This
    # remains a generic shader cache fix independent of any game ID.
    apply_phase87()

    # Retain the reviewed collaborator Phi-origin buffer descriptor fix.
    # It is re-applied to upstream's resource discovery pass, not loaded from
    # the previous local executable, shader dumps, or a game-specific patch.
    rel = g96.RESOURCE
    before = (SRC/rel).read_bytes()
    after, _, phi_proof = g96.adapt_resource(before, g96.collaborator_source())
    write_changed(rel, after)

    # Apply generic equivalents of the tested historical DMA residency,
    # texel/image-range coherency, formatted stream synchronization, bounded
    # GPU write extents, ownership, and SRT arithmetic/dependency fixes.
    # The previous helpers assumed pre-ObtainBufferFlags upstream code and
    # cannot be safely applied to current main.
    generic_proof = upstream.apply_to_tree(SRC)
    (LOG_ROOT).mkdir(parents=True, exist_ok=True)
    proof = {"phi_descriptor": phi_proof,
             "upstream_generic_transform": generic_proof,
             "old_helper_runtime_patches_reused": False,
             "old_binary_or_compiled_objects_reused": False,
             "game_specific_changes": False}
    return proof


def audit_source(out):
    diff = run(["git", "diff", "--binary", UPSTREAM_MAIN, "--", "src", "CMakeLists.txt", "cmake"],
               cwd=SRC).stdout
    (out/"source-diff.patch").write_text(diff)
    bad = []
    for pat in FORBIDDEN:
        if re.search(pat, diff, re.IGNORECASE):
            bad.append(pat)
    # Do not reject ordinary words "game" or "shader"; reject only known targeted controls/IDs.
    if bad:
        raise RuntimeError("game-specific runtime marker in main-proof diff: " + repr(bad))
    required = {
        "generic_srt_order": "MakeSrtRootDependencyPlan" in
            (SRC/"src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp").read_text(),
        "selective_readback": "MarkRegionAsGpuModifiedTracked" in
            (SRC/"src/video_core/buffer_cache/memory_tracker.h").read_text(),
        "bounded_write_extent": "BufferWriteExtent" in
            (SRC/"src/shader_recompiler/resource.h").read_text(),
        "permutation_metadata": "std::unique_ptr<Shader::Info> info" in
            (SRC/"src/video_core/renderer_vulkan/vk_pipeline_cache.h").read_text(),
        "cpu_translation": (SRC/"src/core/cpu_id_translation/launcher.cpp").is_file(),
    }
    (out/"source-audit.json").write_text(json.dumps({
        "upstream_main": UPSTREAM_MAIN,
        "generic_stack": GENERIC_STACK,
        "forbidden_patterns": list(FORBIDDEN),
        "forbidden_hits": bad,
        "required": required,
        "readbacks_required": False,
        "game_specific": False,
    }, indent=2)+"\n")
    if not all(required.values()):
        raise RuntimeError("required generic fixes missing: " +
                           repr([k for k,v in required.items() if not v]))
    run(["git", "diff", "--check"], cwd=SRC)

def package(out, result):
    (out/"result.json").write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    archive = LOG_ROOT/(out.name+".tar.gz")
    with tarfile.open(archive, "w:gz", compresslevel=3) as tf:
        for p in sorted(out.rglob("*")):
            if p.is_file() and not p.is_symlink():
                tf.add(p, arcname=str(Path(out.name)/p.relative_to(out)))
        capture = result.get("capture_archive")
        if capture:
            candidate = Path(capture)
            if (candidate.is_file() and not candidate.is_symlink() and
                    candidate.resolve().is_relative_to(LOG_ROOT.resolve())):
                tf.add(candidate, arcname=str(Path(out.name)/"gameplay-capture.tar.gz"))
    print("UPLOAD="+str(archive), flush=True)
    return archive

def reconcile_known_shader_cache_schema_conflict(out):
    """Union independent shader-cache version changes; refuse other conflicts.

    The merge stages are ancestor (:1), current upstream (:2), and retained
    generic emulator stack (:3).  Versions are cache compatibility IDs, so if
    both sides changed the same ID independently, the merged ID must be new to
    BOTH parents.  No emulator runtime instruction or shader code is changed.
    """
    rel = "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
    unmerged = run(["git", "diff", "--name-only", "--diff-filter=U"],
                   cwd=SRC).stdout.strip().splitlines()
    if unmerged != [rel]:
        raise RuntimeError("unrecognized merge conflicts: " + repr(unmerged))

    staged = {}
    for i, role in ((1, "ancestor"), (2, "upstream"), (3, "generic")):
        staged[role] = run(["git", "show", f":{i}:{rel}"], cwd=SRC).stdout

    patterns = {
        "ShaderBinaryVersion": r"^static constexpr u32 ShaderBinaryVersion = ([0-9]+)u;$",
        "ShaderMetaVersion": r"^static constexpr u32 ShaderMetaVersion = ([0-9]+)u;$",
    }
    def parsed(content):
        found = {}
        for name, pattern in patterns.items():
            matches = re.findall(pattern, content, re.MULTILINE)
            if len(matches) != 1:
                raise RuntimeError("shader cache schema constant missing or ambiguous: " + name)
            found[name] = int(matches[0])
        return found

    versions = {role: parsed(content) for role, content in staged.items()}
    def blank_versions(content):
        for key, pattern in patterns.items():
            content = re.sub(pattern, f"static constexpr u32 {key} = 0u;",
                             content, flags=re.MULTILINE)
        return content

    reference = blank_versions(staged["ancestor"])
    if any(blank_versions(value) != reference for value in staged.values()):
        raise RuntimeError("shader cache source differs beyond the two schema version numbers")

    combined = {}
    for name in patterns:
        b, u, g = (versions[role][name] for role in ("ancestor", "upstream", "generic"))
        if not (1 <= b <= u <= 2147480000 and b <= g <= 2147480000):
            raise RuntimeError(f"{name} not monotonic across ancestor and parents")
        combined[name] = b + (u - b) + (g - b)
        if combined[name] > 2147480000:
            raise RuntimeError("shader cache version overflow")

    resolved = staged["upstream"]
    for name, pattern in patterns.items():
        resolved, count = re.subn(
            pattern, f"static constexpr u32 {name} = {combined[name]}u;",
            resolved, count=1, flags=re.MULTILINE)
        if count != 1:
            raise RuntimeError("failed to write merged schema constant: " + name)

    (SRC/rel).write_text(resolved)
    run(["git", "add", "--", rel], cwd=SRC)
    if run(["git", "ls-files", "-u"], cwd=SRC).stdout.strip():
        raise RuntimeError("some merge conflicts remain after shader cache version union")

    evidence = {"path": rel, "ancestor": versions["ancestor"],
                "upstream": versions["upstream"], "generic_stack": versions["generic"],
                "merged": combined, "only_version_lines_changed": True,
                "resolved_schema_conflict_only": True}
    (out/"shader-cache-version-union.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    note("SHADER_CACHE_VERSION_UNION=" + repr(combined))
    return evidence

def prepare_source(out):
    global UPSTREAM_MAIN
    if WORK.exists():
        raise RuntimeError("refusing to overwrite an existing proof workspace: " + str(WORK))
    WORK.mkdir(parents=True, exist_ok=False)
    remote = run(["git", "ls-remote", UPSTREAM_URL, "refs/heads/main"],
                 timeout=120).stdout.strip().splitlines()
    if len(remote) != 1 or len(remote[0].split()) != 2 or not re.fullmatch(
            r"[a-fA-F0-9]{40}", remote[0].split()[0]):
        raise RuntimeError("could not verify latest upstream main")
    UPSTREAM_MAIN = remote[0].split()[0].lower()
    note("SOURCE=verified latest upstream main " + UPSTREAM_MAIN)
    run(["git", "init", str(SRC)], log=out/"git-init.log", timeout=30)
    run(["git", "remote", "add", "upstream", UPSTREAM_URL], cwd=SRC)
    # Keep ancestry for three-way merging. Depth=1 conceals the common ancestor.
    run(["git", "fetch", "--no-tags", "--filter=blob:none", "upstream", "main"],
        log=out/"fetch-upstream.log", cwd=SRC, timeout=1200)
    run(["git", "checkout", "-B", "main-proof", "FETCH_HEAD"],
        log=out/"checkout.log", cwd=SRC, timeout=30)
    got = run(["git", "rev-parse", "HEAD"], cwd=SRC).stdout.strip()
    if got != UPSTREAM_MAIN:
        raise RuntimeError("upstream main pin mismatch: "+got)

    run(["git", "remote", "add", "chreece", FORK_URL], cwd=SRC)
    run(["git", "fetch", "--no-tags", "chreece",
         f"{GENERIC_STACK}:refs/remotes/chreece/generic-stack"],
        log=out/"fetch-generic-stack.log", cwd=SRC, timeout=1200)
    note("MERGE=generic CPU + Phase84 emulator stack")
    rc = run(["git", "merge", "--no-ff", "--no-commit",
              "refs/remotes/chreece/generic-stack"],
             log=out/"merge-generic-stack.log", cwd=SRC, timeout=300, check=False)
    if rc:
        run(["git", "diff", "--diff-filter=U"], log=out/"merge-conflicts.diff",
            cwd=SRC, check=False)
        reconcile_known_shader_cache_schema_conflict(out)
        # The source file differs only in version fields; other conflicts
        # or merge failure modes still abort rather than guessing.
        if run(["git", "ls-files", "-u"], cwd=SRC).stdout.strip():
            raise RuntimeError("unresolved merge conflicts remain")
    # Verify the original third-party DynamoRIO patch byte-for-byte against
    # the retained stack. Its five historical whitespace-only patch lines must
    # remain intact: changing patch lines can change what the patch applies to.
    vendored = "src/core/cpu_id_translation/dynamorio.patch"
    retained_blob = run(["git", "rev-parse",
                         "refs/remotes/chreece/generic-stack:" + vendored],
                        cwd=SRC).stdout.strip()
    merged_blob = run(["git", "hash-object", "--", vendored],
                      cwd=SRC).stdout.strip()
    indexed = run(["git", "ls-files", "-s", "--", vendored], cwd=SRC).stdout.split()
    known_blob = "6c28c863a8f9f61cf030ac452f634a272ad5c1eb"
    if (retained_blob != known_blob or merged_blob != known_blob or
            len(indexed) < 3 or indexed[1] != known_blob):
        raise RuntimeError("DynamoRIO patch provenance changed; refusing whitespace exception")

    # Check *every* staged change, and accept only five known whitespace lines
    # inherited from that exact immutable vendor patch. No other issue passes.
    whitespace_check = run(["git", "diff", "--cached", "--check"],
                           cwd=SRC, check=False)
    expected_lines = []
    for lineno in (82, 148, 157, 195, 223):
        expected_lines.extend([f"{vendored}:{lineno}: trailing whitespace.", "+ "])
    actual_lines = (whitespace_check.stdout +
                    whitespace_check.stderr).splitlines()
    if whitespace_check.returncode not in (0, 2) or (
            whitespace_check.returncode == 0 and actual_lines) or (
            whitespace_check.returncode != 0 and actual_lines != expected_lines):
        raise RuntimeError("unexpected staged-source whitespace violation: " +
                           repr(actual_lines))
    # The source-formatting gate remains strict for all source changes.
    run(["git", "diff", "--cached", "--check", "--", ".",
         ":(exclude)" + vendored], cwd=SRC)
    (out/"vendor-patch-whitespace-audit.json").write_text(json.dumps({
        "path": vendored, "stack_blob_sha1": known_blob,
        "merged_blob_sha1": merged_blob,
        "preserved_exact_stack_bytes": True,
        "historical_whitespace_lines": [82, 148, 157, 195, 223],
        "all_other_staged_changes_pass_git_diff_check": True,
    }, indent=2, sort_keys=True) + "\n")
    note("VENDOR_PATCH=original retained unchanged; all other files whitespace-clean")

    # The stack contains one development runner; it is not emulator runtime source.
    for p in ("tools/gow45_latest_main_cpu_gow.py",):
        q = SRC/p
        if q.exists():
            q.unlink()
            run(["git", "add", "-A", "--", p], cwd=SRC)
    run(["git", "-c", "user.name=Main Proof", "-c", "user.email=proof@localhost",
         "commit", "-m", "main proof: generic CPU and Phase84 emulator fixes"],
        log=out/"merge-commit.log", cwd=SRC, timeout=60)

def resume_proven_source(out, source_workspace):
    """Resume only the exact source checkpoint whose diff we inspected."""
    global WORK, SRC, BUILD, HELPERS
    requested = Path(source_workspace).expanduser()
    if requested.is_symlink() or not requested.is_dir():
        raise RuntimeError("resume directory missing or symlinked: " + str(requested))
    candidate = requested.resolve(strict=True)
    if candidate.parent != (ROOT/"tests").resolve() or (
            candidate.name != "main-proof-20261015-151529-3107006"):
        raise RuntimeError("resume path does not match the verified proof workspace")
    WORK = candidate
    SRC = WORK/"source"
    BUILD = WORK/"build"
    HELPERS = WORK/"helpers"
    if not (SRC/".git").is_dir() or not HELPERS.is_dir() or SRC.is_symlink():
        raise RuntimeError("source or retained helpers missing")
    parents = run(["git", "rev-list", "--parents", "-n", "1", "HEAD"],
                  cwd=SRC).stdout.strip().split()
    if len(parents) != 3 or parents[1:] != [UPSTREAM_MAIN, GENERIC_STACK]:
        raise RuntimeError("source history is not the verified upstream + general fixes")
    if run(["git", "ls-files", "-u"], cwd=SRC).stdout.strip():
        raise RuntimeError("source has unmerged Git files")
    diff = run(["git", "diff", "--binary", UPSTREAM_MAIN, "--",
                "src", "CMakeLists.txt", "cmake"], cwd=SRC).stdout
    expected = "71c967af7f362ec4bb0c856b3be2f01db350bb1ed3669638057a714f769b6829"
    if hashlib.sha256(diff.encode()).hexdigest() != expected:
        raise RuntimeError("patched source no longer matches uploaded source-diff")
    remote = run(["git", "ls-remote", UPSTREAM_URL, "refs/heads/main"],
                 timeout=120).stdout.strip().splitlines()
    if len(remote) != 1 or remote[0].split()[0].lower() != UPSTREAM_MAIN:
        raise RuntimeError("upstream advanced; a fresh upstream port is required")
    audit_source(out)
    if sha256(out/"source-diff.patch") != expected:
        raise RuntimeError("resumed source audit fingerprint mismatch")
    old_logs = LOG_ROOT/"gow-main-proof-20261015-151529-3107006"
    copies = {
        "generic-transform-proof.json":
            "e6c3121cdbb96217e71960d221aeac28cad7eb493fe6881987ef7b1c2f50a465",
        "helpers-sha256.json":
            "652eb876e6bcbf608e78163c866bfef8f3a0cbbb73e8cf52cb331011f94334de",
        "shader-cache-version-union.json":
            "a946b3b13019b56811cffb8643542fc8af4ce73cb6e1c6adc3fe460ce9a66067",
        "vendor-patch-whitespace-audit.json":
            "c17133d9c118e053cf70ec2aa7f4d2e147e6a8b388e1171bb8d84ae2633711b8",
    }
    retained = []
    for name, checksum in copies.items():
        p = old_logs/name
        if not p.exists():
            continue
        if not p.is_file() or p.is_symlink() or sha256(p) != checksum:
            raise RuntimeError("prior source-proof artifact changed: "+name)
        shutil.copy2(p, out/name)
        retained.append(name)
    (out/"resume-provenance.json").write_text(json.dumps({
        "resume_source": str(WORK),
        "upstream_main": UPSTREAM_MAIN,
        "generic_stack": GENERIC_STACK,
        "source_diff_sha256": expected,
        "old_compiled_objects_reused": False,
        "retained_proof_files": retained,
    }, indent=2, sort_keys=True)+"\n")
    note("RESUME=exact upstream source and general fixes verified")


def initialize_exact_submodules(out):
    """Materialize recorded third-party revisions, with no changes to emulator code."""
    f = SRC/".gitmodules"
    if not f.is_file() or f.is_symlink():
        raise RuntimeError("upstream .gitmodules missing or unsafe")
    blob = run(["git", "hash-object", ".gitmodules"], cwd=SRC).stdout.strip()
    if blob != "6ebfefa22267aa36fae25acb60678cf05c473a70":
        raise RuntimeError("submodule manifest is not the verified upstream revision")
    note("SUBMODULES=fetch exact recorded dependency commits")
    note("SUBMODULE_LOG=" + str(out/"submodules-update.log"))
    run(["git", "submodule", "sync", "--recursive"],
        log=out/"submodules-sync.log", cwd=SRC, timeout=240)
    run(["git", "submodule", "update", "--init", "--recursive", "--jobs", "4"],
        log=out/"submodules-update.log", cwd=SRC, timeout=5400)
    lines = run(["git", "submodule", "status", "--recursive"],
                cwd=SRC, timeout=180).stdout.splitlines()
    if len(lines) < 15 or any(not line.startswith(" ") for line in lines):
        raise RuntimeError("incomplete/unexpected submodule revisions: "+repr(lines[:12]))
    for rel in ("externals/ext-boost", "externals/spdlog", "externals/fmt",
                "externals/ffmpeg-core", "externals/zlib-ng",
                "externals/vulkan-headers", "externals/sdl3"):
        if not (SRC/rel/"CMakeLists.txt").is_file():
            raise RuntimeError("required upstream dependency missing: "+rel)
    (out/"submodule-revisions.txt").write_text(
        "verified_gitmodules_blob=" + blob + "\n" + "\n".join(lines) + "\n")
    note("SUBMODULES=PASS exact pinned modules "+str(len(lines)))


def build(out):
    # Upstream only declares the emulator with ENABLE_TESTS=OFF, while the
    # GCN test target requires ENABLE_TESTS=ON. Keep their CMake caches separate.
    BUILD.mkdir(parents=True, exist_ok=True)
    test_build = WORK/"build-gcn-tests"
    test_build.mkdir(parents=True, exist_ok=True)
    flags = ["-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
             "-DENABLE_UPDATER=OFF"]
    if shutil.which("ccache"):
        flags += ["-DCMAKE_C_COMPILER_LAUNCHER=ccache",
                  "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache"]
    note("CONFIGURE=emulator ENABLE_TESTS=OFF")
    run(["cmake", "-S", SRC, "-B", BUILD, *flags,
         "-DENABLE_TESTS=OFF", "-DENABLE_CPU_ID_TRANSLATION=ON"],
        log=out/"configure.log", timeout=2400)
    note("BUILD=emulator CPU translation enabled")
    run(["cmake", "--build", BUILD, "--target", "shadps4",
         "--parallel", "4"], log=out/"build.log", timeout=7200)
    note("CONFIGURE=GCN tests ENABLE_TESTS=ON")
    run(["cmake", "-S", SRC, "-B", test_build, *flags,
         "-DENABLE_TESTS=ON", "-DENABLE_CPU_ID_TRANSLATION=OFF"],
        log=out/"gcn-configure.log", timeout=2400)
    note("BUILD=independent GCN regression tests")
    run(["cmake", "--build", test_build, "--target", "shadps4_gcn_test",
         "--parallel", "4"], log=out/"gcn-build.log", timeout=7200)
    test_bins = [p for p in test_build.rglob("shadps4_gcn_test")
                 if p.is_file() and not p.is_symlink() and os.access(p, os.X_OK)]
    if len(test_bins) != 1:
        raise RuntimeError("cannot uniquely locate built GCN test binary")
    test_report = out/"gcn-tests.json"
    run([test_bins[0], "--gtest_output=json:"+str(test_report)],
        log=out/"gcn-tests.log", timeout=1800)
    if not test_report.is_file():
        raise RuntimeError("GCN tests produced no results")
    suite = json.loads(test_report.read_text())
    if int(suite.get("tests", 0)) < 1 or int(suite.get("failures", 0)):
        raise RuntimeError("GCN test count/failure gate did not pass")
    note("GCN_TESTS=PASS total="+str(suite["tests"]))
    binary = BUILD/"shadps4"
    if not binary.is_file():
        raise RuntimeError("fresh-main build produced no shadps4")
    run(["ldd", binary], log=out/"ldd.log", timeout=30)
    if "not found" in (out/"ldd.log").read_text(errors="replace"):
        raise RuntimeError("fresh-main binary has unresolved libraries")
    # No game-id dispatch, targeted runtime overrides, or previous GoW test
    # instrumentation may be linked into the proof executable.
    markers = [b"SHADPS4_GOW_", b"CUSA34384", b"GOW_SRT_",
               b"GOW43_", b"GOW57_", b"GOW58_"]
    carry = b""
    with binary.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            window = carry + chunk
            hits = [x.decode() for x in markers if x in window]
            if hits:
                raise RuntimeError("game-specific binary markers: "+repr(hits))
            carry = window[-64:]
    (out/"binary-provenance.json").write_text(json.dumps({
        "sha256": sha256(binary), "no_game_specific_binary_markers": True,
        "readbacks_override": False, "upstream_main": UPSTREAM_MAIN,
        "gcn_test_count": int(suite["tests"]), "gcn_test_failures": int(suite["failures"]),
    }, indent=2)+"\n")
    return binary

def gameplay(binary, out):
    auto = HELPERS/"auto.py"
    if not auto.is_file():
        fetch(HELPER_URLS["auto.py"], auto)
    run([sys.executable, "-m", "py_compile", auto], timeout=30)
    run([sys.executable, auto, "--self-test"], log=out/"auto-self-test.log", timeout=60)
    digest = sha256(binary)
    note("GAMEPLAY_CAPTURE=readbacks 0; no SHADPS4_GOW_*; Cross input")
    note("GAMEPLAY_LOG="+str(out/"gameplay-automation.log"))
    command = [sys.executable, str(auto),
               "--binary", str(binary), "--sha256", digest,
               "--seconds", "300", "--interval", "10", "--after-delay", "1",
               "--readbacks-mode", "0"]
    rc = run(command, log=out/"gameplay-automation.log", timeout=900, check=False)
    lines = (out/"gameplay-automation.log").read_text(errors="replace").splitlines()
    captures = []
    for line in lines:
        if not line.startswith("UPLOAD="):
            continue
        candidate = Path(line[len("UPLOAD="):].strip())
        if (candidate.is_file() and not candidate.is_symlink() and
                candidate.resolve().is_relative_to(LOG_ROOT.resolve())):
            captures.append(candidate)
    capture = captures[-1] if captures else None
    note("AUTOMATION_EXIT="+str(rc)+" CAPTURE="+str(capture or "MISSING"))
    return rc, digest, capture


def summarize_capture(archive):
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        if len(members) > 1500:
            raise RuntimeError("capture archive has unexpected member count")
        metadata = [m for m in members if m.isfile() and m.name.endswith("/result.json")]
        if len(metadata) != 1 or metadata[0].size > 1024 * 1024:
            raise RuntimeError("capture result.json missing or ambiguous")
        fp = tf.extractfile(metadata[0])
        if fp is None:
            raise RuntimeError("capture result cannot be read")
        report = json.loads(fp.read().decode("utf-8"))
        shots = [m for m in members if m.isfile() and
                 m.name.lower().endswith((".jpg", ".jpeg", ".png"))]
        distinct = set()
        for m in shots[:200]:
            if m.size > 15 * 1024 * 1024:
                raise RuntimeError("screenshot unexpectedly large")
            stream = tf.extractfile(m)
            if stream:
                distinct.add(hashlib.sha256(stream.read()).hexdigest())
        return {
            "screenshot_count": len(shots),
            "distinct_screenshot_files": len(distinct),
            "cross_presses": int(report.get("cross_presses") or 0),
            "capture_cycles": int(report.get("cycles") or 0),
            "guest_returncode": report.get("returncode"),
            "automation_failure": report.get("failure"),
            "visual_sequence_collected": (
                len(shots) >= 2 and len(distinct) >= 2 and
                int(report.get("cross_presses") or 0) > 0),
            "rendered_gameplay_verified": False,
            "cross_title_verified": False,
        }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = LOG_ROOT/f"gow-main-proof-{stamp}-{os.getpid()}"
    out.mkdir(mode=0o700)
    result = {
        "status":"STARTED", "phase":"SOURCE",
        "upstream_main":UPSTREAM_MAIN, "generic_stack":GENERIC_STACK,
        "old_gow_binary_used_as_input":False,
        "readbacks_mode":0, "gow_behavior_env":[],
        "automatic_conflict_resolution":"verified_schema_version_union_only",
        "installed_launcher_modified":False,
        "cross_title_verified":False,
        "rendered_gameplay_verified":False,
    }
    try:
        prepare_source(out)
        result["upstream_main"] = UPSTREAM_MAIN
        version_union = out/"shader-cache-version-union.json"
        if version_union.is_file():
            result["shader_cache_schema_union"] = json.loads(version_union.read_text())
        result["phase"]="GENERIC_FIXES"
        note("PATCH=generic fixes only")
        transforms = apply_generic_fixes()
        (out/"generic-transform-proof.json").write_text(json.dumps(
            transforms, indent=2, sort_keys=True)+"\n")
        (out/"helpers-sha256.json").write_text(json.dumps(
            {name: {"sha256": sha256(HELPERS/name), "source": url}
             for name, url in HELPER_URLS.items()}, indent=2, sort_keys=True) + "\n")
        audit_source(out)
        result["phase"]="BUILD"
        binary = build(out)
        result["binary"]=str(binary)
        result["binary_sha256"]=sha256(binary)
        result["status"]="BUILD_PASS"
        if args.build_only:
            package(out, result)
            return 0
        result["phase"]="GAMEPLAY"
        rc, digest, capture = gameplay(binary, out)
        result["gameplay_automation_exit"] = rc
        result["binary_sha256"] = digest
        result["capture_archive"] = str(capture) if capture else None
        if capture is not None:
            result["capture_evidence"] = summarize_capture(capture)
        result["rendered_gameplay_verified"] = False
        result["cross_title_verified"] = False
        capture_ok = bool(result.get("capture_evidence", {}).get("visual_sequence_collected"))
        result["status"] = ("VISUAL_SEQUENCE_CAPTURED_UNREVIEWED"
                            if rc == 0 and capture_ok else "CAPTURE_OR_INPUT_INCOMPLETE")
        package(out, result)
        return 0 if rc == 0 and capture_ok else 1
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (out/"traceback.txt").write_text(traceback.format_exc())
        note("FAILED="+repr(exc))
        package(out, result)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
