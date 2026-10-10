#!/usr/bin/env python3
"""Read-only symbolization of the exact v9 Ghost/RADV crash, no game launch."""
from pathlib import Path
from datetime import datetime
import json, os, re, shutil, subprocess, tarfile, tempfile, urllib.request

ID='8a5c889901316f0caf5e5d8d10be9351dac2eb9c'
ADDR=['0x9ca84','0x12a244','0x1337a7','0x13529b','0x136e54','0x137ae4']
LIB=Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
HOME=Path.home()
OUT=HOME/f'ghost-radv-resolved-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'
MAX_BYTES=220*1024*1024
info={'build_id_expected':ID,'addresses':ADDR,'installed_library':str(LIB),'attempts':[]}


def command(args,limit=60):
    p=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=limit)
    return p.returncode,p.stdout+'\n'+p.stderr


def build_id(p):
    code,text=command(['readelf','-nW',str(p)])
    m=re.search(r'Build ID:\s*([0-9a-fA-F]+)',text)
    return m.group(1).lower() if code==0 and m else None


def download(label,url,path):
    print(f'DOWNLOADING {label} (maximum 220 MiB)...',flush=True)
    req=urllib.request.Request(url,headers={
        'User-Agent':'GhostRADVDebugBuildID/1.0', 'Accept-Encoding':'identity'})
    total=0
    with urllib.request.urlopen(req,timeout=35) as stream, path.open('wb') as out:
        declared=stream.headers.get('Content-Length')
        if declared and int(declared)>MAX_BYTES:
            raise RuntimeError(f'{label} too large: {declared} bytes')
        while True:
            block=stream.read(1024*1024)
            if not block: break
            total+=len(block)
            if total>MAX_BYTES:
                raise RuntimeError(f'{label} exceeded download size limit')
            out.write(block)
            if total//(8*1024*1024)!=(total-len(block))//(8*1024*1024):
                print(f'  downloaded {total//(1024*1024)} MiB',flush=True)
    return total


if not LIB.is_file():
    raise SystemExit('RADV library not found; no system changes made')
if not shutil.which('readelf') or not shutil.which('addr2line'):
    raise SystemExit('readelf/addr2line missing; no system changes made')
actual=build_id(LIB)
if actual!=ID:
    raise SystemExit(f'REFUSED: installed Mesa build-id {actual} differs from captured {ID}')
print('EXACT V9 MESA BUILD ID=PASS',flush=True)
cache=HOME/'.cache'
cache.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='ghost-radv-debug-',dir=cache) as directory:
    work=Path(directory)
    debug=Path('/usr/lib/debug/.build-id')/ID[:2]/(ID[2:]+'.debug')
    if debug.is_file() and build_id(debug)==ID:
        found=debug
        info['source']='installed-debug-file'
    else:
        found=None
        if os.environ.get('GHOST_DEBUG_SKIP_NETWORK')=='1':
            info['attempts'].append('Network disabled for local self-test')
        elif shutil.disk_usage(cache).free<550*1024*1024:
            info['attempts'].append('Less than 550 MiB free; downloads skipped to protect disk space')
        else:
            locations=[
              ('Debian debuginfod',
               f'https://debuginfod.debian.net/buildid/{ID}/debuginfo',
               'radv.debug'),
              ('Debian debug-package fallback',
               'https://deb.debian.org/debian-debug/pool/main/m/mesa/'
               'mesa-vulkan-drivers-dbgsym_25.0.7-2%2Bdeb13u1_amd64.deb',
               'mesa-dbgsym.deb'),
            ]
            for label,url,file in locations:
                target=work/file
                try:
                    num=download(label,url,target)
                    if file.endswith('.deb'):
                        if not shutil.which('dpkg-deb'):
                            raise RuntimeError('dpkg-deb unavailable')
                        extracted=work/'extracted'
                        extracted.mkdir()
                        code,logs=command(['dpkg-deb','-x',str(target),str(extracted)],120)
                        if code:
                            raise RuntimeError('dpkg-deb extraction failed: '+logs[-700:])
                        candidate=extracted/'usr/lib/debug/.build-id'/ID[:2]/(ID[2:]+'.debug')
                    else:
                        candidate=target
                    if not candidate.is_file() or build_id(candidate)!=ID:
                        raise RuntimeError('Downloaded debug symbols do not match the exact build ID')
                    info['source']=label
                    info['download_bytes']=num
                    found=candidate
                    break
                except Exception as exc:
                    print(f'{label}: {type(exc).__name__}: {exc}',flush=True)
                    info['attempts'].append(f'{label}: {type(exc).__name__}: {exc}')
                finally:
                    if target.exists() and target!=found:
                        target.unlink()
    if found:
        print('MATCHING RADV DEBUG SYMBOLS=PASS',flush=True)
        code,output=command(['addr2line','-a','-f','-C','-i','-e',str(found),*ADDR])
        (work/'radv-addr2line.txt').write_text(output[:400000])
        info['addr2line_exit']=code
        if shutil.which('eu-addr2line'):
            code,output=command(['eu-addr2line','-a','-f','-C','-i','-e',str(found),*ADDR])
            (work/'radv-eu-addr2line.txt').write_text(output[:400000])
            info['eu_addr2line_exit']=code
        print((work/'radv-addr2line.txt').read_text()[:9000],flush=True)
    else:
        print('MATCHING RADV DEBUG SYMBOLS=NOT_AVAILABLE; see archive for reasons',flush=True)
    info['result']='SYMBOLIZED' if found and info.get('addr2line_exit')==0 else 'DEBUG_INFO_UNAVAILABLE'
    (work/'report.json').write_text(json.dumps(info,indent=2))
    with tarfile.open(OUT,'w:gz') as t:
        for name in ('report.json','radv-addr2line.txt','radv-eu-addr2line.txt'):
            f=work/name
            if f.is_file(): t.add(f,arcname=name)
print('RESULT='+info['result'],flush=True)
print('UPLOAD_THIS_ARCHIVE='+str(OUT),flush=True)
