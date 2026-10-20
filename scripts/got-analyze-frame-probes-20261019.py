#!/usr/bin/env python3
import collections
import csv
import math
from pathlib import Path
import re
import struct
import sys


def fields(line):
    return dict(re.findall(r"([a-zA-Z_][a-zA-Z_0-9]*)=([^\s]+)", line))


def analyze(run):
    info = run / "info"
    info.mkdir(exist_ok=True)
    draws, queries, ends = {}, {}, {}
    for line in (run / "game.log").read_text(errors="replace").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if "FrameProbeDraw:" in line:
            row = fields(line)
            draws[(row["frame"], row["draw"])] = row
        elif "FrameProbeQuery:" in line:
            row = fields(line)
            queries[(row["frame"], row["draw"])] = row
        elif "FrameProbeEnd:" in line:
            row = fields(line)
            ends[row["frame"]] = row
    groups = collections.defaultdict(collections.Counter)
    for key, draw in draws.items():
        query = queries.get(key, {})
        group = groups[(draw["frame"], draw["fs"], draw["vs"],
                        draw["color0"], draw["depth"], draw["indirect"])]
        group["submitted"] += 1
        valid = query.get("stats_status") == "Success"
        group["stats_complete"] += valid
        group["occlusion_complete"] += query.get("occlusion_status") == "Success"
        if valid:
            for name in ("ia", "vs_calls", "clip_in", "clip_out", "fs_calls"):
                group[name] += int(query[name])
            group["draws_with_vertices"] += int(query["ia"]) > 0
        if query.get("occlusion_status") == "Success":
            group["draws_with_samples"] += int(query["samples"]) > 0
    columns = ["frame", "fs", "vs", "color0", "depth", "indirect", "submitted",
               "stats_complete", "occlusion_complete", "draws_with_vertices",
               "ia", "vs_calls", "clip_in", "clip_out", "fs_calls", "draws_with_samples"]
    with (info / "frame-pipeline-census.csv").open("w") as output:
        writer = csv.writer(output)
        writer.writerow(columns)
        for key, group in sorted(groups.items()):
            writer.writerow([*key, *(group[name] for name in columns[6:])])
    summary = []
    for frame in sorted(set(ends) | {k[0] for k in draws}, key=int):
        captured = sum(k[0] == frame for k in draws)
        complete = sum(k[0] == frame and q.get("stats_status") == "Success"
                       for k, q in queries.items())
        summary.append(f"frame={frame} submitted={ends.get(frame, {}).get('draws', 'UNKNOWN')} "
                       f"selected={captured} stats_complete={complete} "
                       f"coverage={ends.get(frame, {}).get('coverage', 'FRAME_NOT_ENDED')}")
    previews = info / "frame-previews"
    previews.mkdir(exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        Image = None
    for raw in sorted((run / "profile/user/shader/frame-probes").glob("*.raw")):
        meta = dict(line.split("=", 1) for line in Path(str(raw) + ".txt").read_text().splitlines())
        width, rows, fmt = (int(meta[name]) for name in ("width", "rows", "format"))
        data = raw.read_bytes()
        if fmt in (37, 43, 44, 50):
            pixels = [tuple(data[i:i + 3]) for i in range(0, len(data), 4)]
            if fmt in (44, 50):
                pixels = [p[::-1] for p in pixels]
        elif fmt == 16:
            pixels = [(data[i], data[i + 1], 0) for i in range(0, len(data), 2)]
        elif fmt == 91:
            pixels = [tuple(round(v * 255 / 65535) for v in texel[:3])
                      for texel in struct.iter_unpack("<4H", data)]
        elif fmt in (100, 126, 130):
            values = [x[0] for x in struct.iter_unpack("<f", data)]
            finite = [x for x in values if math.isfinite(x)]
            top = max(finite, default=0)
            pixels = [(max(0, min(255, round(x / top * 255))),) * 3
                      if math.isfinite(x) and top > 0 else (0, 0, 0) for x in values]
            summary.append(f"{raw.name} min={min(finite, default=0)} max={top} "
                           f"nonzero={sum(x != 0 for x in finite)}/{len(values)} depth_scaled_to_max=YES")
        elif fmt == 97:
            def display(x):
                return round((max(0, x) / (1 + max(0, x))) ** (1 / 2.2) * 255) if math.isfinite(x) else 255
            pixels = [tuple(display(x) for x in p[:3]) for p in struct.iter_unpack("<4e", data)]
        else:
            continue
        if len(pixels) != width * rows:
            summary.append(f"{raw.name} INVALID_BYTE_COUNT")
            continue
        rgb = bytes(c for p in pixels for c in p)
        if Image:
            picture = Image.frombytes("RGB", (width, rows), rgb)
            picture.resize((640, max(1, int(meta["height"]) * 640 // width))).save(
                previews / (raw.stem + ".png"))
        else:
            (previews / (raw.stem + ".ppm")).write_bytes(
                f"P6\n{width} {rows}\n255\n".encode() + rgb)
    if not draws:
        summary.append("NO_FRAME_PROBE_DRAWS: capture did not reach a checkpoint or probe was disabled")
    (info / "frame-history-summary.txt").write_text("\n".join(summary) + "\n")
    print("\n".join(summary))


if __name__ == "__main__":
    analyze(Path(sys.argv[1]))
