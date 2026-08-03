# Bundled fonts

Drop `.ttf`, `.otf` or `.ttc` files here to ship them with the deployment.
`FONT_DIR` points at this directory and it is searched **first**, before the
system font directories.

It is intentionally empty in the repository: fonts carry licences, and
redistributing someone else's typeface through a public repo is a licensing
problem, not a convenience. The Docker images install DejaVu and Noto
(including CJK) from the distribution instead, which covers every script the
product advertises.

Add fonts here when you need one the system packages do not provide — a brand
face, or a script with poor coverage. Record the licence for each file you add.

The directory being absent is not an error: `vision/fonts.py` skips a missing
`FONT_DIR` and falls back to the system directories.
