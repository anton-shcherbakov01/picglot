# Bundled fonts

Drop `.ttf`, `.otf` or `.ttc` files here to ship them with the deployment.
`FONT_DIR` points at this directory and it is searched **first**, before the
system font directories.

It is intentionally empty in the repository: fonts carry licences, and
redistributing someone else's typeface through a public repo is a licensing
problem, not a convenience. The Docker images install DejaVu (core and extra),
Noto (including CJK), Liberation, Liberation Sans Narrow, Carlito and Caladea
from the distribution instead. That covers every script the product advertises
and, past coverage, gives `vision/typeface.py` a range of shapes and widths to
match photographed lettering against — with only one grotesque installed, every
job comes back set in it.

Add fonts here when you need one the system packages do not provide — a brand
face, a script with poor coverage, or a style the matcher has nothing close to
(script and display faces especially). Record the licence for each file you
add. Anything dropped here joins the matcher's candidates automatically.

The directory being absent is not an error: `vision/fonts.py` skips a missing
`FONT_DIR` and falls back to the system directories.
