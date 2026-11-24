"""Writes captions-truth.json from the hand-read every-frame caption bands (frames/capband-*.png).

Each phase is (first frame, first frame after, displayed text) on the 30 fps decode.
"""
import json
from pathlib import Path

P = [
    (6, 12, "I'm"), (12, 23, "I'm always"), (23, 31, "I'm always gonna"),
    (31, 37, "I'm always gonna love"), (37, 64, "I'm always gonna love you."),
    (114, 121, "Always"), (121, 127, "Always gonna"), (127, 134, "Always gonna love"),
    (134, 139, "Always gonna love you"), (139, 185, "Always gonna love you too."),
    (185, 196, "May"), (196, 205, "Maybe"), (205, 212, "Maybe we"), (212, 217, "Maybe we get"),
    (217, 232, "Maybe we get married"), (232, 242, "Maybe we get married one"),
    (242, 281, "Maybe we get married one day"),
    (281, 288, "but"), (288, 297, "but who"), (297, 358, "but who knows"),
    (423, 431, "Think"), (431, 438, "Think i'll"), (438, 444, "Think i'll take"),
    (444, 454, "Think i'll take that"), (454, 461, "Think i'll take that thought"),
    (461, 467, "to"), (467, 474, "to the"), (474, 513, "to the grave"),
    (520, 526, "but"), (526, 535, "but who"), (535, 660, "but who knows"),
    (661, 666, "I"), (666, 676, "I know"), (676, 682, "I know that"), (682, 688, "I know that I'll"),
    (688, 699, "I know that I'll love"), (699, 707, "I know that I'll love you"),
    (707, 751, "I know that I'll love you always"),
    (751, 761, "yeah"), (761, 776, "yeah girl"), (776, 809, "yeah girl you"),
    (809, 821, "and"), (821, 833, "and I"), (833, 847, "and I like"), (847, 899, "and I like that"),
]
out = {
    "reference": "third",
    "frame_rate": 30,
    "method": "Read frame by frame from caption-band crops of the 30 fps decode (capsheet.py). "
              "Word-by-word reveal; each phase is one displayed text. No captions after frame 899.",
    "styles": {
        "0-64": "yellow film subtitle, bottom centre",
        "114-185": "dark blue subtitle, bottom centre, low contrast over a pale sky (hard to read)",
        "185-899": "white sans lyric, screen centre",
    },
    "uncertain": [
        "520-535: white text over the bright projector light; the first word is partly hidden.",
        "617-622: 'but who knows' is washed out by the white projector flash; labelled as continuous.",
        "357: 'but who knows' is still visible in the first light-leak frame, gone from 358.",
    ],
    "phases": [{"start_frame": a, "end_frame": b, "start": round(a / 30, 4), "end": round(b / 30, 4),
                "text": t} for a, b, t in P],
}
Path(__file__).with_name("captions-truth.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), "utf-8")
print(len(P), "phases;", sum(b - a for a, b, _ in P) / 30, "s captioned")
