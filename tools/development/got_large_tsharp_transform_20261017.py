from pathlib import Path
import hashlib, sys, tarfile
archive_path, member, output_path = sys.argv[1:]
with tarfile.open(archive_path, 'r:gz') as archive:
    entry = archive.extractfile(member)
    assert entry is not None, 'PREVIOUS_WORKER_NOT_IN_ARCHIVE'
    data = entry.read()
assert hashlib.sha256(data).hexdigest()=='58dea8254fe9daff3e6025576008d7f827e57a239ee9ac3f5ac2dd574fda7ae2'
s=data.decode()
repl={
 '268db562d0f58df66a392b1f504c01ae84583c17':('e6af55857bc0de61248c010c7fa6ad5d244ab443',1),
 'fix/got-bounded-gpu-texture-index-20261017':('experiment/got-large-guest-tsharp-indexed-20261017',1),
 'a65198f8cd11889af853871062cddb3ccaa95e3b':('64ea04da09a993b266ab3740f4e38a85c762a61d',1),
 '5919132f0dabc988441f68f37b7d8e28384828a6':('0d4f3a25265b88eeb39703ef6095cac3bf978e09',1),
 '931e335c003f20d8e21dc1b9945676b5985633bb':('1f47d687087444c76b21eb9fbbc9f7b577f77c94',1),
 'c0990841245c7182b42fca09c630c75d7ffca35f':('67bc368c39ee99ea1d51ee662a11443aa0b11a0b',1),
 '000a55f7062cacf4619450dbe86812f1ad9f7d86':('05a2532b3161cfa6281f2c712b10d0638983ca63',1),
 '2c9d663077aeaf1388633eb38f27d3f808aa39e3':('6009480659526add9b045d68718515835d8e94fc',2),
 'SHADER_CACHE_VERSION=39':('SHADER_CACHE_VERSION=40',1),
 'CANDIDATE_KIND=graphics-and-hotplug':('CANDIDATE_KIND=large-guest-tsharp-indexed',2),
 '--depth=64':('--depth=256',2),
 'local shots="12 25 45 60 75"':('local shots="12 25 45 60 75 120 160"',1),
 'if run_phase B "$BIN" 90; then':('if run_phase B "$BIN" 180; then',1),
 'if ! run_phase C "$BIN" 75; then':('if ! run_phase C "$BIN" 120; then',1),
 'export SHADPS4_GUEST_TSHARP_CENSUS=1':('export SHADPS4_GUEST_TSHARP_LARGE_ARRAY=1 SHADPS4_GUEST_TSHARP_CENSUS=0',1),
}
for a,(b,count) in repl.items():
    assert s.count(a)==count,(repr(a),s.count(a),count)
    s=s.replace(a,b)
# Capture all touched pipeline/descriptor-unit files from the exact branch.
needle='apply_blob "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" "1f47d687087444c76b21eb9fbbc9f7b577f77c94"'
extra='''
apply_blob "src/video_core/renderer_vulkan/vk_resource_pool.h" "cf2d1248af856650f5b7e668bfd4f35953bea2b2"
apply_blob "src/video_core/renderer_vulkan/vk_resource_pool.cpp" "619eed4d68e3b8ef46b4f5bfd717aa67338bff48"
apply_blob "src/video_core/renderer_vulkan/vk_pipeline_common.h" "5179400f0667d3e1ee0b54bfc00752124d0b5827"
apply_blob "src/video_core/renderer_vulkan/vk_pipeline_common.cpp" "4ef906906de2f0823ef269b7c6e41a1a5fa62d2b"
apply_blob "src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp" "7a90245a36a7760d8414e41740c678af7b3e5ad5"
apply_blob "src/video_core/renderer_vulkan/vk_compute_pipeline.cpp" "12bb7f91f3cdccfe31a2bd323d9387057d2b8718"
apply_blob "src/video_core/renderer_vulkan/vk_rasterizer.h" "caba62525485731afc673964337fdb6704a3f9df"'''
assert s.count(needle)==1
s=s.replace(needle,needle+extra)
# Fast fail on the highest-risk new compilation units.
needle='  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o \\\n'
added='''  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_resource_pool.cpp.o \\
  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_common.cpp.o \\
  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp.o \\
  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_compute_pipeline.cpp.o \\
  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp.o \\
'''
assert s.count(needle)==1,(needle,s.count(needle))
s=s.replace(needle,needle+added)
# Re-fetch missing source ancestors only; never checkout/change the user's tree.
needle='git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM" "$CANDIDATE"\n# Copy only SHA-pinned C++ source'
replacement='''if ! git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM" "$CANDIDATE"; then
    if [ "$(git -C "$SOURCE" rev-parse --is-shallow-repository)" = true ]; then
        git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \\
            fetch --deepen=256 --no-tags --no-recurse-submodules \\
            https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH"
    fi
fi
git -C "$SOURCE" merge-base --is-ancestor "$UPSTREAM" "$CANDIDATE" || {
    echo "UPSTREAM_ANCESTRY_UNPROVEN_NO_OVERLAY_APPLIED"
    exit 2
}
# Copy only SHA-pinned C++ source'''
assert s.count(needle)==1
s=s.replace(needle,replacement)
assert s.count('SHADPS4_GUEST_TSHARP_LARGE_ARRAY=1')==1
assert s.count('RUN=$NAME')==0
path=Path(output_path)
path.write_text(s)
path.chmod(0o700)
assert hashlib.sha256(s.encode()).hexdigest()=='b0fcc0cf59e4ef7f40e06705aeaf783849d7ddddf25044cdcfe5eeac55e13db0', 'GENERATED_WORKER_SHA_MISMATCH'
print('GEN_WORKER_SHA256='+hashlib.sha256(s.encode()).hexdigest())
print('LINES='+str(len(s.splitlines())))
