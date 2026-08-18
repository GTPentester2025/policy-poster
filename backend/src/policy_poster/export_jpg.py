"""Stage 9 — JPG export: headless-browser render of the React poster
component at 300 DPI print-equivalent (spec §4 stage 9).

The React tree at /render/{run_id}/{orientation} is the source of truth;
Playwright screenshots the poster canvas with deviceScaleFactor 300/96.
"""

from __future__ import annotations

_SCALE = 300 / 96  # CSS px are 96 DPI; scale factor yields 300 DPI pixels
_SIZES = {"landscape": (1280, 720), "portrait": (720, 1280)}


def export_jpg(base_url: str, run_id: str, orientation: str, out_path: str,
               quality: int = 95, timeout_ms: int = 30_000) -> str:
    if orientation not in _SIZES:
        raise ValueError(f"unknown orientation: {orientation!r}")
    width, height = _SIZES[orientation]

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=_SCALE,
            )
            page.goto(f"{base_url}/render/{run_id}/{orientation}",
                      timeout=timeout_ms)
            page.wait_for_selector("[data-render-ready]", timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            canvas = page.locator("[data-poster-canvas]")
            canvas.screenshot(path=out_path, type="jpeg", quality=quality)
        finally:
            browser.close()
    return out_path
