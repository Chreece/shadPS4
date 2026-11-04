# Quit prompt input regression

The runtime report `shadps4-graphics-evidence-2v4gac6j` verified emulator revision
`326e0b82f951624aa1a266f18ecbc054f6604cb9`. It registered controller IDs 5 and 11
in slots 0 and 1. The user reported that the quit notification was visible but
Cross sometimes affected the game instead of confirming quit.

The old Guide shortcut accepted any controller. Confirmation was read during
ImGui drawing, while its SDL backend selected controller slot 0. The notification
used `NoNav`, and ordinary gamepad event capture depended on `io.NavActive`.
Opening the notification did not acquire the guest-pad interception already used
by the on-screen keyboard. These are code defects consistent with the report;
the old logs did not identify the controller that pressed Cross.

The quit controller now handles SDL events before guest keyboard/mouse dispatch
and before ImGui focus filtering. It accepts fresh Guide, Cross/A and Circle/B
presses from any controller ID. Enter/keypad Enter and Escape also use this path.
Confirmation posts the existing SDL quit event. Cancellation consumes both edges;
pad reads remain intercepted until the closing button/key is released. Normal
physical state updates continue while guest pad reads are neutral/intercepted.
The renderer reads atomic visibility and keeps drawing while the prompt is open.
No renderer, audio, guest ABI, settings or host services are changed.

## Focused checks

```sh
cmake -S tests/quit_dialog -B build-quit-dialog -G Ninja
cmake --build build-quit-dialog
ctest --test-dir build-quit-dialog --output-on-failure
```

The fixture uses the actual pinned SDL event definitions, production quit state
controller, and extracted production event-dispatch prefix, quit actions and
pad-capture predicate. SDL queuing, the downstream input consumers and logging
are doubles. It needs no display, Vulkan driver, SDL library or game dump.
Use `-DSDL_HEADERS_PATH=/path/to/SDL/include` if the submodule is elsewhere.

Six cases cover a second controller, a visible prompt without navigation focus,
paired cancellation and subsequent gameplay input, a held confirmation button,
keyboard repeat, controller replacement, and independent OSK interception.
The unfocused-confirmation case fails with the old event-dispatch prefix because
the confirmation reaches the guest consumer. All six pass with the new prefix,
also with UndefinedBehaviorSanitizer. The local Docker installer runs this suite
before compiling and selecting the emulator.

## Runtime acceptance remains pending

On the exact new build, open with Home/PS, cancel with Circle/B, verify ordinary
controls resume, open again, and confirm with Cross/A. Repeat after a controller
reconnect if that is part of the reported failure. The log must contain
`HOST_QUIT action=opened`, the controller ID/button with `consumed=1`,
`HOST_QUIT action=confirmed`, and `HOST_QUIT action=accepted`.
A queue failure is logged explicitly. User confirmation that Cross did not
also act in the game is required; these CPU tests cannot establish that.

The uploaded graphics report recorded two initialized five-to-six-layer depth
conversions and no renderer error records. Vulkan validation was disabled. Its
missing exit record makes the capture partial. Those facts establish the exercised
initialization path, not visual correctness or a verified clean process exit.
