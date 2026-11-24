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


def warp(image, scale, x, y, angle=0.0):
    """Output frame whose content is `image` magnified by `scale`, turned clockwise by `angle`
    radians and moved by (x, y) heights."""
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH].astype(float)
    qx, qy = (cols - WIDTH / 2) / HEIGHT - x, (rows - HEIGHT / 2) / HEIGHT - y
    cos, sin = np.cos(angle), np.sin(angle)
    px, py = (cos * qx + sin * qy) / scale * HEIGHT, (cos * qy - sin * qx) / scale * HEIGHT
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


def soft(seed, frames, level):
    """A dark, out-of-focus shot: a burned-in caption has the only sharp edges in it."""
    shot = []
    for frame in scene(seed, frames, gain=1.2, level=level):
        f = frame.astype(float)
        for _ in range(8):
            f = sum(np.roll(f, s, a) for s in (2, -2) for a in (0, 1)) / 5 + f / 5
        shot.append(f.astype(np.uint8))
    return shot


def caption(frame, words):
    """Burn in a white caption of `words` words (four letter strokes each) across the centre."""
    out = frame.copy()
    x = WIDTH // 2 - words * 30
    for _ in range(words):
        for stroke in range(4):
            out[170:194, x + stroke * 12 : x + stroke * 12 + 5] = 240
        x += 60
    return out


def test_a_caption_changing_over_a_dark_soft_shot_is_not_a_cut(tmp_path):
    # Words appear one at a time, as in lyric edits: 1, then 2, then 4 words.
    frames = [caption(f, 1 + (i >= 20) + 2 * (i >= 40)) for i, f in enumerate(soft(1, 60, 20))]
    found = detected(encode(tmp_path / "caption.mp4", frames), tmp_path / "stage")
    assert found == [], found


def test_a_cut_between_dark_soft_shots_under_the_same_caption_is_found(tmp_path):
    frames = [caption(f, 3) for f in soft(1, 30, 20) + soft(2, 30, 22)]
    found = detected(encode(tmp_path / "same-caption.mp4", frames), tmp_path / "stage")
    assert near(found, [30]), found


def test_measure_splits_a_saved_shot_at_a_detected_cut(tmp_path):
    image = texture()
    frames = [warp(image, 1 + 0.1 * i / 29, 0, 0) for i in range(30)] + scene(3, 30)
    shots = measure(encode(tmp_path / "missed.mp4", frames), tmp_path / "stage")
    assert [(s.start_frame, s.end_frame) for s in shots] == [(0, 30), (30, 60)]
    assert motion.sample(shots[0], 29)[0] == pytest.approx(1.1, abs=0.015)
    assert shots[1].moves == []
    # A saved cut a frame away from the detected one is the same cut: no sliver shot.
    stage = tmp_path / "again"
    shots = measure(tmp_path / "missed.mp4", stage, ((0, 31), (31, 60)))
    assert [(s.start_frame, s.end_frame) for s in shots] == [(0, 31), (31, 60)]


def test_slow_pan_is_measured_without_sub_pixel_bias(tmp_path):
    # Under 0.5 px per frame at analysis size: a parabolic peak fit locks toward whole pixels.
    image = texture()
    frames = [warp(image, 1.0, 0.03 * i / 59, -0.02 * i / 59) for i in range(FRAMES)]
    (shot,) = measure(encode(tmp_path / "slow.mp4", frames), tmp_path / "stage")
    _, x, y, _ = motion.sample(shot, FRAMES - 1, shake=False)
    assert x == pytest.approx(0.03, abs=0.003)
    assert y == pytest.approx(-0.02, abs=0.003)


def roll(path, angles):
    image = texture()
    return encode(path, [warp(image, 1.0, 0.0, 0.0, angle) for angle in angles])


def test_known_roll_is_detected_within_tolerance(tmp_path):
    turn = np.radians(3)
    path = roll(tmp_path / "roll.mp4", [turn * i / 59 for i in range(FRAMES)])
    (shot,) = measure(path, tmp_path / "stage")
    assert "rotate" in shot.moves
    assert motion.sample(shot, FRAMES - 1)[3] == pytest.approx(turn, abs=np.radians(0.2))
    assert motion.sample(shot, 30)[3] == pytest.approx(turn * 30 / 59, abs=np.radians(0.2))
    scale, x, y, _ = motion.sample(shot, FRAMES - 1)
    assert abs(scale - 1) < 0.01 and abs(x) < 0.005 and abs(y) < 0.005


def test_a_still_shot_and_a_roll_below_the_noise_floor_count_as_no_rotation(tmp_path):
    image = texture()
    still = [warp(image, 1.0, 0.02 * i / 59, 0.0) for i in range(FRAMES)]
    (shot,) = measure(encode(tmp_path / "still.mp4", still), tmp_path / "stage")
    assert "rotate" not in shot.moves and shot.shake_angle == []
    assert all(key.angle == 0 for key in shot.keyframes)
    faint = motion.ROTATION_FLOOR * 0.5
    path = roll(tmp_path / "faint.mp4", [faint * i / 59 for i in range(FRAMES)])
    (shot,) = measure(path, tmp_path / "stage")
    assert "rotate" not in shot.moves and shot.shake_angle == []
    assert all(motion.sample(shot, frame)[3] == 0 for frame in range(FRAMES))


def test_shaky_roll_keeps_its_shake_separate_from_the_intentional_roll(tmp_path):
    turn, wobble = np.radians(3), np.radians(1)
    angles = [turn * i / 59 + wobble * np.sin(2 * np.pi * 4 * i / 30) for i in range(FRAMES)]
    (shot,) = measure(roll(tmp_path / "shaky.mp4", angles), tmp_path / "stage")
    assert {"rotate", "shake"} <= set(shot.moves)
    strongest = max(shot.shake_angle, key=lambda wave: wave.amplitude)
    assert strongest.frequency == pytest.approx(4, abs=0.6)
    assert strongest.amplitude == pytest.approx(wobble, abs=np.radians(0.35))
    smooth = motion.sample(shot, FRAMES - 1, shake=False)[3]
    assert smooth == pytest.approx(turn, abs=np.radians(0.4))
    # The shake is added back when sampling with shake.
    middle = 37
    assert motion.sample(shot, middle)[3] == pytest.approx(angles[middle], abs=np.radians(0.4))


def corners_inside(samples, width, height):
    """Every output corner, mapped back through the base zoom, roll and crop, is in the frame."""
    zoom = motion.base_zoom(samples, width, height)
    for scale, x, y, angle in samples:
        z = zoom * scale
        cos, sin = np.cos(angle), np.sin(angle)
        for u in (-width / 2, width / 2):
            for v in (-height / 2, height / 2):
                # Output corner -> rotated source (crop centre offset -x*H/z, -y*H/z) -> source.
                qx, qy = u / z - x * height / z, v / z - y * height / z
                sx, sy = cos * qx + sin * qy, -sin * qx + cos * qy
                if abs(sx) > width / 2 + 1e-6 or abs(sy) > height / 2 + 1e-6:
                    return False
    return True


def test_rolled_crop_corners_stay_inside_the_picture_and_large_rolls_are_scaled_down():
    for width, height in ((WIDTH, HEIGHT), (720, 1280)):
        rolled = [
            (1.0 + 0.05 * i / 59, 0.03 * i / 59, -0.01, np.radians(3) * i / 59)
            for i in range(FRAMES)
        ]
        graph, notice = motion.motion_filter(rolled, width, height)
        assert notice is None and "rotate=" in graph and "zoompan" in graph
        assert corners_inside(rolled, width, height)
        assert motion.base_zoom(rolled, width, height) > motion.base_zoom(
            [s[:3] + (0.0,) for s in rolled], width, height
        )
    huge = [(1.0, 0.0, 0.0, np.radians(20) * i / 59) for i in range(FRAMES)]
    graph, notice = motion.motion_filter(huge, WIDTH, HEIGHT)
    assert notice  # Scaled down, with a notice, like zoom and pan.
    share = int(notice.split("to ")[1].split("%")[0]) / 100
    reduced = [(s, x, y, a * share) for s, x, y, a in huge]
    assert motion.required_zoom(reduced, WIDTH, HEIGHT) <= motion.MAX_BASE_ZOOM * 1.01
    assert corners_inside(reduced, WIDTH, HEIGHT)


def test_render_applies_a_known_roll_without_black_corners(tmp_path):
    turn = np.radians(3)
    curve = motion.ShotMotion(
        start_frame=0,
        end_frame=FRAMES,
        keyframes=[
            motion.Keyframe(frame=0, scale=1.0, x=0.0, y=0.0, angle=0.0),
            motion.Keyframe(frame=FRAMES - 1, scale=1.0, x=0.0, y=0.0, angle=turn),
        ],
        moves=["rotate"],
    )
    samples = [motion.sample(curve, frame) for frame in range(FRAMES)]
    graph, notice = motion.motion_filter(samples, WIDTH, HEIGHT)
    assert notice is None
    source = encode(tmp_path / "flat.mp4", [warp(texture(), 1.0, 0.0, 0.0)] * FRAMES)
    output = tmp_path / "rolled.mp4"
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
    corners = decoded[:, [0, 0, -1, -1], [0, -1, 0, -1]]
    edges = np.concatenate(
        [decoded[:, :2].ravel(), decoded[:, -2:].ravel()]
        + [decoded[:, :, :2].ravel(), decoded[:, :, -2:].ravel()]
    )
    assert corners.min() >= 20 and edges.min() >= 20, "black corner in the rolled output"
    (shot,) = measure(output, tmp_path / "stage")
    assert motion.sample(shot, FRAMES - 1, shake=False)[3] == pytest.approx(turn, abs=0.005)


def test_a_roll_in_a_shot_shorter_than_the_evidence_window_is_kept(tmp_path):
    turn, image = np.radians(3), texture()
    rolled = [warp(image, 1.0, 0.0, 0.0, turn * i / 4) for i in range(5)]
    still = [warp(texture(seed=3), 1.0, 0.0, 0.0)] * (FRAMES - 5)
    path = encode(tmp_path / "short.mp4", rolled + still)
    short, rest = measure(path, tmp_path / "stage", ((0, 5), (5, FRAMES)))
    assert "rotate" in short.moves
    assert motion.sample(short, 4)[3] == pytest.approx(turn, abs=np.radians(0.3))
    assert "rotate" not in rest.moves
