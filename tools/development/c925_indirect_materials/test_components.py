#!/usr/bin/env python3
from pathlib import Path
import contextlib,hashlib,importlib.util,json,os,shutil,signal,subprocess,sys,tempfile,time,unittest
sys.path.insert(0,str(Path(__file__).parent))
import apply_indirect_samples as fix
import run_indirect_samples as run
BASE=Path(os.environ.get('SOURCE_FIXTURE','.'))
class U:
 @staticmethod
 def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
 @staticmethod
 def emit(s):pass
 @staticmethod
 @contextlib.contextmanager
 def heartbeat(s):yield
 @staticmethod
 def stop_owned(p):
  if p and p.poll() is None:
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=2)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
class Components(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.p=Path(self.temp.name);self.source=self.p/'source';self.source.mkdir()
  for rel in fix.FILES:
   f=self.source/rel;f.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/rel,f)
  self.old={p:(self.source/p).read_bytes() for p in fix.FILES}
 def test_transform_has_guards_and_snapshot_check_before_old_walker(self):
  transformed=fix.transform(self.old);self.assertEqual(set(transformed),set(fix.FILES))
  s=transformed[fix.FILES[2]].decode();self.assertLess(s.index('!info.IndirectSamplesCompatible()'),s.index('info.RefreshFlatBuf()',s.index('!info.IndirectSamplesCompatible()')))
  s=transformed[fix.FILES[1]].decode();self.assertIn('ir.IEqual(IR::U32{plan->selector}',s);self.assertIn('clone->SetArg(1, sampler)',s);self.assertIn('guards.emplace(clone, selected)',s)
  for rel,data in self.old.items():self.assertEqual((self.source/rel).read_bytes(),data)
 def test_changed_source_fails_before_all_writes(self):
  p=self.source/fix.FILES[1];p.write_bytes(p.read_bytes()+b'\n')
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):fix.apply(self.source,self.p/'evid')
  self.assertFalse(any((self.source/p).exists() for p in fix.NEW))
  for rel,data in self.old.items():
   if rel!=fix.FILES[1]:self.assertEqual((self.source/rel).read_bytes(),data)
 def test_repeat_apply_fails(self):
  fix.apply(self.source,self.p/'evid')
  after={p:(self.source/p).read_bytes() for p in [*fix.FILES,*fix.NEW]}
  with self.assertRaises(ValueError):fix.apply(self.source,self.p/'evid2')
  self.assertEqual(after,{p:(self.source/p).read_bytes() for p in after})
 def test_git_forward_reverse_restore(self):
  e=self.p/'evid';fix.apply(self.source,e);diff=e/'indirect-sample.patch'
  for args in [['git','apply','--check','-R',str(diff)],['git','apply','-R',str(diff)]]:subprocess.run(args,cwd=self.source,check=True,capture_output=True)
  for p,data in self.old.items():self.assertEqual((self.source/p).read_bytes(),data)
  for p in fix.NEW:self.assertFalse((self.source/p).exists())
  for args in [['git','apply','--check',str(diff)],['git','apply',str(diff)]]:subprocess.run(args,cwd=self.source,check=True,capture_output=True)
 def test_rollback_restores_bytes_and_elf(self):
  fix.apply(self.source,self.p/'evid');b=self.p/'binary';backup=self.p/'backup';b.write_bytes(b'new');backup.write_bytes(b'old')
  self.assertTrue(run.rollback(self.source,self.old,fix.NEW,b,backup,U.sha(backup)))
  self.assertEqual(b.read_bytes(),b'old')
  for p,data in self.old.items():self.assertEqual((self.source/p).read_bytes(),data)
  for p in fix.NEW:self.assertFalse((self.source/p).exists())
 def test_kept_guard_refuses_modified_code(self):
  run.verify(U,self.source,fix.EXPECTED);p=self.source/fix.FILES[0];p.write_bytes(b'changed')
  with self.assertRaisesRegex(RuntimeError,'KEPT_SOURCE_CHANGED'):run.verify(U,self.source,fix.EXPECTED)
 def profile(self,data=None):
  p=self.p/'runs/test/user';p.mkdir(parents=True);(p/'config.json').write_text(json.dumps(data or {}));return p
 def test_profile_no_changes(self):
  p=self.profile();self.assertEqual(run.check_profile(self.p,p),p);self.assertEqual((p/'config.json').read_text(),'{}')
 def test_profile_symlink_reject(self):
  p=self.profile();(p/'bad').symlink_to('/etc/passwd')
  with self.assertRaisesRegex(RuntimeError,'SYMLINK'):run.check_profile(self.p,p)
 def test_external_save_reject(self):
  p=self.profile({'General':{'home_dir':'/other/saves'}})
  with self.assertRaisesRegex(RuntimeError,'EXTERNAL_SAVE'):run.check_profile(self.p,p)
 def test_real_child_success_failure_timeout(self):
  run.logged(U,[sys.executable,'-c','print("yes")'],self.p/'ok.log','TEST',5)
  self.assertIn('yes',(self.p/'ok.log').read_text())
  with self.assertRaisesRegex(RuntimeError,'EXIT_7'):run.logged(U,[sys.executable,'-c','raise SystemExit(7)'],self.p/'bad.log','TEST',5)
  t=time.monotonic()
  with self.assertRaisesRegex(RuntimeError,'TIMEOUT'):run.logged(U,[sys.executable,'-c','import time;time.sleep(5)'],self.p/'timeout.log','TEST',.1)
  self.assertLess(time.monotonic()-t,2)
if __name__=='__main__':unittest.main(verbosity=2)
