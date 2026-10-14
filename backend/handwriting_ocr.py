"""Opt-in, pinned local handwriting experiment. No runtime network or language guessing."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".tools" / "handwriting"
MODEL = ROOT / ".tools" / "handwriting-base-model"
MANIFEST = json.loads(
    Path(__file__).with_name("handwriting_assets.json").read_text(encoding="utf-8")
)
MAX_LINES = 16


def executable():
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def available():
    return (
        MANIFEST.get("license_status") == "permitted"
        and executable().is_file()
        and all((MODEL / name).is_file() for name in MANIFEST["files"])
    )


def verify():
    for name, expected in MANIFEST["files"].items():
        path = MODEL / name
        if path.stat().st_size != expected["bytes"]:
            raise ValueError("Handwriting asset size changed. Run explicit setup again.")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected["sha256"]:
                raise ValueError("Handwriting asset hash changed. Run explicit setup again.")


def setup(download):
    if not download:
        raise ValueError(
            "Use setup --download to explicitly install 1.34 GB of MIT-labelled model assets "
            "and CPU dependencies (about 300 MB of wheels). No media is uploaded."
        )
    if MANIFEST.get("license_status") != "permitted":
        raise ValueError(
            "Checkpoint weight permissions remain unresolved. Setup/download and automatic "
            "selection are disabled; installed files and saved captions are preserved."
        )
    if not executable().is_file():
        venv.EnvBuilder(with_pip=True).create(ENV)
    subprocess.run(
        [
            str(executable()),
            "-m",
            "pip",
            "install",
            "torch==2.7.1",
            "torchvision==0.22.1",
            "--index-url",
            "https://download.pytorch.org/whl/cpu",
        ],
        check=True,
    )
    subprocess.run(
        [
            str(executable()),
            "-m",
            "pip",
            "install",
            "transformers==4.57.6",
            "sentencepiece==0.2.1",
            "protobuf==6.33.5",
        ],
        check=True,
    )
    MODEL.mkdir(parents=True, exist_ok=True)
    for name, expected in MANIFEST["files"].items():
        target = MODEL / name
        if target.is_file() and target.stat().st_size == expected["bytes"]:
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == expected["sha256"]:
                    continue
        temporary = target.with_suffix(target.suffix + ".partial")
        try:
            url = (
                f"https://huggingface.co/{MANIFEST['model']}/resolve/{MANIFEST['revision']}/{name}"
            )
            with (
                urllib.request.urlopen(url, timeout=60) as response,
                temporary.open("wb") as output,
            ):
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > expected["bytes"]:
                        raise ValueError("Handwriting asset exceeds its declared size.")
                    output.write(chunk)
            with temporary.open("rb") as stream:
                if (
                    total != expected["bytes"]
                    or hashlib.file_digest(stream, "sha256").hexdigest() != expected["sha256"]
                ):
                    raise ValueError("Handwriting asset verification failed.")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    verify()


def line_crops(image, mask, box):
    """Geometry comes from pixels, not a vocabulary or reference-specific coordinates."""
    from PIL import ImageOps

    foreground = ImageOps.invert(mask)
    bands, start, last = [], None, None
    for y in range(foreground.height):
        if foreground.crop((0, y, foreground.width, y + 1)).histogram()[255] >= 5:
            if start is None:
                start = y
            last = y
        elif start is not None and y - last > 4:
            bands.append((start, last + 1))
            start = last = None
    if start is not None:
        bands.append((start, last + 1))
    result = []
    for top, bottom in bands:
        if bottom - top < 0.015 * image.height:
            continue
        bounds = foreground.crop((0, top, foreground.width, bottom)).getbbox()
        region = foreground.crop((bounds[0], top, bounds[2], bottom))
        groups = []
        left = last = None
        for column in range(region.width):
            ink = region.crop((column, 0, column + 1, region.height)).histogram()[255]
            if ink >= max(2, region.height * 0.05):
                if left is None:
                    left = column
                last = column
            elif left is not None and column - last > region.height * 1.5:
                groups.append((left, last + 1))
                left = last = None
        if left is not None:
            groups.append((left, last + 1))
        if not groups:
            continue
        left, right = max(groups, key=lambda pair: pair[1] - pair[0])
        bounds = (bounds[0] + max(0, left - 4), top, min(bounds[2], bounds[0] + right + 4), bottom)
        content = foreground.crop(bounds).getbbox()
        x = box[0] + bounds[0] + content[0]
        right = box[0] + bounds[0] + content[2]
        y = box[1] + bounds[1] + content[1]
        end = box[1] + bounds[1] + content[3]
        top, bottom = y - box[1], end - box[1]
        bounds = (x - box[0], top, right - box[0], bottom)
        if (right - x) / (end - y) < 3:
            continue
        binary = mask.crop((bounds[0], max(0, top - 5), bounds[2], min(mask.height, bottom + 5)))
        gray = ImageOps.autocontrast(
            ImageOps.invert(
                image.crop((x, max(0, y - 5), right, min(image.height, end + 5))).convert("L")
            )
        )
        # Color masks can omit low-contrast edge glyphs; retain neighboring source pixels.
        padding = max(8, round((end - y) / 2))
        context = ImageOps.autocontrast(
            ImageOps.invert(
                image.crop(
                    (
                        max(0, x - padding),
                        max(0, y - 8),
                        min(image.width, right + padding),
                        min(image.height, end + 8),
                    )
                ).convert("L")
            )
        )
        context = context.resize((context.width * 2, context.height * 2))
        context.thumbnail((2016, 224))
        result.append(
            (
                (
                    x / image.width,
                    y / image.height,
                    (right - x) / image.width,
                    (end - y) / image.height,
                ),
                [
                    ImageOps.expand(crop, border=16, fill=255).convert("RGB")
                    for crop in (binary, gray, context)
                ],
            )
        )
    return result


def recognize(request_path):
    # The parent owns this process and enforces its deadline, output and staging bounds.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from PIL import Image
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    verify()
    request_path = request_path.resolve()
    entries = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_LINES:
        raise ValueError("Handwriting request exceeds its inference bound.")
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(0)
    processor = TrOCRProcessor.from_pretrained(MODEL, local_files_only=True, use_fast=False)
    model = VisionEncoderDecoderModel.from_pretrained(MODEL, local_files_only=True).eval()
    results = []
    engine = "TrOCR-base-fp32@" + MANIFEST["revision"] + "/transformers-4.57.6/torch-2.7.1+cpu"

    def checkpoint():
        progress = request_path.parent / "progress.json"
        partial = progress.with_suffix(".partial")
        partial.write_text(json.dumps({"results": results, "engine": engine}), encoding="utf-8")
        partial.replace(progress)

    checkpoint()
    for name in entries:
        if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}\.png", name):
            raise ValueError("Invalid owned handwriting image name.")
        path = request_path.parent / name
        if path.resolve().parent != request_path.parent or path.stat().st_size > 256 * 1024:
            raise ValueError("Invalid handwriting image path/size.")
        with Image.open(path) as image:
            if image.width > 2048 or image.height > 256:
                raise ValueError("Handwriting image dimensions exceed their bound.")
            with torch.inference_mode():
                pixels = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values
                generated = model.generate(
                    pixels,
                    max_new_tokens=80,
                    num_beams=1,
                    return_dict_in_generate=True,
                    output_scores=True,
                )
                text = processor.batch_decode(generated.sequences, skip_special_tokens=True)[0]
                capped = len(generated.scores) >= 80 or len(text) > 200
                if capped:
                    text = ""
                scores = [torch.softmax(score, -1).max().item() for score in generated.scores]
        results.append(
            {
                "name": name,
                "text": text,
                "score": sum(scores) / max(1, len(scores)),
                "capped": capped,
            }
        )
        checkpoint()
    print(json.dumps({"results": results, "engine": engine}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "recognize"])
    parser.add_argument("request", nargs="?")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.action == "setup":
        setup(args.download)
        print("Local English handwriting experiment ready. Existing backend extras unchanged.")
    else:
        recognize(Path(args.request))
