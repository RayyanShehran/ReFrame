"""Developer-only, short-interval lettering matte experiment; never publishes app captions."""

import argparse
import json
import math
import shutil
import threading
import time
from pathlib import Path

import automatic_captions as automatic
import color_analysis as color
import reference_engine as engine
import video_render as render

METHOD = "local-median-color-projection-v1"
FPS = 30
MAX_FRAMES = 36
TEMP_BYTES = 192 * 1024 * 1024


def matte(image, foreground, guide=None):
    """Estimate encoded-RGB alpha against a spatial median; true source alpha is unknown."""
    import numpy as np
    from PIL import Image, ImageFilter

    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    background = (
        np.asarray(image.convert("RGB").filter(ImageFilter.MedianFilter(15)), dtype=np.float32)
        / 255
    )
    ink = np.asarray(foreground, dtype=np.float32) / 255
    if ink.shape != (3,) or not np.isfinite(ink).all() or not ((0 <= ink) & (ink <= 1)).all():
        raise ValueError("Foreground must contain three finite byte channels")
    direction = ink - background
    denominator = (direction * direction).sum(axis=2)
    alpha = np.clip(
        ((rgb - background) * direction).sum(axis=2) / np.maximum(denominator, 1e-6), 0, 1
    )
    error = np.sqrt(((rgb - background - alpha[..., None] * direction) ** 2).mean(axis=2))
    seed = (alpha >= 0.35) & (error <= 0.06) & (denominator >= 0.01)
    if guide is not None:
        if guide.size != image.size:
            raise ValueError("Guide must have the crop's dimensions")
        seed |= np.asarray(guide.convert("L")) > 0
    support = (
        np.asarray(Image.fromarray((seed * 255).astype("uint8")).filter(ImageFilter.MaxFilter(5)))
        > 0
    )
    alpha *= support & (error <= 0.06) & (denominator >= 0.01) & (alpha >= 0.02)
    rgba = np.empty((*alpha.shape, 4), dtype="uint8")
    rgba[..., :3] = np.round(ink * 255).astype("uint8")
    rgba[..., 3] = np.round(alpha * 255).astype("uint8")
    rgba[rgba[..., 3] == 0, :3] = 0
    return Image.fromarray(rgba), {
        "nonzero_alpha_pixels": int((alpha > 0).sum()),
        "alpha_mass": float(alpha.sum()),
        "soft_edge_pixels": int(((alpha > 0) & (alpha < 0.95)).sum()),
        "indeterminate_color_pixels": int((denominator < 0.01).sum()),
    }


def rectangle(value):
    values = tuple(float(v) for v in value.split(","))
    if (
        len(values) != 4
        or not all(math.isfinite(v) for v in values)
        or not 0 <= values[0] < 1
        or not 0 <= values[1] < 1
        or not 0 < values[2] <= 1 - values[0]
        or not 0 < values[3] <= 1 - values[1]
    ):
        raise ValueError("Region is normalized x,y,width,height inside the frame")
    return values


def region_box(image, selected):
    if selected is None:
        crops = automatic.retry_crops(image, [])
        return crops[0][1] if crops else None
    x, y, width, height = selected
    return (
        math.floor(x * image.width),
        math.floor(y * image.height),
        math.ceil((x + width) * image.width),
        math.ceil((y + height) * image.height),
    )


def run(source, output, start, end, *, selected=None, calibration=None, background=None):
    from PIL import Image

    if (
        not all(math.isfinite(v) for v in (start, end))
        or not 0 <= start < end
        or end - start > MAX_FRAMES / FPS + 1e-6
    ):
        raise ValueError("Choose at most 1.2 seconds inside the source")
    if calibration is not None and (not math.isfinite(calibration) or calibration < 0):
        raise ValueError("Calibration time must be finite and nonnegative")
    if selected is not None:
        selected = rectangle(",".join(str(v) for v in selected))
    source = Path(source).resolve(strict=True)
    if not source.is_file() or not 0 < source.stat().st_size <= 50 * 1024 * 1024:
        raise ValueError("Reference must be a local file of at most 50 MiB")
    if background is not None:
        background = Path(background).resolve(strict=True)
        if not background.is_file() or not 0 < background.stat().st_size <= 100 * 1024 * 1024:
            raise ValueError("Background must be a local footage file of at most 100 MiB")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    stop = threading.Event()
    engine._control.stop = stop
    started = time.monotonic()
    deadline = started + 120

    def tool(args, label):
        color.guard(stop, deadline)
        return render.tool(
            [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-nostdin", "-threads", "2", *args],
            output,
            label,
            deadline,
            30,
            temp_budget=TEMP_BYTES,
        )

    source_hash = color.digest(source, stop, deadline)
    background_hash = (
        color.digest(background, stop, deadline, max_bytes=100 * 1024 * 1024)
        if background
        else None
    )
    with automatic.temporary(output / "staging") as stage:
        ffmpeg_version = tool(["-version"], "version").decode("utf-8").splitlines()[0]
        video, _, duration = render.probe(source, output, deadline, temp_budget=TEMP_BYTES)
        if end > duration or calibration is not None and calibration >= duration:
            raise ValueError("Requested interval/calibration exceeds the source timeline")
        canvas = automatic.native_geometry(video)
        resize = render.resize_filter(*canvas, color.inspect_colors(video))
        count = math.ceil((end - start) * FPS - 1e-6)
        src = stage / "source"
        src.mkdir()
        tool(
            [
                "-protocol_whitelist",
                "file",
                "-i",
                str(source),
                "-an",
                "-vf",
                f"setpts=PTS-STARTPTS,trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"{resize},fps={FPS}:round=near",
                "-frames:v",
                str(count),
                str(src / "%03d.png"),
            ],
            "source-frames",
        )
        frames = sorted(src.glob("*.png"))
        if len(frames) != count:
            raise ValueError("Source did not provide every requested frame")
        calibration_path = frames[0]
        if calibration is not None:
            calibration_path = stage / "calibration.png"
            tool(
                [
                    "-protocol_whitelist",
                    "file",
                    "-i",
                    str(source),
                    "-an",
                    "-vf",
                    f"setpts=PTS-STARTPTS,trim=start={calibration},setpts=PTS-STARTPTS,{resize}",
                    "-frames:v",
                    "1",
                    str(calibration_path),
                ],
                "calibration",
            )
        with Image.open(calibration_path) as im:
            reference = im.convert("RGB")
        box = region_box(reference, selected)
        if box is None:
            raise ValueError("No automatic calibration region; not a no-caption result")
        r = automatic.match.Rectangle(
            x=box[0] / canvas[0],
            y=box[1] / canvas[1],
            width=(box[2] - box[0]) / canvas[0],
            height=(box[3] - box[1]) / canvas[1],
        )
        ink_hex = automatic.glyph_color(reference, r)
        if ink_hex is None:
            raise ValueError("No observable foreground color")
        ink = tuple(int(ink_hex[i : i + 2], 16) for i in (1, 3, 5))
        calibration_rgba, _ = matte(reference.crop(box), ink)
        guide_canvas = Image.new("L", canvas)
        guide_canvas.paste(calibration_rgba.getchannel("A"), box[:2])
        overlays = output / "rgba"
        overlays.mkdir()
        measurements = []
        for i, frame in enumerate(frames):
            color.guard(stop, deadline)
            with Image.open(frame) as im:
                image = im.convert("RGB")
            current_box = region_box(image, selected)
            overlay = Image.new("RGBA", canvas)
            metrics = {
                "nonzero_alpha_pixels": 0,
                "alpha_mass": 0.0,
                "soft_edge_pixels": 0,
                "indeterminate_color_pixels": 0,
            }
            if current_box:
                crop, metrics = matte(image.crop(current_box), ink, guide_canvas.crop(current_box))
                overlay.paste(crop, current_box[:2])
            overlay.save(overlays / f"{i + 1:03d}.png")
            measurements.append(
                {"timestamp_seconds": start + i / FPS, "region": current_box, **metrics}
            )
        if background is not None:
            bg = stage / "background"
            bg.mkdir()
            tool(
                [
                    "-protocol_whitelist",
                    "file",
                    "-i",
                    str(background),
                    "-an",
                    "-vf",
                    f"setpts=PTS-STARTPTS,scale={canvas[0]}:{canvas[1]}:"
                    f"force_original_aspect_ratio=increase,crop={canvas[0]}:{canvas[1]},"
                    f"setsar=1,fps={FPS}:round=near",
                    "-frames:v",
                    str(count),
                    str(bg / "%03d.png"),
                ],
                "background-frames",
            )
            backgrounds = sorted(bg.glob("*.png"))
            if len(backgrounds) != count:
                raise ValueError("Background is too short; no partial preview is accepted")
            comp = stage / "composites"
            comp.mkdir()
            for i, frame in enumerate(backgrounds):
                color.guard(stop, deadline)
                with Image.open(frame) as image, Image.open(overlays / f"{i + 1:03d}.png") as layer:
                    result = Image.alpha_composite(image.convert("RGBA"), layer)
                    result.convert("RGB").save(comp / f"{i + 1:03d}.png")
                    if i in {0, count // 2, count - 1}:
                        result.convert("RGB").save(output / f"composite-{i:03d}.png")
            tool(
                [
                    "-framerate",
                    str(FPS),
                    "-i",
                    str(comp / "%03d.png"),
                    "-frames:v",
                    str(count),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "2",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(output / "preview.mp4"),
                ],
                "silent-composite-preview",
            )
        if color.digest(source, stop, deadline) != source_hash or (
            background
            and color.digest(background, stop, deadline, max_bytes=100 * 1024 * 1024)
            != background_hash
        ):
            raise ValueError("Source/background changed during the experiment")
        report = {
            "method": METHOD,
            "developer_only": True,
            "status": "needs_visual_review",
            "source_sha256": source_hash,
            "background_sha256": background_hash,
            "ffmpeg_version": ffmpeg_version,
            "selection": "manual_evaluation_region" if selected else "automatic_mask_crop",
            "normalized_region": selected,
            "calibration_seconds": calibration,
            "foreground_rgb": ink,
            "canvas": canvas,
            "fps": FPS,
            "frames": measurements,
            "elapsed_seconds": time.monotonic() - started,
            "limits": {
                "deadline_seconds": 120,
                "maximum_frames": MAX_FRAMES,
                "directory_bytes": TEMP_BYTES,
            },
            "limitations": [
                "Estimated alpha; compressed RGB does not identify true alpha.",
                "Fixed-color spatial median can lose strokes or retain scenery.",
                "Calibration support does not prove visibility or full coverage.",
                "Silent diagnostic preview; not an app export.",
            ],
        }
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "output", type=Path, help="New diagnostic directory, outside retained media"
    )
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--region", help="Manual evaluation x,y,width,height; default is automatic")
    parser.add_argument("--calibration", type=float, help="Explicit evaluation calibration time")
    parser.add_argument(
        "--background", type=Path, help="Existing local footage for a silent preview"
    )
    args = parser.parse_args()
    try:
        report = run(
            args.source,
            args.output,
            args.start,
            args.end,
            selected=rectangle(args.region) if args.region else None,
            calibration=args.calibration,
            background=args.background,
        )
    except (ValueError, engine.RetrievalFailure, engine.ProbeTimeout) as error:
        parser.exit(1, f"Overlay experiment failed: {error}\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "selection": report["selection"],
                "elapsed_seconds": report["elapsed_seconds"],
            }
        )
    )


if __name__ == "__main__":
    main()
