# SEO architecture

The goal is utilitarian search traffic — people looking to _do_ something with
a file — so every landing page opens the working tool above the copy. A visitor
from search lands on something usable, not on an article about the thing.

## Page families

| Family                      | Path                                             | Source                                                            |
| --------------------------- | ------------------------------------------------ | ----------------------------------------------------------------- |
| Home                        | `/{locale}`                                      | Static + config                                                   |
| Tool                        | `/{locale}/{tool-slug}`                          | `seo_pages`, `kind="tool"`                                        |
| Format landing              | `/{locale}/{format-slug}`                        | `seo_pages`, `kind="format"`, `FORMAT_PAGES` in `domain/tools.py` |
| Language pair               | `/{locale}/translate-image/{source}-to-{target}` | `seo_pages`, `kind="language_pair"`                               |
| Blog                        | `/{locale}/blog/{slug}`                          | `blog_posts`                                                      |
| Legal, pricing, API, status | `/{locale}/…`                                    | Static                                                            |

Copy lives in the CMS (`seo_pages`), not in the component tree, so marketing
can edit a landing page without a deploy. One React route serves each family —
`[tool]/page.tsx` covers tools and format pages, `translate-image/[pair]`
covers language pairs — because hundreds of near-identical files would be a
maintenance liability, not an SEO advantage.

## Localised slugs

Some locales publish a tool under a native-language slug because that is what
people actually search for:

| Tool             | `en`                  | `ru`                   |
| ---------------- | --------------------- | ---------------------- |
| Photo translator | `/en/translate-photo` | `/ru/perevod-po-foto`  |
| Image to text    | `/en/image-to-text`   | `/ru/tekst-s-kartinki` |
| Photo to Word    | `/en/jpg-to-word`     | `/ru/foto-v-word`      |
| Photo to Excel   | `/en/image-to-excel`  | `/ru/foto-v-excel`     |
| PDF translator   | `/en/pdf-translator`  | `/ru/perevod-pdf`      |

The map lives in `LOCALIZED_SLUGS` (`apps/api/picglot/domain/tools.py`) and is
served to the web app through `/api/v1/config`, so the backend stays the single
source of truth and the two can't drift.

Three rules make this safe rather than a duplicate-content problem:

1. **Replacement, not addition.** A locale with an override publishes _only_
   under it. The seeder writes one page per tool per locale, at the localised
   path.
2. **The English path redirects.** `/ru/translate-photo` sends a redirect to
   `/ru/perevod-po-foto` rather than serving the same page at two addresses.
3. **hreflang follows each locale's own slug**, so an alternate link never
   points at a URL that redirects.

Startup asserts guard against a localised slug colliding with a tool slug, a
format-page slug, or another localised slug — `test_seo.py` covers the same
invariants.

## Technical SEO

- SSR for every indexable page; the copy is in the HTML, not fetched client-side.
- `canonical` on every page; `hreflang` for all ten locales plus `x-default`.
- Sitemap is generated **from the CMS**, so a page cannot be published without
  becoming discoverable, and `noindex` pages are excluded by the API query
  itself rather than filtered in the template.
- `robots.txt`, web manifest, favicon, Open Graph and Twitter cards.
- Structured data: `BreadcrumbList` and `SoftwareApplication` on tool pages,
  `FAQPage` where real FAQ content exists, `Article` on blog posts. No
  invented ratings or review counts.
- `noindex` on private surfaces: `/app/*`, `/admin`, `/share/*`.

## Locale routing

The locale always comes from the URL, never from a cookie alone, so every page
has exactly one canonical address. The root `/` negotiates from
`Accept-Language` and redirects once, without creating a loop.

## Content rules

Every landing page carries a unique H1, a unique intro, real supported-format
and export lists pulled from the tool spec, and its own FAQ. A page that would
only differ from its neighbour by a substituted word is not worth publishing —
`kind="language_pair"` pages exist only for pairs with genuinely distinct
script guidance.

No invented testimonials or statistics. The testimonials component renders only
when real published rows exist in the database.

## Adding a landing page

1. Add the slug to `FORMAT_PAGES` (format page) or `LOCALIZED_SLUGS`
   (localised tool slug) in `apps/api/picglot/domain/tools.py`.
2. Add its copy to the seeder in `apps/api/picglot/db/seed.py`.
3. Run `python -m picglot.cli seed --baseline-only`.
4. Confirm it appears in `/sitemap.xml` and that its canonical and hreflang
   point where you expect.

No new React route is needed unless the URL shape itself is new.
