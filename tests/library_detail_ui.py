"""Offline browser regression for library autosave and image navigation.
Run with the local Python environment containing playwright and installed Chrome.
"""
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def main():
    image = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='640' height='480'%3E%3Crect width='640' height='480' fill='%23dce9f5'/%3E%3C/svg%3E"
    assets = {str(i): dict(id=str(i), filename=f"Bild-{i}.jpg", tags=[], nsfw_level="sfw",
                          lifecycle="new", width=640, height=480, bytes=1000, mime="image/jpeg",
                          url=image, thumb_url=image, history=[], open_channels=[],
                          assigned_channel_ids=[], used_channel_ids=[], scheduled_channel_ids=[], usage_count=0, set_ids=[], set_names=[])
              for i in range(1, 4)}
    writes, errors, unexpected = [], [], []
    fail = {"enabled": False}
    channels = [dict(id="x", display_name="Test X", color="#168deb", platform="x")]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("pageerror", lambda error: (errors.append(str(error)), print("PAGE ERROR:", error, flush=True)))

        def route_request(route):
            request = route.request
            path = urlparse(request.url).path
            if urlparse(request.url).hostname != "library.test":
                unexpected.append(request.url)
                return route.abort()
            if path == "/":
                return route.fulfill(content_type="text/html", body='''<!doctype html><meta charset=utf-8>
                    <link rel=stylesheet href=/autoposter/app.css><div id=root></div><div id=modals></div><div id=toasts></div>
                    <script type=module>
                    import render from '/autoposter/js/pages/library.js';
                    document.querySelector('#root').appendChild(await render({query:new URLSearchParams()}));
                    </script>''')
            if path.startswith("/autoposter/"):
                file = ROOT / "autoposter/ui" / path.removeprefix("/autoposter/")
                return route.fulfill(body=file.read_bytes(), content_type="text/css" if file.suffix == ".css" else "text/javascript")
            if path == "/api/v1/channels":
                return route.fulfill(json=channels)
            if path in ("/api/v1/views", "/api/v1/media/tags", "/api/v1/sets"):
                return route.fulfill(json=[])
            if path == "/api/v1/media/counts":
                return route.fulfill(json={"total": 3})
            if path == "/api/v1/media/search":
                q = request.post_data_json
                return route.fulfill(json={"items": [assets["3"]] if q.get("cursor") else [assets["1"], assets["2"]], "next_cursor": None if q.get("cursor") else "page2"})
            if path == "/api/v1/assignments/batch":
                payload = request.post_data_json
                for id in payload["asset_ids"]:
                    assets[id]["assigned_channel_ids"] += payload["channel_ids"]
                    assets[id]["open_channels"] += payload["channel_ids"]
                return route.fulfill(json={"created": 1, "rejected": []})
            if path.startswith("/api/v1/media/"):
                id = path.split("/")[4]
                if request.method == "PATCH":
                    time.sleep(0.08)
                    writes.append((id, request.post_data_json))
                    if fail["enabled"]:
                        return route.fulfill(status=503, json={"detail": "Test: Speichern fehlgeschlagen"})
                    assets[id].update(request.post_data_json)
                elif path.endswith("/archive"):
                    assets[id]["lifecycle"] = "archived"
                return route.fulfill(json=assets[id])
            unexpected.append(request.method + " " + path)
            route.abort()

        page.route("**/*", route_request)
        page.goto("http://library.test")
        page.wait_for_timeout(200)
        assert not errors, errors
        assert not unexpected, unexpected
        page.locator(".tile").first.click()
        dialog = page.locator(".modal")
        expect(dialog.locator("h3")).to_have_text("Bild-1.jpg")
        expect(dialog.get_by_role("button", name="Vorheriges Bild")).to_be_disabled()
        page.set_viewport_size({"width": 390, "height": 844})
        nav = dialog.locator('.library-detail-nav')
        box = nav.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390, box
        prev = nav.get_by_role("button", name="Vorheriges Bild").bounding_box()
        next_ = nav.get_by_role("button", name="Nächstes Bild").bounding_box()
        assert prev["x"] + prev["width"] <= next_["x"], (prev, next_)
        page.screenshot(path="/private/tmp/library-detail-mobile.png", full_page=True)
        page.set_viewport_size({"width": 1280, "height": 1000})
        page.screenshot(path="/private/tmp/library-detail-desktop.png", full_page=True)
        assert "Dieses Bild darf" not in dialog.inner_text()
        assert dialog.get_by_role("button", name="Speichern", exact=True).count() == 0
        dialog.locator("select").select_option("explicit")
        expect(dialog.get_by_role("status")).to_have_text("Gespeichert")
        assert assets["1"]["nsfw_level"] == "explicit"
        # Pending tag edits are flushed before switching images, not lost to debounce.
        dialog.locator("input").fill("eins, zwei")
        dialog.get_by_role("button", name="Nächstes Bild").click()
        expect(dialog.locator("h3")).to_have_text("Bild-2.jpg")
        assert assets["1"]["tags"] == ["eins", "zwei"]
        dialog.get_by_role("button", name="Vorheriges Bild").click()
        expect(dialog.locator("input")).to_have_value("eins, zwei")
        expect(dialog.locator("select")).to_have_value("explicit")
        dialog.get_by_role("button", name="+ Test X", exact=True).click()
        expect(dialog.get_by_role("button", name="+ Test X", exact=True)).to_have_count(0)
        expect(dialog.locator("h3")).to_have_text("Bild-1.jpg")
        dialog.get_by_role("button", name="Archivieren", exact=True).click()
        expect(dialog.get_by_role("button", name="Reaktivieren", exact=True)).to_be_visible()
        # Pagination at the end of the loaded search results.
        dialog.get_by_role("button", name="Nächstes Bild").click()
        expect(dialog.locator("h3")).to_have_text("Bild-2.jpg")
        dialog.get_by_role("button", name="Nächstes Bild").click()
        expect(dialog.locator("h3")).to_have_text("Bild-3.jpg")
        expect(dialog.get_by_role("button", name="Nächstes Bild")).to_be_disabled()
        # Failed saves keep the same dialog and values, including Escape and header X.
        fail["enabled"] = True
        dialog.locator("input").fill("nicht verlieren")
        dialog.get_by_role("button", name="Vorheriges Bild").click()
        expect(dialog.get_by_role("status")).to_contain_text("Nicht gespeichert")
        expect(dialog.locator("h3")).to_have_text("Bild-3.jpg")
        page.keyboard.press("Escape")
        expect(dialog.get_by_role("status")).to_contain_text("Nicht gespeichert")
        dialog.locator(".modal-head button").click()
        expect(dialog.get_by_role("status")).to_contain_text("Nicht gespeichert")
        expect(dialog.locator("input")).to_have_value("nicht verlieren")
        fail["enabled"] = False
        dialog.get_by_role("button", name="Schließen", exact=True).click()
        expect(dialog).to_have_count(0)
        assert assets["3"]["tags"] == ["nicht verlieren"]
        # Fast consecutive category changes must finish in the latest selected state.
        page.locator(".tile").nth(1).click()
        expect(dialog.locator("h3")).to_have_text("Bild-2.jpg")
        page.evaluate("""() => { const s = document.querySelector('.modal select');
            for (const v of ['explicit', 'sfw', 'explicit']) { s.value=v; s.dispatchEvent(new Event('change')); } }""")
        expect(dialog.get_by_role("status")).to_have_text("Gespeichert")
        dialog.get_by_role("button", name="Schließen", exact=True).click()
        expect(dialog).to_have_count(0)
        assert assets["2"]["nsfw_level"] == "explicit"
        assert not errors, errors
        assert not unexpected, unexpected
        print(f"Library UI OK: autosave, serialized writes, navigation, pagination, assignments, archive, close/error protection ({len(writes)} simulated writes)")
        browser.close()


if __name__ == "__main__":
    main()
