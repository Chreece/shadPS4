from pathlib import Path
import re,hashlib,tarfile,sys
assert len(sys.argv)==3, 'usage: repair.py <archived_worker.tar.gz> <output.worker.sh>'
arc=Path(sys.argv[1])
with tarfile.open(arc,'r:gz') as tf:
    original=tf.extractfile('got-large-tsharp-20261017-231729.worker.sh').read()
expected='b0fcc0cf59e4ef7f40e06705aeaf783849d7ddddf25044cdcfe5eeac55e13db0'
assert hashlib.sha256(original).hexdigest()==expected
s=original.decode()
for old,new in [
  ('apply_blob "src/shader_recompiler/ir/passes/resource_discover_pass.cpp" "71cbd3f9f87dca933f425534955364f054540a05"',
   'apply_blob "src/shader_recompiler/ir/passes/resource_discover_pass.cpp" "420020e89e1d565433fabda634e54eb7bedcb3b2"'),
  ('apply_blob "src/shader_recompiler/ir/passes/indexed_image_selector.h" "4a3791918648121788f8f1b14df95ebda50e4b45"',
   'apply_blob "src/shader_recompiler/ir/passes/indexed_image_selector.h" "24c45c206819755e7cc2624df49fdbda82a273c1"')]:
    assert s.count(old)==1,(old,s.count(old))
    s=s.replace(old,new)
marker='apply_blob "src/video_core/page_manager.cpp" "3a7b232e10acb2a9db0507f87ca2c2231c450dd4"'
assert s.count(marker)==1
preflight='''# Validate every pinned overlay against this exact Git commit BEFORE
# touching any source file. The manifest contains 57 applications of 55
# unique blobs and must never contain files from a different experiment.
if ! python3 - "$SOURCE" "$CANDIDATE" "$RUN/info/overlay-manifest-check.tsv" \\
    "$LOGS/$NAME.worker.sh" <<'PYMANIFEST'; then
import pathlib, re, subprocess, sys
source, commit, report, worker = sys.argv[1:]
code = pathlib.Path(worker).read_text()
items = re.findall(r'^apply_blob "([^"\\n]+)" "([0-9a-f]{40})"$', code, re.M)
assert len(items) == 57, f"OVERLAY_MANIFEST_WRONG_COUNT:{len(items)}"
listed = subprocess.check_output(
    ['git', '-C', source, 'ls-tree', '-r', commit, '--', 'src'], text=True)
blobs = {}
for line in listed.splitlines():
    prefix, path = line.split('\\t', 1)
    mode, kind, sha = prefix.split()
    if kind == 'blob':
        blobs[path] = sha
bad = [(p, want, blobs.get(p, 'MISSING')) for p, want in items
       if blobs.get(p) != want]
pathlib.Path(report).write_text('\\n'.join(
    f"{path}\\t{pin}\\t{blobs.get(path, 'MISSING')}\\t{'PASS' if blobs.get(path)==pin else 'FAIL'}"
    for path,pin in items) + '\\n')
if bad:
    for p, want, actual in bad:
        print(f"MANIFEST_BLOB_MISMATCH={p} pinned={want} actual={actual}")
    sys.exit(2)
print(f"PINNED_OVERLAY_PREFLIGHT=PASS entries={len(items)} unique={len(set(p for p,_ in items))}")
PYMANIFEST
    STATUS="OVERLAY_MANIFEST_PREFLIGHT_FAILED"
    exit 2
fi
'''
s=s.replace(marker,preflight+marker)
# In the 1MiB log it was the first source stale SHA, now all are corrected.
assert s.count('71cbd3f9f87dca933f425534955364f054540a05')==0
assert s.count('4a3791918648121788f8f1b14df95ebda50e4b45')==0
p=Path(sys.argv[2])
p.write_text(s)
p.chmod(0o700)
assert hashlib.sha256(s.encode()).hexdigest() == '87e592edecb7d6b78d1dd910cfebe394d5ecc17583ca21e5d92bc4b205b8f409', 'OUTPUT_CHECKSUM_MISMATCH'
print('input_sha',hashlib.sha256(original).hexdigest())
print('output_sha',hashlib.sha256(s.encode()).hexdigest())
print('bytes',p.stat().st_size,'lines',len(s.splitlines()))
print('manifest_items',len(re.findall(r'^apply_blob "([^"\n]+)" "([0-9a-f]{40})"$',s,re.M)))
