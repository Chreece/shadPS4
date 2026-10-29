from pathlib import Path
import contextlib,hashlib,json,os,shutil,subprocess as sp,tempfile,types,unittest
from unittest.mock import patch
import run_menu_validation as r
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
TEMPLATE=HERE/'imgui_mapped_upload_fixture.cpp.in'
if not TEMPLATE.is_file():TEMPLATE=REPO/'tools/development/imgui_mapped_upload_fixture.cpp.in'
PATCH_FILE=HERE/'imgui-mapped-upload.patch'
if not PATCH_FILE.is_file():PATCH_FILE=REPO/'scripts/patchsets/imgui-mapped-upload-20261029.patch'
OLD='''v.device.mapMemory(info.upload_buffer_memory, 0, size)
.memory = info.upload_buffer_memory, .size = size,
v.device.mapMemory(frb.vertex.buffer_memory, 0, vertex_size, vk::MemoryMapFlags{})
.memory = frb.vertex.buffer_memory, .size = VK_WHOLE_SIZE,
v.device.mapMemory(frb.index.buffer_memory, 0, index_size, vk::MemoryMapFlags{})
.memory = frb.index.buffer_memory, .size = VK_WHOLE_SIZE,
v.device.mapMemory(staging.memory, 0, upload_size)
.memory = staging.memory, .size = VK_WHOLE_SIZE,
'''
NEW=OLD.replace(', 0, size)',', 0, VK_WHOLE_SIZE)').replace('.size = size,','.size = VK_WHOLE_SIZE,').replace(', 0, vertex_size,',', 0, VK_WHOLE_SIZE,').replace(', 0, index_size,',', 0, VK_WHOLE_SIZE,').replace(', 0, upload_size)',', 0, VK_WHOLE_SIZE)')
class Components(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def profile(self):
  p=self.root/'old/user';p.mkdir(parents=True);(p/'config.json').write_text(json.dumps({'GPU':{'dump_shaders':True},'Vulkan':{'device':0},'General':{'home_dir':str(p/'home')}}));(p/'home').mkdir();(p/'home/save').write_text('save');return p
 def test_profile_changes_only_diagnostics_and_internal_paths(self):
  old=self.profile();before={p:p.read_bytes() for p in old.rglob('*') if p.is_file()};dest=self.root/'new/user';info=r.copy_profile(old,dest)
  cfg=json.loads((dest/'config.json').read_text());self.assertEqual(cfg['GPU'],{'dump_shaders':True});self.assertEqual(cfg['Vulkan']['device'],0);self.assertTrue(cfg['Vulkan']['vkvalidation_sync_enabled']);self.assertFalse(cfg['Vulkan']['vkvalidation_gpu_enabled']);self.assertEqual(cfg['General']['home_dir'],str(dest/'home'));self.assertEqual((dest/'home/save').read_text(),'save');self.assertTrue(all(p.read_bytes()==d for p,d in before.items()))
 def test_pergame_diagnostic_override(self):
  old=self.profile();(old/'custom_configs').mkdir();(old/'custom_configs/CUSA11456.json').write_text('{"Vulkan":{"vkvalidation_enabled":false},"GPU":{"readbacks_mode":2}}');dest=self.root/'new/user';r.copy_profile(old,dest);c=json.loads((dest/'custom_configs/CUSA11456.json').read_text());self.assertTrue(c['Vulkan']['vkvalidation_enabled']);self.assertEqual(c['GPU']['readbacks_mode'],2)
 def test_external_save_refused_before_copy(self):
  old=self.profile();(old/'config.json').write_text('{"General":{"home_dir":"/outside"}}');dest=self.root/'new/user'
  with self.assertRaisesRegex(RuntimeError,'EXTERNAL_SAVE'):r.copy_profile(old,dest)
  self.assertFalse(dest.exists())
 def test_symlink_refused(self):
  old=self.profile();(old/'bad').symlink_to('/etc/passwd')
  with self.assertRaisesRegex(RuntimeError,'SYMLINK'):r.copy_profile(old,self.root/'new/user')
 def test_image_equality_and_shader_warnings_not_errors(self):
  self.assertIsNone(r.first_error('Sharp source was not flatenned\n identical menu\n[<Warning>] VUID-test'))
 def test_error_multiline_detection(self):
  self.assertIn('01389',r.first_error('Validation Error: [ VUID-VkMappedMemoryRange-size-01389 ]\nmessage'))
  self.assertIsNotNone(r.first_error('[Render] <Error> VUID-X-00000'))
  self.assertIsNotNone(r.first_error('Validation Error: SYNC-HAZARD-READ-AFTER-WRITE'))
 def test_fixture_ambiguity_rejected(self):
  t=TEMPLATE.read_text()
  with self.assertRaisesRegex(RuntimeError,'UPLOAD_CONTEXT'):r.fixture(OLD+OLD,t)
 def test_fixture_all_paths_preserved(self):
  t=TEMPLATE.read_text();x=r.fixture(NEW,t);self.assertNotIn('/* MAP_',x);self.assertEqual(x.count('(void)v.device.mapMemory('),4)
 def test_child_output_and_failed_exit(self):
  def stop(p):
   if p and p.poll() is None:p.kill();p.wait()
  u=types.SimpleNamespace(emit=lambda s:None,heartbeat=lambda s:contextlib.nullcontext(),stop_owned=stop)
  r.logged(u,['python3','-c','print("ok")'],self.root/'ok.log','TEST');self.assertIn('ok',(self.root/'ok.log').read_text())
  with self.assertRaisesRegex(RuntimeError,'FAILED_3'):r.logged(u,['python3','-c','raise SystemExit(3)'],self.root/'bad.log','TEST')

class RealOwnedProcess(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.profile=self.root/'run/user';self.profile.mkdir(parents=True);self.evid=self.root/'evidence';self.evid.mkdir()
  def stop(p):
   if p is None or p.poll() is not None:return
   import signal
   try:
    os.killpg(p.pid,signal.SIGTERM)
    try:p.wait(timeout=2)
    except sp.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=3)
   except ProcessLookupError:pass
  self.u=types.SimpleNamespace(emit=lambda s:None,stop_owned=stop)
 def test_real_validation_error_stops_only_spawned_child(self):
  child=self.root/'child';child.write_text('#!/usr/bin/env python3\nimport time\nprint("[Render] <Error> VUID-Fixture-00001",flush=True)\ntime.sleep(30)\n');child.chmod(0o700)
  outcome=r.game(self.u,child,self.root/'fake-eboot',self.profile,self.evid,':unused')
  self.assertIsNotNone(outcome['first_validation_error']);self.assertLess(outcome['seconds'],4);self.assertFalse(outcome['time_limit_reached']);self.assertEqual(json.loads((self.evid/'capture-times.json').read_text()),[])
 def test_real_early_exit_is_not_validation_success(self):
  child=self.root/'child';child.write_text('#!/usr/bin/env python3\nraise SystemExit(3)\n');child.chmod(0o700)
  outcome=r.game(self.u,child,self.root/'fake-eboot',self.profile,self.evid,':unused')
  self.assertEqual(outcome['exit_code'],3);self.assertFalse(outcome['time_limit_reached']);self.assertIsNone(outcome['first_validation_error'])

class Orchestration(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name);self.root=self.home/'projects/shadps4';self.logs=self.root/'logs';self.logs.mkdir(parents=True);self.cand=self.root/'GhostOfTsushima/experiments'/r.BASE;self.src=self.cand/'source';self.bin=self.cand/'build/shadps4';self.bin.parent.mkdir(parents=True);self.bin.write_bytes(b'oldelf');self.code=self.src/r.CODE;self.code.parent.mkdir(parents=True);self.code.write_text(OLD)
  profile=self.cand/'runs/old/user';profile.mkdir(parents=True);(profile/'config.json').write_text('{"GPU":{},"Vulkan":{}}')
  self.save=self.home/'saved.txt';self.save.write_text('original')
  for name in (r.PRIOR,r.PR106):
   e=self.logs/name;e.mkdir();(e/'trial.json').write_text(json.dumps({'build':'PASS','candidate_sha256':hashlib.sha256(b'oldelf').hexdigest(),'source_manifest':{},'private_profile':str(profile)}))
  (self.logs/r.PRIOR/'original-files-before.json').write_text(json.dumps({str(self.save):r.digest(self.save)}))
  self.called=[];self.failbuild=False;self.failprobe=False;self.failgame=False
  def command(args,**kw):
   if args[0]=='cp':r.shutil.copy2(args[-2],args[-1])
   return b'display'
  self.u=types.SimpleNamespace(emit=lambda s:None,heartbeat=lambda s:contextlib.nullcontext(),emulator_pids=lambda:[],command=command,Ollama=lambda e:object(),free_vram=lambda:9*1024**3,restore_models=lambda *a:[])
  self.h=types.SimpleNamespace(MIN_FREE=8*1024**3,verified_headroom=lambda *a:None)
  def logged(u,args,path,label,timeout=None):
   self.called.append(label);path.write_text('PASS\n')
   if label=='INSTANCE_ONLY_LAYER_PREFLIGHT' and self.failprobe:raise RuntimeError('LAYER_NOT_PRESENT')
   if label=='APPLY_EXISTING_PR108':self.code.write_text(NEW)
   if label=='INCREMENTAL_IMGUI_BUILD':
    if self.failbuild:raise RuntimeError('COMPILE_FAILED')
    self.bin.write_bytes(b'newelf')
  def game(u,binary,eboot,profile,evid,screen):
   (evid/'game.log').write_text('Enabled instance layers: VK_LAYER_KHRONOS_validation\nVulkan vkValidation: true\nVulkan vkValidationCore: true\nVulkan vkValidationSync: true\n')
   return {'first_validation_error':{'message':'VUID-new-12345'} if self.failgame else None,'time_limit_reached':not self.failgame,'exit_code':0}
  self.real_file=Path.is_file
  def exists(p):
   return True if str(p)=='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin' else self.real_file(p)
  package=self.home/'package';package.mkdir();shutil.copy2(TEMPLATE,package/TEMPLATE.name);shutil.copy2(PATCH_FILE,package/'imgui-mapped-upload.patch')
  self.patches=[patch.object(r,'__file__',str(package/'run_menu_validation.py')),patch.object(r,'PINS',{}),patch.object(r,'KEPT',{}),patch.object(r,'ELF',r.digest(self.bin)),patch.object(r,'PREIMAGE',r.blob(OLD.encode())),patch.object(r.Path,'home',return_value=self.home),patch.object(r,'load',side_effect=lambda n,p:self.u if n=='runtime' else self.h),patch.object(r,'logged',side_effect=logged),patch.object(r,'game',side_effect=game),patch.object(r.shutil,'which',side_effect=lambda s:'/bin/'+s if s!='clang++' else None),patch.object(Path,'is_file',exists)]
  for p in self.patches:p.start();self.addCleanup(p.stop)
 def result(self):return json.loads(next(self.logs.glob('got-c925-menu-resource-validation-*/trial.json')).read_text())
 def test_success_keeps_fix_and_prior_checkpoint(self):
  self.assertEqual(r.main(),0);out=self.result();self.assertEqual(out['build'],'PASS');self.assertTrue(out['layer_confirmed']);self.assertEqual(self.code.read_text(),NEW);self.assertEqual(Path(out['prior_checkpoint']).read_bytes(),b'oldelf');self.assertEqual(self.save.read_text(),'original')
 def test_compile_failure_restores(self):
  self.failbuild=True;self.assertEqual(r.main(),1);self.assertEqual(self.code.read_text(),OLD);self.assertEqual(self.bin.read_bytes(),b'oldelf');self.assertEqual(self.result()['rollback'],'SOURCE_AND_PR107_ELF_RESTORED')
 def test_layer_failure_before_mutation(self):
  self.failprobe=True;self.assertEqual(r.main(),1);self.assertEqual(self.code.read_text(),OLD);self.assertNotIn('APPLY_EXISTING_PR108',self.called)
 def test_changed_binary_refused(self):
  self.bin.write_bytes(b'other');self.assertEqual(r.main(),1);self.assertEqual(self.code.read_text(),OLD);self.assertFalse(self.called)
 def test_changed_source_captured_not_overwritten(self):
  self.code.write_text('external edit');self.assertEqual(r.main(),1);self.assertEqual(self.code.read_text(),'external edit');self.assertFalse(self.called)
 def test_actual_error_not_claimed_graphics_pass(self):
  self.failgame=True;self.assertEqual(r.main(),0);self.assertEqual(self.result()['status'],'VALIDATION_ERROR_CAPTURED');self.assertFalse(self.result()['main_promotion'])

if __name__=='__main__':unittest.main(verbosity=2)
