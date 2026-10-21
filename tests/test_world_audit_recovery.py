#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/got-world-audit-recovery-20261021.py'
spec = importlib.util.spec_from_file_location('recovery', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

HEADERS = {
 'src/video_core/renderer_vulkan/vk_frame_probe.h': '#include <cstdlib>\nstruct FrameProbeState {\n    void Present(u64 seconds) {\nfoo(96_MB, 128_MB);\n    }\n};\n',
 'src/video_core/renderer_vulkan/vk_frame_probe_images.h': 'auto budget = 128_MB - probe.bytes;\n'
}
HOST = '''#!/bin/bash
set -Eeuo pipefail
STATUS=HOST_TESTS
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_sharp_fetch_bounds.cpp"
STATUS=STAGING_CANDIDATE
STATUS=CONFIGURING_OFFLINE
sha256sum "$BIN" | tee "$RUN/info/candidate-binary.sha256"
'''

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp = tempfile.TemporaryDirectory()
  self.root = Path(self.tmp.name)
 def tearDown(self):
  self.tmp.cleanup()
 def fixtures(self):
  for rel,text in HEADERS.items():
   p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
  return {rel:m.blob(text.encode()) for rel,text in HEADERS.items()}
 def test_exact_hash_rejects_without_writing_any_header(self):
  hashes=self.fixtures()
  second=list(HEADERS)[1]; (self.root/second).write_text('different')
  first=list(HEADERS)[0]
  with patch.object(m,'HEADER_HASHES',hashes),self.assertRaises(RuntimeError):
   m.patch_headers(self.root,self.root/'report')
  self.assertEqual((self.root/first).read_text(),HEADERS[first])
 def test_only_diagnostic_headers_change_and_budget_is_bounded(self):
  hashes=self.fixtures()
  game=self.root/'src/game.cpp';game.write_text('unchanged game source')
  with patch.object(m,'HEADER_HASHES',hashes):m.patch_headers(self.root,self.root/'report')
  text=(self.root/list(HEADERS)[0]).read_text()
  self.assertIn('completed == 0 && !FrameProbeManuallyArmed()',text)
  self.assertIn('12_MB, 16_MB',text)
  self.assertIn('16_MB - probe.bytes',(self.root/list(HEADERS)[1]).read_text())
  self.assertEqual(game.read_text(),'unchanged game source')
  report=json.loads((self.root/'report/capture-policy.json').read_text())
  self.assertEqual(report['baseline'],m.BASE)
  self.assertEqual(len(report['files']),2)
 def test_symlink_rejected(self):
  hashes=self.fixtures(); first=list(HEADERS)[0]
  p=self.root/first;p.unlink();target=self.root/'elsewhere';target.write_text(HEADERS[first]);p.symlink_to(target)
  with patch.object(m,'HEADER_HASHES',hashes),self.assertRaises(RuntimeError):
   m.patch_headers(self.root,self.root/'report')
 def test_duplicate_anchor_rejected(self):
  with self.assertRaises(RuntimeError):m.once('xx','x','q')
 def test_real_source_inventory_and_postbuild_mutation(self):
  subprocess.run(['git','init','-q',str(self.root)],check=True)
  subprocess.run(['git','-C',str(self.root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','fixture'],check=True)
  p=self.root/'src/a.cpp';p.parent.mkdir();p.write_text('before')
  report=self.root/'manifest.json'
  m.source_manifest(self.root,report)
  m.source_manifest(self.root,report,True)
  p.write_text('changed')
  with self.assertRaises(RuntimeError):m.source_manifest(self.root,report,True)
 def test_host_order_and_pipefail_syntax(self):
  p=self.root/'host.sh'
  with patch.object(m,'HOST_SHA',m.blob(HOST.encode())):m.prepare_host(HOST.encode(),p)
  text=p.read_text();subprocess.run(['bash','-n',str(p)],check=True)
  self.assertLess(text.index('patch "$RUN/tmp/staged"'),text.index('STATUS=HOST_TESTS'))
  self.assertLess(text.index('STATUS=STAGING_CANDIDATE'),text.index('manifest "$SOURCE"'))
  self.assertLess(text.index('verify-manifest "$SOURCE"'),text.index('sha256sum "$BIN"'))
  self.assertIn('pipefail',text)
 def test_runtime_environment_and_capture_validation(self):
  parent=self
  class Log:pass
  class Runtime:
   def __init__(self,args):
    self.run=parent.root/'run';(self.run/'info').mkdir(parents=True)
    self.meta={};self.args=args
   def environment(self):return {'DISPLAY':':0','SHADPS4_INDIRECT_ARGUMENT_SNAPSHOT_SECONDS':'60'}
  base=types.SimpleNamespace(GameLog=Log,Runtime=Runtime)
  def main():
   obj=base.Runtime(types.SimpleNamespace(seconds=600))
   env=obj.environment()
   self.assertNotIn('SHADPS4_INDIRECT_ARGUMENT_SNAPSHOT_SECONDS',env)
   self.assertEqual(env['SHADPS4_FRAME_PROBE_FRAME_LIMIT'],'2')
   self.assertEqual(env['SHADPS4_FRAME_PROBE_READY_FILE'],str(obj.ready_file))
   self.assertFalse(obj.ready_file.exists())
   obj.capture_end_seen=True
   source=obj.run/'profile/user/shader/frame-probes';source.mkdir(parents=True)
   (source/'f2-p0-a0.raw').write_bytes(b'abcd')
   (source/'f2-p0-a0.raw.txt').write_text('rows=1\nwidth=1\nheight=1\nformat=37\n')
   obj.collect_indirect_snapshot()
   self.assertEqual(obj.meta['MANUAL_FRAME_CAPTURE'],'COMPLETE')
   self.assertEqual((obj.run/'info/render-targets/f2-p0-a0.raw').read_bytes(),b'abcd')
   return 0
  base.main=main
  m.run_audit(base,Path('candidate'),'a'*40,Path('report'))
 def test_metadata_archive_safety(self):
  import tarfile,io
  p=self.root/'a.tar.gz'
  with tarfile.open(p,'w:gz') as t:
   i=tarfile.TarInfo('bad');i.type=tarfile.SYMTYPE;i.linkname='/etc/passwd';t.addfile(i)
  with tarfile.open(p) as t,self.assertRaises(RuntimeError):m.read_tar(t,'bad')

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
 raise SystemExit(0 if result.wasSuccessful() else 1)
