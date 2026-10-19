#!/usr/bin/env python3
"""Offline GoW Continue-gate regression against captured A and B observations.

Read only numeric auto-progress.json from user-provided archive. No screenshot
decode, X11 input, emulator launch, Ollama, game/save access or network.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tarfile

from gow_auto_scene_input_20261018 import real_continue_menu, ContinueGate


def replay(records):
    gate = ContinueGate()
    first = None
    transitions = []
    for row in records:
        passed = real_continue_menu(
            logo=row["logo"], selected_continue=row["highlighted_continue"],
            blue=row["blue"], warm=row["warm"], texture=row["texture"])
        if passed:
            transitions.append(round(row["t"], 2))
        # After the first simulated Cross, later frames are counterfactual:
        # game state would differ, so do NOT replay further for predictions.
        if gate.observe(row["t"], passed):
            first = round(row["t"], 2)
            break
    return first, transitions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    expected = {
        "A_DENSE_CPU_UPLOAD": 37.24,
        "B_SPARSE_CPU_UPLOAD": 34.04,
    }
    with tarfile.open(args.archive, "r:gz") as tf:
        names = tf.getnames()
        for mode, expected_cross in expected.items():
            match = [
                n for n in names if n.endswith(
                    f"/{mode}/auto-progress.json")]
            if len(match) != 1:
                raise RuntimeError(f"MISSING_OR_DUPLICATE_ARCHIVED_PROGRESS={mode}")
            member = tf.getmember(match[0])
            if not member.isfile() or member.size > 2 * 2**20:
                raise RuntimeError(f"PROGRESS_FILE_TOO_LARGE_OR_UNSAFE={mode}")
            obj = json.load(tf.extractfile(member))
            obs = obj["observations"]
            if len(obs) < 15:
                raise RuntimeError(f"TOO_FEW_REAL_OBSERVATIONS={mode}")
            first, accepted = replay(obs)
            if first is None or abs(first - expected_cross) > .05:
                raise AssertionError(
                    f"{mode}: first_trigger={first}, expected={expected_cross}")
            premature = [r for r in obs
                         if 17.5 <= r["t"] <= 22.1
                         and real_continue_menu(
                             logo=r["logo"],
                             selected_continue=r["highlighted_continue"],
                             blue=r["blue"], warm=r["warm"],
                             texture=r["texture"])]
            if premature:
                raise AssertionError(f"FALSE_EARLY_CROSS={mode}:{premature}")
            if mode.startswith("A_"):
                midgame_false = [r for r in obs
                                 if 162 <= r["t"] <= 167
                                 and real_continue_menu(
                                     logo=r["logo"],
                                     selected_continue=r["highlighted_continue"],
                                     blue=r["blue"], warm=r["warm"],
                                     texture=r["texture"])]
                if midgame_false:
                    raise AssertionError("IN_GAME_FALSE_CONTINUE_DETECTED")
            print(f"OFFLINE_REPLAY={mode} FIRST_REAL_CONTINUE_AT={first:.2f}s "
                  "PREMATURE_SPLASH_REJECTED=YES")
    print("BOTH_PREVIOUS_RUNS_REAL_MENU_IDENTIFICATION=PASS")
    print("NO_EMULATOR_LAUNCH_NO_CONTROLLER_INPUT_NO_SCREENSHOT_COMPARISON=PASS")


if __name__ == "__main__":
    main()
