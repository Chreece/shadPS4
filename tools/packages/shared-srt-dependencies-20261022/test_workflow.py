#!/usr/bin/env python3
"""Packaging/source/real-Git tests; no emulator compilation or GPU execution."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch as mockpatch
import install
import patch_srt_dependencies as delta
import run_srt_dependencies as runner

HERE=Path(__file__).resolve().parent
ENV=dict(os.environ,GIT_AUTHOR_NAME='fixture',GIT_AUTHOR_EMAIL='fixture@localhost',
         GIT_COMMITTER_NAME='fixture',GIT_COMMITTER_EMAIL='fixture@localhost')

def git(repo,*args,input_data=None,env=None):
    return subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-C',str(repo),*args],
      input=input_data,env=env or ENV,stderr=subprocess.PIPE)

def repo(path):
    path.mkdir();git(path,'init','-q');git(path,'config','user.name','fixture');git(path,'config','user.email','fixture@localhost')

def commit(path):
    git(path,'add','.');git(path,'commit','-qm','fixture');return git(path,'rev-parse','HEAD').decode().strip()

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.pkg=self.root/'package';shutil.copytree(HERE,self.pkg,ignore=shutil.ignore_patterns('__pycache__'))
    def test_manifest_integrity(self):runner.verify_package(self.pkg)
    def test_modified_script_rejected(self):
        (self.pkg/'verify_srt_dependencies.py').write_text('# changed\n')
        with self.assertRaisesRegex(RuntimeError,'CHECKSUM'):runner.verify_package(self.pkg)
    def test_missing_required_file_rejected(self):
        (self.pkg/'test_srt_order.cpp').unlink()
        with self.assertRaisesRegex(RuntimeError,'CHECKSUM'):runner.verify_package(self.pkg)
    def test_symlink_rejected(self):
        path=self.pkg/'test_srt_order.cpp';data=path.read_bytes();path.unlink();p=self.root/'outside';p.write_bytes(data);path.symlink_to(p)
        with self.assertRaisesRegex(RuntimeError,'CHECKSUM'):runner.verify_package(self.pkg)
    def test_incomplete_manifest_rejected(self):
        (self.pkg/'MANIFEST.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'MANIFEST'):runner.verify_package(self.pkg)
    def test_source_preimage_rejected(self):
        with self.assertRaisesRegex(ValueError,'PREIMAGE'):delta.transform(delta.FLAT,b'not the retained source')
    def test_missing_anchor_rejected(self):
        with self.assertRaisesRegex(ValueError,'ANCHOR'):delta.transform_flat('#include <unordered_map>')
    def test_cache_versions_both_changed(self):
        data=b'ShaderBinaryVersion = 0x43520008u;\nShaderMetaVersion = 0x43520008u;'
        with mockpatch.dict(delta.EXPECTED,{delta.CACHE:delta.blob(data)}):
            result=delta.transform(delta.CACHE,data)
        self.assertEqual(result.count(b'0x43520009'),2)
    def package_repo(self):
        r=self.root/'git';repo(r);target=r/install.PREFIX;target.parent.mkdir(parents=True)
        shutil.copytree(self.pkg,target);return r,commit(r)
    def test_real_git_fresh_and_repeat_install(self):
        r,rev=self.package_repo();out=self.root/'installed';install.install(r,rev,out);install.install(r,rev,out)
        runner.verify_package(out);self.assertFalse((out/'test_package.py').exists())
    def test_installed_change_refused(self):
        r,rev=self.package_repo();out=self.root/'installed';install.install(r,rev,out);(out/'README.md').write_text('different')
        with self.assertRaisesRegex(RuntimeError,'INSTALLED_PACKAGE_CHANGED'):install.install(r,rev,out)
    def test_corrupt_repository_payload_refused(self):
        r,rev=self.package_repo();(r/install.PREFIX/'README.md').write_text('corruption');rev=commit(r)
        with self.assertRaisesRegex(RuntimeError,'HASH_MISMATCH'):install.install(r,rev,self.root/'installed')
        self.assertFalse((self.root/'installed').exists())
    def test_unsafe_remote_name_refused(self):
        r,rev=self.package_repo();p=r/install.PREFIX/'MANIFEST.json';m=json.loads(p.read_text());m['../escape']='0'*64;p.write_text(json.dumps(m));rev=commit(r)
        with self.assertRaisesRegex(RuntimeError,'UNSAFE'):install.install(r,rev,self.root/'installed')
    def test_destination_symlink_refused(self):
        r,rev=self.package_repo();out=self.root/'installed';out.symlink_to(self.pkg,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'DESTINATION_OCCUPIED'):install.install(r,rev,out)
    def test_new_sources_have_no_game_selectors(self):
        for name in ('srt_dependency_schedule.h','srt_dependency_codegen.inc'):
            text=(self.pkg/name).read_text()
            for forbidden in ('CUSA','getenv','pgm_hash','SkipDraw','Finish()'):self.assertNotIn(forbidden,text)
    def test_build_only_entrypoint(self):
        text=(self.pkg/'run_srt_dependencies.py').read_text()
        self.assertIn("sys.argv=[sys.argv[0],'--build-only']",text)
        self.assertNotIn('runtime.main()',text)
        for name in self.pkg.glob('*.py'):ast.parse(name.read_text())
    def git_delta(self):
        r=self.root/'candidate';repo(r);(r/'keep.txt').write_text('keep original')
        base=commit(r)
        for name in (delta.FLAT,delta.CACHE):
            p=r/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture '+name)
        git(r,'add','.')
        git(r,'update-index','--add','--cacheinfo','160000,'+base+',externals/sirit')
        git(r,'commit','-qm','fixture parent');parent=git(r,'rev-parse','HEAD').decode().strip();tree=git(r,'rev-parse',parent+'^{tree}').decode().strip()
        manifest={'files':{}}
        for name in (delta.FLAT,delta.CACHE,'externals/sirit'):
            oid=git(r,'rev-parse',parent+':'+name).decode().strip()
            record={'original_blob':None,'corrected_blob':oid}
            if name=='externals/sirit':record['kind']='gitlink'
            else:record['sha256']=hashlib.sha256((r/name).read_bytes()).hexdigest()
            manifest['files'][name]=record
        git(r,'checkout','--detach',base)
        work=self.root/'run';work.mkdir();info=work/'info';info.mkdir();stage=work/'stage';stage.mkdir()
        job=types.SimpleNamespace(run=work,info=info,stage=stage,report={},git=lambda *a,**kw:git(r,*a,**kw))
        native=types.SimpleNamespace(SIRIT='externals/sirit',NEW_SIRIT='unchanged',base=types.SimpleNamespace(
            BASE=base,ENV=ENV,digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),
            write_json=lambda p,v:p.write_text(json.dumps(v,indent=2)+'\n')))
        updates={delta.FLAT:b'new flat',delta.CACHE:b'new cache',delta.HEADER:b'new header',delta.CODEGEN:b'new emitter'}
        return r,base,parent,tree,job,native,manifest,updates
    def test_real_git_delta_preserves_checkout_history_and_dependency(self):
        r,base,parent,tree,job,native,manifest,updates=self.git_delta()
        with mockpatch.multiple(runner,PARENT=parent,TREE=tree,SIRIT=base):runner.install_delta(job,native,manifest,updates)
        self.assertEqual(git(r,'rev-parse','HEAD').decode().strip(),base)
        self.assertEqual(git(r,'status','--porcelain').strip(),b'')
        self.assertEqual(git(r,'rev-parse',job.revision+'^').decode().strip(),parent)
        self.assertEqual(job.report['source_parent'],parent)
        self.assertEqual(native.NEW_SIRIT,base)
        self.assertEqual(git(r,'show',job.revision+':keep.txt'),b'keep original')
        self.assertEqual(set(git(r,'diff','--name-only',parent,job.revision).decode().splitlines()),set(updates))
        self.assertTrue((job.info/'srt-dependency.patch').is_file())
    def test_wrong_parent_tree_refused_without_checkout(self):
        r,base,parent,tree,job,native,manifest,updates=self.git_delta()
        with mockpatch.multiple(runner,PARENT=parent,TREE='0'*40,SIRIT=base):
            with self.assertRaisesRegex(RuntimeError,'TREE_CHANGED'):runner.install_delta(job,native,manifest,updates)
        self.assertEqual(git(r,'rev-parse','HEAD').decode().strip(),base)
        self.assertEqual(git(r,'status','--porcelain').strip(),b'')
    def test_missing_delta_file_refused(self):
        r,base,parent,tree,job,native,manifest,updates=self.git_delta();updates.pop(delta.CODEGEN)
        with mockpatch.multiple(runner,PARENT=parent,TREE=tree,SIRIT=base):
            with self.assertRaisesRegex(RuntimeError,'CHANGESET'):runner.install_delta(job,native,manifest,updates)
        self.assertEqual(git(r,'rev-parse','HEAD').decode().strip(),base)
    def test_manifest_mismatch_refused(self):
        r,base,parent,tree,job,native,manifest,updates=self.git_delta();manifest['files'][delta.FLAT]['corrected_blob']='0'*40
        with mockpatch.multiple(runner,PARENT=parent,TREE=tree,SIRIT=base):
            with self.assertRaisesRegex(RuntimeError,'MANIFEST_MISMATCH'):runner.install_delta(job,native,manifest,updates)
        self.assertEqual(git(r,'rev-parse','HEAD').decode().strip(),base)

if __name__=='__main__':unittest.main(verbosity=2)
