# Mixed-component fragment interpolation

OpenOrbis offscreen graphics probe. The vertex shader exports a smooth position in PARAM0.x and raw integer bits `2` in PARAM0.y. The fragment shader reads y with V_INTERP_MOV P0, then x with V_INTERP_P1/P2, and stores both through a buffer descriptor at its pixel coordinate. The 64x64 triangle covers the viewport. Interior pixels must preserve integer 2 and the expected horizontal gradient; output-adjacent sentinels must remain unchanged. This detects attribute-wide interpolation metadata overwriting a raw component.

Set OO_PS4_TOOLCHAIN, run make, and package eboot.bin using an OpenOrbis template. Read /data/component-interpolation-probe.txt; a successful run ends in RESULT=PASS. On submission timeout the process retains mapped GPU memory. Shader sources assemble with llvm-mc for Bonaire; regenerate headers with `python3 make_shader.py llvm-mc`.

The shaders have been assembled, but this application has not been linked with the OpenOrbis SDK or executed on PS4. Hardware behavior and register setup remain unverified. This is a hardware probe source, not a claimed hardware result. No game assets or shader hashes are required.
