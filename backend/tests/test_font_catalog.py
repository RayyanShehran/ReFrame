import hashlib

import pytest

import captions
import font_assets as fonts
import font_catalog
from references import ReferenceError
from tests.test_font_assets import local as local  # noqa: F401


def test_catalog_structure_hashes_and_actual_faces(local):
    client, directory, _ = local
    from tests.test_color_recipe import seed_analyses

    saved, _ = seed_analyses(client, directory)
    pid = saved["id"]
    from font_probe import inspect

    for choice, (name, family, _, sha) in font_catalog.FACES.items():
        path = font_catalog.ROOT / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
        assert inspect(path)["family"] == family
        binding = fonts.selected(pid, choice)
        fonts.validate(pid, binding, [captions.Cue(start=0, end=1, text="Caption")])
        assert binding.choice == choice
    for choice in ("amiri-regular", "amiri-bold"):
        fonts.validate(
            pid, fonts.selected(pid, choice), [captions.Cue(start=0, end=1, text="مرحبا")]
        )
    with pytest.raises(ReferenceError, match="Selected font lacks"):
        fonts.validate(
            pid, fonts.selected(pid, "anton-regular"), [captions.Cue(start=0, end=1, text="مرحبا")]
        )
    assert (
        client.get(f"/api/projects/{pid}/caption-font").json()["candidates"][1]["candidate_id"]
        == "amiri-regular"
    )
