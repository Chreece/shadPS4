#!/usr/bin/env python3
"""Real source/assembly fixtures and explicitly mocked host orchestration.
Set SOURCE_FIXTURE and ATOMIC_ASSEMBLY_FIXTURE to private local test inputs.
No game shader data is embedded in this public test file.
"""
from __future__ import annotations
import contextlib,hashlib,json,os,re,shutil,subprocess,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import apply_float_atomic as fix
import validate_atomic_fixture as val
import run_float_atomic as run
SOURCE=Path(os.environ['SOURCE_FIXTURE'])
ASSEMBLY=Path(os.environ['ATOMIC_ASSEMBLY_FIXTURE'])

class SourceTests(unittest.TestCase):
 def setUp(self):self.old={p:(SOURCE/p).read_bytes() for p in fix.FILES}
 def test_exact_transform_four_files_and_native_paths_retained(self):
  out=fix.transform(self.old);self.assertEqual(set(out),set(fix.FILES))
  s=out[fix.FILES[2]].decode();self.assertEqual(s.count('return FloatAtomicBySign('),4)
  self.assertIn('if (ctx.profile.supports_image_fp32_atomic_min_max)',s)
  self.assertIn('if (ctx.profile.supports_buffer_fp32_atomic_min_max)',s)
 def test_changed_source_rejected_before_writes(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);before={}
   for p,data in self.old.items():q=root/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(data);before[p]=data
   q=root/fix.FILES[0];q.write_bytes(q.read_bytes()+b'\n');before[fix.FILES[0]]=q.read_bytes()
   with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED_NO_WRITE'):fix.apply(root,root/'evidence')
   self.assertEqual({p:(root/p).read_bytes() for p in before},before)
 def test_repeated_transform_refused(self):
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED_NO_WRITE'):fix.transform(fix.transform(self.old))
 def test_real_git_patch_roundtrip(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);evid=root/'evidence';evid.mkdir()
   for p,data in self.old.items():q=root/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(data)
   subprocess.run(['git','init','-q',str(root)],check=True)
   fix.apply(root,evid);diff=evid/'float-atomic-source.patch'
   for args in (['-R','--check'],['-R'],['--check'],[],['-R']):
    subprocess.run(['git','-C',str(root),'apply',*args,str(diff)],check=True,capture_output=True)
   self.assertEqual({p:(root/p).read_bytes() for p in self.old},self.old)

class AssemblyTests(unittest.TestCase):
 def setUp(self):self.text=ASSEMBLY.read_text();self.fixed=val.repair_reference(self.text)
 def test_preserves_normal_float_write_and_separate_uint_alias(self):
  self.assertIn('OpTypeImage %f32_id 2D 0 0 0 2 R32f',self.fixed)
  self.assertIn('OpImageWrite %175 %172 %174 None',self.fixed)
  self.assertIn('OpLoad %56 %cs_img0',self.fixed)
  self.assertIn('OpTypeImage %u32_id 2D 0 0 0 2 R32ui',self.fixed)
  self.assertIn('OpDecorate %atomic_u32_alias_0 Binding 4',self.fixed)
 def test_both_pairs_conditional_and_phi_predecessors(self):
  self.assertEqual(self.fixed.count('OpSelectionMerge %atomic_merge_fix_'),2)
  block=None;pred={};phis=[];atoms=[]
  for line in self.fixed.splitlines():
   m=re.match(r'\s*(%\w+) = OpLabel',line)
   if m:block=m[1]
   m=re.match(r'\s*OpBranch(?:Conditional)?\s+(.*)',line)
   if m:
    tok=m[1].split();targets=tok[:1] if len(tok)==1 else tok[1:3]
    for t in targets:pred.setdefault(t,set()).add(block)
   m=re.match(r'\s*%\w+ = OpPhi\s+(.*)',line)
   if m:phis.append((block,m[1].split()[2::2]))
   if re.search(r'= OpAtomic(?:UMin|SMax)\b',line):atoms.append(block)
  self.assertEqual(set(atoms),{'%atomic_negative_fix_0','%atomic_positive_fix_0','%atomic_negative_fix_1','%atomic_positive_fix_1'})
  for b,parents in phis:self.assertEqual(set(parents),pred.get(b,set()),b)
 def test_wrong_sign_source_rejected(self):
  wrong=self.text.replace('OpBitFieldUExtract %u32_id %343 %u32_id_31 %u32_id_1',
                         'OpBitFieldUExtract %u32_id %343 %u32_id_1 %u32_id_1')
  self.assertNotEqual(wrong,self.text)
  with self.assertRaisesRegex(ValueError,'SIGN_BITS'):val.repair_reference(wrong)
 def test_no_matching_pair_refused(self):
  with self.assertRaisesRegex(ValueError,'PAIR_COUNT'):val.repair_reference('; no module')
 def test_original_fixture_unchanged(self):self.assertEqual(ASSEMBLY.read_text(),self.text)
 def test_repeat_fixture_refused(self):
  with self.assertRaises(ValueError):val.repair_reference(self.fixed)

class HostMocks(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.project=Path(self.temp.name)/'project';self.logs=self.project/'logs';self.logs.mkdir(parents=True)
  self.cand=self.project/'GhostOfTsushima/experiments'/run.BASE
  self.src=self.cand/'source';self.binary=self.cand/'build/shadps4';self.binary.parent.mkdir(parents=True);self.binary.write_bytes(b'pr100')
  for rel in fix.FILES:
   q=self.src/rel;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes((SOURCE/rel).read_bytes())
  self.profile=self.cand/'runs/test/user';self.profile.mkdir(parents=True);(self.profile/'config.json').write_text('{}')
  self.protected=self.project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
  self.protected.parent.mkdir(parents=True);self.protected.write_bytes(b'protected')
  for name,data in ((run.PROFILE_TRIAL,{'diagnostic_profile':str(self.profile)}),):
   p=self.logs/name;p.mkdir();(p/'trial.json').write_text(json.dumps(data))
  p=self.logs/run.PREVIOUS;p.mkdir();(p/'original-files-before.json').write_text(json.dumps({str(self.protected):'old'}))
  self.calls=[];self.pending_restores=[]
  def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
  def command(args,**kw):
   if args[0]=='cp':shutil.copy2(args[-2],args[-1])
   return b'mock display'
  def game(binary,eboot,work,evid,screen):
   self.calls.append('game');(evid/'game.log').write_text('EmulatedImageFloatAtomicU32View: test\n')
   return {'time_limit_reached':True,'exit_code':0}
  self.u=types.SimpleNamespace(sha=sha,emit=lambda s:None,emulator_pids=lambda:[],
   Ollama=lambda e:None,heartbeat=lambda s:contextlib.nullcontext(),command=command,
   free_vram=lambda:10*1024**3,run_game=game,
   restore_models=lambda a,p,e:self.pending_restores.append(list(p)) or [{'result':'RESTORED_VERIFIED'}])
  self.mem=types.SimpleNamespace(SOURCES=fix.EXPECTED.copy(),KEPT={},MIN_FREE=8*1024**3,
   verify_sources=lambda u,s:None,verified_profile=lambda c,p:self.profile,
   verified_headroom=lambda *a:None)
  self.validator=types.SimpleNamespace(run=lambda *a:self.calls.append('offline') or {'fixture':'MOCK_ONLY'})
  origin=run.load
  def loader(name,path):return self.validator if name=='atomic_validation' else origin(name,path)
  self.expected=sha(self.binary)
  def build(u,args,path,label):
   self.calls.append(label);path.write_text('MOCK_COMMAND_PASS\n')
   if label=='INCREMENTAL_BUILD':self.binary.write_bytes(b'new')
  def fresh(*a):return {'failures_by_mode':{'universal':0},'atomic_target_all_modes_pass':True,'skipped':[]}
  self.patches=[patch.object(run,'load',loader),patch.object(run,'EXPECTED',self.expected),
   patch.object(run,'logged',build),patch.object(run,'fresh_validation',fresh),patch.object(run.shutil,'which',side_effect=lambda x:None if x=='identify' else '/mock/'+x)]
  exists=Path.is_file
  self.patches.append(patch.object(Path,'is_file',lambda p:True if str(p)=='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin' else exists(p)))
  for p in self.patches:p.start();self.addCleanup(p.stop)
 def outcome(self):return json.loads(next(self.logs.glob('got-c925-float-atomic-*/trial.json')).read_text())
 def test_success_is_mocked_source_build_game_validation(self):
  self.assertEqual(run.main(self.u,self.mem,self.project),0)
  self.assertEqual(self.calls,['offline','SOURCE_CONTRACT_TESTS','INCREMENTAL_BUILD','game'])
  self.assertEqual(self.protected.read_bytes(),b'protected')
  self.assertEqual(self.outcome()['original_files_changed'],[])
 def test_offline_validator_failure_does_not_mutate_or_build(self):
  self.validator.run=lambda *a:(_ for _ in ()).throw(RuntimeError('REFERENCE_FAIL'))
  self.assertEqual(run.main(self.u,self.mem,self.project),1)
  self.assertEqual(self.binary.read_bytes(),b'pr100');self.assertFalse(self.calls)
  for rel in fix.FILES:self.assertEqual((self.src/rel).read_bytes(),(SOURCE/rel).read_bytes())
 def test_compile_failure_restores_exact_source_and_binary(self):
  def fail(*a):self.binary.write_bytes(b'bad');raise RuntimeError('COMPILE_FAIL')
  with patch.object(run,'logged',fail):self.assertEqual(run.main(self.u,self.mem,self.project),1)
  self.assertEqual(self.binary.read_bytes(),b'pr100');self.assertNotIn('game',self.calls)
  for rel in fix.FILES:self.assertEqual((self.src/rel).read_bytes(),(SOURCE/rel).read_bytes())
  self.assertEqual(self.outcome()['rollback'],'SOURCE_AND_PR100_ELF_RESTORED')
 def test_changed_candidate_refuses(self):
  self.binary.write_bytes(b'different');self.assertEqual(run.main(self.u,self.mem,self.project),1);self.assertFalse(self.calls)
 def test_changed_source_refuses(self):
  (self.src/fix.FILES[0]).write_text('changed');self.assertEqual(run.main(self.u,self.mem,self.project),1);self.assertFalse(self.calls)
 def test_headroom_failure_restores_models_and_no_game(self):
  def fail(u,api,evid,pending,result):pending.append({'model':'m'});raise RuntimeError('HEADROOM')
  self.mem.verified_headroom=fail
  self.assertEqual(run.main(self.u,self.mem,self.project),1)
  self.assertEqual(len(self.pending_restores),1);self.assertNotIn('game',self.calls)
 def test_real_output_validation_failure_not_accepted(self):
  with patch.object(run,'fresh_validation',return_value={'failures_by_mode':{'universal':1},'atomic_target_all_modes_pass':False,'skipped':[]}):
   self.assertEqual(run.main(self.u,self.mem,self.project),1)
  self.assertEqual(self.outcome()['status'],'ACTUAL_SHADER_ACCEPTANCE_FAILED_OR_TARGET_NOT_CAPTURED')
 def test_restore_failure_propagates(self):
  self.mem.verified_headroom=lambda u,a,e,p,r:p.append({'model':'m'})
  self.u.restore_models=lambda *a:[{'result':'RESTORE_FAILED'}]
  self.assertEqual(run.main(self.u,self.mem,self.project),1)

if __name__=='__main__':unittest.main(verbosity=2)
