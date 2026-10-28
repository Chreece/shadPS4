import importlib.util,json,os,shutil,subprocess,tempfile,time,unittest,hashlib,signal
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('capture',Path(__file__).with_name('capture_remaining_shaders.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Util:
 @staticmethod
 def emit(x):pass
 @staticmethod
 def stop_owned(p):
  if p is not None and p.poll() is None:
   os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=2)
 @staticmethod
 def command(args,timeout=10):return subprocess.check_output(args,timeout=timeout)
class Cases(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def profile(self):
  r=self.root/'old';r.mkdir();(r/'home').mkdir();(r/'home/save').write_bytes(b'saved')
  (r/'cache').mkdir();(r/'cache/ignored').write_text('oldcache')
  (r/'config.json').write_text(json.dumps({'GPU':{'dump_shaders':False,'readbacks_mode':2},'General':{'home_dir':str(r/'home')}}));return r
 def test_profile_clone_only_dump_and_path_change(self):
  old=self.profile();before=m.sha(old/'config.json');new=self.root/'new'
  hashes=m.copy_private_profile(old,new);j=json.loads((new/'config.json').read_text())
  self.assertEqual(j['GPU'],{'dump_shaders':True,'readbacks_mode':2});self.assertEqual(j['General']['home_dir'],str(new/'home'))
  (new/'home/save').write_text('copy changed');self.assertEqual((old/'home/save').read_bytes(),b'saved')
  self.assertEqual(m.sha(old/'config.json'),before);self.assertFalse((new/'cache').exists());self.assertIn(str(old/'home/save'),hashes)
 def test_symlink_refused(self):
  old=self.profile();(old/'leak').symlink_to('/etc/passwd')
  with self.assertRaisesRegex(RuntimeError,'SYMLINK'):m.copy_private_profile(old,self.root/'new')
 def test_external_save_refused(self):
  old=self.profile();(old/'config.json').write_text(json.dumps({'General':{'home_dir':'/external/saves'}}))
  before=m.sha(old/'config.json')
  with self.assertRaisesRegex(RuntimeError,'EXTERNAL'):m.copy_private_profile(old,self.root/'new')
  self.assertEqual(m.sha(old/'config.json'),before)
 def test_path_relocation_prefix_boundaries(self):
  data={'x':['/a/home/save','/a/home2/save','/a/home']}
  self.assertEqual(m.relocate(data,'/a/home','/b/home'),{'x':['/b/home/save','/a/home2/save','/b/home']})
 def test_existing_fresh_evidence_and_no_profile(self):
  old=self.root/'old';new=self.root/'new';e=self.root/'e';old.mkdir();new.mkdir();e.mkdir()
  f='cs_0x000000005065ed7b.pre-res-patch.irprogram.txt'
  (old/f).write_text('old');(new/f).write_text('fresh');(new/'private.json').write_text('secret')
  records=m.collect_dumps([old,new],e,new)
  self.assertEqual(len(records),2);self.assertEqual({r['provenance'] for r in records},{'existing','fresh'})
  self.assertTrue(all('private' not in r['relative'] for r in records))
 def test_size_limit_and_symlink(self):
  r=self.root/'d';r.mkdir();e=self.root/'e';e.mkdir();f=r/'cs_0x000000005065ed7b_0.bin'
  with f.open('wb') as q:q.truncate(13*1024**2)
  (r/'cs_0x000000005065ed7b_0.spv').symlink_to(f)
  self.assertEqual(m.collect_dumps([r],e,r),[])
 def test_process_snapshot_permission_and_missing(self):
  proc=self.root/'proc';task=proc/'123/task/124';task.mkdir(parents=True)
  (task/'comm').write_text('GpuComm');(task/'stat').write_text('124 (x) S 0 1')
  out=m.thread_snapshot(123,proc);self.assertEqual(out[0]['tid'],124);self.assertEqual(out[0]['comm'],'GpuComm');self.assertIn('error',out[0]['wchan'])
  self.assertEqual(m.thread_snapshot(456,proc),[])
 def test_gpu_snapshot_readonly(self):
  p=self.root/'card0/device';p.mkdir(parents=True);(p/'gpu_busy_percent').write_text('42')
  self.assertIn('42',m.gpu_snapshot(self.root).values())
 def test_no_validator_no_install(self):
  with patch.object(m.shutil,'which',return_value=None):out=m.validate_spirv([],self.root,Util)
  self.assertIn('NOT_INSTALLED',out['tool'])
 def test_early_owned_child_exit(self):
  f=self.root/'game';f.write_text('#!/bin/sh\nexit 0\n');f.chmod(0o755);e=self.root/'e';e.mkdir()
  result=m.capture_game(Util,f,self.root,e,':999')
  self.assertEqual(result['exit_code'],0);self.assertFalse(result['time_limit_reached']);self.assertTrue((e/'threads-and-gpu.jsonl').exists());self.assertTrue((e/'capture-times.json').exists())
 def test_pixel_signatures_ignore_png_metadata(self):
  if not shutil.which('identify'):self.skipTest('ImageMagick unavailable')
  from PIL import Image,PngImagePlugin
  for i in (55,85,112):
   meta=PngImagePlugin.PngInfo();meta.add_text('timestamp',str(i));Image.new('RGB',(4,4),(1,2,3)).save(self.root/f'menu-{i}.png',pnginfo=meta)
  rows=m.pixel_signatures(self.root,Util)
  self.assertEqual(len({r['pixel_signature'] for r in rows}),1);self.assertEqual(len({r['png_sha256'] for r in rows}),3)
if __name__=='__main__':unittest.main(verbosity=2)
