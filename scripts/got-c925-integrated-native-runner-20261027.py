#!/usr/bin/env python3
"""One controlled native GoT gameplay/graphics test on the recovered c925 source.

Source experiment: known menu-positive c925 + original PR74 coherence + one
locally resolved PR75/77/78 compact typed GPU patch. No PR78 crash-lineage
checkout and no changes to existing binaries, worktrees, ES-DE, SSH or GPU setup.
This is an experiment, not a claim that a source fixture proves rendered pixels.
"""
from __future__ import annotations

from datetime import datetime
import gzip
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
import time
import traceback
import urllib.request
import zipfile

REPO = "Chreece/shadPS4"
C925 = "c925523b49ef39434024b16b2b014d120471ee1a"
MENU_ELF = "260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d"
PATCH_REV = "978fd068e8c7b9d57edbcae5e1a21b28700b91da"
PATCHES = [
    ("PR74-coherence.patch", "scripts/patchsets/c925-pr74-adapted-source.patch",
     "91f4857007e579933cf88fd27a51418a9b63c312"),
    ("c925-integrated-typed.patch", "scripts/patchsets/c925-verified-integrated-typed-gpu-20261027.patch",
     "94b9152c5cb7db8d687082d91cba506e027064bc"),
]
ROOT = Path.home() / "projects/shadps4/GhostOfTsushima"
CLEAN = ROOT / "worktrees/got-proven-20261026"
PROVEN = ROOT / "candidates" / f"{C925}-strided-precise-image-readbacks-{MENU_ELF}" / "shadps4"
GAME = Path("/mnt/roms-all/ps4-games/CUSA11456/eboot.bin")
LOGS = Path.home() / "projects/shadps4/logs"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
EXP = ROOT / "experiments" / f"c925-integrated-gpu-{STAMP}"
SRC = EXP / "source"
BUILD = EXP / "build"
OUT = LOGS / f"c925-integrated-gpu-{STAMP}"
MODEL_SNAPSHOT = OUT / "ollama-models.json"
STATE = {"record_type": "LOCAL_C925_INTEGRATED_GPU_TRIAL", "schema_version": 2,
         "c925_commit": C925, "patch_rev": PATCH_REV,
         "game": "CUSA11456", "status": "PREFLIGHT", "game_exit": None,
         "source_rollback": "NOT_REQUIRED_NEW_ISOLATED_WORKTREE",
         "proven_binary_overwritten": False, "tested_games": [],
         "patches": [x[1] for x in PATCHES]}
LOGS.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "patches").mkdir(exist_ok=True)


class Abort(Exception):
    def __init__(self, status: str, code: int = 7):
        self.status, self.code = status, code
        super().__init__(status)


def stage(status: str) -> None:
    STATE["status"] = status
    with (OUT / "stages.txt").open("a") as stream:
        stream.write(status + "\n")
    print("STAGE=" + status, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def command(name: str, cmd: list[str], *, cwd: Path | None = None, timeout: int = 180,
            show: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    log = OUT / (name + ".log")
    with log.open("w") as stream:
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=stream,
                                  stderr=subprocess.STDOUT, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            stream.write(str(error) + "\n")
            raise Abort(name.upper() + "_PROCESS_ERROR") from error
    if show:
        print(f"{name}: exit={proc.returncode}", flush=True)
    if proc.returncode:
        print("COMMAND_FAILED=" + name, file=sys.stderr)
        print("\n".join(log.read_text(errors="replace").splitlines()[-45:]), file=sys.stderr)
        raise Abort(name.upper() + f"_EXIT_{proc.returncode}")
    return proc


def output(name: str, args: list[str], *, cwd: Path | None = None) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env, timeout=20)
    if p.returncode:
        raise Abort(name.upper() + "_FAILED", 2)
    return p.stdout.strip()


def preflight() -> None:
    stage("VERIFY_PROTECTED_MENU_ELF_AND_LOCAL_C925")
    for prog in ("git", "curl", "cmake", "ninja", "c++", "timeout", "python3", "tar"):
        if not shutil.which(prog):
            raise Abort("MISSING_" + prog, 2)
    if not (CLEAN / ".git").exists() or not PROVEN.is_file() or not GAME.is_file():
        raise Abort("PROVEN_SOURCE_ELF_OR_GAME_MISSING", 2)
    if sha256(PROVEN) != MENU_ELF:
        raise Abort("PROTECTED_PROVEN_MENU_ELF_SHA_MISMATCH", 2)
    if EXP.exists():
        raise Abort("EXPERIMENT_PATH_ALREADY_EXISTS", 2)
    output("local_source", ["git", "-C", str(CLEAN), "cat-file", "-t", C925 + "^{commit}"])
    (OUT / "proven-elf.txt").write_text("sha256=" + MENU_ELF + "\npath=" + str(PROVEN) + "\n")


def prepare_source() -> None:
    stage("CREATE_NEW_ISOLATED_C925_WORKTREE")
    EXP.mkdir(parents=True, exist_ok=False)
    command("worktree-create", ["git", "-C", str(CLEAN), "worktree", "add", "--detach", str(SRC), C925])
    if output("source_head", ["git", "-C", str(SRC), "rev-parse", "HEAD"]) != C925:
        raise Abort("CREATED_SOURCE_COMMIT_MISMATCH")
    rasterizer = SRC / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
    blob = output("rasterizer_blob", ["git", "-C", str(SRC), "hash-object", str(rasterizer)])
    if blob != "2309395360af9067fd3bee671e3b1553b3cc7aa0":
        raise Abort("C925_RASTERIZER_BLOB_DIFFERENT")


def fetch_apply_patch(filename: str, path: str, expected: str) -> None:
    dest = OUT / "patches" / filename
    url = f"https://raw.githubusercontent.com/{REPO}/{PATCH_REV}/{path}"
    stage("DOWNLOAD_AND_APPLY_" + filename)
    command("download-" + filename, ["curl", "-4", "--http1.1", "-fLsS", "--retry", "3", url,
                                     "-o", str(dest)], timeout=100)
    actual = output("gitblob-" + filename, ["git", "-C", str(SRC), "hash-object", str(dest)])
    if actual != expected:
        raise Abort("PATCH_GIT_BLOB_MISMATCH_" + filename)
    command("check-" + filename, ["git", "-C", str(SRC), "apply", "--check", str(dest)])
    command("apply-" + filename, ["git", "-C", str(SRC), "apply", str(dest)])
    command("diffcheck-" + filename, ["git", "-C", str(SRC), "diff", "--check"])
    print(f"APPLIED={filename} VERIFIED_GIT_BLOB={expected}", flush=True)


def tests() -> None:
    stage("RUN_REAL_CPP_NDEBUG_MATERIAL_FIXTURES")
    tests = ["tests/guest_descriptor_coherence_test.cpp", "tests/compact_image_indices_test.cpp"]
    for compiler in ("g++", "clang++"):
        if not shutil.which(compiler):
            if compiler == "g++":
                raise Abort("GCC_MISSING_FOR_SOURCE_TEST")
            continue
        for test in tests:
            target = OUT / (Path(test).stem + "-" + compiler.replace("+", "p"))
            command("build-" + target.name,
                    [compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                     "-pedantic", "-I", str(SRC / "src"), str(SRC / test), "-o", str(target)], timeout=90)
            command("test-" + target.name, [str(target)], timeout=25)
            print("CPP_TEST_PASS=" + compiler + " " + test, flush=True)
    source = SRC / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
    shader = SRC / "src/shader_recompiler/backend/spirv/emit_spirv_image.cpp"
    src = source.read_text()
    sh = shader.read_text()
    for required in ("GpuDescriptorTableCpuCoherence", "image_prefix", "IndexedUniformSamplerBound",
                     "CompactImageIndices::Assign", "GuestIndexedImageDimensions"):
        # IndexedUniformSamplerBound belongs in shader resource patching code.
        content = (SRC / "src/shader_recompiler/ir/passes/resource_patching_pass.cpp").read_text() if required == "IndexedUniformSamplerBound" else src
        if required not in content:
            raise Abort("REQUIRED_RENDERER_CODE_MISSING_" + required)
    if "compact_index_map" not in sh or "OpDPdx" not in sh:
        raise Abort("SHADER_GPU_TYPED_REMAP_OR_SAFE_DERIVATIVES_MISSING")
    instance_source = (SRC / "src/video_core/renderer_vulkan/vk_instance.h").read_text()
    if instance_source.count("bool IsShaderImageGatherExtendedSupported() const") != 1:
        raise Abort("VULKAN_DUPLICATE_GATHER_QUERY_SOURCE_ERROR")
    (OUT / "source-sha256.json").write_text(json.dumps({
        "vulkan_rasterizer": sha256(source), "spirv_image_emitter": sha256(shader)}, indent=2) + "\n")


FFMPEG_CORE_COMMIT = "94dde08c8a9e4271a93a2a7e4159e9fb05d30c0a"
FFMPEG_ASSET_SHA256 = "aacbbfb8e622b684bc5d3b4cd6c9f9f77f5def64ae8d83c0c5b3ebe657aa33dd"


def prefetch_verified_ffmpeg() -> None:
    """Prepopulate only a SHA-pinned FFmpeg asset in the isolated build.

    c925 uses the same ext-ffmpeg-core gitlink proven in the 26-Oct native
    build. Never substitute a different release or touch a system package.
    """
    stage("PREFETCH_VERIFIED_FFMPEG")
    submodule = SRC / "externals/ffmpeg-core"
    if output("ffmpeg_core_commit", ["git", "-C", str(submodule), "rev-parse", "HEAD"]) != FFMPEG_CORE_COMMIT:
        raise Abort("UNKNOWN_FFMPEG_CORE_REVISION", 4)
    target = BUILD / "externals/ffmpeg-94dde08.zip"
    target.parent.mkdir(parents=True, exist_ok=True)

    def valid(path: Path) -> bool:
        try:
            if not path.is_file() or sha256(path) != FFMPEG_ASSET_SHA256:
                return False
            with zipfile.ZipFile(path) as archive:
                names = {Path(name).name for name in archive.namelist()}
            return {"libavcodec.a", "libavformat.a"} <= names
        except (OSError, ValueError, zipfile.BadZipFile):
            return False

    if target.exists() and not valid(target):
        raise Abort("EXISTING_ISOLATED_FFMPEG_CACHE_INCORRECT", 4)

    if not target.exists():
        search_dir = Path.home() / "projects/shadps4"
        known = [ROOT / "experiments/mixed-types-82cc0cdd2e996cc747106b0a0bb9a6ebc5f4a104"
                 / "build/externals/ffmpeg-94dde08.zip"]
        try:
            found = subprocess.run(
                ["find", str(search_dir), "-type", "f", "-name", "ffmpeg-94dde08.zip"],
                capture_output=True, text=True, timeout=90, check=False)
            if found.returncode == 0:
                known += [Path(line) for line in found.stdout.splitlines()]
            else:
                (OUT / "ffmpeg-cache-search.log").write_text(found.stderr)
        except (OSError, subprocess.TimeoutExpired) as error:
            (OUT / "ffmpeg-cache-search.log").write_text(str(error) + "\n")
        for candidate in known:
            if candidate == target or not valid(candidate):
                continue
            shutil.copyfile(candidate, target)
            STATE["ffmpeg_verified_source"] = str(candidate)
            break

    if not target.exists():
        stage("DOWNLOAD_PINNED_FFMPEG_RELEASE_ASSET")
        urls = [
            "https://github.com/shadps4-emu/ext-ffmpeg-core/releases/download/94dde08/ffmpeg-linux-x64.zip",
            "https://api.github.com/repos/shadps4-emu/ext-ffmpeg-core/releases/assets/327924109",
        ]
        for index, url in enumerate(urls):
            temp = target.with_name(f"ffmpeg-94dde08-attempt-{index}.zip")
            with (OUT / f"ffmpeg-download-{index}.log").open("w") as log:
                try:
                    attempt = subprocess.run(
                        ["curl", "-4", "--http1.1", "-fLsS", "--retry", "3",
                         "--retry-all-errors", "--connect-timeout", "15",
                         "--max-time", "240", "-H", "Accept: application/octet-stream",
                         "-H", "User-Agent: shadps4-native-test", url, "-o", str(temp)],
                        stdout=log, stderr=subprocess.STDOUT, timeout=255, check=False)
                    log.write("curl_exit=" + str(attempt.returncode) + "\n")
                except (OSError, subprocess.TimeoutExpired) as error:
                    log.write("download_error=" + str(error) + "\n")
            if valid(temp):
                temp.replace(target)
                STATE["ffmpeg_verified_source"] = url
                break
            temp.unlink(missing_ok=True)

    if not valid(target):
        raise Abort("PINNED_FFMPEG_ASSET_UNAVAILABLE_OR_INVALID", 4)
    STATE["ffmpeg_sha256"] = FFMPEG_ASSET_SHA256
    (OUT / "ffmpeg-verified.txt").write_text(
        "gitlink=" + FFMPEG_CORE_COMMIT + "\nasset_sha256=" + FFMPEG_ASSET_SHA256 + "\n"
        + "source=" + STATE.get("ffmpeg_verified_source", "existing-verified-cache") + "\n")
    print("VERIFIED_FFMPEG_ASSET=" + FFMPEG_ASSET_SHA256, flush=True)


def build() -> Path:
    stage("INIT_DEPENDENCIES_IN_NEW_WORKTREE_ONLY")
    command("submodules", ["git", "-C", str(SRC), "-c", "http.version=HTTP/1.1",
                           "submodule", "update", "--init", "--recursive", "--jobs", "8"], timeout=1200)
    prefetch_verified_ffmpeg()
    stage("CONFIGURE_NATIVE_RELEASE")
    command("cmake-configure", ["cmake", "-S", str(SRC), "-B", str(BUILD), "-G", "Ninja",
                                "-DCMAKE_BUILD_TYPE=Release", "-DENABLE_TESTS=OFF",
                                "-DENABLE_DISCORD_RPC=OFF"], timeout=360)
    stage("FULL_NATIVE_CPP_COMPILE_AND_LINK")
    # Build in this isolated new tree; no installed shadPS4 ELF is replaced.
    command("native-build", ["cmake", "--build", str(BUILD), "--parallel", "8", "--", "-k", "0"],
            timeout=3600, show=True)
    elf = BUILD / "shadps4"
    if not (elf.is_file() and os.access(elf, os.X_OK)):
        raise Abort("LINK_COMPLETED_BUT_SHADPS4_MISSING")
    digest = sha256(elf)
    STATE["candidate_sha256"] = digest
    (OUT / "candidate.txt").write_text("source_commit=" + C925 + "\nnew_elf_sha256=" + digest + "\n")
    stage("FULL_NATIVE_LINK_PASS")
    return elf




def prepare_portable_user_data() -> None:
    """Keep emulated save writes in BUILD/user, not the user's original profile.

    c925 path_util.cpp chooses <cwd>/user when it exists; launch() sets cwd=BUILD.
    Copy old settings/saves as actual files, never symlink or hardlink them.
    The user profile itself is never moved, deleted or edited.
    """
    stage("PREPARE_ISOLATED_PORTABLE_USER_PROFILE")
    portable = BUILD / "user"
    if portable.exists():
        raise Abort("PORTABLE_PROFILE_PREEXISTS_IN_NEW_BUILD")
    portable.mkdir(mode=0o700)
    base = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")) / "shadPS4"
    summary = {"portable": str(portable), "source_profile_found": base.is_dir(),
               "copied_files": 0, "copied_bytes": 0, "skipped_symlinks": 0,
               "skipped_large_dirs": [], "profile_protected": True}
    # Configuration can point home_dir outside the portable tree. Normalize
    # every source of guest save writes to this experiment's own home directory.
    for name in ("config.json", "users.json"):
        src = base / name
        if not src.is_file() or src.is_symlink():
            continue
        try:
            if src.stat().st_size > 4 * 1024 * 1024:
                summary["skipped_large_dirs"].append(name)
                continue
            if name == "config.json":
                raw = json.loads(src.read_text())
                general = raw.get("General")
                if isinstance(general, dict):
                    general["home_dir"] = ""
                    general["addon_install_dir"] = ""
                    # Direct sys_modules and font_dir back to normal READ-ONLY
                    # dependencies when user has configured custom locations.
                (portable / name).write_text(json.dumps(raw, indent=2) + "\n")
            else:
                shutil.copyfile(src, portable / name)
            summary["copied_files"] += 1
            summary["copied_bytes"] += src.stat().st_size
        except (OSError, ValueError, json.JSONDecodeError):
            summary["skipped_large_dirs"].append(name + "_copy_failed")
    # Save data used by Continue can be copied privately, but never via
    # symlinks (which could point back to live saves). Cap copies at 1 GiB.
    max_copy = 1024 ** 3
    for dirname in ("home", "savedata"):
        src = base / dirname
        dest = portable / dirname
        if not src.is_dir() or src.is_symlink():
            continue
        files: list[tuple[Path, Path, int]] = []
        size = 0
        for root, dirs, filenames in os.walk(src, followlinks=False):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
            for filename in filenames:
                item = root_path / filename
                if item.is_symlink() or not item.is_file():
                    summary["skipped_symlinks"] += 1
                    continue
                st = item.stat()
                size += st.st_size
                if size > max_copy - summary["copied_bytes"]:
                    break
                files.append((item, dest / item.relative_to(src), st.st_size))
            if size > max_copy - summary["copied_bytes"]:
                break
        if size > max_copy - summary["copied_bytes"]:
            summary["skipped_large_dirs"].append(dirname)
            continue
        for frm, to, amount in files:
            to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(frm, to)
            summary["copied_bytes"] += amount
            summary["copied_files"] += 1
    STATE["portable_user_data"] = summary
    (OUT / "portable-profile-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    # Deliberately never include the private copied saves/config in UPLOAD_THIS.


def free_vram() -> int:
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*/device")):
        try:
            if (card / "vendor").read_text().strip() == "0x1002":
                total = int((card / "mem_info_vram_total").read_text())
                used = int((card / "mem_info_vram_used").read_text())
                return total - used
        except (OSError, ValueError):
            continue
    raise Abort("AMD_VRAM_HEADROOM_UNAVAILABLE", 5)


def offload_if_needed() -> None:
    stage("CHECK_8GIB_VRAM_FOR_GPU")
    free = free_vram()
    (OUT / "vram.txt").write_text(f"free_initial={free}\n")
    if free >= 8 * 2**30:
        return
    try:
        base = "http://127.0.0.1:7869"
        models = json.loads(urllib.request.urlopen(base + "/api/ps", timeout=8).read()).get("models", [])
        MODEL_SNAPSHOT.write_text(json.dumps(models) + "\n")
        for model in models:
            name = model.get("name") or model.get("model")
            if not name:
                continue
            params = json.dumps({"model": name, "prompt": "", "stream": False, "keep_alive": 0}).encode()
            request = urllib.request.Request(base + "/api/generate", params,
                                             {"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=40).read()
    except Exception as exc:
        (OUT / "ollama-offload-error.txt").write_text(str(exc))
        raise Abort("VRAM_LOW_CANNOT_OFFLOAD_OLLAMA", 5) from exc
    time.sleep(2)
    free = free_vram()
    with (OUT / "vram.txt").open("a") as stream:
        stream.write(f"free_after_offload={free}\n")
    if free < 8 * 2**30:
        raise Abort("VRAM_STILL_BELOW_8GIB", 5)


def restore_ollama() -> None:
    if not MODEL_SNAPSHOT.exists():
        return
    try:
        for model in json.loads(MODEL_SNAPSHOT.read_text()):
            name = model.get("name") or model.get("model")
            if not name:
                continue
            params = {"model": name, "prompt": "", "stream": False, "keep_alive": "5m"}
            if isinstance(model.get("context_length"), int):
                params["options"] = {"num_ctx": model["context_length"]}
            request = urllib.request.Request("http://127.0.0.1:7869/api/generate",
                        json.dumps(params).encode(), {"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=60).read()
    except Exception:
        (OUT / "ollama-restore-error.txt").write_text(traceback.format_exc())


def capture_screen(sec: int, env: dict[str, str]) -> None:
    target = OUT / f"menu-{sec}.png"
    if shutil.which("scrot"):
        cmd = ["scrot", str(target)]
    elif shutil.which("import"):
        cmd = ["import", "-window", "root", str(target)]
    else:
        return
    try:
        subprocess.run(cmd, env=env, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return


def launch(elf: Path) -> None:
    stage("GO_T_118SEC_GPU_RENDER_AND_TYPED_IMAGE_CAPTURE")
    env = dict(os.environ)
    if not env.get("DISPLAY"):
        sockets = sorted(Path("/tmp/.X11-unix").glob("X[0-9]*"))
        if sockets:
            env["DISPLAY"] = ":" + sockets[0].name[1:]
    cmd = ["timeout", "-s", "TERM", "-k", "2s", "118s", str(elf), "--game", str(GAME),
           "--fullscreen", "true", "--show-fps"]
    (OUT / "game-command.json").write_text(json.dumps(cmd) + "\n")
    # BUILD/user is intentionally the private, portable save directory
    # recognized by src/common/path_util.cpp; don't run with the live profile.
    if not (BUILD / "user").is_dir():
        raise Abort("ISOLATED_PORTABLE_PROFILE_MISSING")
    env["XDG_DATA_HOME"] = str(EXP / "xdg-data")
    (EXP / "xdg-data").mkdir(parents=True, exist_ok=True)
    with (OUT / "game-console.log").open("w") as stream:
        proc = subprocess.Popen(cmd, cwd=BUILD, env=env, stdout=stream,
                                stderr=subprocess.STDOUT)
        start = time.monotonic()
        # Poll regularly: don't sleep 40s after a game crash and mistake
        # missing screenshots for a normal 118s timeout.
        for sec in (40, 55, 85, 112):
            while proc.poll() is None and time.monotonic() < start + sec:
                time.sleep(min(0.6, max(0.01, start + sec - time.monotonic())))
            if proc.poll() is not None:
                break
            capture_screen(sec, env)
        try:
            exit_code = proc.wait(timeout=13)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                exit_code = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = proc.wait(timeout=5)
    STATE["game_exit"] = exit_code
    STATE["tested_games"] = ["CUSA11456"]
    log = (OUT / "game-console.log").read_text(errors="replace")
    events = []
    max_bound = {"2d": 0, "3d": 0, "cube": 0, "entries": 0}
    for line in log.splitlines():
        if any(token in line for token in ("GuestIndexedImageDimensions:", "IndexedUniformSamplerBound",
                                             "ErrorDeviceLost", "context is lost", "GPU reset", "GpuDescriptorTableCpuCoherence")):
            if len(events) < 500:
                events.append(line[:1250])
        if "GuestIndexedImageDimensions:" in line:
            for label, key in (("bound_2d", "2d"), ("bound_3d", "3d"),
                               ("bound_cube", "cube"), ("entries", "entries")):
                match = re.search(r"\b" + label + r"=(\d+)", line)
                if match:
                    max_bound[key] = max(max_bound[key], int(match.group(1)))
    (OUT / "key-gpu-events.txt").write_text("\n".join(events) + "\n")
    STATE["maximum_typed_bindings"] = max_bound
    STATE["gpu_device_lost"] = "ErrorDeviceLost" in log or "context is lost" in log
    STATE["screenshots"] = {f.name: sha256(f) for f in OUT.glob("menu-*.png")}
    STATE["no_graphics_frame_captured"] = len(STATE["screenshots"]) == 0
    STATE["source_commit_tested"] = output("tested_tree", ["git", "-C", str(SRC), "rev-parse", "HEAD"])
    if STATE["no_graphics_frame_captured"]:
        print("NO_SCREENSHOTS=GAME_EXITED_BEFORE_FIRST_FRAME_OR_CAPTURE_FAILED", flush=True)
    print("GAME_EXIT=" + str(exit_code), flush=True)
    print("MAX_GPU_GUEST_TABLE=" + json.dumps(max_bound), flush=True)
    print("SCREENSHOTS=" + str(len(STATE["screenshots"])), flush=True)
    print("GPU_DEVICE_LOST=" + str(STATE["gpu_device_lost"]), flush=True)
    stage("NATIVE_GPU_CAPTURE_COMPLETE_VISUAL_REVIEW_REQUIRED")
    if exit_code not in (0, 124):
        raise Abort("GAME_CRASH_OR_DEVICE_LOSS", 10)
    if STATE["no_graphics_frame_captured"]:
        raise Abort("NO_VISUAL_FRAME_EVIDENCE", 10)


def finish(rc: int) -> None:
    restore_ollama()
    # Verify the user's historically menu-capable ELF after the experiment.
    try:
        STATE["proven_elf_still_verified"] = PROVEN.is_file() and sha256(PROVEN) == MENU_ELF
    except OSError:
        STATE["proven_elf_still_verified"] = False
    (OUT / "trial.json").write_text(json.dumps(STATE, indent=2) + "\n")
    try:
        with (LOGS / "emulator-trials-local.jsonl").open("a") as stream:
            stream.write(json.dumps(STATE, separators=(",", ":")) + "\n")
    except OSError:
        pass
    full = OUT / "game-console.log"
    if full.exists():
        with full.open("rb") as raw, gzip.open(str(full) + ".gz", "wb", compresslevel=1) as compressed:
            shutil.copyfileobj(raw, compressed)
        full.unlink()
    archive = OUT.with_suffix(".tar.gz")
    try:
        with tarfile.open(archive, "w:gz", compresslevel=1) as bundle:
            bundle.add(OUT, arcname=".")
        print("UPLOAD_THIS=" + str(archive), flush=True)
    except OSError as error:
        print("ARCHIVE_WRITE_ERROR=" + str(error), file=sys.stderr)
    print("SSH_SESSION=OPEN", flush=True)
    print("PROVEN_MENU_ELF_UNMODIFIED=" + str(STATE["proven_elf_still_verified"]), flush=True)
    print("RESULT=" + STATE["status"], flush=True)


def main() -> int:
    result = 0
    try:
        preflight()
        prepare_source()
        for name, path, expected in PATCHES:
            fetch_apply_patch(name, path, expected)
        tests()
        elf = build()
        prepare_portable_user_data()
        offload_if_needed()
        launch(elf)
    except Abort as error:
        STATE["status"] = error.status
        result = error.code
        print("TRIAL_STOP=" + error.status, file=sys.stderr, flush=True)
    except BaseException:
        STATE["status"] = "UNEXPECTED_RUNNER_EXCEPTION"
        result = 9
        (OUT / "unexpected-python-error.txt").write_text(traceback.format_exc())
        print("TRIAL_STOP=UNEXPECTED_RUNNER_EXCEPTION", file=sys.stderr, flush=True)
    finally:
        finish(result)
    return result


if __name__ == "__main__":
    sys.exit(main())
