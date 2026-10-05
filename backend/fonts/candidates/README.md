# Curated caption candidates

Four bundled faces (plus at most one uploaded project font) are compared:

- Existing DejaVu Sans Book: `../DejaVuSans.ttf`, license `../LICENSE.txt`.
- Amiri Regular (400) and Bold (700): Arabic Naskh and Latin, SIL Open Font License 1.1, `amiri-OFL.txt`.
- Anton Regular (400): condensed display sans, Latin; unsupported Arabic is skipped, SIL Open Font License 1.1, `anton-OFL.txt`.

The three new static TTFs and complete unmodified license notices were obtained from the official [Google Fonts repository](https://github.com/google/fonts/tree/7085eb89a950e85db5b166b7a58d414544b4140c/ofl) at commit `7085eb89a950e85db5b166b7a58d414544b4140c`: `ofl/amiri/Amiri-Regular.ttf`, `ofl/amiri/Amiri-Bold.ttf`, `ofl/amiri/OFL.txt`, `ofl/anton/Anton-Regular.ttf`, `ofl/anton/OFL.txt`.

Original content hashes and stable candidate IDs are fixed in `backend/font_catalog.py`. Native staging renames only the font family to avoid system-font collisions; original files and readable names remain unchanged. No runtime downloads. Names/weights identify an actual face, not a synthetic variant. Cmap coverage does not establish correct shaping or reference identity.
