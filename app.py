"""
app.py — LegalFlow OS · GOVMAP Gush/Chelka Agent
=================================================
מריץ שרת Flask שמציג את index_19.html ומספק endpoint
שפותח Playwright → GOVMAP → מחלץ גוש/חלקה.

שרת חיצוני: 185.241.6.63:5000

הרצה:
    python app.py

דרישות:
    python -m pip install flask flask-cors playwright playwright-stealth
    python -m playwright install chromium --with-deps
"""

import asyncio
import os
import re

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from playwright.async_api import async_playwright

try:
    from playwright_stealth import stealth_async
except ImportError:
    async def stealth_async(page):
        pass

# ── תצורה ────────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
HOST      = "0.0.0.0"          # מקשיב על כל הממשקים — נדרש לשרת חיצוני
PORT      = 5000
SERVER_IP = "185.241.6.63"     # כתובת השרת החיצוני
HEADLESS  = True               # חובה בשרת Linux ללא GUI

app = Flask(__name__, static_folder=BASE_DIR)

# ── CORS: מאפשר קריאות מהשרת החיצוני ──────────────────────────────────────
CORS(app, resources={
    r"/api/*": {
        "origins": [
            f"http://{SERVER_IP}",
            f"http://{SERVER_IP}:{PORT}",
            f"https://{SERVER_IP}",
            "http://localhost:5000",
            "http://127.0.0.1:5000",
            "null",   # file:// תוך כדי פיתוח מקומי
        ]
    }
})


# ════════════════════════════════════════════════════════════════
#  ROUTES — קבצים סטטיים
# ════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index_19.html")


@app.route("/logo.png")
def logo():
    if os.path.exists(os.path.join(BASE_DIR, "logo.png")):
        return send_from_directory(BASE_DIR, "logo.png")
    return "", 404


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


# ════════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ════════════════════════════════════════════════════════════════
@app.route("/health")
def health():
    return jsonify({
        "status":   "ok",
        "server":   f"{SERVER_IP}:{PORT}",
        "headless": HEADLESS,
    })


# ════════════════════════════════════════════════════════════════
#  PLAYWRIGHT AGENT — חילוץ גוש/חלקה מ-GOVMAP
# ════════════════════════════════════════════════════════════════
async def _govmap_agent(city: str, street: str, number: str) -> dict:
    address_q = f"{street} {number}, {city}".strip().strip(",")

    result = {
        "gush":    None,
        "chelka":  None,
        "address": address_q,
        "source":  None,
        "error":   None,
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=80,
            args=[
                "--no-sandbox",                    # נדרש בשרת Linux
                "--disable-setuid-sandbox",        # נדרש בשרת Linux
                "--disable-dev-shm-usage",         # מונע crash ב-VPS
                "--disable-gpu",                   # headless ללא GPU
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await stealth_async(page)

        try:
            # ── שלב 1: נווט ─────────────────────────────────
            await page.goto(
                "https://www.govmap.gov.il/?c=210000,610000&z=0",
                wait_until="domcontentloaded",
                timeout=40_000,
            )
            await page.wait_for_timeout(3_500)

            # ── שלב 2: סגור popup / cookie banner ───────────
            for btn_sel in [
                "button:has-text('אישור')",
                "button:has-text('סגור')",
                "button:has-text('אישור ושמירה')",
                "[class*='close']",
                "[aria-label='Close']",
            ]:
                try:
                    btn = page.locator(btn_sel).first
                    if await btn.is_visible(timeout=1_500):
                        await btn.click()
                        await page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            # ── שלב 3: שדה חיפוש ─────────────────────────────
            search_input = None
            for sel in [
                "input#searchInput",
                "input[placeholder*='חפש']",
                "input[placeholder*='הזן']",
                ".search-box input",
                "#topSearch input",
                "input[type='text']",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2_000):
                        search_input = el
                        break
                except Exception:
                    continue

            if search_input:
                await search_input.click(click_count=3)
                await page.keyboard.press("Backspace")
                await search_input.fill(f"{street} {number}, {city}")

                # ── שלב 4: autocomplete ──────────────────────
                try:
                    await page.wait_for_selector(
                        ".autocomplete-suggestions, .govmap-autocomplete, "
                        "[class*='autocomplete'], [class*='suggestion']",
                        timeout=5_000,
                    )
                    await page.wait_for_timeout(500)

                    clicked = False
                    for sel in [
                        "li:has-text('כתובת')",
                        "div:has-text('כתובת') >> nth=0",
                        "[class*='suggestion']:has-text('כתובת')",
                        "[class*='result']:has-text('כתובת')",
                        "[class*='suggestion'] [class*='address']",
                        ".autocomplete-suggestions li:first-child",
                        "[class*='autocomplete'] li:first-child",
                        "[class*='suggestion']:first-child",
                    ]:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=1_500):
                                await el.click()
                                clicked = True
                                print(f"  ✅ כתובת: {sel}")
                                break
                        except Exception:
                            continue

                    if not clicked:
                        await page.keyboard.press("ArrowDown")
                        await page.keyboard.press("Enter")
                        print("  ⚠️  fallback: ArrowDown+Enter")

                except Exception:
                    await page.keyboard.press("Enter")
                    print("  ⚠️  fallback: Enter")

                await page.wait_for_timeout(3_000)

                # ── שלב 5: איתור גוש/חלקה ───────────────────
                parcel_clicked = False
                for sel in [
                    "text=איתור גוש/חלקה",
                    "text=איתור גוש",
                    "a:has-text('איתור גוש')",
                    "button:has-text('איתור גוש')",
                    "span:has-text('איתור גוש')",
                    "[class*='search-result'] a",
                    "[class*='result-actions'] a:first-child",
                    "[class*='card'] a:has-text('גוש')",
                ]:
                    try:
                        btn = page.locator(sel).first
                        await btn.wait_for(state="visible", timeout=4_000)
                        await btn.click()
                        parcel_clicked = True
                        print(f"  ✅ איתור גוש/חלקה: {sel}")
                        break
                    except Exception:
                        continue

                if not parcel_clicked:
                    print("  ⚠️  כפתור 'איתור גוש/חלקה' לא נמצא")

                await page.wait_for_timeout(3_500)

                # ── שלב 6a: regex על פאנל ───────────────────
                try:
                    panel_text = await page.locator(
                        "[class*='panel'], [class*='result'], [class*='info'], "
                        "[class*='parcel'], #rightPanel, .right-panel"
                    ).first.inner_text(timeout=4_000)

                    gm = re.search(r'גוש[:\s]*(\d+)', panel_text)
                    hm = re.search(r'חלק[הא][:\s]*(\d+)', panel_text)

                    if gm and hm:
                        result.update({"gush": gm.group(1), "chelka": hm.group(1), "source": "panel-regex"})
                        print(f"  ✅ גוש {gm.group(1)} חלקה {hm.group(1)}")
                        return result
                except Exception:
                    pass

                # ── שלב 6b: locators ישירים ──────────────────
                for g_sel, h_sel in [
                    ("[class*='gush']",                "[class*='helka']"),
                    ("[class*='GUSH']",                "[class*='HELKA']"),
                    ("[data-field='GUSH']",            "[data-field='HELKA']"),
                    (".gush-value",                    ".helka-value"),
                    (".parcel-info span:nth-child(1)", ".parcel-info span:nth-child(2)"),
                ]:
                    try:
                        g_txt = (await page.locator(g_sel).first.inner_text(timeout=2_000)).strip()
                        h_txt = (await page.locator(h_sel).first.inner_text(timeout=2_000)).strip()
                        if re.search(r'\d+', g_txt) and re.search(r'\d+', h_txt):
                            result.update({
                                "gush":   re.search(r'\d+', g_txt).group(),
                                "chelka": re.search(r'\d+', h_txt).group(),
                                "source": "locator",
                            })
                            print(f"  ✅ גוש {result['gush']} חלקה {result['chelka']}")
                            return result
                    except Exception:
                        continue

            # ── Fallback layers ──────────────────────────────
            body_text = await page.inner_text("body")
            gush, chelka, source = _extract(body_text, page.url, await page.content())

            if gush and chelka:
                result.update({"gush": gush, "chelka": chelka, "source": source})
                print(f"  ✅ גוש {gush} חלקה {chelka} ({source})")
            else:
                result["error"] = (
                    "לא נמצאו גוש/חלקה אוטומטית. "
                    "ייתכן שהכתובת לא מדויקת — נסה לבדוק ידנית ב-GovMap."
                )

        except Exception as exc:
            result["error"] = f"שגיאת סוכן: {exc}"
            print(f"  ❌ {exc}")

        finally:
            await page.wait_for_timeout(2_000)
            await browser.close()

    return result


# ════════════════════════════════════════════════════════════════
#  4 שכבות חילוץ
# ════════════════════════════════════════════════════════════════
def _extract(body: str, url: str, html: str):
    g = re.search(r'גוש[:\s\u00a0]+(\d+)', body)
    c = re.search(r'חלק[הא][:\s\u00a0]+(\d+)', body)
    if g and c:
        return g.group(1), c.group(1), "text"

    g2 = re.search(r'[Gg][Uu][Ss][Hh]=(\d+)', url)
    c2 = re.search(r'[Cc][Hh][Ee][Ll][KkQq][Aa]=(\d+)|[Hh][Ee][Ll][Kk][Aa]=(\d+)', url)
    if g2 and c2:
        return g2.group(1), (c2.group(1) or c2.group(2)), "url"

    g3 = re.search(r'"GUSH"\s*:\s*"?(\d+)"?', html, re.I)
    c3 = re.search(r'"(?:CHELKA|HELKA|PARCEL)"\s*:\s*"?(\d+)"?', html, re.I)
    if g3 and c3:
        return g3.group(1), c3.group(1), "json"

    g4 = re.search(r'gush["\']?\s*[=:]\s*["\']?(\d+)', html, re.I)
    c4 = re.search(r'(?:chelka|helka)["\']?\s*[=:]\s*["\']?(\d+)', html, re.I)
    if g4 and c4:
        return g4.group(1), c4.group(1), "attr"

    return None, None, None


# ════════════════════════════════════════════════════════════════
#  הרץ coroutine מתוך Flask (sync context)
# ════════════════════════════════════════════════════════════════
def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ════════════════════════════════════════════════════════════════
#  ROUTE  POST /api/govmap
#  Body JSON: { "city": "תל אביב", "street": "הרצל", "number": "1" }
#  Response:  { "gush": "7110", "chelka": "44", "source": "text", "error": null }
# ════════════════════════════════════════════════════════════════
@app.route("/api/govmap", methods=["POST"])
def govmap_api():
    data   = request.get_json(silent=True) or {}
    city   = (data.get("city")   or "").strip()
    street = (data.get("street") or "").strip()
    number = (data.get("number") or "").strip()

    if not city and not street:
        return jsonify({"error": "נא לספק עיר ורחוב לפחות"}), 400

    print(f"\n🔍 GOVMAP: {street} {number}, {city}")
    result = _run(_govmap_agent(city, street, number))
    return jsonify(result), (200 if not result["error"] else 422)


# ════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("━" * 52)
    print("  ⚖️   LegalFlow OS — GOVMAP Agent Server")
    print(f"  🌐  http://{SERVER_IP}:{PORT}")
    print(f"  🖥️   Listening: {HOST}:{PORT}")
    print(f"  🤖  Headless: {HEADLESS}")
    print("━" * 52)
    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )