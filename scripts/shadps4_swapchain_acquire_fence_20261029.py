#!/usr/bin/env python3
"""Retain all native GoW correctness fixes and close a recorded present-after-present gap.

Only the two swapchain files change. This is a conservative host-acquire fence
synchronization with no device-wide idle, per-game branch, or shader override.
It is tested against exact extracted source methods and a modeled present engine.
Native Vulkan build and a validation-enabled gameplay trial are still required.
"""
from __future__ import annotations
import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import shlex
import subprocess
import types

REPO='Chreece/shadPS4'
BASE_RUNNER_REF='c72ece1327dac3415a64a26afb8364ca136cb3aa'
BASE_RUNNER_PATH='scripts/shadps4_typed_image_cache_20261029.py'
BASE_RUNNER_SHA='30dae27ac20355f43e1a09d6db1e5d896e987692b6784ffc8369cbc3ac64e359'
PKG='311f88a246fee98b707efaae3a47d4abd7385286'
PATCH_PATH='scripts/patchsets/swapchain-reacquire-fence-20261029.patch'
TEST_PATH='tools/development/swapchain_acquire_fence_fixture.cpp.in'
PATCH_BLOB='5b9eb1f8a8a5d2c89db03f7d52646cedabf735f1'
TEST_BLOB='96ab5daab306a10ddd91fd439136e69edfd3ded3'
CPP='src/video_core/renderer_vulkan/vk_swapchain.cpp'
HPP='src/video_core/renderer_vulkan/vk_swapchain.h'
PREIMAGES={CPP:'f2d6bbab9b4c18d0d2d21c66da9faf365f423b56',
           HPP:'826705d40ba78b5fe83b2f173fe454ec97a0894b'}
LAST='eb2fd024697d21ba70d2a1055eb4c6f6db4d5fa9'
LAST_ELF='a3edb42d4a270aa82ffeb18733c2e412526f8376eabac0e15c5beaf4ba138e85'
RUNTIME_SHA='22cdf96cf35ffb2cd95e583ac749cc7e0af2e81da6d2549fbcc411a8d97eaf59'
RUNTIME_BLOB='509e837fe64b4ee4ef3fc834d0ba95a223e0ea6a'


def verified_runner():
    directory=Path.home()/'projects/shadps4/scripts'
    directory.mkdir(parents=True,exist_ok=True)
    for candidate in sorted(directory.glob('gow-typed-image-*.py')):
        if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size<100000:
            data=candidate.read_bytes()
            if hashlib.sha256(data).hexdigest()==BASE_RUNNER_SHA:
                return candidate,data
    url=f'https://raw.githubusercontent.com/{REPO}/{BASE_RUNNER_REF}/{BASE_RUNNER_PATH}'
    data=subprocess.run(['curl','-4fsSL','--retry','3','--connect-timeout','15','--max-time','90',url],
                        capture_output=True,check=True,timeout=180).stdout
    if hashlib.sha256(data).hexdigest()!=BASE_RUNNER_SHA:
        raise RuntimeError('PREVIOUS_TYPED_RUNNER_SHA_MISMATCH')
    ast.parse(data)
    path=directory/('pinned-'+BASE_RUNNER_SHA+'.py')
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes()!=data:
            raise RuntimeError('EXISTING_PINNED_SCRIPT_MODIFIED')
    else:
        path.write_bytes(data)
        path.chmod(0o700)
    return path,data


def exact_function(code,signature):
    if code.count(signature)!=1:
        raise RuntimeError('SWAPCHAIN_SOURCE_ANCHOR_CHANGED='+signature)
    start=code.index(signature)
    p=code.index('{',start)+1
    depth=1
    while depth and p<len(code):
        depth+=(code[p]=='{')-(code[p]=='}')
        p+=1
    if depth:
        raise RuntimeError('UNBALANCED_CPP_SOURCE')
    return code[start:p]


def configured_worker(typed, validation, previous_raw):
    parent_script,parent_bytes=typed.pinned_helper('gow-auto-write-*.py',typed.PARENT_REF,
                                                  typed.PARENT_PATH,typed.PARENT_SHA)
    m=typed.load_worker(parent_bytes,validation)
    m.LAST,m.LAST_ELF=LAST,LAST_ELF
    m.LAST_BIN=m.ROOT/'GodOfWarRagnarok/tests/gow-typed-image-20261029T094518Z-3015905/candidate/shadps4'
    m.PACKAGE,m.PATCH_PATH,m.TEST_PATH=PKG,PATCH_PATH,TEST_PATH
    m.PACKAGE_BLOBS={PATCH_PATH:PATCH_BLOB,TEST_PATH:TEST_BLOB}
    m.M,m.B,m.H=CPP,CPP,HPP
    m.CHANGED={CPP,HPP}
    m.PREIMAGES=PREIMAGES
    m.UPSTREAM=m.UPSTREAM_OLD='0cd6e4f2cb6617af12eb8267cca848cc41ad20d7'
    m.UPSTREAM_PATHS=set()
    m.TEMPLATE=m.LOGS/'gow-typed-image-20261029T094518Z-3015905-evidence/native-game-runner.sh'
    m.TEMPLATE_BLOB=RUNTIME_BLOB
    # The inherited worker performs all locking, preimage checks, preserved ELF
    # checks, private profile, append-only diary, archival and PR94 restoration.
    if 'vulkan-runtime-validation.lock' not in m.LOCKS:
        m.LOCKS=(*m.LOCKS,'vulkan-runtime-validation.lock')
    session={}
    results={}
    m.validation_results=results

    def cpu_tests(old,template,work,evidence):
        corrected=(m.SRC/CPP).read_text()
        header=(m.SRC/HPP).read_text()
        m.require(header.count('std::vector<vk::Fence> image_acquired_fences;')==1,
                  'ACQUIRE_FENCE_STORAGE_MISSING')
        for token in ('device.createFence(', 'device.destroyFence(', 'device.waitForFences(',
                      'device.resetFences(', 'image_acquired_fences.resize(image_count)'):
            m.require(corrected.count(token)==1, 'ACQUIRE_FENCE_LIFECYCLE_INCOMPLETE='+token)
        m.require('device.waitIdle()' not in exact_function(corrected,'bool Swapchain::AcquireNextImage('),
                  'DEVICE_WIDE_WAIT_IN_ACQUIRE_REFUSED')
        for name,code,args in [('baseline',old,['--baseline']),('corrected',corrected,[])]:
            fixture=template.replace('/* EXACT_ACQUIRE */',
                                     exact_function(code,'bool Swapchain::AcquireNextImage('))
            m.require('/* EXACT_ACQUIRE */' not in fixture,'TEST_METHOD_NOT_EXTRACTED')
            cpp=evidence/(name+'-swapchain-acquire.cpp')
            cpp.write_text(fixture)
            exe=work/(name+'-swapchain-acquire')
            m.cmd(['g++','-std=c++23','-O2','-Wall','-Wextra','-Werror','-pedantic',cpp,'-o',exe],
                  evidence/(name+'-swapchain-acquire-build.log'),timeout=180)
            output=m.cmd([exe,*args],evidence/(name+'-swapchain-acquire-result.txt'),timeout=30)
            expected=(b'BASELINE_HOST_ACQUIRE_FENCE_ABSENT=REPRODUCED' if args
                      else b'SWAPCHAIN_ACQUIRE_FENCE=PASS')
            m.require(expected in output,'SWAPCHAIN_ACQUIRE_CPU_TEST_FAILED='+name)
        env,layer=validation.prepare_layer(work,evidence)
        session['env']=env
        results['validation_layer']=layer
        print('SWAPCHAIN_ACQUIRE_CPU_FIXTURE=PASS VALIDATION_LAYER_LOAD=PASS',flush=True)
    m.cpu_tests=cpu_tests

    def derive_runtime(target,pin,binary_sha):
        raw=m.TEMPLATE.read_bytes()
        m.require(hashlib.sha256(raw).hexdigest()==RUNTIME_SHA,
                  'PREVIOUS_GAME_RUNTIME_SHA_CHANGED')
        text=raw.decode()
        for before,after,count in ((LAST,pin,2),(LAST_ELF,binary_sha,2),
                                   ('TYPED_IMAGE_CACHE_VALIDATION_GOW',
                                    'SWAPCHAIN_REACQUIRE_FENCE_VALIDATION_GOW',1)):
            m.require(text.count(before)==count,'GAME_RUNTIME_ANCHOR_CHANGED='+before[:55])
            text=text.replace(before,after)
        pattern=r'^python3 -B [^\n]+ --private-config "\$WORK/user/config.json" "\$EVIDENCE/diagnostic-config-change.json"$'
        m.require(len(re.findall(pattern,text,re.M))==1,'PRIVATE_PROFILE_HOOK_CHANGED')
        repl='python3 -B '+shlex.quote(str(Path(validation.__file__).resolve()))+' --private-config "$WORK/user/config.json" "$EVIDENCE/diagnostic-config-change.json"'
        text=re.sub(pattern,repl,text,flags=re.M)
        m.require('--seconds 118' in text and 'shutil.copy2(old,dest)' in text and
                  'GOW_COHERENCE_PARENT_LOCKS' in text,'TEST_GUARDS_MISSING')
        target.write_text(text)
        m.cmd(['bash','-n',target])
    m.derive_runtime=derive_runtime

    def run_game(runner,tag,game_log):
        evidence=runner.parent
        env=session['env'].copy()
        env.update(GOW_COHERENCE_PARENT_LOCKS='YES',
                   VK_KHRONOS_VALIDATION_DEBUG_ACTION='VK_DBG_LAYER_ACTION_LOG_MSG',
                   VK_KHRONOS_VALIDATION_LOG_FILENAME=str(evidence/'validation-layer.log'),
                   VK_KHRONOS_VALIDATION_ENABLE_MESSAGE_LIMIT='true',
                   VK_KHRONOS_VALIDATION_DUPLICATE_MESSAGE_LIMIT='1')
        work=m.ROOT/'GodOfWarRagnarok/tests'/('playtest-'+tag)
        rc,first=validation.monitored_run(runner,tag,work,game_log,env,m.BIN,evidence)
        results['first_validation_error']=first
        results['intentional_validation_stop']=first is not None
        text=''
        for path in (evidence/'validation-layer.log',work/'evidence/game/full-console.log'):
            if path.is_file() and path.stat().st_size<64*1024**2:
                text+='\n'+path.read_text(errors='replace')
        results['validation_ids']=sorted(set(re.findall(
            r'(?:VUID-[A-Za-z0-9_-]+|SYNC-HAZARD-[A-Z_-]+)',text)))
        results['present_hazard_returned']='SYNC-HAZARD-WRITE-AFTER-PRESENT' in text
        results['previous_descriptor_errors_returned']=any(s in text for s in
            ('VUID-VkDescriptorImageInfo-descriptorType-06714',
             'VUID-VkDescriptorImageInfo-imageView-07796'))
        results['device_loss_reported']='Device lost during submit' in text
        results['runtime_validation_enabled']=all(s in text for s in
            ('Enabled instance layers: VK_LAYER_KHRONOS_validation',
             'Vulkan vkValidationCore: true','Vulkan vkValidationSync: true'))
        return rc
    m.run_game=run_game
    return m


def main():
    path,raw=verified_runner()
    spec=importlib.util.spec_from_file_location('pinned_typed_image_runner',path)
    if spec is None or spec.loader is None:
        raise RuntimeError('PREVIOUS_RUNNER_IMPORT_FAILED')
    typed=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(typed)
    valpath,valbytes=typed.pinned_helper('gow-vk-validation-*.py',typed.HELPER_REF,
                                        typed.HELPER_PATH,typed.HELPER_SHA)
    validation_spec=importlib.util.spec_from_file_location('pinned_validation_helper',valpath)
    if validation_spec is None or validation_spec.loader is None:
        raise RuntimeError('VALIDATION_HELPER_IMPORT_FAILED')
    validation=importlib.util.module_from_spec(validation_spec)
    validation_spec.loader.exec_module(validation)
    return configured_worker(typed,validation,raw).main()


if __name__=='__main__':
    raise SystemExit(main())
