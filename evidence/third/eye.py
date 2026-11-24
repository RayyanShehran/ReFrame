"""Roll by eye: 4 frames per truth shot with horizontal guide lines. Writes frames/eye-N.png.

Usage: python eye.py   (shots from truth.json; also: python eye.py A B -> every 4th frame A..B)
"""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
import sheets

HERE = Path(__file__).parent
W, H = 288, 216


def tile(frame, label):
    im = Image.fromarray(frame).resize((W, H))
    d = ImageDraw.Draw(im)
    for y in range(0, H, 36):
        d.line([(0, y), (W, y)], fill=(0, 255, 0))
    d.rectangle([0, 0, 110, 12], fill="white")
    d.text((2, 0), label, fill="black")
    return im


def rows(frames, groups, path):
    cols = max(len(g) for g in groups)
    out = Image.new("RGB", (cols * W, len(groups) * H), "white")
    for r, g in enumerate(groups):
        for c, (i, label) in enumerate(g):
            out.paste(tile(frames[i], label), (c * W, r * H))
    out.save(path)


if __name__ == "__main__":
    sheets.W, sheets.H = W, H
    frames = sheets.decode(30)
    if len(sys.argv) == 3:
        a, b = int(sys.argv[1]), int(sys.argv[2])
        idx = list(range(a, b + 1, 4))
        rows(frames, [[(i, str(i)) for i in idx[k:k + 6]] for k in range(0, len(idx), 6)],
             HERE / "frames" / f"eye-{a}-{b}.png")
    else:
        truth = json.load(open(HERE / "truth.json"))
        edges = [0, *truth["cuts"], len(frames)]
        groups = []
        for n, (a, b) in enumerate(zip(edges, edges[1:])):
            picks = sorted({a + 2, a + (b - a) // 3, a + 2 * (b - a) // 3, b - 3})
            groups.append([(min(max(i, a), b - 1), f"shot {n} f{i}") for i in picks])
        for k in range(0, len(groups), 7):
            rows(frames, groups[k:k + 7], HERE / "frames" / f"eye-shots-{k // 7 + 1}.png")
