"""Cues of a project's latest export (render spec), as {start, end, text} UTF-8 JSON.

Usage: python export_cues.py <project-id> <out.json>
"""
import json, sys, urllib.request
from pathlib import Path

spec = json.load(urllib.request.urlopen(
    f"http://127.0.0.1:8000/api/projects/{sys.argv[1]}/render", timeout=30))["output"]["spec"]
cues = [{"start": c["start"], "end": c["end"], "text": c["text"]}
        for c in spec["captions"]["cues"]] if spec["captions"]["enabled"] else []
Path(sys.argv[2]).write_text(json.dumps(cues, indent=1, ensure_ascii=False), "utf-8")
for c in cues:
    print(f"{c['start']:7.3f}-{c['end']:7.3f} {c['text']!r}")
