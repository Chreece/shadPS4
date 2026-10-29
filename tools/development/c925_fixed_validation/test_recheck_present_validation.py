#!/usr/bin/env python3
"""Tests runner safety and source/tool provenance; no GPU or native VVL build."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess as sp
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import recheck_present_validation as r

class Helpers(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name)
    def test_layer_environment_preserves_driver_and_host(self):
        base={'VK_LAYER_PATH':'old','VK_ADD_LAYER_PATH':'old2','VK_INSTANCE_LAYERS':'old',
              'VK_KHRONOS_VALIDATION_DISABLES':'bad','VK_ICD_FILENAMES':'keep-driver',
              'VK_DRIVER_FILES':'keep-driver2','DISPLAY':':0','HOME':'keep'}
        out=r.fixed_layer_environment(self.p,base)
        self.assertEqual(out['VK_LAYER_PATH'],str(self.p));self.assertEqual(out['VK_ICD_FILENAMES'],'keep-driver')
        self.assertEqual(out['VK_DRIVER_FILES'],'keep-driver2');self.assertNotIn('VK_INSTANCE_LAYERS',out)
        self.assertNotIn('VK_KHRONOS_VALIDATION_DISABLES',out);self.assertEqual(base['VK_LAYER_PATH'],'old')
    def test_child_environment_restored_on_error(self):
        before=os.environ.copy()
        with self.assertRaises(ValueError):
            with r.child_environment({'TEST':'yes'}):
                self.assertEqual(dict(os.environ),{'TEST':'yes'});raise ValueError()
        self.assertEqual(dict(os.environ),before)
    def test_manifest_uses_exact_library(self):
        original={'file_format_version':'1.2.0','layer':{'name':r.LAYER_NAME,'library_path':'old','api_version':'1.4.363'}}
        out=r.tool_manifest(json.dumps(original),self.p/'real.so')
        self.assertEqual(out['layer']['library_path'],str(self.p/'real.so'))
        self.assertEqual(out['layer']['api_version'],'1.4.363');self.assertEqual(original['layer']['library_path'],'old')
    def test_manifest_wrong_layer_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'WRONG_LAYER'):
            r.tool_manifest('{"layer":{"name":"not-validation"}}',self.p/'x')
    def test_probe_handles_loader_trailer(self):
        obj={'layer':r.LAYER_NAME,'spec_version':(1<<22)+(4<<12)+363,'instance_create':'PASS'}
        self.assertEqual(r.parse_probe('loader start\n'+json.dumps(obj)+'\nloader tail\n'),obj)
    def test_probe_old_version_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'OLD_LAYER'):
            r.parse_probe(json.dumps({'layer':r.LAYER_NAME,'instance_create':'PASS','spec_version':4210997}))
    def test_probe_missing_json_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'JSON_MISSING'):r.parse_probe('loader said nothing\n{}')
    def test_source_guard_captures_changed_bytes_without_writing(self):
        source=self.p/'source';source.mkdir();(source/'a').write_text('before');(source/'b').write_text('two')
        hashes={n:r.sha(source/n) for n in ('a','b')};(source/'a').write_text('external edit')
        with self.assertRaisesRegex(RuntimeError,'SOURCE_CHANGED'):
            r.verify_source(source,hashes,self.p/'evidence')
        self.assertEqual((source/'a').read_text(),'external edit')
        self.assertEqual((self.p/'evidence/a').read_text(),'external edit');self.assertTrue((self.p/'evidence/b').exists())
    def test_source_guard_refuses_symlink(self):
        source=self.p/'source';source.mkdir();(self.p/'outside').write_text('x');(source/'bad').symlink_to(self.p/'outside')
        with self.assertRaisesRegex(RuntimeError,'SOURCE_PATH_UNSAFE'):
            r.verify_source(source,{'bad':r.sha(self.p/'outside')})
    def test_command_real_success_and_failure(self):
        out=r.command([sys.executable,'-c','print("real child")'],self.p/'success.log',timeout=5)
        self.assertIn('real child',out)
        with self.assertRaisesRegex(RuntimeError,'EXIT_7'):
            r.command([sys.executable,'-c','import sys;print("failure details");sys.exit(7)'],self.p/'failure.log',timeout=5)
        self.assertIn('failure details',(self.p/'failure.log').read_text())
    def test_command_real_timeout_child_only(self):
        code='echo $$; exec sleep 30'
        with self.assertRaisesRegex(RuntimeError,'TIMEOUT'):
            r.command(['/bin/sh','-c',code],self.p/'timeout.log',timeout=1.0,beat=.2)
        pid=int((self.p/'timeout.log').read_text().strip())
        self.assertFalse(Path(f'/proc/{pid}').exists());self.assertTrue(Path(f'/proc/{os.getpid()}').exists())
    def test_actual_git_object_parity(self):
        (self.p/'x').write_bytes(b'content\n')
        real=sp.check_output(['git','hash-object',str(self.p/'x')],text=True).strip()
        self.assertEqual(real,r.blob(b'content\n'))

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name);self.e=self.p/'e';self.e.mkdir()
        self.tool=self.p/'scripts/validation-tools'/('vvl-'+r.VVL_COMMIT)
        self.src=self.tool/'source';self.src.mkdir(parents=True)
        sp.run(['git','init','-q',str(self.src)],check=True)
        sp.run(['git','-C',str(self.src),'remote','add','origin',r.VVL_REPO],check=True)
        (self.src/'scripts').mkdir();(self.src/'scripts/known_good.json').write_text('{"repos":[]}')
        (self.src/'layers/sync').mkdir(parents=True)
        (self.src/'layers/sync/sync_submit.cpp').write_text('last_synchronized_present.Merge(signal_info.batch->last_synchronized_present);')
        sp.run(['git','-C',str(self.src),'add','.'],check=True)
        sp.run(['git','-C',str(self.src),'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
        self.head=r.git_read(self.src,'rev-parse','HEAD')
        # The source-ref validator is mocked only for its fixed remote commit.
        actual=r.git_read
        def read(root,*args):
            value=actual(root,*args)
            if root==self.src and args in [('rev-parse','HEAD'),('rev-parse','--verify','HEAD')]:return r.VVL_COMMIT
            return value
        self.patchers=[patch.object(r,'git_read',read),patch.object(r,'KNOWN_GOOD_BLOB',r.blob((self.src/'scripts/known_good.json').read_bytes()))]
        for p in self.patchers:p.start();self.addCleanup(p.stop)
        self.build=self.tool/'build/layers';self.build.mkdir(parents=True)
        self.lib=self.build/'libVkLayer_khronos_validation.so';self.lib.write_bytes(b'fixture library, not executable')
        self.man=self.build/'VkLayer_khronos_validation.json';self.man.write_text(json.dumps({'layer':{'name':r.LAYER_NAME,'library_path':'old'}}))
        self.meta={'commit':r.VVL_COMMIT,'library':str(self.lib),'library_sha256':r.sha(self.lib),
                   'build_manifest':str(self.man),'build_manifest_sha256':r.sha(self.man)}
        r.save(self.tool/'built-layer.json',self.meta)
    def test_reuse_verified_cache_no_build(self):
        with patch.object(r,'command',side_effect=AssertionError('must not build')):
            lib,folder,meta=r.build_layer(self.p,self.e)
        self.assertEqual(lib,self.lib);self.assertEqual(meta,self.meta)
        self.assertEqual(json.loads((folder/'VkLayer_khronos_validation.json').read_text())['layer']['library_path'],str(self.lib))
    def test_modified_cached_library_refused(self):
        self.lib.write_bytes(b'external edit')
        with self.assertRaisesRegex(RuntimeError,'PRIVATE_LAYER_CHANGED'):
            r.build_layer(self.p,self.e)
        self.assertEqual(self.lib.read_bytes(),b'external edit')
    def test_changed_source_refused(self):
        (self.src/'layers/sync/sync_submit.cpp').write_text('edited')
        with self.assertRaisesRegex(RuntimeError,'TRACKED_SOURCE_CHANGED'):r.build_layer(self.p,self.e)
    def test_changed_origin_refused(self):
        sp.run(['git','-C',str(self.src),'remote','set-url','origin','https://example.invalid/not-expected'],check=True)
        with self.assertRaisesRegex(RuntimeError,'ORIGIN_CHANGED'):r.build_layer(self.p,self.e)

class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name)/'project';self.logs=self.project/'logs';self.logs.mkdir(parents=True)
        self.here=Path(self.tmp.name)/'package';self.here.mkdir();(self.here/'validation_layer_probe.py').write_text('fixture')
        self.cand=self.project/'GhostOfTsushima/experiments'/r.BASE
        self.source=self.cand/'source';self.source.mkdir(parents=True)
        (self.source/'shader').write_text('accepted shader');(self.source/'wsi').write_text('acquire wait signal present')
        self.binary=self.cand/'build/shadps4';self.binary.parent.mkdir();self.binary.write_bytes(b'verified emulator')
        self.lib=self.project/'scripts/libVkLayer_khronos_validation.so';self.lib.parent.mkdir(parents=True);self.lib.write_bytes(b'fixture layer')
        self.original=Path(self.tmp.name)/'protected-save';self.original.write_text('original save')
        self.private=self.cand/'runs/prior/user';self.private.mkdir(parents=True);(self.private/'config.json').write_text('{}')
        prior=self.logs/r.PRIOR;prior.mkdir()
        r.save(prior/'trial.json',{'build':'PASS','candidate_sha256':r.sha(self.binary),
            'source_manifest':{'shader':{'after':r.sha(self.source/'shader')}},'private_profile':str(self.private)})
        r.save(prior/'retained-source-sha256.json',{'shader':r.sha(self.source/'shader')})
        r.save(prior/'original-files-before.json',{str(self.original):r.sha(self.original)})
        self.game_calls=[];self.restores=[]
        def game(u,binary,eboot,profile,evid,screen):
            self.game_calls.append((binary,profile))
            (evid/'game.log').write_text(str(self.lib)+'\nEnabled instance layers: VK_LAYER_KHRONOS_validation\nVulkan vkValidationCore: true\nVulkan vkValidationSync: true\n')
            return {'exit_code':0,'time_limit_reached':True,'first_validation_error':None}
        def copy_profile(old,new):new.mkdir(parents=True);(new/'config.json').write_text('{}');return {'private_only':True}
        self.u=types.SimpleNamespace(emulator_pids=lambda:[],Ollama=lambda e:object(),
            heartbeat=lambda n:contextlib.nullcontext(),free_vram=lambda:9*1024**3,
            restore_models=lambda a,p,e:self.restores.append(list(p)) or [{'result':'RESTORED_VERIFIED'}])
        self.h=types.SimpleNamespace(MIN_FREE=8*1024**3,verified_headroom=lambda *a:None)
        self.menu=types.SimpleNamespace(game=game,copy_profile=copy_profile)
        def load(name,p):return {'runtime':self.u,'headroom':self.h,'menu':self.menu}[name]
        def command(args,log,**kwargs):
            text=''
            if log.name=='corrected-layer-probe.log':
                text=str(self.lib)+'\n'+json.dumps({'layer':r.LAYER_NAME,'spec_version':(1<<22)+(4<<12)+363,'instance_create':'PASS'})
            log.write_text(text);return text
        existing=Path.is_file
        def is_file(p):return True if str(p)=='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin' else existing(p)
        self.patches=[patch.object(r,'PINS',{}),patch.object(r,'PROBE_BLOB',r.blob(b'fixture')),
            patch.object(r,'EXPECTED_ELF',r.sha(self.binary)),patch.object(r,'WSI_SOURCE',{'wsi':r.sha(self.source/'wsi')}),
            patch.object(r,'load',load),patch.object(r,'command',command),patch.object(r.shutil,'which',return_value='/fixture/tool'),
            patch.object(r,'build_layer',return_value=(self.lib,self.lib.parent,{'library_sha256':r.sha(self.lib)})),
            patch.object(Path,'is_file',is_file)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.hashes={str(p):r.sha(p) for p in (self.binary,self.original,self.source/'shader',self.source/'wsi')}
        self.env=os.environ.copy()
    def trial(self):
        choices=list(self.logs.glob('got-c925-corrected-layer-*/trial.json'));self.assertEqual(len(choices),1)
        return json.loads(choices[0].read_text())
    def test_mocked_resume_success_preserves_everything(self):
        self.assertEqual(r.main(self.project,self.here),0);self.assertEqual(len(self.game_calls),1)
        for p,h in self.hashes.items():self.assertEqual(r.sha(p),h)
        self.assertEqual(os.environ.copy(),self.env);self.assertEqual(self.trial()['original_files_changed'],[])
        self.assertEqual(len(list(self.logs.glob('got-c925-corrected-layer-*.tar.gz'))),1)
    def test_changed_elf_prevents_build_and_game(self):
        self.binary.write_bytes(b'changed');self.assertEqual(r.main(self.project,self.here),1)
        self.assertFalse(self.game_calls);self.assertEqual(self.trial()['status'],'NATIVE_BINARY_CHANGED_NO_GAME')
    def test_tool_build_failure_archives_without_game(self):
        with patch.object(r,'build_layer',side_effect=RuntimeError('VVL_BUILD_FAILED')):
            self.assertEqual(r.main(self.project,self.here),1)
        self.assertFalse(self.game_calls);self.assertEqual(self.trial()['status'],'VVL_BUILD_FAILED')
    def test_layer_version_gate_prevents_game(self):
        with patch.object(r,'parse_probe',side_effect=RuntimeError('OLD_LAYER_STILL_LOADED')):
            self.assertEqual(r.main(self.project,self.here),1)
        self.assertFalse(self.game_calls)
    def test_headroom_failure_restores_models(self):
        def failure(u,a,e,pending,result):pending.append({'name':'original-model'});raise RuntimeError('NO_HEADROOM')
        self.h.verified_headroom=failure
        self.assertEqual(r.main(self.project,self.here),1);self.assertFalse(self.game_calls)
        self.assertEqual(self.restores,[[{'name':'original-model'}]])
    def test_first_error_not_filtered(self):
        original=self.menu.game
        def error(*args):
            result=original(*args);result.update(first_validation_error={'message':'SYNC-HAZARD-WRITE-AFTER-PRESENT'},time_limit_reached=False)
            return result
        self.menu.game=error
        self.assertEqual(r.main(self.project,self.here),0)
        self.assertEqual(self.trial()['status'],'FIRST_ERROR_CAPTURED_WITH_CORRECTED_LAYER')
        self.assertTrue(self.trial()['no_suppressed_error_ids'])
    def test_unverified_actual_layer_fails_result(self):
        original=self.menu.game
        def wrong(*args):
            result=original(*args);(args[4]/'game.log').write_text('different layer loaded')
            return result
        self.menu.game=wrong
        self.assertEqual(r.main(self.project,self.here),1)
        self.assertEqual(self.trial()['status'],'RUNTIME_LAYER_OR_CORE_SYNC_NOT_CONFIRMED')
    def test_final_memory_drop_prevents_game(self):
        self.u.free_vram=lambda:7*1024**3
        self.assertEqual(r.main(self.project,self.here),1);self.assertFalse(self.game_calls)

if __name__=='__main__':unittest.main(verbosity=2)
