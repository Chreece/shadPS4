# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Process-local Vulkan validation and an unsubmitted synchronization canary."""

import ctypes as c
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ENVIRONMENT = {
    'VK_INSTANCE_LAYERS': 'VK_LAYER_KHRONOS_validation',
    'VK_KHRONOS_VALIDATION_VALIDATE_CORE': 'true',
    'VK_KHRONOS_VALIDATION_VALIDATE_SYNC': 'true',
    'VK_KHRONOS_VALIDATION_SYNCVAL_SUBMIT_TIME_VALIDATION': 'true',
    'VK_KHRONOS_VALIDATION_GPUAV_ENABLE': 'false',
    'VK_KHRONOS_VALIDATION_DEBUG_ACTION': 'VK_DBG_LAYER_ACTION_LOG_MSG',
    'VK_KHRONOS_VALIDATION_LOG_FILENAME': 'stdout',
    'VK_KHRONOS_VALIDATION_REPORT_FLAGS': 'error,warn,info',
    'VK_KHRONOS_VALIDATION_ENABLE_MESSAGE_LIMIT': 'true',
    'VK_KHRONOS_VALIDATION_DUPLICATE_MESSAGE_LIMIT': '3',
}


def probe():
    """Record two conflicting fills, without submitting any work to the GPU."""
    u32, u64, ptr = c.c_uint32, c.c_uint64, c.c_void_p
    def structure(name, fields, header=True):
        return type(name, (c.Structure,), {'_fields_':
            ([('sType', u32), ('pNext', ptr)] if header else []) + fields})
    App = structure('App', [('name', c.c_char_p), ('version', u32),
                            ('engine', c.c_char_p), ('engineVersion', u32), ('api', u32)])
    InstanceInfo = structure('InstanceInfo', [('flags', u32), ('app', c.POINTER(App)),
        ('layerCount', u32), ('layers', ptr), ('extensionCount', u32), ('extensions', ptr)])
    Setting = structure('Setting', [('layer', c.c_char_p), ('name', c.c_char_p),
        ('type', u32), ('count', u32), ('values', ptr)], False)
    Settings = structure('Settings', [('count', u32), ('settings', c.POINTER(Setting))])
    QueueInfo = structure('QueueInfo', [('flags', u32), ('family', u32),
                                      ('count', u32), ('priority', c.POINTER(c.c_float))])
    DeviceInfo = structure('DeviceInfo', [('flags', u32), ('queueCount', u32),
        ('queues', c.POINTER(QueueInfo)), ('layerCount', u32), ('layers', ptr),
        ('extensionCount', u32), ('extensions', ptr), ('features', ptr)])
    BufferInfo = structure('BufferInfo', [('flags', u32), ('size', u64), ('usage', u32),
        ('sharing', u32), ('familyCount', u32), ('families', ptr)])
    Requirements = structure('Requirements', [('size', u64), ('alignment', u64),
                                              ('types', u32)], False)
    AllocateInfo = structure('AllocateInfo', [('size', u64), ('type', u32)])
    PoolInfo = structure('PoolInfo', [('flags', u32), ('family', u32)])
    CommandInfo = structure('CommandInfo', [('pool', ptr), ('level', u32), ('count', u32)])
    BeginInfo = structure('BeginInfo', [('flags', u32), ('inheritance', ptr)])
    QueueProperties = structure('QueueProperties', [('flags', u32), ('count', u32),
        ('timestampBits', u32), ('granularity', u32 * 3)], False)
    vk = c.CDLL('libvulkan.so.1')
    def call(name, types, *args, result=c.c_int32):
        fn = getattr(vk, name)
        fn.argtypes, fn.restype = types, result
        value = fn(*args)
        if result is c.c_int32 and value != 0:
            raise RuntimeError(name + ' returned ' + str(value))
    instance, device, buffer, memory, pool, command = (ptr() for _ in range(6))
    try:
        # Match the emulator's disabled setting; the process environment must override it.
        disabled = u32(0)
        setting = Setting(b'VK_LAYER_KHRONOS_validation', b'validate_sync', 0, 1,
                          c.cast(c.pointer(disabled), ptr))
        settings = Settings(1000496000, None, 1, c.pointer(setting))
        app = App(0, None, b'PES validation preflight', 1, b'canary', 1, (1 << 22) | (3 << 12))
        extensions = (c.c_char_p * 1)(b'VK_EXT_layer_settings')
        info = InstanceInfo(1, c.cast(c.pointer(settings), ptr), 0, c.pointer(app),
                            0, None, 1, c.cast(extensions, ptr))
        call('vkCreateInstance', [ptr, ptr, ptr], c.byref(info), None, c.byref(instance))
        count = u32()
        call('vkEnumeratePhysicalDevices', [ptr, ptr, ptr], instance, c.byref(count), None)
        if not count.value:
            raise RuntimeError('No Vulkan device available')
        devices = (ptr * count.value)()
        call('vkEnumeratePhysicalDevices', [ptr, ptr, ptr], instance, c.byref(count), devices)
        physical = devices[0]
        call('vkGetPhysicalDeviceQueueFamilyProperties', [ptr, ptr, ptr], physical,
             c.byref(count), None, result=None)
        queues = (QueueProperties * count.value)()
        call('vkGetPhysicalDeviceQueueFamilyProperties', [ptr, ptr, ptr], physical,
             c.byref(count), queues, result=None)
        family = next(i for i, queue in enumerate(queues) if queue.count and queue.flags & 7)
        priority = c.c_float(1)
        queue = QueueInfo(2, None, 0, family, 1, c.pointer(priority))
        info = DeviceInfo(3, None, 0, 1, c.pointer(queue), 0, None, 0, None, None)
        call('vkCreateDevice', [ptr, ptr, ptr, ptr], physical, c.byref(info), None, c.byref(device))
        info = BufferInfo(12, None, 0, 4096, 2, 0, 0, None)
        call('vkCreateBuffer', [ptr, ptr, ptr, ptr], device, c.byref(info), None, c.byref(buffer))
        requirements = Requirements()
        call('vkGetBufferMemoryRequirements', [ptr, ptr, ptr], device, buffer,
             c.byref(requirements), result=None)
        memory_type = (requirements.types & -requirements.types).bit_length() - 1
        info = AllocateInfo(5, None, requirements.size, memory_type)
        call('vkAllocateMemory', [ptr, ptr, ptr, ptr], device, c.byref(info), None, c.byref(memory))
        call('vkBindBufferMemory', [ptr, ptr, ptr, u64], device, buffer, memory, 0)
        info = PoolInfo(39, None, 0, family)
        call('vkCreateCommandPool', [ptr, ptr, ptr, ptr], device, c.byref(info), None, c.byref(pool))
        info = CommandInfo(40, None, pool, 0, 1)
        call('vkAllocateCommandBuffers', [ptr, ptr, ptr], device, c.byref(info), c.byref(command))
        info = BeginInfo(42, None, 1, None)
        call('vkBeginCommandBuffer', [ptr, ptr], command, c.byref(info))
        for value in (0, 1):
            call('vkCmdFillBuffer', [ptr, ptr, u64, u64, u32], command, buffer, 0, 4096,
                 value, result=None)
        call('vkEndCommandBuffer', [ptr], command)
    finally:
        for name, handle in [('vkDestroyCommandPool', pool), ('vkDestroyBuffer', buffer),
                             ('vkFreeMemory', memory)]:
            if device.value and handle.value:
                call(name, [ptr, ptr, ptr], device, handle, None, result=None)
        if device.value:
            call('vkDestroyDevice', [ptr, ptr], device, None, result=None)
        if instance.value:
            call('vkDestroyInstance', [ptr, ptr], instance, None, result=None)
    print('VULKAN_CANARY_RECORDED_NO_SUBMIT', flush=True)


def ensure_layer(work):
    class Layer(c.Structure):
        _fields_ = [('name', c.c_char * 256), ('spec', c.c_uint32),
                    ('version', c.c_uint32), ('description', c.c_char * 256)]
    loader = c.CDLL('libvulkan.so.1')
    enumerate_layers = loader.vkEnumerateInstanceLayerProperties
    enumerate_layers.argtypes = [c.POINTER(c.c_uint32), c.POINTER(Layer)]
    count = c.c_uint32()
    if enumerate_layers(c.byref(count), None) != 0:
        raise RuntimeError('Cannot enumerate Vulkan layers')
    layers = (Layer * count.value)()
    if enumerate_layers(c.byref(count), layers) != 0:
        raise RuntimeError('Vulkan layer list changed')
    minimum_version = (1 << 22) | (4 << 12) | 309
    if any(layer.name == b'VK_LAYER_KHRONOS_validation' and layer.spec >= minimum_version
           for layer in layers):
        return
    print('Downloading a private validation layer; system packages are unchanged.', flush=True)
    package = work / 'validation-package'
    package.mkdir()
    with (work / 'validation-package.log').open('w') as output:
        subprocess.run(['apt-get', 'download', 'vulkan-validationlayers'], cwd=package,
                       stdout=output, stderr=subprocess.STDOUT, timeout=120, check=True)
        archives = list(package.glob('vulkan-validationlayers_*_amd64.deb'))
        if len(archives) != 1:
            raise RuntimeError('Expected one amd64 Vulkan validation package')
        root = work / 'validation-runtime'
        subprocess.run(['dpkg-deb', '-x', str(archives[0]), str(root)], stdout=output,
                       stderr=subprocess.STDOUT, timeout=30, check=True)
    manifest = root / 'usr/share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json'
    data = json.loads(manifest.read_text())
    libraries = list(root.rglob('libVkLayer_khronos_validation.so'))
    if len(libraries) != 1:
        raise RuntimeError('Validation package library missing or ambiguous')
    data['layer']['library_path'] = str(libraries[0])
    manifest.write_text(json.dumps(data))
    ENVIRONMENT['VK_ADD_LAYER_PATH'] = str(manifest.parent)


def preflight(work):
    ensure_layer(work)
    env = dict(os.environ, **ENVIRONMENT)
    path = work / 'validation-canary.log'
    with path.open('w') as output:
        result = subprocess.run([sys.executable, __file__, '--probe'], env=env,
                                stdout=output, stderr=subprocess.STDOUT, timeout=45,
                                start_new_session=True)
    text = path.read_text(errors='replace')
    if (result.returncode or 'SYNC-HAZARD-WRITE-AFTER-WRITE' not in text or
            'VULKAN_CANARY_RECORDED_NO_SUBMIT' not in text):
        print(text[-5000:], flush=True)
        raise RuntimeError('Vulkan validation preflight failed; PES was not launched. Log: ' + str(path))
    print('PES_VULKAN_PREFLIGHT=PASS; synchronization error detected in unsubmitted canary', flush=True)
    return path


def analyze(paths):
    counts = {}
    active_sync = False
    for path in paths:
        for line in path.read_text(errors='replace').splitlines():
            active_sync |= 'VK_VALIDATION_FEATURE_ENABLE_SYNCHRONIZATION_VALIDATION' in line
            for name in re.findall(r'\b(?:VUID-[\w-]+|SYNC-HAZARD-[\w-]+)\b', line):
                counts[name] = counts.get(name, 0) + 1
    return {'synchronization_enabled_in_log': active_sync, 'message_counts': counts}


if __name__ == '__main__':
    if sys.argv[1:] != ['--probe']:
        raise SystemExit('This helper is invoked by run_pes_vulkan_test.py')
    probe()
