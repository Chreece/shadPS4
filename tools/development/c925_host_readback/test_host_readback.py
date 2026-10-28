#!/usr/bin/env python3
from __future__ import annotations
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import apply_host_readback as fix
import run_host_readback as run

# Supply the two captured source-before files when running these exact-source tests.
SOURCE=Path(sys.argv.pop(1)) if len(sys.argv)>1 and not sys.argv[1].startswith('-') else Path(__file__).parent/'evidence/source-before'
COPY='''void Runtime::CopyBuffer(const Buffer* src,const Buffer* dst) {
 scheduler.EndRendering(); auto cmdbuf=scheduler.CommandBuffer(); cmdbuf.copyBuffer(src,dst);
 AccessBuffer(dst,0,64,vk::PipelineStageFlagBits2::eCopy,vk::AccessFlagBits2::eTransferWrite);
}
void Runtime::FillBuffer() {}
memory_barrier.dstStageMask=vk::PipelineStageFlagBits2::eAllCommands;
memory_barrier.dstAccessMask=vk::AccessFlagBits2::eMemoryRead|vk::AccessFlagBits2::eMemoryWrite;
'''

def helpers(root):
 for rel in run.REQUIRED_HELPERS+run.OPTIONAL_HELPERS:
  p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('// no CPU handoff here\n')
 (root/run.REQUIRED_HELPERS[0]).write_text(COPY)
 (root/'src/video_core/texture_cache/tile_manager.cpp').write_text('void TileManager::TileImage() { runtime.DownloadImage(); }')

class SourceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.src=self.root/'srcroot';self.evid=self.root/'e';self.evid.mkdir()
  self.old={p:(SOURCE/p).read_bytes() for p in fix.EXPECTED}
  for p,data in self.old.items():
   q=self.src/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(data)
 def test_exact_patch_preserves_wait_and_invalidation(self):
  after=fix.transform(self.old)
  for p,text in after.items():
   s=text.decode();before=self.old[p].decode()
   self.assertEqual(s.count('GPU_HOST_READBACK_VISIBILITY_V1'),1)
   self.assertEqual(s.count('scheduler.Finish();'),before.count('scheduler.Finish();'))
   self.assertEqual(s.count('download.Invalidate();'),before.count('download.Invalidate();'))
   self.assertEqual(s.count('TryWriteBacking'),before.count('TryWriteBacking'))
   self.assertIn('.dstAccessMask = vk::AccessFlagBits2::eHostRead',s)
   self.assertLess(s.index('host_readback_barrier'),s.index('const auto write_data'))
  self.assertIn('GpuReadbacksMode::Precise',after[fix.TEXTURE].decode())
 def test_wrong_source_no_writes(self):
  p=self.src/fix.TEXTURE;p.write_bytes(self.old[fix.TEXTURE]+b' ')
  before={k:(self.src/k).read_bytes() for k in self.old}
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):fix.apply(self.src,self.evid)
  self.assertEqual(before,{k:(self.src/k).read_bytes() for k in self.old})
 def test_apply_then_repeat_refuses(self):
  fix.apply(self.src,self.evid);before={k:(self.src/k).read_bytes() for k in self.old}
  with self.assertRaises(ValueError):fix.apply(self.src,self.root/'again')
  self.assertEqual(before,{k:(self.src/k).read_bytes() for k in self.old})
 def test_git_forward_reverse_roundtrip(self):
  fix.apply(self.src,self.evid)
  for p,data in self.old.items():(self.src/p).write_bytes(data)
  subprocess.run(['git','init','-q',str(self.src)],check=True)
  change=self.evid/'host-readback.patch'
  for opts in (['--check'],[],['-R','--check'],['-R']):
   subprocess.run(['git','-C',str(self.src),'apply',*opts,str(change)],check=True,capture_output=True)
  self.assertEqual(self.old,{p:(self.src/p).read_bytes() for p in self.old})
 def test_helper_missing_source_only(self):
  with self.assertRaises(run.SourceReviewOnly):run.helper_gate(self.src,self.evid)
  self.assertTrue((self.evid/'readback-helper-inventory.json').is_file())
 def test_known_copy_helper_passes(self):
  helpers(self.src);result=run.helper_gate(self.src,self.evid)
  self.assertTrue(result['result'].startswith('KNOWN_COPY'))
 def test_existing_host_dependency_source_only(self):
  helpers(self.src);(self.src/run.REQUIRED_HELPERS[1]).write_text('auto access=vk::AccessFlagBits2::eHostRead;')
  with self.assertRaisesRegex(run.SourceReviewOnly,'EXISTING_HOST'):run.helper_gate(self.src,self.evid)
 def test_host_text_in_comment_not_implementation(self):
  helpers(self.src);p=self.src/run.REQUIRED_HELPERS[1];p.write_text('// vk::AccessFlagBits2::eHostRead\n')
  run.helper_gate(self.src,self.evid)
 def test_unreviewed_copy_shape_source_only(self):
  helpers(self.src);(self.src/run.REQUIRED_HELPERS[0]).write_text(COPY.replace('AccessBuffer(dst','Unknown(dst'))
  with self.assertRaisesRegex(run.SourceReviewOnly,'COPY_HELPER_SHAPE'):run.helper_gate(self.src,self.evid)
 def test_helper_symlink_refused(self):
  helpers(self.src);p=self.src/run.REQUIRED_HELPERS[1];p.unlink();p.symlink_to('/etc/passwd')
  with self.assertRaisesRegex(RuntimeError,'PATH_OR_SIZE'):run.helper_gate(self.src,self.evid)

class Util:
 def __init__(self):self.games=[];self.restores=[];self.messages=[]
 def sha(self,p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
 def emit(self,s):self.messages.append(s)
 def emulator_pids(self):return []
 def heartbeat(self,s):return nullcontext()
 def command(self,args,timeout=10):
  if args[0]=='cp':shutil.copy2(args[-2],args[-1])
  return b'PASS\n'
 def free_vram(self):return 20*1024**3
 def Ollama(self,e):return object()
 def run_game(self,b,eb,work,e,screen):
  self.games.append(str(b));(e/'game.log').write_text('HostReadbackBarrier: kind=buffer bytes=65536\n')
  return {'time_limit_reached':True,'exit_code':0,'graphics':'PENDING_REVIEW'}
 def restore_models(self,api,pending,e):self.restores.append(pending.copy());return [{'result':'RESTORED_VERIFIED'}]
 def stop_owned(self,p):
  if p and p.poll() is None:
   import os,signal
   os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=3)

class RunnerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.project=Path(self.tmp.name)/'project';self.logs=self.project/'logs';self.logs.mkdir(parents=True)
  self.cand=self.project/'GhostOfTsushima/experiments'/run.BASE;self.src=self.cand/'source';self.bin=self.cand/'build/shadps4';self.bin.parent.mkdir(parents=True);self.bin.write_bytes(b'old-elf')
  self.u=Util();self.sha=self.u.sha(self.bin)
  self.old={p:(SOURCE/p).read_bytes() for p in fix.EXPECTED}
  for p,data in self.old.items():q=self.src/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(data)
  helpers(self.src)
  # helpers() does not include either patched production file.
  self.renderer=self.src/run.RENDERER;self.renderer.parent.mkdir(parents=True,exist_ok=True);self.renderer.write_text('accepted stencil')
  self.profile=self.cand/'runs/isolated/user';self.profile.mkdir(parents=True);(self.profile/'config.json').write_text('{}')
  self.protected=self.project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4';self.protected.parent.mkdir(parents=True);self.protected.write_bytes(b'protected')
  prev=self.logs/run.PREVIOUS;prev.mkdir();(prev/'trial.json').write_text(json.dumps({'candidate_after_sha256':self.sha,'private_profile':str(self.profile)}))
  (prev/'original-files-before.json').write_text(json.dumps({str(self.protected):self.u.sha(self.protected)}))
  self.headers=self.src/'externals/vulkan-headers/include/vulkan/vulkan.hpp';self.headers.parent.mkdir(parents=True);self.headers.write_text('mock header, not compiled')
  self.expected={p:fix.digest(data) for p,data in self.old.items()};self.expected[run.RENDERER]=self.u.sha(self.renderer)
  self.game=self.project/'eboot.bin';self.game.write_bytes(b'game-marker')
  self.commands=[];self.fail_build=False;self.fail_fixture=False
  def fake_logged(u,args,path,label,timeout=None):
   self.commands.append(args);path.write_text(label+' mock only\n')
   if label.startswith('REAL_VULKAN_HEADER'):
    if self.fail_fixture:raise RuntimeError('MOCK_NATIVE_HEADER_REJECTION')
    Path(args[args.index('-o')+1]).write_bytes(b'mock unit')
   if args[0]=='cmake':
    if self.fail_build:raise RuntimeError('MOCK_BUILD_REJECTION')
    self.bin.write_bytes(b'new-elf')
  class H:
   @staticmethod
   def verified_headroom(u,api,e,pending,result):result['vram_at_launch_bytes']=u.free_vram()
  self.h=H
  patches=(patch.object(run,'EXPECTED',self.sha),patch.object(run,'EBOOT',self.game),
   patch.object(run,'manifest_from_history',return_value=self.expected),patch.object(run,'logged',side_effect=fake_logged),
   patch.object(run.shutil,'which',side_effect=lambda n:'/fake/'+n if n!='clang++' else None))
  for p in patches:p.start();self.addCleanup(p.stop)
 def outcome(self):
  q=list(self.logs.glob('got-c925-host-readback-*/trial.json'));self.assertEqual(len(q),1);return json.loads(q[0].read_text())
 def test_mock_success_preserves_original(self):
  self.assertEqual(run.main(self.u,self.h,fix,self.project),0);out=self.outcome();self.assertEqual(out['build'],'PASS');self.assertEqual(out['original_files_changed'],[]);self.assertEqual(len(self.u.games),1)
  self.assertEqual(self.protected.read_bytes(),b'protected');self.assertEqual((self.profile/'config.json').read_text(),'{}')
 def test_build_failure_restores_two_files_and_elf(self):
  self.fail_build=True;self.assertEqual(run.main(self.u,self.h,fix,self.project),1)
  self.assertEqual(self.outcome()['rollback'],'SOURCE_AND_PR102_ELF_RESTORED');self.assertFalse(self.u.games)
  self.assertEqual(self.bin.read_bytes(),b'old-elf');self.assertEqual(self.old,{p:(self.src/p).read_bytes() for p in self.old})
 def test_native_header_failure_before_source_writes(self):
  self.fail_fixture=True;self.assertEqual(run.main(self.u,self.h,fix,self.project),1)
  self.assertFalse(self.u.games);self.assertEqual(self.bin.read_bytes(),b'old-elf');self.assertEqual(self.old,{p:(self.src/p).read_bytes() for p in self.old})
 def test_existing_lower_host_barrier_skips_build_and_game(self):
  (self.src/run.REQUIRED_HELPERS[1]).write_text('vk::AccessFlagBits2::eHostRead;')
  self.assertEqual(run.main(self.u,self.h,fix,self.project),0);self.assertFalse(self.commands);self.assertFalse(self.u.games);self.assertFalse(self.outcome().get('source_manifest'))
 def test_changed_elf_refused(self):
  self.bin.write_bytes(b'changed');self.assertEqual(run.main(self.u,self.h,fix,self.project),1);self.assertFalse(self.commands)
 def test_changed_preserved_shader_source_refused(self):
  self.renderer.write_text('lost stencil');self.assertEqual(run.main(self.u,self.h,fix,self.project),1);self.assertFalse(self.commands)
 def test_external_save_root_refused(self):
  (self.profile/'config.json').write_text('{"General":{"home_dir":"/not/isolated"}}')
  self.assertEqual(run.main(self.u,self.h,fix,self.project),1);self.assertFalse(self.commands)
 def test_model_restored_when_gate_fails_after_build(self):
  def gate(u,api,e,pending,result):pending.append({'name':'model'});raise RuntimeError('NO_HEADROOM')
  with patch.object(self.h,'verified_headroom',side_effect=gate):self.assertEqual(run.main(self.u,self.h,fix,self.project),1)
  self.assertEqual(self.outcome()['build'],'PASS');self.assertFalse(self.u.games);self.assertEqual(len(self.u.restores),1)
 def test_late_vram_drop_blocks_game(self):
  self.u.free_vram=lambda:1
  self.assertEqual(run.main(self.u,self.h,fix,self.project),1);self.assertFalse(self.u.games)
 def test_model_restore_failure_not_pass(self):
  def gate(u,api,e,pending,result):pending.append({'name':'model'})
  self.u.restore_models=lambda *a:[{'result':'RESTORE_FAILED'}]
  with patch.object(self.h,'verified_headroom',side_effect=gate):self.assertEqual(run.main(self.u,self.h,fix,self.project),1)
  self.assertEqual(self.outcome()['runner_exit_code'],1)

class ChildProcessTests(unittest.TestCase):
 def test_real_success_and_failure(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);u=Util()
   run.logged(u,[sys.executable,'-c','print("test-output")'],root/'ok','success',4)
   with self.assertRaisesRegex(RuntimeError,'EXIT_3'):run.logged(u,[sys.executable,'-c','raise SystemExit(3)'],root/'bad','failure',4)
   self.assertIn('test-output',(root/'ok').read_text())

if __name__=='__main__':unittest.main(verbosity=2)
