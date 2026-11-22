import shutil
import subprocess
import threading
import time

import pytest

np = pytest.importorskip("numpy")  # Optional visual extra; CI may not install it.

import camera_motion as motion  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
WIDTH, HEIGHT, FRAMES = 640, 360, 60


def texture(seed=7, size=(900, 1500)):
    rng = np.random.default_rng(seed)
    noise = rng.random(size)
    for _ in range(3):  # Box blur: detail at several scales, like real scenery.
        noise = (noise + np.roll(noise, 1, 0) + np.roll(noise, 1, 1) + np.roll(noise, -1, 0)) / 4
    noise = (noise - noise.min()) / np.ptp(noise)
    return 40 + 175 * noise


def warp(image, scale, x, y):
    """Output frame whose content is `image` magnified by `scale`, moved by (x, y) heights."""
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH].astype(float)
    qx, qy = (cols - WIDTH / 2) / HEIGHT, (rows - HEIGHT / 2) / HEIGHT
    px, py = (qx - x) / scale * HEIGHT, (qy - y) / scale * HEIGHT
    sx, sy = px + image.shape[1] / 2, py + image.shape[0] / 2
    x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
    fx, fy = sx - x0, sy - y0
    a, b = image[y0, x0], image[y0, x0 + 1]
    c, d = image[y0 + 1, x0], image[y0 + 1, x0 + 1]
    return (a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy).astype(
        np.uint8
    )


def encode(path, frames):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray"]
        + ["-s", f"{WIDTH}x{HEIGHT}", "-r", "30", "-i", "-", "-c:v", "libx264", "-crf", "16"]
        + ["-pix_fmt", "yuv420p", str(path)],
        input=b"".join(frame.tobytes() for frame in frames),
        check=True,
    )
    return path


def measure(path, directory, shots=((0, FRAMES),)):
    return motion.measure(
        path, list(shots), directory, threading.Event(), time.monotonic() + 60
    ).shots


def test_known_zoom_in_and_pan_are_detected_within_tolerance(tmp_path):
    image = texture()
    frames = [warp(image, 1 + 0.12 * i / 59, -0.06 * i / 59, 0.03 * i / 59) for i in range(FRAMES)]
    (shot,) = measure(encode(tmp_path / "moving.mp4", frames), tmp_path / "stage")
    scale, x, y, _ = motion.sample(shot, FRAMES - 1)
    assert scale == pytest.approx(1.12, abs=0.015)
    assert x == pytest.approx(-0.06, abs=0.006)
    assert y == pytest.approx(0.03, abs=0.006)
    halfway = motion.sample(shot, 30)
    assert halfway[0] == pytest.approx(1 + 0.12 * 30 / 59, abs=0.015)
    assert {"zoom_in", "pan"} <= set(shot.moves)


def test_static_clip_gives_no_motion(tmp_path):
    frames = [warp(texture(), 1.0, 0.0, 0.0)] * FRAMES
    (shot,) = measure(encode(tmp_path / "static.mp4", frames), tmp_path / "stage")
    for frame in (0, 30, FRAMES - 1):
        scale, x, y, angle = motion.sample(shot, frame)
        assert abs(scale - 1) < 0.005 and abs(x) < 0.003 and abs(y) < 0.003 and abs(angle) < 0.003
    assert shot.moves == [] and shot.shake_x == [] and shot.shake_y == []


def test_shake_is_separated_from_an_intentional_move(tmp_path):
    image = texture()
    frames = [warp(image, 1.0, 0.012 * np.sin(2 * np.pi * 4 * i / 30), 0.0) for i in range(FRAMES)]
    (shot,) = measure(encode(tmp_path / "shake.mp4", frames), tmp_path / "stage")
    assert "shake" in shot.moves and "pan" not in shot.moves
    strongest = max(shot.shake_x, key=lambda wave: wave.amplitude)
    assert strongest.frequency == pytest.approx(4, abs=0.6)
    assert strongest.amplitude == pytest.approx(0.012, abs=0.004)
    assert abs(motion.sample(shot, FRAMES - 1, shake=False)[1]) < 0.004


def test_cut_resets_the_curve_for_each_shot(tmp_path):
    image = texture()
    first = [warp(image, 1 + 0.1 * i / 29, 0, 0) for i in range(30)]
    second = [warp(texture(seed=3), 1.0, 0.0, 0.0)] * 30
    shots = measure(
        encode(tmp_path / "cut.mp4", first + second), tmp_path / "stage", ((0, 30), (30, 60))
    )
    assert motion.sample(shots[0], 29)[0] == pytest.approx(1.1, abs=0.015)
    assert abs(motion.sample(shots[1], 59)[0] - 1) < 0.005


def test_render_applies_a_known_curve_without_black_borders(tmp_path):
    curve = motion.ShotMotion(
        start_frame=0,
        end_frame=FRAMES,
        keyframes=[
            motion.Keyframe(frame=0, scale=1.0, x=0.0, y=0.0, angle=0.0),
            motion.Keyframe(frame=FRAMES - 1, scale=1.15, x=0.05, y=-0.02, angle=0.0),
        ],
        moves=["zoom_in", "pan"],
    )
    samples = [motion.sample(curve, frame) for frame in range(FRAMES)]
    graph, notice = motion.motion_filter(samples, WIDTH, HEIGHT)
    assert notice is None
    source = encode(tmp_path / "flat.mp4", [warp(texture(), 1.0, 0.0, 0.0)] * FRAMES)
    output = tmp_path / "moved.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", graph]
        + ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(output)],
        check=True,
    )
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(output), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
        check=True,
    ).stdout
    decoded = np.frombuffer(raw, np.uint8).reshape(-1, HEIGHT, WIDTH)
    assert len(decoded) == FRAMES
    edges = np.concatenate(
        [decoded[:, :2].ravel(), decoded[:, -2:].ravel()]
        + [decoded[:, :, :2].ravel(), decoded[:, :, -2:].ravel()]
    )
    assert edges.min() >= 20, "black edge in the moved output"
    (shot,) = measure(output, tmp_path / "stage")
    scale, x, y, _ = motion.sample(shot, FRAMES - 1)
    assert scale == pytest.approx(1.15, abs=0.02)
    assert x == pytest.approx(0.05, abs=0.008)
    assert y == pytest.approx(-0.02, abs=0.008)


def test_base_zoom_covers_the_largest_move_and_is_bounded():
    pan = [(1.0, 0.1 * i / 59, 0.0) for i in range(FRAMES)]
    graph, notice = motion.motion_filter(pan, WIDTH, HEIGHT)
    base = motion.base_zoom(pan, WIDTH, HEIGHT)
    # The crop window (width/zoom) must still contain the shifted centre at every frame; the
    # pan is applied after the base zoom, so its centre moves x * height / zoom source pixels.
    assert WIDTH / 2 - WIDTH / (2 * base) >= 0.1 * HEIGHT / base - 1e-9
    assert notice is None and "zoompan" in graph
    huge = [(1.0, 0.6 * i / 59, 0.0) for i in range(FRAMES)]
    assert motion.base_zoom(huge, WIDTH, HEIGHT) <= motion.MAX_BASE_ZOOM
    assert motion.motion_filter(huge, WIDTH, HEIGHT)[1]  # Scaled down, with a notice.


def test_dark_scene_with_a_moving_light_is_not_read_as_camera_motion(tmp_path):
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH].astype(float)
    frames = []
    for i in range(FRAMES):
        # A soft club light sweeps across an otherwise black, textureless frame.
        cx, cy = 120 + 6 * i, 180
        light = 200 * np.exp(-((cols - cx) ** 2 + (rows - cy) ** 2) / (2 * 30**2))
        frames.append(np.clip(8 + light, 0, 255).astype(np.uint8))
    (shot,) = measure(encode(tmp_path / "dark.mp4", frames), tmp_path / "stage")
    assert shot.moves == []


def test_a_pure_pan_keeps_its_magnitude_despite_the_base_zoom(tmp_path):
    pan = [(1.0, 0.08 * i / 59, 0.0) for i in range(FRAMES)]
    graph, _ = motion.motion_filter(pan, WIDTH, HEIGHT)
    assert motion.base_zoom(pan, WIDTH, HEIGHT) > 1.05  # The margin is a real zoom here.
    source = encode(tmp_path / "flat.mp4", [warp(texture(), 1.0, 0.0, 0.0)] * FRAMES)
    output = tmp_path / "panned.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", graph]
        + ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(output)],
        check=True,
    )
    (shot,) = measure(output, tmp_path / "stage")
    scale, x, y, _ = motion.sample(shot, FRAMES - 1, shake=False)
    assert x == pytest.approx(0.08, abs=0.005)
    assert abs(scale - 1) < 0.01 and abs(y) < 0.005


def scene(seed, frames, gain=1.0, level=None, pan=0.0):
    """A textured shot with a slow pan; `gain`/`level` darken it around a new mean."""
    image = texture(seed)
    shot = []
    for i in range(frames):
        frame = warp(image, 1.0, pan * i / 30, 0.0).astype(float)
        if level is not None:
            frame = gain * (frame - 129) + level
        shot.append(np.clip(frame, 0, 255).astype(np.uint8))
    return shot


def detected(path, directory):
    directory.mkdir()
    return motion.cut_frames(np, motion.decode(path, directory, time.monotonic() + 60))


def near(found, expected, tolerance=1):
    return len(found) == len(expected) and all(
        abs(f - e) <= tolerance for f, e in zip(found, expected)
    )


def test_hard_cuts_are_found_including_a_four_frame_shot(tmp_path):
    frames = scene(1, 20, pan=0.02) + scene(2, 4) + scene(3, 26, pan=-0.03)
    found = detected(encode(tmp_path / "cuts.mp4", frames), tmp_path / "stage")
    assert near(found, [20, 24]), found


def test_a_dissolve_is_one_cut_near_its_middle(tmp_path):
    a, b = scene(1, 60), scene(2, 60)
    frames = (
        a[:24]
        + [
            (a[24 + i] * (1 - (i + 1) / 13) + b[24 + i] * ((i + 1) / 13)).astype(np.uint8)
            for i in range(12)
        ]
        + b[36:]
    )
    found = detected(encode(tmp_path / "dissolve.mp4", frames), tmp_path / "stage")
    assert near(found, [30], tolerance=3), found


def test_cuts_between_dark_scenes_and_into_near_black_are_found(tmp_path):
    frames = (
        scene(1, 20, gain=1.0, level=22)  # Dark, textured, similar brightness to the next.
        + scene(2, 20, gain=1.0, level=30)
        + scene(3, 20, gain=0.15, level=4)  # Near-black: no structure to compare.
    )
    found = detected(encode(tmp_path / "dark.mp4", frames), tmp_path / "stage")
    assert near(found, [20, 40]), found


def test_a_flash_is_not_a_cut(tmp_path):
    frames = scene(1, 60, pan=0.02)
    for i in (20, 21):  # Two over-exposed frames, then the same shot again.
        frames[i] = np.clip(frames[i].astype(float) * 0.4 + 170, 0, 255).astype(np.uint8)
    frames[45] = np.full_like(frames[45], 250)  # One white frame.
    found = detected(encode(tmp_path / "flash.mp4", frames), tmp_path / "stage")
    assert found == [], found


def test_measure_splits_a_saved_shot_at_a_detected_cut(tmp_path):
    image = texture()
    frames = [warp(image, 1 + 0.1 * i / 29, 0, 0) for i in range(30)] + scene(3, 30)
    shots = measure(encode(tmp_path / "missed.mp4", frames), tmp_path / "stage")
    assert [(s.start_frame, s.end_frame) for s in shots] == [(0, 30), (30, 60)]
    assert motion.sample(shots[0], 29)[0] == pytest.approx(1.1, abs=0.015)
    assert shots[1].moves == []


def test_slow_pan_is_measured_without_sub_pixel_bias(tmp_path):
    # Under 0.5 px per frame at analysis size: a parabolic peak fit locks toward whole pixels.
    image = texture()
    frames = [warp(image, 1.0, 0.03 * i / 59, -0.02 * i / 59) for i in range(FRAMES)]
    (shot,) = measure(encode(tmp_path / "slow.mp4", frames), tmp_path / "stage")
    _, x, y, _ = motion.sample(shot, FRAMES - 1, shake=False)
    assert x == pytest.approx(0.03, abs=0.003)
    assert y == pytest.approx(-0.02, abs=0.003)
