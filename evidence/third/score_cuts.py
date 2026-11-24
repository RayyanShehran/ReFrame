"""Cut precision/recall of the app's pacing cuts and sequence slot boundaries against truth.json.

Matching is one-to-one: a found cut matches a truth cut within 2 frames, or anywhere from the
cut to new_shot_from + 2 for flash transitions. Found cuts within 2 frames of an uncertain frame
are ignored. Usage: python score_cuts.py <project-id> <out.json>
"""
import json, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
API = "http://127.0.0.1:8000/api/projects/"
truth = json.loads((HERE / "truth.json").read_text("utf-8"))
flash = {int(k): v["new_shot_from"] for k, v in truth["flash_transitions"].items()}
uncertain = [int(k) for k in truth["uncertain"]]


def window(c):
    return c - 2, flash.get(c, c) + 2


def score(found):
    found = sorted(found)
    ignored = [f for f in found if any(abs(f - u) <= 2 for u in uncertain)]
    pool = [f for f in found if f not in ignored]
    matched, used = [], set()
    for t in truth["cuts"]:
        lo, hi = window(t)
        options = [f for f in pool if lo <= f <= hi and f not in used]
        if options:
            f = min(options, key=lambda f: abs(f - t))
            used.add(f)
            matched.append((t, f))
    false = [f for f in pool if f not in used]
    return {
        "found": len(found), "ignored_uncertain": ignored, "matched": len(matched),
        "precision": round(len(matched) / len(pool), 3) if pool else None,
        "recall": round(len(matched) / len(truth["cuts"]), 3),
        "missed": sorted(set(truth["cuts"]) - {t for t, _ in matched}), "false": false,
        "offsets": {t: f - t for t, f in matched if f != t},
    }


get = lambda path: json.load(urllib.request.urlopen(API + sys.argv[1] + path, timeout=30))
b = get("/pacing")["blueprint"]
cuts = lambda times: [round(t * 30) for t in times]
result = {
    "pacing_all": score(cuts(b["candidate_cut_timestamps"])),
    "pacing_scdet_only": score(cuts(b["scdet_cut_timestamps"])),
    "detector_frames": b["detected_cut_frames"],
    "rhythm": b["rhythm"] and {k: v for k, v in b["rhythm"].items() if k != "beats"},
    "pacing_timings": b.get("timings"),
}
seq = get("/sequence")["sequence"]
slots = [s["output_start_frame"] for s in seq["slots"][1:]]
result["slots"] = score(slots)
result["slot_count"] = len(seq["slots"])
result["music"] = seq.get("music")
result["output_frames"] = seq["output_frames"]
Path(sys.argv[2]).write_text(json.dumps(result, indent=1))
for k in ("pacing_scdet_only", "pacing_all", "slots"):
    r = result[k]
    print(f"{k:18s} found {r['found']:2d} matched {r['matched']:2d}/{len(truth['cuts'])} "
          f"P {r['precision']} R {r['recall']} missed {r['missed']} false {r['false']} "
          f"ignored {r['ignored_uncertain']} offsets {r['offsets']}")
print("slots", result["slot_count"], "music", result["music"], "rhythm", result["rhythm"])
