#!/usr/bin/env python3
from __future__ import annotations
import hashlib, importlib.util, json, os, subprocess, sys
from pathlib import Path

ROOT=Path.home()/'projects/shadps4'
SOURCE=ROOT/'clean-rebase-verified/source'
BASE='04d891dc33bb1c8c89789b5ff039049901f95a41'
INFO=ROOT/'logs/masked-wave-build-20261023-130845-3042481/info'
REV='975ee9812d93fb0e8094e0581640fb705c336d68'
TREE='d921f3f51c546aa8b9dfe7e230aaefeaf0b3c446'
PARENT='4bc7df9c1370b0eb10c84f385b8c2d041e791945'
BIN_SHA='fb5d18e8342cd6701ef31831d389e99f5b9b877de1214e42f1655658d25cb7fb'
REPORT_SHA='0c0a96188e02f1c86be6f6e1e26f280e0f4bf3c06445a2e563a742d66cd82590'
MANIFEST_SHA='28c88069192f5b56689e59eef3ea2045f1280394a3cde3840913d16230823c8c'
OLD=ROOT/'scripts/shadps4-buffer-phi-launch-20261023.py'
OLD_SHA='d6c0c858a6f60d16a42248cff3589fb33cdd4f8897780eda4ad07437a771b44c'

def digest(p):
    if p.is_symlink() or not p.is_file(): raise RuntimeError('REGULAR_FILE_REQUIRED='+str(p))
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def git(*args):
    env={k:v for k,v in os.environ.items() if k not in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_COMMON_DIR')}
    return subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-C',str(SOURCE),*args],env=env,stderr=subprocess.PIPE,timeout=30)

if digest(OLD)!=OLD_SHA: raise SystemExit('PROTECTED_LAUNCHER_CHANGED_OR_MISSING')
spec=importlib.util.spec_from_file_location('image_resource_runtime_parent',OLD)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
m.INFO=INFO;m.REV=REV;m.TREE=TREE;m.PARENT=PARENT;m.BIN_SHA=BIN_SHA
m.REPORT_SHA=REPORT_SHA;m.MANIFEST_SHA=MANIFEST_SHA
m.SESSION='got-image-resource-'+REV[:12]

def verify_build():
    if digest(INFO/'build-result.json')!=REPORT_SHA or digest(INFO/'correction.json')!=MANIFEST_SHA:
        raise RuntimeError('BUILD_EVIDENCE_CHANGED')
    r=json.loads((INFO/'build-result.json').read_bytes())
    expected={'status':'MASKED_WAVE_BUILD_PASS','base':BASE,'revision':REV,'tree':TREE,
      'source_parent':PARENT,'candidate_sha256':BIN_SHA,'manifest_sha256':MANIFEST_SHA,
      'full_build':'PASS','exit':0,'game_started':False,'source_restored':True,
      'build_binary_restored':True,'sirit_restored':True,
      'image_resource_correctness':'MASKS_AND_FINITE_PHI',
      'image_resource_production_objects':'COMPILED','automatic_coherence':'RETAINED',
      'buffer_phi_selection':'RETAINED','runtime_masked_reduction':'RETAINED'}
    for k,v in expected.items():
        if r.get(k)!=v: raise RuntimeError('BUILD_GATE_MISMATCH='+k)
    candidate=ROOT/'clean-rebase-verified/candidates'/f'{REV}-{BIN_SHA}'/'shadps4'
    if Path(r['candidate'])!=candidate or digest(candidate)!=BIN_SHA or not os.access(candidate,os.X_OK):
        raise RuntimeError('CANDIDATE_CHANGED')
    if git('rev-parse',REV+'^{tree}').decode().strip()!=TREE or git('rev-parse',REV+'^').decode().strip()!=PARENT:
        raise RuntimeError('CANDIDATE_HISTORY_CHANGED')
    if git('rev-parse','HEAD').decode().strip()!=BASE: raise RuntimeError('SOURCE_NOT_RESTORED')
    git('diff','--exit-code','--no-ext-diff','HEAD','--')
    manifest=json.loads((INFO/'correction.json').read_bytes())
    for name,e in manifest['files'].items():
        if git('rev-parse',REV+':'+name).decode().strip()!=e['corrected_blob']:
            raise RuntimeError('SOURCE_BLOB_CHANGED='+name)
    return r,candidate

m.verify_build=verify_build
print('IMAGE_RESOURCE_CANDIDATE='+REV+' FULLSCREEN=ON FPS=ON REBUILD=NO',flush=True)
raise SystemExit(m.main())
