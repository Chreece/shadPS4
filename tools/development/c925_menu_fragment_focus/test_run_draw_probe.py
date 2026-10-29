#!/usr/bin/env python3
"""No-host-mutation fixture for owned source/object/executable rollback."""
import hashlib, tempfile
from pathlib import Path
import run_draw_probe as r

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);ck=root/'checkpoint';ck.mkdir()
    managed=[];before={}
    for name in ('source','object','elf'):
        file=root/name;file.write_bytes((name+'_original').encode())
        (ck/name).write_bytes(file.read_bytes())
        before[name]=digest(file);managed.append((name,file))
    for name,file in managed:file.write_bytes((name+'_patched').encode())
    post={'source':digest(root/'source')}
    got=r.restore(managed,ck,before,post)
    assert all(x=='RESTORED_VERIFIED' for x in got.values()),got
    assert all(digest(file)==before[name] for name,file in managed)
    for name,file in managed:file.write_bytes((name+'_incomplete_build').encode())
    got=r.restore(managed,ck,before,{})
    assert got['source']=='REFUSED_UNRECOGNIZED_EXTERNAL_SOURCE_CHANGE'
    assert got['object']=='RESTORED_VERIFIED' and got['elf']=='RESTORED_VERIFIED'
    assert (root/'source').read_bytes()==b'source_incomplete_build'
    # On a handled transient source write, the recorded patch hash permits restore.
    got=r.restore(managed,ck,before,{'source':digest(root/'source')})
    assert all(x=='RESTORED_VERIFIED' for x in got.values()),got
print('DRAW_PROBE_ROLLBACK_TESTS=PASS (normal, interrupted, external-change refused)')
