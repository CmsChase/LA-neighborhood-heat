# Publication materials

The complete local publication package is under
`exports/PUBLICATION_MATERIALS`. A shareable ZIP is written to
`exports/PUBLICATION_MATERIALS.zip`.

The ZIP contains 17 files and 12,293,058 bytes. Its SHA-256 is
`2a91f09e993ebb0438bd987169862d308cb5182b5b8b56293d6b4ca41aae9493`;
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
| `LA_Surface_Heat_Research_Paper.docx` | `efdc41f67b9362bcea0c3172e0f9026112b8430bcd15e43bb1531e180289d738` |
| `LA_Surface_Heat_Research_Paper.pdf` | `24e7d2754ecc559ebd47fc955f51dd739d54b1cc24ee1168c70fb74b994edcaf` |
| `LA_Surface_Heat_Poster_36x48.pptx` | `9e6f516c8a7bbb1ce8ad61e742a86d5afa1ac3f6b62208a92f0383e63203e47d` |
| `LA_Surface_Heat_Poster_36x48.pdf` | `1b485aa276d5646a59f620607b29cbfccaa1d97d3b55afdaaef02d7282cbff68` |
| `LA_Surface_Heat_Defense_10_Slides.pptx` | `02f8ed81b88ad93a5fbd04b06ef5a61025b2c404570056d744a7820279c2f169` |

## Reproducible paper build

```powershell
.\.venv\Scripts\python -m pip install -r requirements-publication.txt
.\.venv\Scripts\python scripts\build_research_paper.py
```

The script uses `python-docx` for the editable document and ReportLab for a
matching deterministic PDF. The PDF was rendered with Poppler and every page
was visually reviewed. The DOCX was independently exported through Microsoft
Word as a 15-page review PDF and checked for its headings, paragraphs, tables,
media, section geometry, and fixed-width styles.

The editable slide-generation source is preserved inside the publication
package. It uses `@oai/artifact-tool`; the final PPTX files contain
repository-relative `[Sources]` notes on every slide.

## QA summary

- full project test suite: 743 tests passed;
- full-repository Ruff: passed;
- research paper: 15 pages, no clipped text or figures in the rendered PDF;
- Word-rendered editable paper: 15 pages with no orphaned final source page;
- poster: exact 36 × 48 inch PPTX and PDF;
- defense presentation: ten slides;
- inspected slide bounding boxes outside canvas: zero;
- machine-specific local paths in final speaker notes: zero.
