# forecast

Utilities for collecting brochure material used by forecasting experiments.

## Extract the current Winners.mu brochure

Winners publishes its current brochure at `https://www.winners.mu/ebrochure`; the page usually points to a `winners.promo` short link that resolves to a Paperturn flipbook. The extractor follows that chain and saves the brochure PDF when available, falling back to Paperturn page images when no PDF URL is exposed.

```bash
python3 extract_winners_brochure.py --output-dir extracted/winners_brochure
```

Useful troubleshooting flags:

- `--brochure-url <url>`: skip source-page discovery and fetch a known `winners.promo`/Paperturn link directly.
- `--save-debug-html`: save fetched HTML payloads (`debug-source-page.html` and `debug-brochure-page.html`) for debugging extraction issues.

The output directory contains:

- `manifest.json` — source URLs, final redirect targets, timestamp, and downloaded asset metadata.
- `assets/` — the extracted PDF or page images.

The script uses only Python's standard library so it can run without installing dependencies.
