import hashlib, io, json, os, struct, sys, tarfile, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import audit_generated_spirv as a
VALID = struct.pack('<5I',0x07230203,0x10600,0,1,0)
class Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name)
        self.inputs=a.Inputs(self.root/'inputs')
    def test_container_and_dedup(self):
        self.inputs.add(VALID,'one','latest','a.spv');self.inputs.add(VALID,'two','old','b.spv')
        self.assertEqual(len(self.inputs.items),1);self.assertEqual(len(next(iter(self.inputs.items.values()))['origins']),2)
    def test_bad_magic_and_short(self):
        for b in (b'',b'x'*20,VALID+b'x'):self.inputs.add(b,'bad','latest','a')
        self.assertEqual(len(self.inputs.items),0);self.assertEqual(len(self.inputs.skipped),3)
    def test_budget(self):
        with patch.object(a,'MAX_FILES',0):self.inputs.add(VALID,'a','b','c')
        self.assertEqual(self.inputs.skipped[0]['reason'],'INPUT_BUDGET')
    def test_symlink_not_followed(self):
        f=self.root/'real';f.write_bytes(VALID);link=self.root/'link.spv';link.symlink_to(f)
        self.inputs.file(link,'latest');self.assertFalse(self.inputs.items)
    def test_archive_member_safety(self):
        f=self.root/'input.tar.gz'
        with tarfile.open(f,'w:gz') as t:
            for name in ('../escape.spv','/absolute.spv','okay/shader.spv'):
                m=tarfile.TarInfo(name);m.size=len(VALID);t.addfile(m,io.BytesIO(VALID))
            m=tarfile.TarInfo('okay/symlink.spv');m.type=tarfile.SYMTYPE;m.linkname='/etc/passwd';t.addfile(m)
        self.inputs.archive(f,'prior');self.assertEqual(len(self.inputs.items),1)
        self.assertEqual(next(iter(self.inputs.items.values()))['origins'][0]['name'],'shader.spv')
    def test_command_exit(self):
        rc,text=a.command([sys.executable,'-c','print("actual child");raise SystemExit(3)'])
        self.assertEqual(rc,3);self.assertIn('actual child',text)
    def test_owned_timeout(self):
        with self.assertRaises(TimeoutError):a.command([sys.executable,'-c','import time;time.sleep(10)'],timeout=.1)
    def test_bad_download_hash_never_extracts_or_executes(self):
        evid=self.root/'evid';evid.mkdir()
        calls=[]
        def run(args,timeout=15):
            calls.append(args)
            if args[0]=='curl':Path(args[-1]).write_bytes(b'bad package')
            return 0,''
        def which(name):return None if name=='spirv-val' else '/mock/'+name
        with patch.object(a.shutil,'which',which),patch.object(a.platform,'system',return_value='Linux'),patch.object(a.platform,'machine',return_value='x86_64'),patch.object(a,'command',run):
            with self.assertRaisesRegex(RuntimeError,'CHECKSUM_FAILED'):a.tools(self.root,evid)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],'curl')
    def test_installed_tool_no_download(self):
        with patch.object(a.shutil,'which',side_effect=lambda n:'/bin/'+n):
            val,dis=a.tools(self.root,self.root)
        self.assertEqual(str(val),'/bin/spirv-val')
    def test_end_to_end_mock_validator_readonly_modes_archived(self):
        project=self.root/'project';logs=project/'logs';prior=logs/a.LATEST;(prior/'fresh-shaders').mkdir(parents=True)
        base=project/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
        binary=base/'build/shadps4';binary.parent.mkdir(parents=True);binary.write_bytes(b'not executed')
        profile=base/'runs/test/user';(profile/'shader/dumps').mkdir(parents=True)
        (profile/'config.json').write_text('{}');(prior/'fresh-shaders/test.spv').write_bytes(VALID)
        (prior/'trial.json').write_text(json.dumps({'expected_binary_sha256':a.EXPECTED,'private_profile':str(profile)}))
        originals={p:p.read_bytes() for p in project.rglob('*') if p.is_file()}
        tool=self.root/'validator';tool.write_text('#!'+sys.executable+'\nimport sys\nprint("synthetic test tool, not SPIR-V validation")\nraise SystemExit(0 if "--version" in sys.argv or "spv1.6" in sys.argv else 1)\n');tool.chmod(0o755)
        with patch.object(a,'tools',return_value=(tool,None)):
            self.assertEqual(a.main(project),0)
        for p,b in originals.items():self.assertEqual(p.read_bytes(),b)
        trials=list(logs.glob('got-c925-spirv-offline-*/trial.json'));self.assertEqual(len(trials),1)
        result=json.loads(trials[0].read_text());self.assertEqual(result['mode_results']['universal']['pass'],1)
        self.assertEqual(result['mode_results']['vulkan13-strict']['nonzero'],1)
        self.assertEqual(result['game'],'NOT_RUN');self.assertTrue(result['binary_unchanged_during_audit'])
        self.assertEqual(len(list(logs.glob('got-c925-spirv-offline-*.tar.gz'))),1)
if __name__=='__main__':unittest.main(verbosity=2)
