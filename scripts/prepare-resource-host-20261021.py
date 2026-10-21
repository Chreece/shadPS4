#!/usr/bin/env python3
"""Prepare the existing host builder without changing the checkout or submodules."""
import argparse
import ast
import hashlib
from pathlib import Path
import subprocess

HOST_BLOB = "08cf59c7693265d1b9af7106d18e7a7a720c7e45"
RUNTIME_BLOB = "006ca831bbbe4ee149d34c93f895d540dda44025"
NEW_HEADERS = (
    "src/shader_recompiler/buffer_access_range.h",
    "src/shader_recompiler/ir/passes/buffer_access_bounds.h",
)

def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("HOST_ANCHOR_NOT_UNIQUE=" + repr(old[:100]))
    return text.replace(old, new, 1)

def prepare(data: bytes) -> str:
    if blob(data) != HOST_BLOB:
        raise ValueError("PINNED_HOST_SCRIPT_BLOB_MISMATCH")
    text = data.decode("utf-8")
    text = once(text, "\nGOT_PINNED_OVERLAY_FILES\n",
                "\n" + "\n".join(NEW_HEADERS) + "\nGOT_PINNED_OVERLAY_FILES\n")
    text = once(text, '-eq 110 || {', '-eq 112 || {')
    anchor = 'STATUS=HOST_TESTS\ngit -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_sharp_fetch_bounds.cpp"'
    correction = '''STATUS=APPLYING_PINNED_RESOURCE_CORRECTION
git -C "$SOURCE" show "$CANDIDATE:scripts/apply-resource-access-ranges-20261021.py" \\
    > "$RUN/tmp/apply-resource-access-ranges.py"
python3 "$RUN/tmp/apply-resource-access-ranges.py" "$RUN/tmp/staged" "$RUN/info"
# Check every corrected source and new production header again just before
# staging. The manifest records what was built, not only the package revision.
'''
    text = once(text, anchor, correction + anchor)
    new_test = '''STATUS=BUFFER_ACCESS_RANGE_REGRESSION
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_buffer_access_range.cpp" \\
    > "$RUN/tmp/test-buffer-access-range.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror -I"$RUN/tmp/staged/src" \\
    "$RUN/tmp/test-buffer-access-range.cpp" -o "$RUN/tmp/test-buffer-access-range" \\
    >> "$RUN/host-tests.log" 2>&1 || {
        tail -100 "$RUN/host-tests.log"
        STATUS=BUFFER_ACCESS_RANGE_COMPILE_FAILED; exit 2;
    }
"$RUN/tmp/test-buffer-access-range" | tee -a "$RUN/host-tests.log" || {
    STATUS=BUFFER_ACCESS_RANGE_TEST_FAILED; exit 2;
}
python3 - "$RUN/tmp/staged" "$RUN/info/resource-correction.json" <<'PYRESOURCECHECK' | tee -a "$RUN/host-tests.log"
import hashlib
import json
from pathlib import Path
import sys
root, report = Path(sys.argv[1]), Path(sys.argv[2])
manifest = json.loads(report.read_text())
for relative, record in manifest["files"].items():
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == record["sha256"], relative
print("CORRECTED_SOURCE_MANIFEST=PASS")
PYRESOURCECHECK
'''
    anchor = 'echo "HOST_ONLY_TESTS=PASS" | tee -a "$RUN/host-tests.log"'
    text = once(text, anchor, new_test + '\n' + anchor)
    # Compile the real transformed pass and its final-IR consumer before the
    # renderer objects. Arithmetic-only helper tests do not type-check these.
    anchor = 'STATUS=BUILDING_VK_RASTERIZER_OBJECT_FIRST'
    compile_first = '''STATUS=COMPILE_RESOURCE_CORRECTION_OBJECTS
nice -n 10 ninja -C "$BUILD" -j 2 \\
    CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/resource_patching_pass.cpp.o \\
    CMakeFiles/shadps4.dir/src/shader_recompiler/recompiler.cpp.o \\
    > "$RUN/info/resource-correction-objects.log" 2>&1 || {
        tail -120 "$RUN/info/resource-correction-objects.log"
        STATUS=RESOURCE_CORRECTION_OBJECTS_FAILED
        exit 2
    }
echo "RESOURCE_CORRECTION_OBJECTS=PASS" | tee -a "$RUN/host-tests.log"

'''
    text = once(text, anchor, compile_first + anchor)
    return text

def prepare_runtime(data: bytes) -> str:
    if blob(data) != RUNTIME_BLOB:
        raise ValueError("PINNED_RUNTIME_SCRIPT_BLOB_MISMATCH")
    text = data.decode("utf-8")
    anchor = '        self.meta["HOST_REPORT_VERIFIED"] = "PASS"'
    extra = '\n        # Carry the exact source correction into the small runtime archive.\n        with tarfile.open(self.args.host_report, "r:gz") as provenance:\n            for name in ("resource-correction.json", "resource-correction.diff"):\n                member = provenance.getmember("info/" + name)\n                if not member.isfile() or member.size > 1024 * 1024:\n                    raise RuntimeError("INVALID_RESOURCE_CORRECTION_METADATA")\n                (self.run / "info" / name).write_bytes(provenance.extractfile(member).read())\n        self.meta["SOURCE_CORRECTION"] = "PINNED_RESOURCE_ACCESS_RANGES"\n'
    text = once(text, anchor, anchor + extra)
    ast.parse(text)
    return text

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    changed = prepare(args.input.read_bytes())
    subprocess.run(["bash", "-n"], input=changed, text=True, check=True)
    runtime = prepare_runtime(args.runtime.read_bytes()) if args.runtime else None
    args.output.write_text(changed)
    if runtime is not None:
        args.runtime.write_text(runtime)
    print("RESOURCE_HOST_PREPARATION=PASS overlay=112 submodule_changes=NONE")

if __name__ == "__main__":
    main()
