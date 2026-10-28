#!/usr/bin/env python3
"""Validate existing shader binaries offline. Never run or rebuild the emulator."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess as sp
import sys
import tarfile
import time

LATEST = 'got-c925-finite-raw-resume-20261028T172412183495Z'
PREVIOUS = 'got-c925-remaining-shaders-20261028T155744943557Z'
EXPECTED = '9f0f6c9fe83f15aec6dea1dcf3f23226d944472daa0bdb6258cff13a95f219d3'
PACKAGE_URL = 'https://deb.debian.org/debian/pool/main/s/spirv-tools/spirv-tools_2025.1~rc1-1_amd64.deb'
PACKAGE_SHA = '2cc272ef1340cf0b47de6c5fcccaf91574d7a8f4061abb718d6c639463e85aab'
MAX_FILE = 16 * 1024**2
MAX_INPUT = 192 * 1024**2
MAX_FILES = 1024
MAGIC = b'\x03\x02\x23\x07'


def emit(s):
    print(time.strftime('%H:%M:%S') + ' ' + s, flush=True)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024**2), b''): h.update(block)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def command(args, timeout=15):
    """Signal only the process group created here, never an emulator/service."""
    p = sp.Popen(args, stdin=sp.DEVNULL, stdout=sp.PIPE, stderr=sp.STDOUT,
                 start_new_session=True)
    start = last = time.monotonic()
    try:
        while True:
            try:
                out, _ = p.communicate(timeout=min(1.0, max(.01, timeout-(time.monotonic()-start))))
                return p.returncode, out.decode(errors='replace')
            except sp.TimeoutExpired:
                now = time.monotonic()
                if now-start >= timeout: raise TimeoutError('TOOL_TIMEOUT:' + Path(args[0]).name)
                if now-last >= 15:
                    emit('TOOL_ACTIVE=' + Path(args[0]).name + f' elapsed={now-start:.0f}s')
                    last = now
    finally:
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=2)
            except sp.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL); p.wait(timeout=3)
            except ProcessLookupError: pass
        if p.stdout is not None: p.stdout.close()


class Inputs:
    def __init__(self, work):
        self.work = work; self.items = {}; self.total = 0; self.skipped = []
    def add(self, data, origin, group, name, modified=None):
        if not (20 <= len(data) <= MAX_FILE and len(data) % 4 == 0 and data[:4] == MAGIC):
            self.skipped.append({'origin': origin, 'reason': 'NOT_VALID_SPIRV_CONTAINER'}); return
        h = sha_bytes(data)
        if h not in self.items:
            if len(self.items) >= MAX_FILES or self.total+len(data) > MAX_INPUT:
                self.skipped.append({'origin': origin, 'reason': 'INPUT_BUDGET'}); return
            self.total += len(data)
            self.work.mkdir(parents=True, exist_ok=True)
            (self.work/(h+'.spv')).write_bytes(data)
            self.items[h] = {'sha256': h, 'bytes': len(data), 'origins': []}
        self.items[h]['origins'].append({'path': origin, 'group': group, 'name': name, 'mtime': modified})
    def file(self, path, group):
        path = Path(path)
        if path.is_symlink() or not path.is_file(): return
        before = path.stat()
        if not 20 <= before.st_size <= MAX_FILE: return
        data = path.read_bytes(); after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            self.skipped.append({'origin': str(path), 'reason': 'CHANGED_DURING_READ'}); return
        self.add(data, str(path), group, path.name, before.st_mtime)
    def archive(self, path, group):
        path = Path(path)
        if not path.is_file(): return
        with tarfile.open(path, 'r:gz') as archive:
            for m in archive:
                rel = Path(m.name)
                if (m.isfile() and not rel.is_absolute() and '..' not in rel.parts
                    and rel.suffix == '.spv' and 20 <= m.size <= MAX_FILE):
                    f = archive.extractfile(m)
                    if f is not None:
                        self.add(f.read(MAX_FILE+1), str(path)+'::'+m.name, group, rel.name, m.mtime)


def tools(project, evid):
    installed = shutil.which('spirv-val')
    if installed:
        return Path(installed), Path(shutil.which('spirv-dis')) if shutil.which('spirv-dis') else None
    if platform.system() != 'Linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise RuntimeError('NO_VALIDATOR_FOR_THIS_HOST_NO_INSTALL_PERFORMED')
    if not shutil.which('curl') or not shutil.which('dpkg-deb'):
        raise RuntimeError('VALIDATOR_MISSING_AND_DOWNLOAD_EXTRACT_TOOLS_UNAVAILABLE')
    dest = project/'scripts'/('spirv-tools-private-'+PACKAGE_SHA[:16])
    dest.mkdir(parents=True, exist_ok=True)
    package = dest/'spirv-tools.deb'
    if not package.is_file() or sha_file(package) != PACKAGE_SHA:
        emit('STAGE=FETCHING_PINNED_VALIDATOR no_system_install=YES')
        partial = dest/('download-'+str(os.getpid())+'.deb')
        rc, output = command(['curl','--fail','--location','--proto','=https','--tlsv1.2',
            '--silent','--show-error','--connect-timeout','10','--max-time','45',
            PACKAGE_URL,'-o',str(partial)], timeout=50)
        (evid/'validator-download.log').write_text(output)
        if rc or not partial.is_file() or sha_file(partial) != PACKAGE_SHA:
            raise RuntimeError('VALIDATOR_DOWNLOAD_OR_CHECKSUM_FAILED_NO_EXECUTION')
        partial.replace(package)
    root = dest/('unpacked-'+str(os.getpid()))
    emit('STAGE=EXTRACTING_PRIVATE_VALIDATOR no_root=YES')
    rc, output = command(['dpkg-deb','--extract',str(package),str(root)],timeout=30)
    (evid/'validator-extract.log').write_text(output)
    if rc: raise RuntimeError('PRIVATE_VALIDATOR_EXTRACTION_FAILED')
    save(evid/'validator-package.json', {'url':PACKAGE_URL,'sha256':PACKAGE_SHA,
        'extracted_to':str(root),'system_package_install':False})
    return root/'usr/bin/spirv-val', root/'usr/bin/spirv-dis'


def main(project=None):
    project = Path(project) if project else Path.home()/'projects/shadps4'
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid = project/'logs'/('got-c925-spirv-offline-'+stamp)
    evid.mkdir(parents=True)
    work = project/'scripts'/('spirv-audit-inputs-'+stamp)
    inputs = Inputs(work)
    result = {'record_id':evid.name,'recorded_on':dt.date.today().isoformat(),
        'journal_scope':'UNIVERSAL_SHADPS4','related_prs':[99,100],'tested_games':[],
        'build':'NOT_RUN','game':'NOT_RUN','emulator_source_changed':False,
        'gpu_access':False,'ollama_access':False,'system_install':False,
        'graphics_fixed':'UNVERIFIED','input_trial':LATEST}
    emit('EVIDENCE_DIRECTORY='+str(evid))
    emit('MODE=OFFLINE_VALIDATION NO_EMULATOR_BUILD_OR_GAME_OR_GPU_ACCESS')
    rc = 1
    binary = None; before = None
    try:
        prior = project/'logs'/LATEST
        trial = json.loads((prior/'trial.json').read_text())
        if trial.get('expected_binary_sha256') != EXPECTED:
            raise RuntimeError('UNEXPECTED_PRIOR_TRIAL')
        for p in sorted((prior/'fresh-shaders').glob('*.spv')): inputs.file(p,'uploaded_pr100')
        profile = Path(trial['private_profile']).resolve()
        experiments = (project/'GhostOfTsushima/experiments').resolve()
        if profile.name != 'user' or not profile.is_relative_to(experiments):
            raise RuntimeError('PROFILE_PATH_OUTSIDE_EXPERIMENTS')
        binary = project/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z/build/shadps4'
        before = sha_file(binary) if binary.is_file() else None
        result['current_binary_sha256'] = before
        if before == EXPECTED:
            for p in sorted((profile/'shader/dumps').glob('*.spv')):
                inputs.file(p,'existing_profile_unverified_per_file_source_revision')
        else:
            result['profile_inventory_skipped'] = 'CURRENT_BINARY_DIFFERS_USE_ARCHIVED_OUTPUT_ONLY'
        inputs.archive(project/'logs'/(PREVIOUS+'.tar.gz'),'archived_pr99_diagnostic')
        save(evid/'inputs.json',list(inputs.items.values()))
        save(evid/'skipped.json',inputs.skipped)
        if not inputs.items: raise RuntimeError('NO_EXISTING_SPIRV_MODULES')
        emit('UNIQUE_MODULES='+str(len(inputs.items)))
        validator, disassembler = tools(project,evid)
        version_rc, version = command([str(validator),'--version'])
        (evid/'validator-version.txt').write_text(version)
        if version_rc: raise RuntimeError('VALIDATOR_NOT_EXECUTABLE_ON_HOST')
        result['validator_sha256'] = sha_file(validator)
        result['validator_version'] = version.strip()
        modes = [('universal',['--target-env','spv1.6']),
                 ('vulkan13-strict',['--target-env','vulkan1.3']),
                 ('vulkan13-scalar-layout',['--target-env','vulkan1.3','--scalar-block-layout'])]
        records = []; copied = set(); evidence_bytes = 0; started = last = time.monotonic()
        ordered = sorted(inputs.items.items(),key=lambda item:not any(
            o['group']=='uploaded_pr100' for o in item[1]['origins']))
        timed_out = False
        for number,(h,item) in enumerate(ordered,1):
            if time.monotonic()-started > 180:
                timed_out=True; break
            source = work/(h+'.spv'); per_file=[]
            for name,flags in modes:
                try: code, output = command([str(validator),*flags,str(source)],timeout=8)
                except TimeoutError as exc: code,output=124,str(exc)
                row={'sha256':h,'mode':name,'exit_code':code,'diagnostic':output[:32768],
                     'status':'VALIDATOR_PASS' if code==0 else 'VALIDATOR_REPORTED_FAILURE_OR_UNSUPPORTED_INPUT'}
                records.append(row);per_file.append(row)
                with (evid/'validation.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
            target = any(o['group']=='uploaded_pr100' for o in item['origins'])
            failed = any(row['exit_code'] for row in per_file)
            if (target or failed) and evidence_bytes+item['bytes'] <= 32*1024**2:
                dest=evid/'modules'/(h+'.spv');dest.parent.mkdir(exist_ok=True)
                shutil.copy2(source,dest);copied.add(h);evidence_bytes+=item['bytes']
                if disassembler and disassembler.is_file() and (target or len(copied)<=8):
                    try:
                        code,text=command([str(disassembler),'--no-color',str(source)],timeout=8)
                        (dest.with_suffix('.spvasm.txt')).write_text(text[:2*1024**2])
                    except Exception as exc:(dest.with_suffix('.dis-error.txt')).write_text(str(exc))
            now=time.monotonic()
            if target or failed or now-last>=15:
                emit(f'VALIDATED={number}/{len(ordered)} MODULE={h[:12]} '+
                     ' '.join(r['mode']+'='+str(r['exit_code']) for r in per_file));last=now
        result['modules_total']=len(ordered)
        result['modules_processed']=len(records)//len(modes)
        result['bounded_scan_stopped']=timed_out
        result['mode_results']={name:{'pass':sum(x['mode']==name and x['exit_code']==0 for x in records),
            'nonzero':sum(x['mode']==name and x['exit_code']!=0 for x in records)} for name,_ in modes}
        result['qualification']='Validator errors need feature/environment review; passing is not proof of correct GPU execution. Scalar-layout mode is separate, not assumed enabled on the device. Existing profile files retain individual mtime/hash provenance.'
        result['status']='OFFLINE_VALIDATION_COMPLETE' if not timed_out else 'OFFLINE_VALIDATION_PARTIAL_TIME_LIMIT'
        rc=0
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__;emit('STOP='+result['status'])
        rc=130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if binary is not None:
            after=sha_file(binary) if binary.is_file() else None
            result['binary_unchanged_during_audit']=before==after
            if before!=after:rc=1
        result['runner_exit_code']=rc
        save(evid/'trial.json',result)
        with tarfile.open(str(evid)+'.tar.gz','w:gz') as archive:archive.add(evid,arcname=evid.name)
        emit('RESULT='+result.get('status','UNKNOWN'));emit('EVIDENCE='+str(evid)+'.tar.gz')
    return rc


if __name__=='__main__':
    def terminated(signum,frame):raise KeyboardInterrupt('STOP_REQUESTED')
    signal.signal(signal.SIGTERM,terminated)
    sys.exit(main())
