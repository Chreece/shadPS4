#!/usr/bin/env python3
"""Source/dump capture unit tests; no game, GPU or network operations."""
import importlib.util
import tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('menu_only_capture',Path(__file__).with_name('menu_only_capture.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_fragment_selection():
 with tempfile.TemporaryDirectory() as tmp:
  p=Path(tmp);e=p/'evidence';e.mkdir();d=p/'user/shader/dumps';d.mkdir(parents=True)
  (e/'game.log').write_text('Compiling fs shader 0xb5c898c7\nSHARP_RESOURCE_PROVENANCE: stage=0 shader=0xb5c898c7\nSHARP_RESOURCE_PROVENANCE: stage=6 shader=0x04950cf6\nIndexedUniformSamplerBound: shader=0xb5c898c7\n')
  (d/'fs_0xb5c898c7_0.spv').write_bytes(b'0\x00\x00\x00')
  (d/'fs_0xother.spv').write_bytes(b'zz')
  x=m.focus(e,p/'user')
  assert x['fragment_shader_hashes']==['b5c898c7'],x
  assert x['fragment_resource_event_count']==2,x
  assert x['fragment_files']==1,x
  assert (e/'fragment-shaders/fs_0xb5c898c7_0.spv').read_bytes()==b'0\x00\x00\x00'
  assert (d/'fs_0xb5c898c7_0.spv').read_bytes()==b'0\x00\x00\x00'
  assert 'fragment_events' in (e/'fragment-events.json').read_text()

def test_empty():
 with tempfile.TemporaryDirectory() as tmp:
  p=Path(tmp);e=p/'evidence';e.mkdir();d=p/'user';d.mkdir()
  assert m.focus(e,d)['fragment_files']==0
  assert (e/'fragment-events.json').is_file()

def test_resource_only_fragment_hint():
 with tempfile.TemporaryDirectory() as tmp:
  p=Path(tmp);e=p/'evidence';e.mkdir();d=p/'user/shader/dumps';d.mkdir(parents=True)
  (e/'game.log').write_text('GuestIndexedImageDimensions: stage=0xb5c898c7 entries=9 bound_2d=9\\nSHARP_RESOURCE_PROVENANCE shader=0xb5c898c7 stage=0 role=image indexed=true\\nSHARP_RESOURCE_PROVENANCE shader=0x04950cf6 stage=6 role=image\\n')
  (d/'fs_0xb5c898c7_0.irprogram.txt').write_text('OpImageSample')
  x=m.focus(e,p/'user')
  assert x['compiled_fragment_shaders']==0,x
  assert x['fragment_shader_hashes']==['b5c898c7'],x
  assert x['fragment_files']==1,x
  assert x['fragment_resource_event_count']==2,x
  assert 'execution not inferred' in (e/'fragment-files.json').read_text()

if __name__=='__main__':
 test_fragment_selection();test_resource_only_fragment_hint();test_empty();print('MENU_ONLY_FRAGMENT_TESTS=PASS (3)')
