# Publication materials

The complete local publication package is under
`exports/PUBLICATION_MATERIALS`. A shareable ZIP is written to
`exports/PUBLICATION_MATERIALS.zip`.

The ZIP contains 17 files and 12,292,163 bytes. Its SHA-256 is
`dd3729a8c7f0a45efa86f9e14614490c1aa2d1f7d16467ddbc0db4ce1d4a05a5`;
an isolated extraction matched all 17 source-file hashes.

## Contents

- `LA_Surface_Heat_Research_Paper.docx` and `.pdf`: a complete 15-page English
  paper with abstract, methods, results, discussion, limitations,
  reproducibility, conclusion, references, and frozen-identity appendix;
- `LA_Surface_Heat_Poster_36x48.pptx` and `.pdf`: a 36 × 48 inch portrait
  scientific poster;
- `LA_Surface_Heat_Defense_10_Slides.pptx`: a ten-slide 16:9 oral-defense
  presentation;
- slide inspection inventories, the editable slide-generation source, and
  prepared presentation assets;
- a package README, publication dependency list, scientific boundaries,
  hashes, and rebuild notes.

All three formats share the same warm-ivory, deep-ink, terracotta, and forest
visual system as the interactive results site. The poster and presentation put
Landsat-observed LST beside B1 and M2 predictions, then show residuals,
date-to-date error, uncertainty, and hotspot ranking.

## Fixed message

The frozen held-out result is:

- B1 equal-date MAE: 3.1165 °C;
- M2 equal-date MAE: 2.1650 °C;
- point reduction: 0.9516 °C or 30.53%;
- 95% relative-improvement interval: -10.13% to 58.46%;
- overall protocol success: false.

The materials therefore say **promising, not protocol-confirmed**. They do not
describe LST as air temperature or human exposure, do not present the
historical hindcast as a live forecast, and do not infer causation from the
B1/M2 contrast.

## Deliverable hashes

| File | SHA-256 |
|---|---|
| `LA_Surface_Heat_Research_Paper.docx` | `6e6d79799abcd2b22eb6f0ad654019b37b9e212286f25b1e71358412dcd840c5` |
| `LA_Surface_Heat_Research_Paper.pdf` | `6bc9e1fdd9d3302c0abe5a40cb542f9241a093351908a9419a649af980fd80e8` |
| `LA_Surface_Heat_Poster_36x48.pptx` | `fdc18bc09a45906c2f29ecc71921175fac652125a76aeb757fd696d2e00b2fd8` |
| `LA_Surface_Heat_Poster_36x48.pdf` | `173728d30fa00499adc4760fd4c5859b80ff8392beb1b30b203baedb36837a19` |
| `LA_Surface_Heat_Defense_10_Slides.pptx` | `9bf91facee189fdbc133f9ab09c1cd54d2fb372eecf201bbc7725be4d14e1e74` |

## Reproducible paper build

```powershell
.\.venv\Scripts\python -m pip install -r requirements-publication.txt
.\.venv\Scripts\python scripts\build_research_paper.py
```

The script uses `python-docx` for the editable document and ReportLab for a
matching PDF because Microsoft Word COM initialization was unavailable in the
headless session. The PDF was rendered with Poppler and every page was visually
reviewed. The DOCX was independently checked for its headings, paragraphs,
tables, media, section geometry, and fixed-width styles.

The editable slide-generation source is preserved inside the publication
package. It uses `@oai/artifact-tool`; the final PPTX files contain
repository-relative `[Sources]` notes on every slide.

## QA summary

- full project test suite: passed;
- full-repository Ruff: passed;
- research paper: 15 pages, no clipped text or figures in the rendered PDF;
- poster: exact 36 × 48 inch PPTX and PDF;
- defense presentation: ten slides;
- inspected slide bounding boxes outside canvas: zero;
- machine-specific local paths in final speaker notes: zero.
