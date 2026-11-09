#!/usr/bin/env python3
"""Extract the currently published Winners.mu brochure.

The Winners brochure page embeds/links to a Paperturn flipbook. This script:
1. downloads the Winners brochure page,
2. discovers the brochure/short-link URL,
3. follows it to the Paperturn publication,
4. saves the original PDF when Paperturn exposes one, otherwise downloads page
   images that Paperturn exposes in the HTML/JavaScript payload, and
5. writes a manifest describing what was extracted.

Only the Python standard library is used so it can run in constrained runners.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import time
from dataclasses import dataclass, asdict
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

DEFAULT_BROCHURE_PAGE = "https://www.winners.mu/ebrochure"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


@dataclass
class DownloadedAsset:
    url: str
    path: str
    content_type: str | None
    bytes: int


@dataclass
class ExtractionManifest:
    source_page: str
    source_page_final_url: str
    brochure_url: str
    brochure_final_url: str
    extracted_at_utc: str
    assets: list[DownloadedAsset]


def fetch(url: str, timeout: int = 45) -> tuple[str, bytes, dict[str, str]]:
    """Fetch a URL and return final URL, body bytes, and lower-case headers."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urlopen(request, timeout=timeout) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        return response.geturl(), response.read(), headers


def decode_text(body: bytes, headers: dict[str, str]) -> str:
    content_type = headers.get("content-type", "")
    match = re.search(r"charset=([^;]+)", content_type, flags=re.I)
    encoding = match.group(1).strip() if match else "utf-8"
    return body.decode(encoding, errors="replace")


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def extract_urls(html: str, base_url: str) -> list[str]:
    """Extract common href/src/data URL attributes and bare URLs from HTML/JS."""
    html = unescape(html)
    urls: list[str] = []
    attr_pattern = re.compile(
        r"(?:href|src|data-src|data-href|content)\s*=\s*(['\"])(.*?)\1",
        flags=re.I | re.S,
    )
    for match in attr_pattern.finditer(html):
        value = match.group(2).strip()
        if value and not value.startswith(("#", "mailto:", "tel:", "javascript:")):
            urls.append(urljoin(base_url, value))

    bare_url_pattern = re.compile(r"https?://[^\s'\"<>),]+", flags=re.I)
    urls.extend(unescape(match.group(0)) for match in bare_url_pattern.finditer(html))
    return unique_preserve_order(urls)


def find_brochure_url(html: str, base_url: str) -> str:
    urls = extract_urls(html, base_url)
    preferred_markers = ("winners.promo", "paperturn-view.com", "paperturn.com")
    for marker in preferred_markers:
        for url in urls:
            if marker in urlparse(url).netloc.lower() or marker in url.lower():
                return url
    raise RuntimeError("Could not find a Winners promo or Paperturn brochure URL on the page")


def is_asset_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return (
        path.endswith(".pdf")
        or path.endswith((".jpg", ".jpeg", ".png", ".webp"))
        or "images.paperturn" in host
        or "paperturn" in host and any(token in path for token in ("/pages/", "/page/", "/files/"))
    )


def discover_asset_urls(flipbook_html: str, flipbook_url: str) -> list[str]:
    urls = [url for url in extract_urls(flipbook_html, flipbook_url) if is_asset_url(url)]

    # Paperturn payloads often include escaped URLs in JSON/JavaScript strings.
    unescaped = flipbook_html.replace("\\/", "/")
    urls.extend(url for url in extract_urls(unescaped, flipbook_url) if is_asset_url(url))

    return unique_preserve_order(urls)


def safe_filename(url: str, index: int, content_type: str | None = None) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name or f"asset-{index:03d}"
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or f"asset-{index:03d}"
    if "." not in name and content_type:
        extension = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if extension:
            name += extension
    return f"{index:03d}-{name}"


def download_assets(urls: list[str], output_dir: Path) -> list[DownloadedAsset]:
    output_dir.mkdir(parents=True, exist_ok=True)
    assets: list[DownloadedAsset] = []
    for index, url in enumerate(urls, start=1):
        try:
            final_url, body, headers = fetch(url)
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"warning: failed to download {url}: {exc}", file=sys.stderr)
            continue
        content_type = headers.get("content-type")
        filename = safe_filename(final_url, index, content_type)
        path = output_dir / filename
        path.write_bytes(body)
        assets.append(
            DownloadedAsset(
                url=final_url,
                path=str(path),
                content_type=content_type,
                bytes=len(body),
            )
        )
    return assets


def extract(
    source_page: str,
    output_dir: Path,
    brochure_url_override: str | None = None,
    save_debug_html: bool = False,
) -> ExtractionManifest:
    page_final_url, page_body, page_headers = fetch(source_page)
    page_html = decode_text(page_body, page_headers)
    brochure_url = brochure_url_override or find_brochure_url(page_html, page_final_url)

    if save_debug_html:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "debug-source-page.html").write_text(page_html, encoding="utf-8")

    brochure_final_url, brochure_body, brochure_headers = fetch(brochure_url)
    brochure_content_type = brochure_headers.get("content-type", "")

    if "application/pdf" in brochure_content_type.lower():
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = assets_dir / "001-winners-brochure.pdf"
        pdf_path.write_bytes(brochure_body)
        assets = [
            DownloadedAsset(
                url=brochure_final_url,
                path=str(pdf_path),
                content_type=brochure_content_type,
                bytes=len(brochure_body),
            )
        ]
    else:
        flipbook_html = decode_text(brochure_body, brochure_headers)
        if save_debug_html:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "debug-brochure-page.html").write_text(flipbook_html, encoding="utf-8")
        asset_urls = discover_asset_urls(flipbook_html, brochure_final_url)
        if not asset_urls:
            raise RuntimeError(
                "Reached the brochure URL, but no PDF/page-image assets were found. "
                "Paperturn may have changed its payload or disabled downloads."
            )
        pdfs = [url for url in asset_urls if urlparse(url).path.lower().endswith(".pdf")]
        images = [url for url in asset_urls if url not in pdfs]
        assets = download_assets(pdfs or images, output_dir / "assets")
        if not assets:
            raise RuntimeError("Discovered asset URLs, but none could be downloaded.")

    manifest = ExtractionManifest(
        source_page=source_page,
        source_page_final_url=page_final_url,
        brochure_url=brochure_url,
        brochure_final_url=brochure_final_url,
        extracted_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        assets=assets,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_BROCHURE_PAGE, help="Winners brochure page URL")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("extracted/winners_brochure"),
        help="Directory where assets and manifest.json are written",
    )
    parser.add_argument(
        "--brochure-url",
        default=None,
        help="Optional direct brochure URL (for example a winners.promo or paperturn URL).",
    )
    parser.add_argument(
        "--save-debug-html",
        action="store_true",
        help="Save fetched source and brochure HTML pages in the output directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = extract(
            args.source,
            args.output_dir,
            brochure_url_override=args.brochure_url,
            save_debug_html=args.save_debug_html,
        )
    except Exception as exc:  # surface a concise CLI error while preserving library exceptions above
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(manifest), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
