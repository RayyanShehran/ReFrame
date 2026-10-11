# Curated caption candidates

Seventeen bundled static faces (plus at most one uploaded project font) are compared:

- Existing DejaVu Sans Book: `../DejaVuSans.ttf`, license `../LICENSE.txt`.
- Amiri Regular (400) and Bold (700): Arabic Naskh and Latin, SIL Open Font License 1.1, `amiri-OFL.txt`.
- Anton Regular (400): condensed display sans, Latin; unsupported Arabic is skipped, SIL Open Font License 1.1, `anton-OFL.txt`.

The three new static TTFs and complete unmodified license notices were obtained from the official [Google Fonts repository](https://github.com/google/fonts/tree/7085eb89a950e85db5b166b7a58d414544b4140c/ofl) at commit `7085eb89a950e85db5b166b7a58d414544b4140c`: `ofl/amiri/Amiri-Regular.ttf`, `ofl/amiri/Amiri-Bold.ttf`, `ofl/amiri/OFL.txt`, `ofl/anton/Anton-Regular.ttf`, `ofl/anton/OFL.txt`.

Original content hashes and stable candidate IDs are fixed in `backend/font_catalog.py`. Native staging renames only the font family to avoid system-font collisions; original files and readable names remain unchanged. No runtime downloads. Names/weights identify an actual face, not a synthetic variant. Cmap coverage does not establish correct shaping or reference identity.

Additional unmodified static faces from the same pinned official Google Fonts commit:

- Barlow Condensed: Regular, Bold, Italic; `ofl/barlowcondensed/`, SIL OFL 1.1, `barlowcondensed-OFL.txt`.
- Poppins: Regular, Bold, Italic; `ofl/poppins/`, SIL OFL 1.1, `poppins-OFL.txt`.
- Varela Round: Regular; `ofl/varelaround/`, SIL OFL 1.1, `varelaround-OFL.txt`.
- Space Mono: Regular, Bold, Italic; `ofl/spacemono/`, SIL OFL 1.1, `spacemono-OFL.txt`.
- Crimson Text: Regular, Bold, Italic; `ofl/crimsontext/`, SIL OFL 1.1, `crimsontext-OFL.txt`.

Barlow Condensed covers condensed sans, Poppins geometric sans, Varela Round rounded sans, Crimson Text serif, Space Mono monospace; existing Amiri covers Arabic. Each regular/bold/italic entry is an actual static face. All hashes are pinned in the catalog. Font matching retains its existing execution and staging bounds and may report a bound failure instead of a partial ranking.
