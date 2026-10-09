# Locally bundled interface fonts

Source Serif 4 (normal and italic) supports weight 200–900 and optical size 8–60; headings use real weight 300. Inter Tight supports weight 100–900; controls use 400–600. PT Serif's requested 300 is not assumed.

Unmodified variable TTFs from the Google Fonts repository:
- https://github.com/google/fonts/tree/main/ofl/sourceserif4
- https://github.com/google/fonts/tree/main/ofl/intertight

The separate SIL Open Font License files are bundled beside the fonts. Next.js local font loading serves them from the app, with no runtime font-network dependency. Arabic editing retains the system-font fallback; these interface fonts do not replace the separately licensed renderer caption fonts.
