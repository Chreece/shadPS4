#!/usr/bin/env python3
"""Resolve *new* Ghost v13b SIGSEGV with exact matching RADV debug info.
No game runs, no builds, no package installs, no file modifications outside temp and report.
"""
from pathlib import Path
from datetime import datetime
import json, re, shutil, subprocess, tarfile, tempfile, urllib.request

HOME = Path.home()
LIB = Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
BID = '8a5c889901316f0caf5e5d8d10be9351dac2eb9c'
ADDRESSES = ['0x396d25','0x12a731','0x1337a7','0x13529b','0x136e54','0x137ae4','0x9ca84']
OUTPUT = HOME / f'ghost-v13b-radv-resolved-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'
LIMIT = 48 * 1024 * 1024

def capture(args, timeout=45):
    proc = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, errors='replace', timeout=timeout)
    return proc.returncode, (proc.stdout + '\n' + proc.stderr)

def elf_id(path):
    code, out = capture(['readelf', '-nW', str(path)])
    found = re.search(r'Build ID:\s*([a-fA-F0-9]+)', out)
    return found.group(1).lower() if code == 0 and found else None

report = {'crash':'Ghost v13b SIGSEGV in private Mesa RADV',
          'expected_build_id':BID, 'addresses': ADDRESSES,
          'private_patch_at':'0x9ca84 only; new fault is 0x396d25',
          'result':'UNRESOLVED'}
with tempfile.TemporaryDirectory(prefix='ghost-v13b-symbols-', dir=HOME) as tmp:
    work = Path(tmp)
    try:
        if not LIB.is_file():
            raise RuntimeError('Installed RADV binary not found; check driver path')
        if not all(shutil.which(n) for n in ('readelf','addr2line','objdump')):
            raise RuntimeError('readelf/addr2line/objdump unavailable')
        actual = elf_id(LIB)
        report['installed_build_id'] = actual
        if actual != BID:
            raise RuntimeError('REFUSED: driver build ID differs from captured v13b')
        print('MESA_BUILD_ID_MATCH=PASS', flush=True)
        _, out = capture(['dpkg-query','-W','-f=\${Version}\n','mesa-vulkan-drivers'])
        report['mesa_package_version'] = out.strip()
        for index, addr in enumerate(ADDRESSES[:2]):
            a = int(addr,16)
            _, out = capture(['objdump','-d','-M','intel',
                              f'--start-address={a-96}',
                              f'--stop-address={a+112}',str(LIB)], timeout=55)
            (work/f'disassembly-{index}-{a:x}.txt').write_text(out[:250000])
        dbg = Path('/usr/lib/debug/.build-id') / BID[:2] / (BID[2:] + '.debug')
        if not dbg.is_file() or elf_id(dbg) != BID:
            if shutil.disk_usage(HOME).free < 100*1024*1024:
                raise RuntimeError('Less than 100 MiB free; not downloading debug symbols')
            dbg = work / 'radv.debug'
            url = f'https://debuginfod.debian.net/buildid/{BID}/debuginfo'
            request = urllib.request.Request(url,headers={
                'User-Agent':'GhostV13bRadvSymbols/1.0',
                'Accept-Encoding':'identity'})
            print('Fetching exact Debian RADV debug symbols (around 20 MiB)...',flush=True)
            total = 0
            with urllib.request.urlopen(request,timeout=45) as remote, dbg.open('wb') as dest:
                declared=remote.headers.get('Content-Length')
                if declared and int(declared) > LIMIT:
                    raise RuntimeError('Debug download exceeds 48 MiB limit')
                while True:
                    block=remote.read(1024*1024)
                    if not block: break
                    total += len(block)
                    if total > LIMIT:
                        raise RuntimeError('Debug download exceeded 48 MiB limit')
                    dest.write(block)
            report['download_bytes'] = total
            if elf_id(dbg) != BID:
                raise RuntimeError('Downloaded debug symbols do not match exact build ID')
        print('EXACT_DEBUG_SYMBOLS=PASS',flush=True)
        for tool in ('addr2line', 'eu-addr2line'):
            if not shutil.which(tool): continue
            code, out = capture([tool,'-a','-f','-C','-i','-e',str(dbg),*ADDRESSES])
            (work/f'{tool}-resolved.txt').write_text(out[:250000])
            report[tool+'_exit_code'] = code
            if tool == 'addr2line':
                print('NEW_CRASH_STACK_RESOLVED:\n'+out[:6000],flush=True)
        report['result'] = 'SYMBOLIZED'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        print('SYMBOLIZATION_ERROR='+report['error'],flush=True)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    with tarfile.open(OUTPUT,'w:gz') as archive:
        for file in sorted(work.glob('*')):
            if file.is_file() and file.name != 'radv.debug':
                archive.add(file,arcname=file.name)
print('RESULT='+report['result'],flush=True)
print('UPLOAD_THIS_ARCHIVE='+str(OUTPUT),flush=True)
