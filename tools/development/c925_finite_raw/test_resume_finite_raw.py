#!/usr/bin/env python3
from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import resume_finite_raw as r

class Clock:
    def __init__(self): self.now=0
    def __call__(self): return self.now
    def sleep(self,t): self.now+=t

MODEL={'name':'qwen3.8-greek:latest','digest':'real-digest', 'context_length':16384,
       'expires_at':'2319-02-07T16:58:14.876646438Z','size_vram':9607661484}

class Util:
    def __init__(self, clock=None, free=None):
        self.clock=clock or Clock(); self.free=free or (lambda:r.MIN_FREE-100)
        self.messages=[];self.called=[]
    def emit(self,s): self.messages.append(s)
    def free_vram(self): return self.free()
    def model_expiry(self,m):
        value=dt.datetime.fromisoformat(m['expires_at'].replace('Z','+00:00'))
        if value.tzinfo is None:raise ValueError('Missing timezone')
        return value
    def sha(self,p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    def emulator_pids(self):return []

class API:
    def __init__(self,clock=None,models=None,state=None):
        self.clock=clock or Clock();self.models=[dict(MODEL)] if models is None else models
        self.state=state or (lambda:self.models);self.selected=None;self.calls=[];self.probes=[]
        self.raise_post=False;self.raise_get=False;self.raise_initial=False
        self.reply={'done':True,'done_reason':'unload','response':''}
    def request_at(self,e,p,timeout=4):
        self.calls.append(('initial',e,p))
        if self.raise_initial:raise ConnectionResetError('published port reset')
        return {'models':self.models}
    def discover(self):
        self.calls.append(('discover',));self.selected=('local-container','http://127.0.0.1:11434')
        return {'models':self.models}
    def record(self,e,s):self.probes.append((e,s))
    def request(self,p,body=None,timeout=None):
        self.calls.append((p,body))
        if p=='/api/generate':
            if self.raise_post:raise ConnectionResetError('post response lost')
            return self.reply
        if self.raise_get:raise ConnectionResetError('ps unavailable')
        return {'models':self.state()}

class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.clock=Clock();self.u=Util(self.clock)
        self.api=API(self.clock);self.pending=[];self.result={}
    def run_gate(self,grace=15):
        r.verified_headroom(self.u,self.api,self.root,self.pending,self.result,
                           grace=grace,clock=self.clock,sleep=self.clock.sleep)
    def posts(self):return [x for x in self.api.calls if x[0]=='/api/generate']
    def test_sufficient_memory_never_calls_api(self):
        self.u.free=lambda:r.MIN_FREE;self.run_gate();self.assertFalse(self.api.calls)
    def test_unknown_memory_never_unloads(self):
        self.u.free=lambda:-1
        with self.assertRaisesRegex(RuntimeError,'COUNTER_UNAVAILABLE'):self.run_gate()
        self.assertFalse(self.api.calls);self.assertFalse(self.pending)
    def test_delayed_release_past_previous_six_seconds(self):
        self.u.free=lambda:r.MIN_FREE+100 if self.clock.now>=10 else r.MIN_FREE-100
        self.api.state=lambda:[] if self.clock.now>=8 else self.api.models
        self.run_gate();self.assertEqual(self.clock.now,11);self.assertEqual(len(self.posts()),1)
        self.assertEqual(self.result['headroom_result'],'MODEL_ABSENT_AND_VRAM_VERIFIED_TWICE')
    def test_request_contains_empty_prompt_and_actual_context(self):
        self.u.free=lambda:r.MIN_FREE+100 if self.clock.now>=1 else r.MIN_FREE-100
        self.api.state=lambda:[];self.run_gate()
        body=self.posts()[0][1]
        self.assertEqual(body['prompt'],'');self.assertEqual(body['keep_alive'],0)
        self.assertEqual(body['options'],{'num_ctx':16384})
        self.assertEqual(json.loads((self.root/'ollama-unload-0.json').read_text())['response']['done_reason'],'unload')
    def test_no_race_on_one_high_sample(self):
        self.u.free=lambda:r.MIN_FREE+100 if self.clock.now in (1,3,4) else r.MIN_FREE-100
        self.api.state=lambda:[];self.run_gate();self.assertEqual(self.clock.now,4)
    def test_http_success_not_enough_still_listed(self):
        with self.assertRaisesRegex(RuntimeError,'UNLOAD_NOT_CONFIRMED'):self.run_gate(3)
        self.assertEqual(len(self.posts()),1);self.assertEqual(len(self.pending),1)
    def test_model_absent_but_memory_still_low(self):
        self.api.state=lambda:[]
        with self.assertRaisesRegex(RuntimeError,'VRAM_REMAINS'):self.run_gate(3)
    def test_lost_post_response_still_observes_and_restores(self):
        self.api.raise_post=True;self.api.state=lambda:[]
        self.u.free=lambda:r.MIN_FREE+100 if self.clock.now>=2 else r.MIN_FREE-100
        self.run_gate();self.assertEqual(len(self.pending),1)
        self.assertIn('request_error',json.loads((self.root/'ollama-unload-0.json').read_text()))
    def test_unknown_context_refused_before_any_unload(self):
        self.api.models[0].pop('context_length')
        with self.assertRaisesRegex(RuntimeError,'RESTORATION_METADATA'):self.run_gate()
        self.assertFalse(self.posts());self.assertFalse(self.pending)
    def test_bool_context_not_an_integer_context(self):
        self.api.models[0]['context_length']=True
        with self.assertRaisesRegex(RuntimeError,'RESTORATION_METADATA'):self.run_gate()
    def test_second_invalid_model_prevents_first_unload(self):
        self.api.models.append({'name':'bad'})
        with self.assertRaisesRegex(RuntimeError,'RESTORATION_METADATA'):self.run_gate()
        self.assertFalse(self.posts())
    def test_cpu_only_model_left_alone(self):
        self.api.models[0]['size_vram']=0
        with self.assertRaisesRegex(RuntimeError,'VRAM_REMAINS'):self.run_gate()
        self.assertFalse(self.posts());self.assertFalse(self.pending)
    def test_no_loaded_models_no_unload(self):
        self.api.models=[]
        with self.assertRaisesRegex(RuntimeError,'VRAM_REMAINS'):self.run_gate()
        self.assertFalse(self.posts())
    def test_reappearing_model_not_repeatedly_unloaded(self):
        self.api.state=lambda:[] if self.clock.now<2 else self.api.models
        with self.assertRaisesRegex(RuntimeError,'RELOADED_EXTERNALLY'):self.run_gate()
        self.assertEqual(len(self.posts()),1)
    def test_alias_same_digest_is_still_resident(self):
        self.api.state=lambda:[dict(MODEL,name='another-alias')]
        with self.assertRaisesRegex(RuntimeError,'UNLOAD_NOT_CONFIRMED'):self.run_gate(2)
    def test_no_ps_cannot_verify_release_even_when_vram_high(self):
        self.api.raise_get=True
        self.u.free=lambda:r.MIN_FREE+100 if self.clock.now>=1 else r.MIN_FREE-100
        with self.assertRaisesRegex(RuntimeError,'UNLOAD_NOT_CONFIRMED'):self.run_gate(3)
    def test_known_port_failure_keeps_same_fallback_server(self):
        self.api.raise_initial=True;self.api.state=lambda:[]
        self.u.free=lambda:r.MIN_FREE+100 if self.clock.now>=1 else r.MIN_FREE-100
        self.run_gate()
        self.assertEqual(self.api.selected,('local-container','http://127.0.0.1:11434'))
        self.assertEqual(json.loads((self.root/'ollama-restore-pending.json').read_text())['endpoint'][0],'local-container')
    def test_counter_disappears_after_request(self):
        self.u.free=lambda:-1 if self.clock.now>=1 else r.MIN_FREE-100
        self.api.state=lambda:[]
        with self.assertRaisesRegex(RuntimeError,'COUNTER_LOST'):self.run_gate()
        self.assertEqual(len(self.pending),1)
    def test_api_output_text_not_saved(self):
        self.api.reply['response']='not an unload confirmation: text'
        with self.assertRaises(RuntimeError):self.run_gate(1)
        receipt=json.loads((self.root/'ollama-unload-0.json').read_text())
        self.assertNotIn('response',receipt['response']);self.assertGreater(receipt['response_characters'],0)
    def test_wait_is_bounded_no_repeated_request(self):
        with self.assertRaises(RuntimeError):self.run_gate(45)
        self.assertEqual(self.clock.now,45);self.assertEqual(len(self.posts()),1)


class FileGuards(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.u=Util()
    def test_source_guard_is_read_only_and_rejects_change(self):
        p=self.root/'a';p.write_bytes(b'a');good=hashlib.sha256(b'a').hexdigest()
        with patch.object(r,'SOURCES',{'a':good}),patch.object(r,'KEPT',{}):
            r.verify_sources(self.u,self.root);self.assertEqual(p.read_bytes(),b'a')
            p.write_bytes(b'b')
            with self.assertRaisesRegex(RuntimeError,'NATIVE_SOURCE_OR_ACCEPTED'):r.verify_sources(self.u,self.root)
            self.assertEqual(p.read_bytes(),b'b')
    def test_profile_external_save_rejected(self):
        p=self.root/'runs/run/user';p.mkdir(parents=True)
        (p/'config.json').write_text(json.dumps({'General':{'home_dir':'/outside/saves'}}))
        with self.assertRaisesRegex(RuntimeError,'EXTERNAL_SAVE_ROOT'):r.verified_profile(self.root,{'diagnostic_profile':str(p)})
    def test_profile_symlink_rejected(self):
        p=self.root/'runs/run/user';p.mkdir(parents=True)
        (p/'config.json').write_text('{}');(p/'link').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(RuntimeError,'SYMLINK'):r.verified_profile(self.root,{'diagnostic_profile':str(p)})
    def test_private_profile_unchanged(self):
        p=self.root/'runs/run/user';p.mkdir(parents=True);f=p/'config.json';f.write_text('{}')
        self.assertEqual(r.verified_profile(self.root,{'diagnostic_profile':str(p)}),p)
        self.assertEqual(f.read_text(),'{}')
    def test_capture_existing_vs_fresh(self):
        p=self.root/'profile';d=p/'shader/dumps';d.mkdir(parents=True)
        old=d/'ee963aae-old.txt';old.write_text('old');fresh=d/'ee963aae-new.spv';fresh.write_bytes(b'new')
        import os,time
        now=time.time();os.utime(old,(now-60,now-60));os.utime(fresh,(now+1,now+1))
        e=self.root/'e';e.mkdir();(e/'game.log').write_text('')
        r.capture_fresh(self.u,p,e,now)
        inventory=json.loads((e/'fresh-shader-inventory.json').read_text())
        self.assertEqual([x['file'] for x in inventory],['ee963aae-new.spv'])
        self.assertEqual(old.read_text(),'old')


class MainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name)/'project';self.logs=self.project/'logs';self.logs.mkdir(parents=True)
        self.cand=self.project/'GhostOfTsushima/experiments'/r.BASE
        self.binary=self.cand/'build/shadps4';self.binary.parent.mkdir(parents=True);self.binary.write_bytes(b'candidate')
        self.source=self.cand/'source';self.source.mkdir();(self.source/'checked.cpp').write_text('source')
        self.profile=self.cand/'runs/test/user';self.profile.mkdir(parents=True);(self.profile/'config.json').write_text('{}')
        self.protected=self.project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
        self.protected.parent.mkdir(parents=True);self.protected.write_bytes(b'protected')
        old=self.logs/r.PREVIOUS;old.mkdir()
        expected=hashlib.sha256(b'candidate').hexdigest()
        (old/'trial.json').write_text(json.dumps({'build':'PASS','candidate_sha256':expected}))
        (old/'original-files-before.json').write_text(json.dumps({str(self.protected):'old'}))
        p=self.logs/r.PROFILE_TRIAL;p.mkdir();(p/'trial.json').write_text(json.dumps({'diagnostic_profile':str(self.profile)}))
        self.u=Util(free=lambda:r.MIN_FREE+10);self.u.Ollama=lambda e:API()
        self.u.command=lambda *a,**k:b'fake display'
        self.game_calls=[];self.restore_calls=[]
        def game(*args):
            self.game_calls.append(args);(args[3]/'game.log').write_text('FiniteRawImageRead: shader=0xee963aae\n')
            return {'time_limit_reached':True,'exit_code':0,'graphics':'PENDING_REVIEW'}
        self.u.run_game=game
        self.u.restore_models=lambda api,pending,e:self.restore_calls.append(pending) or [{'result':'RESTORED_VERIFIED'}]
        self.patches=[patch.object(r,'EXPECTED',expected),patch.object(r,'SOURCES',{'checked.cpp':hashlib.sha256(b'source').hexdigest()}),
                      patch.object(r,'KEPT',{}),patch.object(r.shutil,'which',return_value='/mock/tool'),
                      patch.object(r,'capture_fresh'),patch.object(r,'memory_evidence')]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.project.rglob('*') if p.is_file()}
        self.exists=Path.is_file
        def is_file(p):return True if str(p)=='/mnt/roms-all/ps4-games/CUSA11456/eboot.bin' else self.exists(p)
        f=patch.object(Path,'is_file',is_file);f.start();self.addCleanup(f.stop)
    def outcome(self):
        paths=list(self.logs.glob('got-c925-finite-raw-resume-*/trial.json'));self.assertEqual(len(paths),1)
        return json.loads(paths[0].read_text())
    def test_mock_success_no_build_no_source_writes(self):
        self.assertEqual(r.main(self.u,self.project),0);self.assertEqual(len(self.game_calls),1)
        out=self.outcome();self.assertEqual(out['tested_games'],['CUSA11456']);self.assertEqual(out['original_files_changed'],[])
        for p,h in self.files.items():self.assertEqual(hashlib.sha256(Path(p).read_bytes()).hexdigest(),h)
        self.assertEqual(len(list(self.logs.glob('got-c925-finite-raw-resume-*.tar.gz'))),1)
    def test_changed_binary_fails_before_api_and_launch(self):
        self.binary.write_bytes(b'changed');self.assertEqual(r.main(self.u,self.project),1)
        self.assertFalse(self.game_calls);self.assertEqual(self.outcome()['status'],'CANDIDATE_CHANGED_NO_GAME')
    def test_pending_model_restored_after_gate_failure(self):
        def gate(u,api,evid,pending,result):
            pending.append(dict(MODEL));result['vram_before_bytes']=1;raise RuntimeError('MODEL_STILL_LOADED')
        with patch.object(r,'verified_headroom',gate):self.assertEqual(r.main(self.u,self.project),1)
        self.assertFalse(self.game_calls);self.assertEqual(len(self.restore_calls),1)
    def test_memory_diagnostic_failure_does_not_skip_model_restore(self):
        def gate(u,api,evid,pending,result):
            pending.append(dict(MODEL));result['vram_before_bytes']=1;raise RuntimeError('NO_MEMORY')
        with patch.object(r,'verified_headroom',gate),patch.object(r,'memory_evidence',side_effect=RuntimeError('diag')):
            self.assertEqual(r.main(self.u,self.project),1)
        self.assertEqual(len(self.restore_calls),1);self.assertIn('memory_evidence_error',self.outcome())
    def test_final_memory_drop_prevents_game(self):
        self.u.free=lambda:1
        with patch.object(r,'verified_headroom'):self.assertEqual(r.main(self.u,self.project),1)
        self.assertFalse(self.game_calls);self.assertEqual(self.outcome()['status'],'VRAM_DROPPED_BEFORE_LAUNCH_NO_GAME')
    def test_renderer_modified_after_gate_blocks_launch(self):
        def gate(*a): (self.source/'checked.cpp').write_text('changed')
        with patch.object(r,'verified_headroom',gate):self.assertEqual(r.main(self.u,self.project),1)
        self.assertFalse(self.game_calls())
    def test_restoration_failure_affects_exit(self):
        def gate(u,api,evid,pending,result):pending.append(dict(MODEL))
        self.u.restore_models=lambda *a:[{'result':'RESTORE_FAILED'}]
        with patch.object(r,'verified_headroom',gate):self.assertEqual(r.main(self.u,self.project),1)
        self.assertEqual(self.outcome()['runner_exit_code'],1)

if __name__=='__main__':unittest.main(verbosity=2)
