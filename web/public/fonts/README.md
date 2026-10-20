# Barlow fonts

The TTF files are used by the dashboard stylesheet. The WOFF2 files are lossless,
full-glyph conversions of those same files, embedded in chart exports so exported
SVGs and rasterized PNGs do not depend on locally installed fonts. Both formats
are covered by `OFL.txt`.

To regenerate the WOFF2 files from the project root, install `fonttools[woff]`
in the development environment and run:

```sh
.venv/bin/python -m fontTools.ttLib.woff2 compress web/public/fonts/Barlow-Medium.ttf -o web/public/fonts/Barlow-Medium.woff2
.venv/bin/python -m fontTools.ttLib.woff2 compress web/public/fonts/Barlow-Bold.ttf -o web/public/fonts/Barlow-Bold.woff2
```

Only the faces used by a chart are embedded, once per file. Medium covers weights
400 and 500; Bold covers 600 and 700, matching the dashboard stylesheet. Both
faces together add about 101 KB of base64 font data to an SVG.
