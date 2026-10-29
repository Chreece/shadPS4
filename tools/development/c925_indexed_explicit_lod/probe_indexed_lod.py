"""Read only existing IR; do not compile an unobserved sampling variant blindly."""
from __future__ import annotations
import hashlib,json,re,shutil
from pathlib import Path


def parse(text):
    out={}
    for line in text.splitlines():
        m=re.search(r'(%\d+)\s*=\s*(\w+)\s*(.*)',line)
        if not m:continue
        ident,op,body=m.groups();metadata=''
        if body.startswith('('):
            end=body.find(')');metadata=body[1:end];body=body[end+1:].strip()
        body=re.sub(r'\s*\(uses:.*','',body)
        out[ident]=(op,[x.strip() for x in body.split(',') if x.strip()],metadata)
    return out


def immediate(value):
    if not value.startswith('#'):return None
    try:return int(value[1:],0)
    except ValueError:return None


def normalized(nodes,value,depth=0):
    if depth>16:return None
    imm=immediate(value)
    if imm is not None:return (None,imm&0xffffffff)
    op,args,_=nodes.get(value,('',[],''))
    if op=='IAdd32' and len(args)==2:
        a,b=map(immediate,args)
        if (a is None)!=(b is None):
            child=normalized(nodes,args[0] if a is None else args[1],depth+1)
            if child:return (child[0],(child[1]+(b if a is None else a))&0xffffffff)
    if op=='ISub32' and len(args)==2 and immediate(args[1]) is not None:
        child=normalized(nodes,args[0],depth+1)
        if child:return (child[0],(child[1]-immediate(args[1]))&0xffffffff)
    return (value,0) if value in nodes else None


def strided(nodes,word):
    n=normalized(nodes,word)
    if not n or n[0] is None:return None
    op,args,_=nodes[n[0]]
    if op!='ShiftRightLogical32' or len(args)!=2 or immediate(args[1])!=2:return None
    scaled=args[0];prefix=0
    op,args,_=nodes.get(scaled,('',[],''))
    if op=='IAdd32':
        if len(args)!=2:return None
        a,b=map(immediate,args)
        if (a is None)==(b is None):return None
        scaled=args[0] if a is None else args[1];prefix=b if a is None else a
    if prefix%4:return None
    op,args,_=nodes.get(scaled,('',[],''))
    if op!='IMul32' or len(args)!=2:return None
    a,b=map(immediate,args)
    if (a is None)==(b is None):return None
    stride=b if a is None else a;idx=args[0] if a is None else args[1]
    offset=prefix//4+n[1]
    if stride<32 or stride>65536 or stride%4 or idx not in nodes or offset>stride//4:return None
    return idx,stride,offset


def new_mode(flags):
    common_mask=1|4|512|1024|2048|4096
    return not flags&common_mask and bool(flags&8)!=bool(flags&16) and not flags&(2|256)


def analyze(text):
    nodes=parse(text);found=[];modes={}
    for ident,(op,args,meta) in nodes.items():
        if op!='ImageSampleRaw' or len(args)<2:continue
        m=re.search(r'flags=(0x[0-9a-fA-F]+)',meta)
        if not m:continue
        raw=int(m[1],16);flags=raw&0xffff;modes[hex(flags)]=modes.get(hex(flags),0)+1
        if not new_mode(flags):continue
        iop,halves,_=nodes.get(args[0],('',[],''))
        if iop!='ImageHandle' or len(halves)!=2:continue
        words=[]
        for half in halves:
            cop,vals,_=nodes.get(half,('',[],''))
            if cop!='CompositeConstructU32x4' or len(vals)!=4:break
            words.extend(vals)
        if len(words)!=8:continue
        layouts=[];headers=[];unflattened=True
        for w in words:
            rop,reads,rmeta=nodes.get(w,('',[],''))
            if rop!='ReadConstBuffer' or len(reads)!=2:break
            unflattened &= 'flatbuf_off_dw=0x0' in rmeta
            bop,bwords,_=nodes.get(reads[0],('',[],''))
            if bop!='CompositeConstructU32x4' or len(bwords)!=4:break
            headers.append(tuple(bwords));layouts.append(strided(nodes,reads[1]))
        if len(layouts)!=8 or not unflattened or len(set(headers))!=1 or not all(layouts):continue
        idx,stride,offset=layouts[0]
        if any(x!=(idx,stride,offset+i) for i,x in enumerate(layouts)) or (offset+8)*4>stride:continue
        # Header source must be in the class accepted by actual discovery.
        if not all(immediate(w) is not None or nodes.get(w,('',[],''))[0] in ('GetUserData','ReadConst','ReadConstBuffer') for w in headers[0]):continue
        found.append({'instruction':ident,'flags':hex(flags),'pc':raw>>16,'mode':'LZ' if flags&8 else 'L',
                      'record_stride':stride,'descriptor_offset':offset*4,
                      'gpu_selector':idx,'bounds_and_sampler_acceptance':'NOT_PROVEN_BY_IR'})
    return found,modes


def probe(profile:Path, original:Path, previous_log:Path, evidence:Path):
    text=previous_log.read_text(errors='replace')
    observed={int(x,16) for x in re.findall(r'SHARP_RESOURCE_PROVENANCE shader=(0x[0-9a-f]+) stage=6 .*?indexed=false',text)}
    dirs=[original/'shader/dumps',profile/'shader/dumps']
    files={}
    for directory in dirs:
        if not directory.is_dir():continue
        for p in directory.glob('*.pre-res-patch.irprogram.txt'):
            m=re.search(r'_(0x[0-9a-fA-F]+)\.',p.name)
            if not m or int(m[1],16) not in observed or p.is_symlink() or p.stat().st_size>12*1024**2:continue
            h=int(m[1],16)
            if h not in files or p.stat().st_mtime>files[h].stat().st_mtime:files[h]=p
    summary={'observed_compute_hashes':len(observed),'existing_IR_files':len(files),'shaders':[],
             'new_candidates':0,'qualification':'IR/source eligibility only; real nonzero bounded table and sampler still required.'}
    for h,p in sorted(files.items()):
        raw=p.read_bytes();matches,modes=analyze(raw.decode(errors='replace'))
        item={'shader':hex(h),'file':str(p),'sha256':hashlib.sha256(raw).hexdigest(),'modes':modes,'matches':matches}
        summary['new_candidates']+=len(matches);summary['shaders'].append(item)
    # Preserve at most 8 targeted shader IR files, never a whole user directory.
    selected=[s for s in summary['shaders'] if s['matches']]
    if not selected:selected=sorted(summary['shaders'], key=lambda s: sum(v for k,v in s['modes'].items() if new_mode(int(k,16))), reverse=True)[:8]
    out=evidence/'shader-evidence';out.mkdir(exist_ok=True);total=0
    for item in selected[:8]:
        p=Path(item['file']);size=p.stat().st_size
        if total+size<=48*1024**2:shutil.copy2(p,out/p.name);total+=size
    (evidence/'sampling-mode-probe.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary
