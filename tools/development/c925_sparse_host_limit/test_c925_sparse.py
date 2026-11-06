from __future__ import annotations
import contextlib,hashlib,json,os,shutil,subprocess as sp,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import apply_c925_sparse as a
import donor_fragments as donor
import run_c925_sparse as r
ROOT=Path(__file__).resolve().parent

def synthetic_patch():
 parts=['--- a/'+a.CPP,'+++ b/'+a.CPP,'@@ -1 +1 @@']
 parts+=['+'+l for l in ('\n\n'.join((donor.BIND,donor.SYNC,donor.GET))).splitlines()]
 parts+=['--- /dev/null','+++ b/'+a.NEW,'@@ -0,0 +1,51 @@']
 parts+=['+'+l for l in donor.PLAN.splitlines()]
 return ('\n'.join(parts)+'\n').encode()

class SourceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)
  self.root=self.path/'source';shutil.copytree(ROOT/'source',self.root)
  self.old={p:(self.root/p).read_bytes() for p in a.FILES};self.new=a.transform(self.old)
 def test_actual_captured_four_hashes_and_preservation(self):
  self.assertEqual(set(self.new),set(a.FILES)|{a.NEW})
  for p in a.EXPECTED:self.assertEqual(a.sha(self.old[p]),a.EXPECTED[p])
  for signature in ('void BufferCache::DownloadMemory(', 'bool BufferCache::SynchronizeMemoryFromImage(',
                    'void BufferCache::ReadMemory(', 'void BufferCache::ReadEdgeImagePages('):
   self.assertEqual(a.function(self.old[a.CPP].decode(),signature),a.function(self.new[a.CPP].decode(),signature))
  self.assertIn(b'const auto* arena = range.owner;',self.new[a.HDR])
 def test_changes_refused_without_writes(self):
  old=dict(self.old);old[a.CPP]+=b'// changed\n'
  with self.assertRaisesRegex(ValueError,'PREIMAGE'):a.transform(old)
  for p,v in self.old.items():self.assertEqual((self.root/p).read_bytes(),v)
 def test_header_missing_context_fails(self):
  old=dict(self.old);old[a.INH]=b'not the expected API'
  with self.assertRaises(ValueError):a.transform(old)
 def test_donor_parser_identity_not_a_remote_validation(self):
  data=synthetic_patch()
  with patch.object(a,'PATCH_BLOB',a.blob(data)):self.assertTrue(a.verify_donor(data))
  with self.assertRaisesRegex(ValueError,'PATCH_HASH'):a.verify_donor(data)
 def test_prepare_failure_retains_source_evidence(self):
  data=synthetic_patch();(self.root/a.INH).write_text('wrong header')
  with patch.object(a,'PATCH_BLOB',a.blob(data)):
   with self.assertRaises(ValueError):a.prepare(self.root,self.path/'evidence',data)
  self.assertEqual((self.path/'evidence/source-before'/a.INH).read_text(),'wrong header')
 def test_apply_and_rollback_original_bytes(self):
  a.install(self.root,self.old,self.new)
  with self.assertRaises(ValueError):a.install(self.root,self.old,self.new)
  binary=self.path/'elf';binary.write_bytes(b'new');backup=self.path/'old';backup.write_bytes(b'old')
  with patch.object(r,'EXPECTED',r.sha(backup)):
   result=r.restore(self.root,self.old,self.new,binary,backup)
  self.assertTrue(result['elf_restored']);self.assertFalse(result['external_source_conflicts'])
  for p,v in self.old.items():self.assertEqual((self.root/p).read_bytes(),v)
  self.assertFalse((self.root/a.NEW).exists())
 def test_external_edit_preserved_on_rollback(self):
  a.install(self.root,self.old,self.new);(self.root/a.CPP).write_bytes(b'external modification')
  binary=self.path/'elf';binary.write_bytes(b'new');backup=self.path/'old';backup.write_bytes(b'old')
  with patch.object(r,'EXPECTED',r.sha(backup)):out=r.restore(self.root,self.old,self.new,binary,backup)
  self.assertIn(a.CPP,out['external_source_conflicts']);self.assertEqual((self.root/a.CPP).read_bytes(),b'external modification')
  self.assertEqual(binary.read_bytes(),b'new')
 def test_git_patch_reverse_roundtrip(self):
  sp.run(['git','init','-q',str(self.root)],check=True)
  sp.run(['git','-C',str(self.root),'add','.'],check=True)
  sp.run(['git','-C',str(self.root),'-c','user.name=Local Test','-c','user.email=test@example.invalid','commit','-qm','before'],check=True)
  data=synthetic_patch()
  with patch.object(a,'PATCH_BLOB',a.blob(data)):a.prepare(self.root,self.path/'evid',data)
  patchfile=self.path/'evid/c925-pr109-adaptation.patch'
  sp.run(['git','-C',str(self.root),'apply','--check',str(patchfile)],check=True)
  sp.run(['git','-C',str(self.root),'apply',str(patchfile)],check=True)
  for p,v in self.new.items():self.assertEqual((self.root/p).read_bytes(),v)
  sp.run(['git','-C',str(self.root),'apply','-R',str(patchfile)],check=True)
  for p,v in self.old.items():self.assertEqual((self.root/p).read_bytes(),v)

class RunnerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.project=self.root/'project';self.logs=self.project/'logs';self.logs.mkdir(parents=True)
  self.cand=self.project/'GhostOfTsushima/experiments'/r.BASE;self.src=self.cand/'source'
  shutil.copytree(ROOT/'source',self.src);self.binary=self.cand/'build/shadps4';self.binary.parent.mkdir(parents=True);self.binary.write_bytes(b'old')
  self.elf_sha=r.sha(self.binary);self.originals={p:(self.src/p).read_bytes() for p in a.FILES}
  (self.src/r.IMGUI).parent.mkdir(parents=True,exist_ok=True);(self.src/r.IMGUI).write_bytes(b'imgui kept')
  prior=self.logs/r.PRIOR;prior.mkdir();profile=self.cand/'runs/previous/user';profile.mkdir(parents=True);(profile/'config.json').write_text('{}')
  r.save(prior/'trial.json',{'build':'PASS','candidate_sha256':self.elf_sha,'private_profile':str(profile)})
  r.save(prior/'retained-source-sha256.json',{a.CPP:r.sha(self.src/a.CPP)})
  protected=self.project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
  protected.parent.mkdir(parents=True);protected.write_bytes(b'protected');self.protected=protected
  r.save(prior/'original-files-before.json',{str(protected):r.sha(protected)})
  self.here=self.root/'scripts';self.here.mkdir();self.data=synthetic_patch();(self.here/'shared-pr109.patch').write_bytes(self.data)
  self.game_calls=[];self.restore_calls=[];self.build_fail=False;self.gate_fail=False
  def command(args,**kwargs):
   if args[0]=='cp':shutil.copy2(args[-2],args[-1])
   return b'fixture command output'
  self.u=types.SimpleNamespace(emit=lambda x:None,heartbeat=lambda x:contextlib.nullcontext(),
   Ollama=lambda e:object(),emulator_pids=lambda:[],command=command,free_vram=lambda:9*1024**3,
   restore_models=lambda *args:self.restore_calls.append(args) or [{'result':'RESTORED_VERIFIED'}])
  def logged(u,args,path,label,*rest):
   path.write_text('mocked '+label)
   if 'cmake'==args[0]:
    if self.build_fail:raise RuntimeError('MOCK_NATIVE_BUILD_FAILURE')
    self.binary.write_bytes(b'candidate')
  def profile_copy(old,new):new.mkdir(parents=True);(new/'config.json').write_text('{}');return {'test_mock':True}
  def game(*args):
   self.game_calls.append(args);ev=args[4]
   (ev/'game.log').write_text('Enabled instance layers: VK_LAYER_KHRONOS_validation\nVulkan vkValidation: true\nVulkan vkValidationCore: true\nVulkan vkValidationSync: true\nSparseArenaWindow:')
   return {'first_validation_error':None,'exit_code':0,'time_limit_reached':True}
  self.m=types.SimpleNamespace(logged=logged,copy_profile=profile_copy,game=game)
  def headroom(u,api,evid,pending,result):
   if self.gate_fail:pending.append({'name':'test-model'});raise RuntimeError('MOCK_HEADROOM_FAILURE')
  self.hr=types.SimpleNamespace(verified_headroom=headroom,MIN_FREE=8*1024**3)
  for ctx in [patch.object(r,'EXPECTED',self.elf_sha),patch.object(r,'IMGUI_BLOB',r.blob(b'imgui kept')),
              patch.object(r,'__file__',str(self.here/'run_c925_sparse.py')),patch.object(a,'PATCH_BLOB',a.blob(self.data)),
              patch.object(r,'native_contracts',return_value=['mock-compiler']),patch.object(r.shutil,'which',return_value='/mock/tool')]:
   ctx.start();self.addCleanup(ctx.stop)
  original_is_file=Path.is_file
  def is_file(p):return True if str(p)=='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin' else original_is_file(p)
  ctx=patch.object(Path,'is_file',is_file);ctx.start();self.addCleanup(ctx.stop)
 def run_host(self):return r.main(self.project,(self.u,self.m,self.hr,a))
 def result(self):
  files=list(self.logs.glob('got-c925-sparse-host-limit-*/trial.json'));self.assertEqual(len(files),1)
  return json.loads(files[0].read_text())
 def test_mock_native_success_preserves_protected(self):
  self.assertEqual(self.run_host(),0);result=self.result()
  self.assertEqual(result['build'],'PASS');self.assertEqual(len(self.game_calls),1)
  self.assertEqual(result['original_files_changed'],[]);self.assertEqual(self.protected.read_bytes(),b'protected')
 def test_mock_native_failure_restores_source_elf(self):
  self.build_fail=True;self.assertEqual(self.run_host(),1);result=self.result()
  self.assertTrue(result['rollback']['elf_restored']);self.assertFalse(self.game_calls)
  for p,v in self.originals.items():self.assertEqual((self.src/p).read_bytes(),v)
  self.assertFalse((self.src/a.NEW).exists())
 def test_native_fixture_failure_no_patch_or_launch(self):
  with patch.object(r,'native_contracts',side_effect=RuntimeError('MOCK_CONTRACT_FAILED')):self.assertEqual(self.run_host(),1)
  self.assertFalse(self.game_calls);self.assertFalse((self.src/a.NEW).exists());self.assertEqual(self.binary.read_bytes(),b'old')
 def test_source_changed_before_start_refused(self):
  (self.src/a.HDR).write_bytes(b'external');self.assertEqual(self.run_host(),1)
  self.assertFalse(self.game_calls);self.assertEqual(self.binary.read_bytes(),b'old')
 def test_elf_changed_refused(self):
  self.binary.write_bytes(b'external');self.assertEqual(self.run_host(),1)
  self.assertFalse(self.game_calls);self.assertEqual(self.binary.read_bytes(),b'external')
 def test_headroom_failure_restores_model_not_build(self):
  self.gate_fail=True;self.assertEqual(self.run_host(),1)
  self.assertEqual(len(self.restore_calls),1);self.assertFalse(self.game_calls)
  self.assertEqual(self.result()['build'],'PASS')
 def test_layer_failure_never_patches(self):
  self.m.logged=lambda *args:(_ for _ in ()).throw(RuntimeError('MOCK_LAYER_UNAVAILABLE'))
  self.assertEqual(self.run_host(),1);self.assertFalse((self.src/a.NEW).exists());self.assertFalse(self.game_calls)
 def test_new_validation_error_saved_not_graphics_pass(self):
  original=self.m.game
  def fail(*args):
   result=original(*args);result['first_validation_error']={'message':'VUID-test'};result['time_limit_reached']=False;return result
  self.m.game=fail
  self.assertEqual(self.run_host(),0);self.assertEqual(self.result()['status'],'VALIDATION_ERROR_CAPTURED')
  self.assertEqual(self.result()['graphics'],'NOT_INFERRED_FROM_EXIT_OR_IDENTICAL_MENU_IMAGES')

if __name__=='__main__':unittest.main(verbosity=2)
