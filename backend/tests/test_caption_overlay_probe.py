import math

import pytest

import caption_overlay_probe as probe


@pytest.mark.parametrize("value", ["0,0,0,1", "0,0,2,1", "nan,0,1,1", "0,inf,1,1", "0,0,1"])
def test_invalid_evaluation_region_is_rejected(value):
    with pytest.raises(ValueError):
        probe.rectangle(value)


@pytest.mark.parametrize("start,end", [(0, math.inf), (math.nan, 1), (1, 0), (0, 2)])
def test_bad_or_unbounded_interval_is_rejected_before_any_io(tmp_path, monkeypatch, start, end):
    import builtins

    original = builtins.__import__

    def without_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise AssertionError("Optional imaging must not precede input validation")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pillow)
    with pytest.raises(ValueError, match="at most"):
        probe.run(tmp_path / "absent.mp4", tmp_path / "output", start, end)
    assert not (tmp_path / "output").exists()


@pytest.fixture
def arrays():
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    return np, Image


@pytest.mark.parametrize("foreground", [(240, 210, 25), (48, 108, 255), (255, 255, 255)])
def test_thin_glyph_edges_remain_soft_and_background_is_transparent(arrays, foreground):
    np, Image = arrays
    alpha = np.zeros((80, 160), dtype=np.float32)
    for x in (20, 60, 100):
        alpha[20:60, x : x + 3] = 1
        alpha[20:60, x - 1] = 0.4
        alpha[20:60, x + 3] = 0.6
        alpha[38:41, x : x + 22] = 1
    bg = np.array([30, 40, 50], dtype=np.float32)
    rgb = bg + alpha[..., None] * (np.array(foreground) - bg)
    output, metrics = probe.matte(Image.fromarray(np.round(rgb).astype("uint8")), foreground)
    got = np.asarray(output)[..., 3] / 255
    assert np.max(np.abs(got - alpha)) < 0.025
    assert metrics["soft_edge_pixels"] > 100
    assert metrics["nonzero_alpha_pixels"] == int((alpha > 0).sum())
    assert not got[alpha == 0].any()  # No rectangular crop or original background is retained.
    assert np.max(np.abs(np.asarray(output)[got > 0, :3] - np.array(foreground))) == 0


def test_calibrated_guide_preserves_a_fade_without_fabricating_pixels_after_disappearance(arrays):
    np, Image = arrays
    fg = np.array([240, 210, 25], dtype=np.float32)
    bg = np.array([30, 40, 50], dtype=np.float32)
    alpha = np.zeros((80, 160), dtype=np.float32)
    alpha[20:60, 30:33] = 1

    def frame(strength):
        return Image.fromarray(
            np.round(bg + alpha[..., None] * strength * (fg - bg)).astype("uint8")
        )

    opaque, _ = probe.matte(frame(1), fg)
    faded, metrics = probe.matte(frame(0.15), fg, opaque.getchannel("A"))
    strength = np.asarray(faded)[..., 3][alpha > 0].mean() / 255
    assert abs(strength - 0.15) < 0.02
    gone, empty = probe.matte(frame(0), fg, opaque.getchannel("A"))
    assert not np.asarray(gone)[..., 3].any()
    assert metrics["alpha_mass"] > 0 and empty["alpha_mass"] == 0


def test_identical_foreground_background_does_not_claim_recoverable_alpha(arrays):
    np, Image = arrays
    image = Image.new("RGB", (80, 40), (230, 210, 25))
    output, metrics = probe.matte(image, (230, 210, 25), Image.new("L", image.size, 255))
    assert metrics["indeterminate_color_pixels"] == 80 * 40
    assert not np.asarray(output)[..., 3].any()


def test_python_written_diagnostics_also_enforce_directory_budget(tmp_path, monkeypatch):
    import threading
    import time

    monkeypatch.setattr(probe, "TEMP_BYTES", 10)
    (tmp_path / "rgba").mkdir()
    (tmp_path / "rgba" / "001.png").write_bytes(b"x" * 11)
    with pytest.raises(ValueError, match="budget"):
        probe.check_bounds(tmp_path, threading.Event(), time.monotonic() + 10)
