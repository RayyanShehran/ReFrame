"""First two references after regenerating on the branch vs the main state saved before
(regress/before-<ref>.json): pacing version and cut sources, slot cuts vs truth, slot boundaries,
camera motion shots/curves/rolls unchanged. Writes regress/checks.txt (never reads real data/).
"""
import json, sqlite3
from pathlib import Path

HERE = Path(__file__).parent
EV = HERE.parent
db = sqlite3.connect("file:" + str(EV.parent / "data" / "reframe.sqlite3") + "?mode=ro", uri=True)
assert "ReFrame-e2e" in str(EV)
out = []
for name, pid in (("handwritten", "3747d745-e997-4ddb-bcee-caaed59a8442"),
                  ("printed", "8c8d8a34-9add-4429-8ba4-68613507a6e2")):
    bp = json.loads(db.execute("SELECT blueprint FROM pacing_operations WHERE project_id=?", (pid,)).fetchone()[0])
    seq = json.loads(db.execute("SELECT sequence FROM sequences WHERE project_id=?", (pid,)).fetchone()[0])
    motion = json.loads(db.execute("SELECT settings FROM camera_motion_settings WHERE project_id=?", (pid,)).fetchone()[0])
    before = json.load(open(HERE / "regress" / f"before-{name}.json"))
    before_motion = json.loads(before["camera_motion"]["settings"])
    truth = json.load(open(EV / "shots" / f"truth-{name}.json"))["cuts"]
    cuts = [s["output_start_frame"] for s in seq["slots"][1:]]
    before_cuts = [s["output_start_frame"] for s in before["sequence"]["slots"][1:]]
    key = lambda m: [(a["start_frame"], a["end_frame"], a["keyframes"], a.get("shake_x"), a.get("shake_y"),
                      a.get("shake_angle"), a.get("moves")) for a in m["shots"]]
    rolls = lambda m: [(a["start_frame"], round(max(abs(k["angle"]) for k in a["keyframes"]) * 57.2958, 2))
                       for a in m["shots"] if any(k["angle"] for k in a["keyframes"])]
    out.append(f"{name}: pacing {bp['algorithm_version']} detector {bp['cut_detector']}: "
               f"{len(bp['scdet_cut_timestamps'])} scdet + {bp['estimated_cut_count'] - len(bp['scdet_cut_timestamps'])} "
               f"detector cuts; timings {bp['timings']}")
    out.append(f"  slots {len(seq['slots'])} (main {len(before['sequence']['slots'])}); slot cuts == truth: "
               f"{cuts == truth} ({sum(c in truth for c in cuts)}/{len(truth)}); slot cuts == main: {cuts == before_cuts}; "
               f"music {seq['music']}")
    out.append(f"  camera motion shots/curves/shakes/moves identical to main: {key(motion) == key(before_motion)} "
               f"({len(motion['shots'])} shots); rolls {rolls(motion)} (main {rolls(before_motion)})")
(HERE / "regress" / "checks.txt").write_text("\n".join(out) + "\n")
print("\n".join(out))
