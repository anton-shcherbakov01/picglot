# The font foundry

Building a typeface out of the lettering in a picture, and setting the
translation in it.

The rest of the product picks a face: it measures the lettering on the page and
finds the installed font closest to it (`vision/fontmatch.py`). That is a
choice among what a server happens to have installed — a few dozen text faces —
and a poster hand-lettered in condensed capitals is not among them.

The foundry (`picglot/foundry/`) does not choose. It builds.

## What it produces

A real TrueType file. The same binary is used to render the page, offered to
the user to download, and installable in any application. Nothing about it is
specific to this service: if it needed a special loader it would not be a font.

## How a face is built

    picture ─┬─ harvest ── the letters the page actually shows
             │
             └─ derive ─── the letters it does not, from the closest
                           installed face at the measured weight,
                           slant and width
                                    │
                              trace ── outlines
                                    │
                              build ── TrueType

**Harvesting** (`harvest.py`) splits a line of ink into marks and pairs them
with the letters of the string OCR already read. If the counts do not match
exactly the line is dropped whole, because a mispaired sample is a font where
one letter is quietly drawn as another — worse than a missing letter, which is
visibly missing.

**Tracing** (`outlines.py`) turns a glyph bitmap into quadratic contours.
Vertices the outline turns gently at become off-curve control points, which is
the difference between a letter and a scan of a letter.

**Deriving** (`donor.py`) rasterises a donor face and bends it: shear for the
lean, a horizontal scale for the width, morphology for the weight. The weight
is calibrated by measuring — the derived letters are built into a font,
rendered, and measured with the same measurement that read the original off the
page, because tracing an outline and rasterising it again does not preserve
stroke width to the pixel.

**Building** (`build.py`) assembles the outlines into a TTF with fontTools:
cmap, metrics from the sample's own baseline and descenders, and names that a
font manager will show sensibly.

`service.py` runs those in order for one page, and `materialize` registers the
result with `vision/fonts.py` so the renderer selects it by the ordinary path —
coverage checks included, so a generated face missing a character is skipped
for that string rather than drawn as tofu.

## What it cannot do

English lettering contains no **Ж**. Four Latin letters do not say what the Ж
of the same hand looks like, and nothing in this package invents one: a Ж comes
from a donor, made heavier, narrower or more slanted to match. Where the source
and the target share an alphabet — English to German, Russian to Ukrainian —
the letters really are the page's own.

Closing that gap means learning a mapping between alphabets from many hands,
which is a trained model, not a measurement. The seam is deliberate:
`donor.derive` is the only thing that produces a letter nobody wrote, so a
model would replace that one function and everything around it stays.

## Turning it on

`FONT_SYNTHESIS_ENABLED=true`, and per job `options.synthesize_font` (defaults
to on when the setting allows it). Off by default: it costs a second or two per
page, and the identified face is a reasonable answer without it.

The generated file is stored as an asset of kind `font` on the project, with
the characters traced and derived recorded in its metadata.

## Not built yet

- **The handwriting sheet.** A printable grid, filled in by hand, photographed
  and cut back into glyphs — the same `outlines`/`build` core with a different
  front end. `harvest.py` covers the segmentation; what is missing is the sheet
  itself and the registration marks that let a phone photo be squared up.
- **An endpoint and a page in the editor.** Font creation is currently reachable
  only through the pipeline; there is no `POST /fonts` and no UI for managing a
  library of generated faces.
- **Reuse across pages.** Each page builds its own face. A document set in one
  typeface should build one font and share it.
