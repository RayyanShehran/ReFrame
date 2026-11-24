"""Contact sheets / frame strips of the third reference on the truth grid (setpts=PTS-STARTPTS,fps=30).

Usage: python sheets.py contact            -> contact/sheet-NN.png, every 3rd frame, 8x6 per sheet
       python sheets.py strip A B [fps]    -> frames/strip-A-B.png, every frame A..B (fps 30 or 60)
"""
import subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
MEDIA = HERE / "media" / "reference.mp4"
W, H = 192, 144  # 4:3 thumbnails


def decode(fps):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(MEDIA), "-vf",
                          f"setpts=PTS-STARTPTS,fps={fps},scale={W}:{H}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def sheet(frames, indices, cols, path, fps):
    rows = -(-len(indices) // cols)
    img = Image.new("RGB", (cols * W, rows * (H + 14)), "white")
    d = ImageDraw.Draw(img)
    for k, i in enumerate(indices):
        x, y = k % cols * W, k // cols * (H + 14)
        img.paste(Image.fromarray(frames[i]), (x, y + 14))
        d.text((x + 2, y + 1), f"{i}" + (f" ({i / fps:.3f}s)" if fps != 30 else ""), fill="black")
    img.save(path)


if __name__ == "__main__":
    if sys.argv[1] == "contact":
        frames = decode(30)
        print("frames_30fps", len(frames))
        (HERE / "contact").mkdir(exist_ok=True)
        idx = list(range(0, len(frames), 3))
        for n, s in enumerate(range(0, len(idx), 48)):
            sheet(frames, idx[s:s + 48], 8, HERE / "contact" / f"sheet-{n + 1:02d}.png", 30)
    else:
        a, b = int(sys.argv[2]), int(sys.argv[3])
        fps = int(sys.argv[4]) if len(sys.argv) > 4 else 30
        frames = decode(fps)
        (HERE / "frames").mkdir(exist_ok=True)
        sheet(frames, list(range(a, min(b + 1, len(frames)))), 8,
              HERE / "frames" / f"strip-{a}-{b}{'' if fps == 30 else f'-{fps}fps'}.png", fps)
