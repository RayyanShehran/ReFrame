import pytest

np = pytest.importorskip("numpy")  # Optional visual extra; CI may not install it.

import beats  # noqa: E402

FRAME = 1 / 30


def clicks(times, duration, seed=0):
    """Percussive clicks (decaying noise bursts) at `times` over a quiet noise floor."""
    rng = np.random.default_rng(seed)
    y = 0.01 * rng.standard_normal(int(duration * beats.SR))
    n = int(0.03 * beats.SR)
    burst = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.005 * beats.SR))
    for t in times:
        i = int(t * beats.SR)
        y[i : i + n] += 0.8 * burst[: len(y) - i]
    return y.astype(np.float32)


def near(found, truth, tolerance=FRAME):
    """Share of `truth` times with a `found` time within `tolerance`."""
    found = np.asarray(found)
    if not len(found):
        return 0.0
    return float(np.mean([np.min(np.abs(found - t)) <= tolerance for t in truth]))


def test_a_known_tempo_gives_clear_beats_on_the_clicks():
    truth = np.arange(0.5, 12, 0.5)  # 120 BPM
    rhythm = beats.analyze(np, clicks(truth, 12.5))
    assert rhythm.clear
    assert rhythm.tempo_bpm == pytest.approx(120, abs=2)
    assert near(rhythm.beats, truth) >= 0.9  # Beats on the clicks, within a frame...
    assert near(truth, rhythm.beats) >= 0.9  # ...and no beats between them.


def test_a_tempo_change_is_followed():
    first = np.arange(0.5, 8, 0.5)  # 120 BPM, then 90 BPM.
    second = 8 + np.arange(0, 8, 60 / 90)
    rhythm = beats.analyze(np, clicks(np.concatenate([first, second]), 16.5))
    assert rhythm.clear
    assert near(rhythm.beats, first) >= 0.85
    assert near(rhythm.beats, second) >= 0.85


def test_audio_without_a_beat_has_no_clear_beats():
    rng = np.random.default_rng(3)
    random_hits = np.sort(rng.uniform(0.2, 12, 24))  # Irregular sound events, no pulse.
    rhythm = beats.analyze(np, clicks(random_hits, 12.5, seed=3))
    assert not rhythm.clear
    assert rhythm.beats == []


def test_on_beat_cuts_follow_the_music_when_it_is_offset():
    reference_beats = [0.5 * k for k in range(1, 20)]
    cuts = [30, 60, 90, 120]  # Every 2nd beat.
    music = [b - FRAME for b in reference_beats]  # The output plays the music one frame early.
    snapped, moved = beats.snap(cuts, reference_beats, music, total_frames=300)
    assert snapped == [29, 59, 89, 119] and moved == 4
    assert np.diff(snapped).tolist() == [30, 30, 30]  # Still every 2nd beat.


def test_a_cut_keeps_its_phase_against_the_beat():
    reference_beats = [1.0, 2.0]
    cuts = [31]  # One frame after its beat.
    music = [b - 2 * FRAME for b in reference_beats]
    assert beats.snap(cuts, reference_beats, music, total_frames=90) == ([29], 1)


def test_at_offset_zero_the_reference_cuts_are_kept_exactly():
    reference_beats = [0.5 * k for k in range(1, 20)]
    cuts = [31, 59, 92]  # Within 2 frames of a beat, but not exactly on it.
    assert beats.snap(cuts, reference_beats, reference_beats, total_frames=300) == (cuts, 0)


def test_off_beat_reference_cuts_are_not_snapped():
    reference_beats = [0.5 * k for k in range(1, 20)]
    cuts = [38, 68]  # A quarter second after the beat: deliberately off-beat.
    music = [b - FRAME for b in reference_beats]
    assert beats.snap(cuts, reference_beats, music, total_frames=300) == (cuts, 0)


def test_no_clear_beats_falls_back_to_the_reference_cuts():
    cuts = [30, 60]
    assert beats.snap(cuts, [], [], total_frames=300) == (cuts, 0)
    assert beats.snap(cuts, [1.0, 2.0], [], total_frames=300) == (cuts, 0)


def test_snapping_follows_a_tempo_change():
    reference_beats = [0.5 * k for k in range(1, 16)] + [8 + k * 60 / 90 for k in range(12)]
    cuts = [round(b * 30) for b in reference_beats[::2]]
    music = [b - FRAME for b in reference_beats]
    snapped, moved = beats.snap(cuts, reference_beats, music, total_frames=480)
    assert moved == len(cuts)
    assert snapped == [c - 1 for c in cuts]


def test_snapping_never_makes_a_slot_shorter_than_three_frames():
    # A 3-frame montage shot: moving the on-beat cut two frames earlier would leave 1 frame.
    reference_beats = [33 / 30]
    cuts = [30, 33, 36]
    music = [31 / 30]
    assert beats.snap(cuts, reference_beats, music, total_frames=90) == (cuts, 0)
