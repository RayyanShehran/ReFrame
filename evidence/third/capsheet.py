"""Every-frame caption-band crops (bottom band before frame 185, centre band after), 4 per row,
200 frames per sheet -> frames/capband-NN.png. Labelling aid for caption truth."""
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import sheets

HERE = Path(__file__).parent
sheets.W, sheets.H = 720, 540
frames = sheets.decode(30)
CW, CH = 480, 44
for s in range(0, len(frames), 200):
    idx = range(s, min(s + 200, len(frames)))
    out = Image.new("RGB", (4 * CW, 50 * CH), "white")
    for k, i in enumerate(idx):
        y = 492 if i < 185 else 244
        im = Image.fromarray(frames[i, y:y + CH, 120:120 + CW])
        d = ImageDraw.Draw(im); d.rectangle([0, 0, 30, 11], fill="white"); d.text((1, 0), str(i), fill="red")
        out.paste(im, (k % 4 * CW, k // 4 * CH))
    out.save(HERE / "frames" / f"capband-{s // 200 + 1:02d}.png")
