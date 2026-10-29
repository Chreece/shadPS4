#!/usr/bin/env python3
"""Offline locator/audit tests. Mock validators do not validate shader semantics."""
import contextlib,hashlib,io,json,os,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import complete_indirect_validation as m

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.project=self.root/'project';self.project.mkdir()
  self.evid=self.root/'evidence';self.evid.mkdir();(self.project/'scripts').mkdir();(self.project/'logs').mkdir()
 def private(self,data=b'known-tool'):
  p=self.project/'scripts'/('spirv-tools-private-'+m.PACKAGE_SHA[:16])/'unpacked-123/usr/bin/spirv-val'
  p.parent.mkdir(parents=True);p.write_bytes(data);p.chmod(0o700);return p
 def archive(self,items):
  path=self.root/'inputs.tar.gz'
  with tarfile.open(path,'w:gz') as tf:
   for name,data,kind in items:
    info=tarfile.TarInfo(name);info.size=len(data);info.type=kind
    if kind==tarfile.SYMTYPE:info.linkname='/etc/passwd';info.size=0
    tf.addfile(info,io.BytesIO(data) if info.isfile() else None)
  return path
 def module_bytes(self,token=0):return b'\x03\x02#\x07'+bytes([token])+bytes(15)
 def fake_run(self,args,timeout=0):
  return (0,'SPIRV-Tools fixture\n' if '--version' in [str(x) for x in args] else '--allow-offset-texture-operand')
 def test_known_private_location_found_without_path_install(self):
  tool=self.private();digest=m.sha(tool)
  with patch.object(m,'VALIDATOR_SHA',digest),patch.object(m.shutil,'which',return_value=None),patch.object(m,'run',self.fake_run):
   found,meta=m.locate_validator(self.project,self.evid)
  self.assertEqual(found,tool);self.assertEqual(meta['result'],'SELECTED')
 def test_bad_private_hash_not_executed(self):
  self.private()
  with patch.object(m.shutil,'which',return_value=None),patch.object(m,'run') as run:
   with self.assertRaisesRegex(RuntimeError,'VALIDATOR_UNAVAILABLE'):m.locate_validator(self.project,self.evid)
   run.assert_not_called()
 def test_metadata_path_outside_scripts_refused(self):
  p=self.project/'logs/got-c925-spirv-offline-20261028T175021642972Z';p.mkdir()
  external=self.root/'external/usr/bin/spirv-val';external.parent.mkdir(parents=True);external.write_text('tool');external.chmod(0o700)
  (p/'validator-package.json').write_text(json.dumps({'sha256':m.PACKAGE_SHA,'extracted_to':str(self.root/'external')}))
  with patch.object(m.shutil,'which',return_value=None),patch.object(m,'run') as run:
   with self.assertRaisesRegex(RuntimeError,'VALIDATOR_UNAVAILABLE'):m.locate_validator(self.project,self.evid)
   run.assert_not_called()
 def test_installed_tool_version_checked(self):
  tool=self.root/'installed';tool.write_text('tool');tool.chmod(0o700)
  with patch.object(m.shutil,'which',return_value=str(tool)),patch.object(m,'run',self.fake_run):
   found,meta=m.locate_validator(self.project,self.evid)
  self.assertEqual(meta['origin'],'installed-PATH-tool')
 def test_missing_tool_returns_failure_not_pass(self):
  with patch.object(m.shutil,'which',return_value=None):
   with self.assertRaisesRegex(RuntimeError,'UNAVAILABLE'):m.locate_validator(self.project,self.evid)
  self.assertEqual(json.loads((self.evid/'validator-discovery.json').read_text()),[])
 def test_dedup_retains_all_origins(self):
  b=self.module_bytes();c=self.module_bytes(1)
  arc=self.archive([('x/fresh-shaders/a.spv',b,tarfile.REGTYPE),('x/fresh-shaders/b.spv',b,tarfile.REGTYPE),('x/fresh-shaders/c.spv',c,tarfile.REGTYPE)])
  mods,n=m.snapshot_modules(arc,self.evid);self.assertEqual(n,3);self.assertEqual(len(mods),2)
  self.assertEqual(len(mods[0]['origins']),2)
 def test_unsafe_tar_members_ignored(self):
  b=self.module_bytes()
  arc=self.archive([('../fresh-shaders/bad.spv',b,tarfile.REGTYPE),('/fresh-shaders/bad.spv',b,tarfile.REGTYPE),('x/fresh-shaders/link.spv',b,tarfile.SYMTYPE),('x/fresh-shaders/good.spv',b,tarfile.REGTYPE)])
  mods,n=m.snapshot_modules(arc,self.evid);self.assertEqual(n,1)
 def test_invalid_shader_container_rejected(self):
  arc=self.archive([('x/fresh-shaders/invalid.spv',b'not-a-shader-at-allxx',tarfile.REGTYPE)])
  with self.assertRaisesRegex(RuntimeError,'INVALID_SPIRV'):m.snapshot_modules(arc,self.evid)
 def test_validation_modes_remain_separate(self):
  (self.evid/'modules').mkdir();h=hashlib.sha256(self.module_bytes()).hexdigest();(self.evid/'modules'/(h+'.spv')).write_bytes(self.module_bytes())
  def fake(args,timeout):return (1,'strict diagnostic') if args[1:]==m.MODES['vulkan13-strict']+[self.evid/'modules'/(h+'.spv')] else (0,'')
  with patch.object(m,'run',fake),contextlib.redirect_stdout(io.StringIO()):rows=m.audit_modules('fixture',[{'sha256':h}],self.evid)
  self.assertEqual([r['result'] for r in rows],['PASS','DIAGNOSTIC_REQUIRES_REVIEW','PASS'])
 def test_gow_only_exact_run_captured(self):
  logs=self.project/'logs';(logs/m.GOW_GAME).write_text('actual error line')
  (logs/'unrelated-game.log').write_text('private unrelated')
  rows=m.collect_existing_gow(self.project,self.evid)
  self.assertTrue((self.evid/'gow-existing'/m.GOW_GAME).is_file())
  self.assertFalse((self.evid/'gow-existing/unrelated-game.log').exists())
  self.assertEqual((logs/m.GOW_GAME).read_text(),'actual error line')
 def test_real_child_exit_and_timeout(self):
  code,out=m.run([sys.executable,'-c','print("ok");raise SystemExit(3)'],timeout=3)
  self.assertEqual((code,out),(3,'ok\n'))
  with self.assertRaises(subprocess.TimeoutExpired):m.run([sys.executable,'-c','import time;time.sleep(10)'],timeout=.05)
 def test_mock_audit_end_to_end_no_emulator_writes(self):
  b=self.module_bytes();arc=self.archive([('x/fresh-shaders/a.spv',b,tarfile.REGTYPE),('x/fresh-shaders/b.spv',b,tarfile.REGTYPE)])
  target=self.project/'logs'/(m.PRIOR+'.tar.gz');target.write_bytes(arc.read_bytes())
  tool=self.private();binary=self.project/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z/build/shadps4'
  binary.parent.mkdir(parents=True);binary.write_bytes(b'protected-test')
  originals={p:p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
  with patch.object(m,'ARCHIVE_SHA',m.sha(target)),patch.object(m,'VALIDATOR_SHA',m.sha(tool)),patch.object(m.shutil,'which',return_value=None),patch.object(m,'run',self.fake_run),contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(m.main(self.project),0)
  results=list((self.project/'logs').glob('got-c925-indirect-validation-*/trial.json'));self.assertEqual(len(results),1)
  result=json.loads(results[0].read_text());self.assertEqual(result['distinct_module_bytes'],1);self.assertEqual(result['game'],'NOT_RUN')
  for p,data in originals.items():self.assertEqual(p.read_bytes(),data)
  self.assertEqual(len(list((self.project/'logs').glob('got-c925-indirect-validation-*.tar.gz'))),1)

if __name__=='__main__':unittest.main(verbosity=2)
