"""Real-browser acceptance checks; run explicitly, outside pytest collection.

python tests/browser_acceptance.py [--screenshot]
Install .[dev,browser] and `python -m playwright install chromium` first.
BROWSER_EXECUTABLE may point to an installed Chrome/Edge executable.
"""
import asyncio
import csv
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import urllib.request

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


async def verify(base):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=os.environ.get("BROWSER_EXECUTABLE"))
        page = await browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(base)
        await page.get_by_role("button", name="Sign in", exact=True).click()
        await expect(page.locator("#library-status")).to_have_text("58 matching assets")
        await page.locator("#search").fill("harbor")
        await page.locator("#filter-collection").select_option(label="Quiz Harbor Campaign")
        for name in ("approved", "campaign"):
            await page.locator("#include-input").fill(name)
            await page.locator("#include-form").get_by_role("button").click()
        await page.locator("#exclude-input").fill("internal")
        await page.locator("#exclude-form").get_by_role("button").click()
        await expect(page.locator("#library-status")).to_have_text("54 matching assets")
        await page.locator("#filter-media").select_option("image")
        await expect(page.locator("#library-status")).to_have_text("53 matching assets")
        await expect(page.locator("#grid .card")).to_have_count(24)
        await expect(page.locator("#prev-page")).to_be_disabled()
        await page.locator("#sort").select_option("oldest")
        await expect(page.locator("#page-info")).to_have_text("Page 1 of 3 · 53 matches")
        if "--screenshot" in sys.argv:
            for image in await page.locator("#grid img").all():
                await image.scroll_into_view_if_needed()
            await page.wait_for_function("[...document.querySelectorAll('#grid img')].every(i => i.complete && i.naturalWidth > 0)")
            await page.evaluate("scrollTo(0, 0)")
            await page.screenshot(path=str(ROOT / "docs/screenshots/07-search-export.png"), full_page=True)
        all_ids = []
        for count in (24, 24, 5):
            await expect(page.locator("#grid .card")).to_have_count(count)
            all_ids.extend(await page.locator("#grid .card").evaluate_all("cards => cards.map(c => c.dataset.uuid)"))
            if count == 24:
                await page.locator("#next-page").click()
                await expect(page.locator("#library-status")).to_have_text("53 matching assets")
        await expect(page.locator("#next-page")).to_be_disabled()
        assert len(set(all_ids)) == 53
        async with page.expect_download() as download_event:
            await page.locator("#btn-export").click()
        download = await download_event.value
        rows = list(csv.DictReader(io.StringIO(Path(await download.path()).read_text(encoding="utf-8-sig"), newline="")))
        assert [r["uuid"] for r in rows] == all_ids
        await page.locator("#include-input").fill("标签,完整名称")
        await page.locator("#include-form").get_by_role("button").click()
        await expect(page.locator("#library-status")).to_have_text("1 matching assets")
        await expect(page.locator("#page-info")).to_have_text("Page 1 of 1 · 1 matches")
        await page.get_by_role("button", name="Remove Include: 标签,完整名称", exact=True).click()
        await expect(page.locator("#library-status")).to_have_text("53 matching assets")
        await page.locator("#sort").select_option("newest")
        await expect(page.locator("#page-info")).to_have_text("Page 1 of 3 · 53 matches")

        # Delay the old query; change keywords again while it is in flight.
        delayed = asyncio.Event()

        async def delay_old(route):
            delayed.set()
            await asyncio.sleep(.8)
            try:
                await route.continue_()
            except Exception:
                pass  # Aborted by the newer query, as intended.

        await page.route("**/api/assets?q=mountain&*", delay_old)
        await page.locator("#search").fill("mountain")
        await asyncio.wait_for(delayed.wait(), 5)
        await expect(page.locator("#btn-export")).to_be_disabled()
        await page.locator("#search").fill("harbor")
        await expect(page.locator("#library-status")).to_have_text("53 matching assets")
        await page.wait_for_timeout(1100)
        await expect(page.locator("#library-status")).to_have_text("53 matching assets")
        await page.unroute("**/api/assets?q=mountain&*", delay_old)

        async def fail(route):
            await route.fulfill(status=500, body="simulated failure")

        await page.route("**/api/assets/export.csv?*", fail)
        await page.locator("#btn-export").click()
        await expect(page.locator("#export-status")).to_have_text("CSV download failed. Please try again.")
        await page.unroute("**/api/assets/export.csv?*", fail)
        await page.route("**/api/assets?*", fail)
        await page.locator("#sort").select_option("oldest")
        await expect(page.locator("#library-error")).to_be_visible()
        await expect(page.locator("#btn-export")).to_be_disabled()
        await page.unroute("**/api/assets?*", fail)
        await page.locator("#retry-library").click()
        await expect(page.locator("#library-status")).to_have_text("53 matching assets")

        # Original edit entry point must update list and subsequent export.
        await page.locator("#grid .card").first.click()
        await page.locator("#e-title").fill("Renamed acceptance sample")
        await page.locator("#d-save").click()
        await expect(page.locator("#library-status")).to_have_text("53 matching assets")
        await page.locator("#drawer-close").click()
        await page.locator("#search").fill("Renamed")
        await expect(page.locator("#library-status")).to_have_text("1 matching assets")
        await page.locator("#grid .card").first.click()
        await page.locator("#tag-add").fill("browser-added/活动")
        await page.locator("#tag-add").press("Enter")
        await expect(page.locator('#tag-row .rm[data-tag="browser-added/活动"]')).to_be_visible()
        await page.locator("#drawer-close").click()
        await page.locator("#search").fill("browser-added")
        await expect(page.locator("#library-status")).to_have_text("1 matching assets")
        await page.locator("#grid .card").first.click()
        await page.locator('#tag-row .rm[data-tag="browser-added/活动"]').click()
        await expect(page.locator("#library-status")).to_have_text("0 matching assets")
        await page.locator("#drawer-close").click()
        await page.locator("#search").fill("Renamed")
        await expect(page.locator("#library-status")).to_have_text("1 matching assets")

        await page.locator("#include-input").fill("missing-tag")
        await page.locator("#include-form").get_by_role("button").click()
        await expect(page.locator("#empty")).to_be_visible()
        async with page.expect_download() as empty_download_event:
            await page.locator("#btn-export").click()
        empty_download = await empty_download_event.value
        assert len(Path(await empty_download.path()).read_text(encoding="utf-8-sig").splitlines()) == 1
        await page.get_by_role("button", name="Remove Include: missing-tag", exact=True).click()
        await expect(page.locator("#library-status")).to_have_text("1 matching assets")
        await page.locator("#clear-filters").click()
        await expect(page.locator("#library-status")).to_have_text("58 matching assets")
        await expect(page.locator("#active-filters")).to_have_text("All active assets")
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        await browser.close()
        print("Browser acceptance passed: filters, 3 pages, CSV order/download, race, failures/retry, editing, empty CSV, clear, mobile.")


async def main():
    with tempfile.TemporaryDirectory(prefix="keepstack-browser-") as data_dir:
        env = dict(os.environ, KEEPSTACK_DATA_DIR=data_dir, KEEPSTACK_AI_ENABLED="false", KEEPSTACK_OCR_ENABLED="false")
        # Rerunning the generator must restore exactly the same 60 records.
        for _ in range(2):
            subprocess.run([sys.executable, "-m", "keepstack", "seed-quiz"], cwd=ROOT, env=env, check=True)
        import sqlite3
        with sqlite3.connect(str(Path(data_dir) / "keepstack.db")) as conn:
            assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 60
        conn.close()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        env.update(KEEPSTACK_HOST="127.0.0.1", KEEPSTACK_PORT=str(port))
        base = f"http://127.0.0.1:{port}"
        with tempfile.TemporaryFile(mode="w+b") as log:
            server = subprocess.Popen([sys.executable, "-m", "keepstack"], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        log.seek(0); raise RuntimeError(log.read().decode("utf-8", "replace"))
                    try:
                        await asyncio.to_thread(urllib.request.urlopen, base + "/healthz", timeout=1)
                        break
                    except OSError:
                        await asyncio.sleep(.1)
                else:
                    raise RuntimeError("Server startup timed out")
                await verify(base)
            finally:
                server.terminate(); server.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(main())
