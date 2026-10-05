"""Bounded subprocess entry point; never invoked in the API process for uploaded fonts."""

import json
import sys
import unicodedata
from pathlib import Path

from fontTools import version
from fontTools.pens.basePen import NullPen
from fontTools.ttLib import TTFont


def inspect(path, output=None, family=None):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Font exceeds 2 MiB.")
    if path.read_bytes()[:4] not in {b"\x00\x01\x00\x00", b"OTTO"}:
        raise ValueError("Use a static TTF or OTF, not a collection or web font.")
    with TTFont(path, lazy=False, checkChecksums=2, ignoreDecompileErrors=False) as font:
        if font.flavor or not 1 <= len(font.reader.tables) <= 64:
            raise ValueError("Unsupported font container or table count.")
        if any(t in font for t in ("fvar", "CFF2", "COLR", "CBDT", "sbix", "SVG ")):
            raise ValueError("Variable and color fonts are unsupported; use a static outline font.")
        if not {"head", "hhea", "hmtx", "maxp", "name", "cmap"}.issubset(font.keys()):
            raise ValueError("Font is missing required outline/metric tables.")
        if not any(t in font for t in ("glyf", "CFF ")):
            raise ValueError("Font has no supported outlines.")
        if not 1 <= font["maxp"].numGlyphs <= 20000:
            raise ValueError("Use a font with at most 20,000 glyphs.")
        font.ensureDecompiled(recurse=True)
        cmap = font.getBestCmap()
        if (
            not cmap
            or len(cmap) > 65535
            or any(not 0 <= c <= 0x10FFFF or 0xD800 <= c <= 0xDFFF for c in cmap)
        ):
            raise ValueError("Font has an unsupported Unicode character map.")
        glyphs = font.getGlyphSet()
        for glyph in glyphs.values():
            glyph.draw(NullPen())

        def readable(value):
            return " ".join(
                "".join(
                    c for c in value or "" if unicodedata.category(c) not in {"Cc", "Cf", "Cs"}
                ).split()
            )[:128]

        info = {
            "family": readable(font["name"].getBestFamilyName()),
            "style": readable(font["name"].getBestSubFamilyName()),
            "codepoints": sorted(cmap),
            "parser_version": version,
        }
        if not info["family"] or not info["style"]:
            raise ValueError("Font has no readable family/style name.")
        if output:
            # A server-owned unique name prevents collisions with installed/bundled fonts.
            for record in font["name"].names:
                if record.nameID in {1, 3, 4, 6, 16}:
                    record.string = family.encode(record.getEncoding())
            if "CFF " in font:
                cff = font["CFF "].cff
                cff.fontNames[0] = family
                cff.topDictIndex[0].FamilyName = family
                cff.topDictIndex[0].FullName = family
            font.save(output)
            if output.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Prepared font exceeds 2 MiB.")
        return info


if __name__ == "__main__":
    try:
        info = inspect(
            Path(sys.argv[1]),
            Path(sys.argv[2]) if len(sys.argv) > 2 else None,
            sys.argv[3] if len(sys.argv) > 3 else None,
        )
        print(json.dumps(info))
    except ValueError as exc:
        # Only our fixed validation descriptions may reach the client.
        print(
            json.dumps(
                {
                    "error": str(exc)
                    if exc.args and str(exc).endswith(".")
                    else "Font structure could not be validated."
                }
            )
        )
        sys.exit(1)
    except Exception:
        print(json.dumps({"error": "Font structure could not be validated."}))
        sys.exit(1)
