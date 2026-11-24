"""Score a cue file against captions-truth.json with the PR #3 half-open word-second scorer
(../score_captions.py, same metrics and output; only the phases are this reference's).

Usage: python score.py cues.json [label]   (cues: list of {start, end, text} in reference seconds)
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import score_captions as s  # noqa: E402

truth = json.loads((HERE / "captions-truth.json").read_text("utf-8"))
s.PHASES = [(p["start"], p["end"], p["text"]) for p in truth["phases"]]
s.MANUAL = (-2.0, -1.0)  # Nothing excluded on this reference.
source = Path(s.__file__).read_text("utf-8")
exec(source.split('if __name__ == "__main__":', 1)[1].replace("\n    ", "\n"), vars(s))
