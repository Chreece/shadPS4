#!/usr/bin/env python3
"""Offline diagnostic fixture only. Never installs/replaces a game shader.
Re-express the observed invalid atomic pair using the exact type/branch repair;
validate it with Khronos tools before asking for a native C++ build.
"""
from __future__ import annotations
import hashlib,json,os,pathlib,re,shutil,subprocess,sys
BAD_SHA='694f56399556d5fcf0baedc0e05b9870964fbef6165a98b719b5946ad4e07fb5'
AUDIT='got-c925-spirv-offline-20261028T175021642972Z'
VALIDATOR_SHA='f386e67676bc134802b6bfec6d96fff0f9437d0f60e4c97ee0afa59ab4f1c4ea'

def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def defs(lines):
 result={}
 for i,line in enumerate(lines):
  m=re.match(r'\s*(%\w+)\s*=\s*(Op\w+)\s*(.*)',line)
  if m:result[m[1]]=(i,m[2],m[3].split())
 return result

def repair_reference(text):
 lines=text.splitlines();d=defs(lines);matches=[]
 for rid,(index,op,args) in d.items():
  if op!='OpSelect' or len(args)!=4:continue
  out_type,condition,yes,no=args
  if d.get(out_type,(0,'',[]))[1:]!=('OpTypeFloat',['32']):continue
  branches=[]
  for bitcast in (yes,no):
   bc=d.get(bitcast)
   if not bc or bc[1]!='OpBitcast' or bc[2][0]!=out_type:break
   atomid=bc[2][1];at=d.get(atomid)
   if not at or at[1] not in ('OpAtomicUMin','OpAtomicSMax','OpAtomicUMax','OpAtomicSMin'):break
   ptrid=at[2][1];pt=d.get(ptrid)
   if not pt or pt[1]!='OpImageTexelPointer':break
   branches.append({'cast':bitcast,'atomic':atomid,'pointer':ptrid,'at':at,'pt':pt})
  if len(branches)!=2:continue
  yesb,nob=branches
  if (yesb['at'][1],nob['at'][1]) not in (('OpAtomicUMin','OpAtomicSMax'),('OpAtomicUMax','OpAtomicSMin')):
   continue
  ya,na=yesb['at'][2],nob['at'][2];yp,np=yesb['pt'][2],nob['pt'][2]
  if ya[0]!=na[0] or ya[2:]!=na[2:] or yp!=np:raise ValueError('ATOMIC_PAIR_MISMATCH')
  uint=ya[0]
  if d[uint][1:]!=('OpTypeInt',['32','0']):raise ValueError('NON_U32_FALLBACK')
  cond=d.get(condition);bits=ya[-1]
  if not cond or cond[1]!='OpINotEqual':raise ValueError('SIGN_CONDITION_NOT_FOUND')
  extract=d.get(cond[2][1]);zero=d.get(cond[2][2])
  if not extract or extract[1]!='OpBitFieldUExtract' or extract[2][1]!=bits:raise ValueError('SIGN_SOURCE_MISMATCH')
  constants=[d[x] for x in extract[2][2:]]
  if [x[2][-1] for x in constants]!=['31','1'] or zero[2][-1]!='0':raise ValueError('SIGN_BITS_MISMATCH')
  allowed={rid,yes,no,yesb['atomic'],nob['atomic'],yesb['pointer'],nob['pointer']}
  a=min(d[x][0] for x in allowed)
  if index-a!=6 or any(not any(re.match(r'\s*'+re.escape(x)+r'\s*=',line) for x in allowed) for line in lines[a:index+1]):
   raise ValueError('NONCONTIGUOUS_OBSERVED_ATOMIC_PAIR')
  previous=[key for key,(j,o,aa) in d.items() if o=='OpLabel' and j<a]
  if not previous:raise ValueError('MISSING_PARENT_BLOCK')
  parent=max(previous,key=lambda k:d[k][0])
  # Resolve the actual image variable/type, not a guessed binding number.
  imagevar=yp[1];variable=d[imagevar]
  if variable[1]!='OpVariable':raise ValueError('NOT_IMAGE_VARIABLE')
  pointer=d[variable[2][0]];imageid=pointer[2][1];image=d[imageid]
  if image[1]!='OpTypeImage' or image[2][0]!=out_type or image[2][-1]!='R32f':
   raise ValueError('NOT_OBSERVED_FLOAT32_ATOMIC_IMAGE')
  if d[yp[0]][2]!=['Image',uint]:raise ValueError('POINTER_POINTEE_MISMATCH')
  matches.append((a,index,rid,condition,parent,yesb,nob,imageid,uint,out_type))
 if not matches or len(matches)>64:raise ValueError('UNEXPECTED_PAIR_COUNT:'+str(len(matches)))
 if '%atomic_negative_fix_' in text:raise ValueError('FIXTURE_ALREADY_REWRITTEN')
 # A normal float ImageWrite shares the original descriptor in this actual
 # module. Create a separate UINT alias; never turn that float write into an
 # integer-typed write. This mirrors production descriptor identity splitting.
 aliases={};declarations=[];annotations=[]
 for match in matches:
  a,b,rid,cond,parent,yesb,nob,imageid,uint,floatid=match
  imagevar=yesb['pt'][2][1]
  if imagevar in aliases:continue
  number=len(aliases);alias=f'%atomic_u32_alias_{number}'
  image_type=f'%atomic_u32_image_type_{number}';ptr_type=f'%atomic_u32_image_ptr_{number}'
  imageargs=list(d[imageid][2]);imageargs[0]=uint;imageargs[-1]='R32ui'
  decorations=[]
  for line in lines:
   m=re.match(r'\s*OpDecorate\s+'+re.escape(imagevar)+r'\s+(.*)',line)
   if m:decorations.append(m.group(1).split())
  sets=[int(x[1]) for x in decorations if x[0]=='DescriptorSet']
  if len(sets)!=1:raise ValueError('IMAGE_DESCRIPTOR_SET_MISSING')
  used=[]
  for line in lines:
   m=re.match(r'\s*OpDecorate\s+%\w+\s+Binding\s+(\d+)',line)
   if m:used.append(int(m.group(1)))
  binding=max(used,default=-1)+1+number
  annotations.extend([f'OpDecorate {alias} DescriptorSet {sets[0]}',f'OpDecorate {alias} Binding {binding}'])
  # The captured variable has only its ordinary set/binding decorations.
  # Refuse unexpected alias/coherency requirements rather than dropping them.
  if any(x[0] not in ('Binding','DescriptorSet') for x in decorations):
   raise ValueError('UNEXPECTED_IMAGE_DECORATION_IN_FIXTURE')
  declarations.extend([image_type+' = OpTypeImage '+' '.join(imageargs),
    f'{ptr_type} = OpTypePointer UniformConstant {image_type}',
    f'{alias} = OpVariable {ptr_type} UniformConstant'])
  aliases[imagevar]=alias
 final_predecessors={}
 for number,match in enumerate(matches):
  a,b,rid,cond,parent,yesb,nob,imageid,uint,floatid=match
  final_predecessors[parent]=f'%atomic_merge_fix_{number}'
 for number,match in reversed(list(enumerate(matches))):
  a,b,rid,cond,parent,yesb,nob,imageid,uint,floatid=match
  y,n,m,old=(f'%atomic_negative_fix_{number}',f'%atomic_positive_fix_{number}',
             f'%atomic_merge_fix_{number}',f'%atomic_old_bits_fix_{number}')
  imagevar=yesb['pt'][2][1];alias=aliases[imagevar]
  def alias_pointer(line):return re.sub(re.escape(imagevar)+r'(?!\w)',alias,line)
  emit=[f'OpSelectionMerge {m} None',f'OpBranchConditional {cond} {y} {n}',f'{y} = OpLabel',
        alias_pointer(lines[yesb['pt'][0]]),lines[yesb['at'][0]],f'OpBranch {m}',f'{n} = OpLabel',
        alias_pointer(lines[nob['pt'][0]]),lines[nob['at'][0]],f'OpBranch {m}',f'{m} = OpLabel',
        f'{old} = OpPhi {uint} {yesb["atomic"]} {y} {nob["atomic"]} {n}',
        f'{rid} = OpBitcast {floatid} {old}']
  lines=lines[:a]+emit+lines[b+1:]
 # Splitting a block changes its outgoing predecessor identity. Match the
 # production emitter's first_to_last_label_map treatment of later Phi nodes.
 for i,line in enumerate(lines):
  if re.search(r'\bOpPhi\b',line):
   for parent,merge in final_predecessors.items():
    line=re.sub(re.escape(parent)+r'(?!\w)',merge,line)
   lines[i]=line
 # Add the alias to the same entry-point interface as its real float image.
 for imagevar,alias in aliases.items():
  changed=0
  for i,line in enumerate(lines):
   if re.search(r'\bOpEntryPoint\b',line) and re.search(re.escape(imagevar)+r'(?!\w)',line):
    lines[i]=line+' '+alias;changed+=1
  if changed==0:raise ValueError('IMAGE_NOT_IN_ENTRY_INTERFACE')
 first_function=next(i for i,line in enumerate(lines) if re.search(r'=\s*OpFunction\b',line))
 lines[first_function:first_function]=declarations
 first_type=next(i for i,line in enumerate(lines) if re.search(r'=\s*OpType\w+\b',line))
 lines[first_type:first_type]=annotations
 return '\n'.join(lines)+'\n'

def find_tools(project):
 candidates=[]
 if shutil.which('spirv-val'):candidates.append(pathlib.Path(shutil.which('spirv-val')))
 candidates.extend(sorted((project/'scripts').glob('**/spirv-val'))[:24])
 for val in candidates:
  asm=val.with_name('spirv-as');dis=val.with_name('spirv-dis')
  if all(p.is_file() and os.access(p,os.X_OK) for p in (val,asm,dis)):
   # Private downloaded tools must be the package already audited. A normal
   # installed executable is recorded by hash/version, not silently assumed.
   if val.is_relative_to(project/'scripts') and sha(val)!=VALIDATOR_SHA:continue
   return val,asm,dis
 raise RuntimeError('PREVIOUS_VALIDATOR_TOOLS_UNAVAILABLE_NO_BUILD')

def call(args,log,check=False):
 with log.open('wb') as f:
  r=subprocess.run([str(x) for x in args],stdout=f,stderr=subprocess.STDOUT,timeout=20)
 if check and r.returncode:raise RuntimeError('TOOL_FAILED:'+str(log))
 return r.returncode

def run(project,evid):
 evid.mkdir(parents=True,exist_ok=True);old=project/'logs'/AUDIT
 val,asm,dis=find_tools(project);helptext=subprocess.check_output([str(val),'--help'],text=True,timeout=10)
 feature_flag='--allow-offset-texture-operand' in helptext
 (evid/'validator-version.txt').write_bytes(subprocess.check_output([str(val),'--version'],timeout=10))
 modules=old/'modules';bad=modules/(BAD_SHA+'.spv')
 if sha(bad)!=BAD_SHA:raise RuntimeError('ATOMIC_EVIDENCE_HASH_CHANGED_NO_BUILD')
 original_asm=evid/'original-atomic.spvasm'
 call([dis,bad,'-o',original_asm],evid/'disassembly.log',True)
 fixed_asm=evid/'offline-reference-only.spvasm';fixed_asm.write_text(repair_reference(original_asm.read_text()))
 fixed=evid/'offline-reference-only.spv'
 call([asm,'--target-env','spv1.6',fixed_asm,'-o',fixed],evid/'assembly.log',True)
 results=[]
 modes={'universal':['--target-env','spv1.6'],'vulkan13-strict':['--target-env','vulkan1.3']}
 if feature_flag:modes['vulkan13-maintenance8']=['--target-env','vulkan1.3','--allow-offset-texture-operand']
 for p in sorted(modules.glob('*.spv')):
  if sha(p)!=p.stem:raise RuntimeError('MODULE_HASH_MISMATCH')
  for mode,flags in modes.items():
   log=evid/(p.stem+'-'+mode+'.log');rc=call([val,*flags,p],log)
   results.append({'sha256':p.stem,'mode':mode,'exit_code':rc,'diagnostic':log.read_text(errors='replace')})
 for mode,flags in modes.items():
  log=evid/('reference-'+mode+'.log');rc=call([val,*flags,fixed],log)
  results.append({'sha256':sha(fixed),'kind':'OFFLINE_REFERENCE_NOT_RUNTIME_BINARY','mode':mode,'exit_code':rc,'diagnostic':log.read_text(errors='replace')})
  if rc:
   (evid/'validation-results.json').write_text(json.dumps(results,indent=2)+'\n')
   raise RuntimeError('CORRECTED_REFERENCE_VALIDATION_FAILED_NO_BUILD')
 (evid/'validation-results.json').write_text(json.dumps(results,indent=2)+'\n')
 print('OFFLINE_ATOMIC_REFERENCE_VALIDATION=PASS (not a native recompile)',flush=True)
 return {'validator':str(val),'has_maintenance8_validator_option':feature_flag,'reference_sha256':sha(fixed),
         'scope':'Offline reference transformation only, actual C++ shader output still needs native test'}
if __name__=='__main__':run(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))
