# Bundled fonts

Drop `.ttf`, `.otf` or `.ttc` files here to ship them with the deployment.
`FONT_DIR` points at this directory and it is searched **first**, before the
system font directories.

Only fonts that may be redistributed belong here: shipping someone else's
typeface through a public repo is a licensing problem, not a convenience. The
Docker images install DejaVu (core and extra), Noto (including CJK), Liberation
(whose v1 package carries Sans Narrow), Carlito and Caladea from the
distribution. That covers every script the product advertises and, past
coverage, gives `vision/fontmatch.py` a range of shapes and widths to identify
photographed lettering against — with one grotesque installed, every job comes
back set in it.

Add fonts here when you need one the system packages do not provide — a brand
face, a script with poor coverage, or a style nothing installed comes close to
(script and display faces especially). Record the licence for each file below.
Anything dropped here joins the candidates automatically.

## Shipped

| File                                    | Family | Licence                        | Why                                                                                                                                                                                                                                                                                                                                              |
| --------------------------------------- | ------ | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `Caveat-Regular.ttf`, `Caveat-Bold.ttf` | Caveat | SIL OFL 1.1 (`Caveat-OFL.txt`) | The only handwriting face available to the renderer. Every other name in `FontClass.HANDWRITING` — Comic Sans, Segoe Script, Bradley Hand — is a Windows or macOS font absent from a Linux image, so detected handwriting fell through to the grotesque and was drawn as if it had never been detected at all. Caveat covers Latin and Cyrillic. |

Anything added here needs the script coverage to match its purpose: a
handwriting face that cannot draw Cyrillic leaves Russian output back on the
fallback. `vision/fonts.py` checks the cmap before selecting, so a font that
cannot draw the string is skipped rather than rendered as tofu.

The directory being absent is not an error: `vision/fonts.py` skips a missing
`FONT_DIR` and falls back to the system directories.
