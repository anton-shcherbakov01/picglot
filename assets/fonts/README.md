# Bundled fonts

Drop `.ttf`, `.otf` or `.ttc` files here to ship them with the deployment.
`FONT_DIR` points at this directory and it is searched **first**, before the
system font directories.

Only fonts that may be redistributed belong here: shipping someone else's
typeface through a public repo is a licensing problem, not a convenience. The
Docker images install DejaVu and Noto (including CJK) from the distribution,
which covers every script the product advertises — in a grotesque, a serif and
a monospace.

Add fonts here when you need one the system packages do not provide — a brand
face, or a script with poor coverage. Record the licence for each file below.

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
