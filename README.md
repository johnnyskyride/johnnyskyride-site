# johnnyskyride-site
Johnny Skyride website

## Section pages
Each section has its own address (/, /articles/, /music/, /imagery/, /sessions/, /members/, /store/).
`index.html` is the source of truth. After editing it, run:

    python3 tools/build-pages.py

and commit `index.html` together with all generated files (`<section>/index.html`, `404.html`).
Section titles, descriptions and preview images live in the `#jsr-pages` JSON block in `index.html`.
