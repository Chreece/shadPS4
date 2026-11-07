# ES-DE VRAM / Ollama session guard

This is a **host-side launcher guard**, not an emulator or Sunshine modification.
It reserves **8 GiB of free AMD GPU VRAM** before ES-DE starts by temporarily
unloading **only enough GPU-resident Ollama models**. It restores only the models
it actually unloaded after the ES-DE AppImage terminates.

## Install / integration

Evidence from the 2026-11-07 Sunshine system journal shows that Sunshine directly executes
`~/apps/es/extracted/squashfs-root/usr/bin/es-de`. The previous wrapper at
`~/apps/es/ES-DE_x64.AppImage` was bypassed and therefore did not run.

Install `scripts/esde-ollama-vram-guard.py` at:
`~/projects/shadps4/scripts/esde-ollama-vram-guard.py`.

Then run:

```bash
python3 ~/projects/shadps4/scripts/esde-ollama-vram-guard.py install
python3 ~/projects/shadps4/scripts/esde-ollama-vram-guard.py status
```

The installer now expects the real executable at
`~/apps/es/extracted/squashfs-root/usr/bin/es-de`.
It **retains the original ELF unmodified** at
`~/apps/es/extracted/squashfs-root/usr/bin/es-de.real` and installs a tiny shell wrapper at
the original path. Existing launchers continue using their existing command
line; all arguments are forwarded unchanged. It does not touch the installed
shadPS4 core, `~/.local/bin/shadps4-esde`, Sunshine, or the Ollama service.

The installer is idempotent and refuses to overwrite preexisting `.real`
files or replace symlinked/non-ELF AppImages. To revert the wrapper:

```bash
python3 ~/projects/shadps4/scripts/esde-ollama-vram-guard.py uninstall
```

This restores the original ES-DE AppImage filename.

## Session behavior

1. Read `mem_info_vram_total` and `mem_info_vram_used` from the AMD GPU
   with the largest total VRAM, via Linux DRM sysfs.
2. If free VRAM is at least 8 GiB, start ES-DE immediately without touching Ollama.
3. Otherwise ask the IPv4 loopback Ollama API (`http://127.0.0.1:11434/api/ps`)
   for resident models. Unload GPU-resident models via `POST /api/generate`,
   empty prompt, and `keep_alive: 0`, largest GPU allocation first. Stop
   unloading when at least 8 GiB is available or no candidates remain.
4. Keep Ollama server running. Start and wait for the **original** ES-DE AppImage.
   When ES-DE terminates, preload only the models unloaded by this session,
   with their prior long-lived keep-alive where possible.
5. Write per-session JSONL events to `~/projects/shadps4/logs/esde-vram-*.jsonl`.

If VRAM cannot be measured, or it remains below 8 GiB after trying Ollama,
the launcher **blocks ES-DE before starting the GPU workload** (exit 75),
records `BLOCKED_ESDE_TO_PROTECT_SUNSHINE`, and restores models this session
unloaded. Other GPU consumers may prevent the threshold being reached.
Sunshine remains running; this is a deliberate fail-closed safeguard.
During the session, the guard samples GPU free VRAM and Sunshine's read-only
file descriptor count/limits every five seconds in `SESSION_RESOURCE_SAMPLE`.
This adds evidence for `VK_ERROR_OUT_OF_DEVICE_MEMORY` and `Too many open files`
without touching Sunshine's process, services, audio, or configuration.
This is a **startup** reservation, not continuous memory policing.
Normal exits and handled termination signals trigger restoration; forced
SIGKILL, system power loss, or an unavailable Ollama server cannot guarantee
restoration.

## Configuration

Optional environment overrides:

- `ESDE_GUARD_MIN_FREE_GIB` — default `8`.
- `ESDE_GUARD_OLLAMA_API` — default IPv4 `http://127.0.0.1:11434`.
- `ESDE_GUARD_APPIMAGE` — default `~/apps/es/extracted/squashfs-root/usr/bin/es-de`.
- `ESDE_GUARD_PROJECT` — default `~/projects/shadps4`.
- `ESDE_GUARD_DRM_ROOT` — default `/sys/class/drm`, also used for offline testing.

Run the offline suite (does not require a GPU, Ollama or ES-DE):

```bash
python3 -m unittest discover -s scripts -p 'test_esde_ollama_vram_guard.py' -v
```
