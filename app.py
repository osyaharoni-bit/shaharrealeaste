"""
app.py — LegalFlow OS · GOVMAP Gush/Chelka Agent
=================================================
מריץ שרת Flask שמציג את index_19.html ומספק endpoint
שפותח Playwright → GOVMAP → מחלץ גוש/חלקה.

הרצה:
    python app.py

דרישות:
    python -m pip install flask playwright playwright-stealth
    python -m playwright install chromium
"""

import asyncio
import os
import re
from urllib.parse import quote

from flask import Flask, jsonify, request, send_from_directory
from playwright.async_api import async_playwright

# playwright_stealth — אופציונלי, לא נופלים אם לא מותקן
try:
    from playwright_stealth import stealth_async
except ImportError:
    async def stealth_async(page):
        pass  # ממשיך בלי stealth

# ── נתיב לתיקיית הפרויקט ─────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR)


# ════════════════════════════════════════════════════════════════
#  ROUTE  /  — מגיש את index_19.html
# ════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index_19.html")


# ════════════════════════════════════════════════════════════════
#  ROUTE  /logo.png  — מגיש לוגו אם קיים
# ════════════════════════════════════════════════════════════════
@app.route("/logo.png")
def logo():
    if os.path.exists(os.path.join(BASE_DIR, "logo.png")):
        return send_from_directory(BASE_DIR, "logo.png")
    return "", 404


# ════════════════════════════════════════════════════════════════
#  PLAYWRIGHT AGENT — חילוץ גוש/חלקה מ-GOVMAP
# ════════════════════════════════════════════════════════════════
async def _govmap_agent(city: str, street: str, number: str) -> dict:
    """
    שלבי הסוכן:
      1. נווט ל-govmap.gov.il עם query מוכן
      2. סגור popups
      3. מצא שדה חיפוש, נקה אותו והזן כתובת
      4. לחץ Enter / autocomplete
      5. לחץ על marker לפתיחת פאנל
      6. חלץ גוש/חלקה — 4 שכבות fallback
    """
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
            headless=False,   # ← שנה ל-True להרצה שקטה בייצור
            slow_mo=80,
            args=[
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
        await stealth_async(page)   # no-op אם החבילה לא מותקנת

        try:
            # ── שלב 1: נווט ───────────────────────────────────
            govmap_url = "https://www.govmap.gov.il/?c=210000,610000&z=0"
            await page.goto(govmap_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3_000)

            # ── שלב 2: סגור popup / cookie banner אם קיים ────
            for btn_sel in [
                "button:has-text('אישור')",
                "button:has-text('סגור')",
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

            # ── שלב 3: מצא שדה חיפוש ─────────────────────────
            search_candidates = [
                "input#searchInput",
                "input[placeholder*='חפש']",
                "input[placeholder*='הזן']",
                ".search-box input",
                "#topSearch input",
                "input[type='text']",
            ]
            search_input = None
            for sel in search_candidates:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2_000):
                        search_input = el
                        break
                except Exception:
                    continue

            if search_input:
                # 1. הקלדה והמתנה לרשימת הכתובות
                await search_input.click(click_count=3)
                await page.keyboard.press("Backspace")
                await search_input.fill(f"{street} {number}, {city}")

                # מחכה שהרשימה תיפתח ולוחץ על שורת הכתובת עם אייקון הבית
                # בתמונה: שורה עם 🏠 וטקסט "כתובת" מתחת לשם הרחוב
                try:
                    # מחכים לכל dropdown שיופיע
                    await page.wait_for_selector(
                        ".autocomplete-suggestions, .govmap-autocomplete, [class*='autocomplete'], [class*='suggestion']",
                        timeout=5_000,
                    )
                    await page.wait_for_timeout(500)

                    # מנסים ללחוץ על שורת "כתובת" (עם אייקון בית) — הסלקטורים
                    # לפי מה שנראה בתמונה: div עם sub-text "כתובת"
                    address_row_selectors = [
                        # שורה שמכילה את המילה "כתובת" כ-subtext
                        "li:has-text('כתובת')",
                        "div:has-text('כתובת') >> nth=0",
                        "[class*='suggestion']:has-text('כתובת')",
                        "[class*='result']:has-text('כתובת')",
                        # אייקון בית בתוך שורת תוצאה
                        "[class*='suggestion'] [class*='address']",
                        "[class*='result'] [class*='address']",
                        # כל שורה ראשונה ב-dropdown
                        ".autocomplete-suggestions li:first-child",
                        "[class*='autocomplete'] li:first-child",
                        "[class*='suggestion']:first-child",
                    ]
                    clicked = False
                    for sel in address_row_selectors:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=1_500):
                                await el.click()
                                clicked = True
                                print(f"  ✅ לחצנו על שורת כתובת: {sel}")
                                break
                        except Exception:
                            continue

                    if not clicked:
                        # fallback: ArrowDown + Enter
                        await page.keyboard.press("ArrowDown")
                        await page.keyboard.press("Enter")
                        print("  ⚠️  fallback: ArrowDown+Enter")

                except Exception:
                    await page.keyboard.press("Enter")
                    print("  ⚠️  fallback: Enter בלבד")

                await page.wait_for_timeout(3_000)

                # 2. לחיצה על "איתור גוש/חלקה" בפאנל "תוצאות חיפוש כתובת"
                # בתמונה: הכפתור מופיע בתחתית הכרטיס, עם חץ ← משמאל
                parcel_clicked = False
                parcel_selectors = [
                    # טקסט מדויק כפי שנראה בתמונה
                    "text=איתור גוש/חלקה",
                    "text=איתור גוש",
                    # קישור/כפתור עם החץ
                    "a:has-text('איתור גוש')",
                    "button:has-text('איתור גוש')",
                    "span:has-text('איתור גוש')",
                    # לפי class שקשור לפאנל תוצאות
                    "[class*='search-result'] a",
                    "[class*='result-actions'] a:first-child",
                    "[class*='address-result'] a",
                    "[class*='card'] a:has-text('גוש')",
                ]
                for sel in parcel_selectors:
                    try:
                        btn = page.locator(sel).first
                        await btn.wait_for(state="visible", timeout=4_000)
                        await btn.click()
                        parcel_clicked = True
                        print(f"  ✅ לחצנו על 'איתור גוש/חלקה': {sel}")
                        break
                    except Exception:
                        continue

                if not parcel_clicked:
                    print("  ⚠️  כפתור 'איתור גוש/חלקה' לא נמצא")

                # המתנה לטעינת נתוני הגוש/חלקה
                await page.wait_for_timeout(3_000)

                # 3. שליפת גוש/חלקה מהפאנל שנפתח אחרי הלחיצה
                # הפאנל החדש מציג: גוש XXXX | חלקה YY
                try:
                    # Layer 0a: regex על טקסט הפאנל כולו
                    import re as _re
                    panel_text = await page.locator(
                        "[class*='panel'], [class*='result'], [class*='info'], [class*='parcel'], #rightPanel, .right-panel"
                    ).first.inner_text(timeout=3_000)

                    g_m = _re.search(r'גוש[:\s]*(\d+)', panel_text)
                    h_m = _re.search(r'חלק[הא][:\s]*(\d+)', panel_text)

                    if g_m and h_m:
                        result.update({
                            "success": True,
                            "gush":    g_m.group(1),
                            "chelka":  h_m.group(1),
                            "source":  "panel-regex",
                        })
                        print(f"  ✅ גוש {g_m.group(1)} חלקה {h_m.group(1)} (panel-regex)")
                        return result
                except Exception:
                    pass

                # Layer 0b: locators ישירים לשדות גוש/חלקה
                direct_pairs = [
                    ("[class*='gush']",        "[class*='helka']"),
                    ("[class*='GUSH']",        "[class*='HELKA']"),
                    ("[data-field='GUSH']",    "[data-field='HELKA']"),
                    (".gush-value",            ".helka-value"),
                    (".parcel-info span:nth-child(1)", ".parcel-info span:nth-child(2)"),
                ]
                for g_sel, h_sel in direct_pairs:
                    try:
                        g_txt = (await page.locator(g_sel).first.inner_text(timeout=2_000)).strip()
                        h_txt = (await page.locator(h_sel).first.inner_text(timeout=2_000)).strip()
                        if _re.search(r'\d+', g_txt) and _re.search(r'\d+', h_txt):
                            result.update({
                                "success": True,
                                "gush":    _re.search(r'\d+', g_txt).group(),
                                "chelka":  _re.search(r'\d+', h_txt).group(),
                                "source":  "locator",
                            })
                            print(f"  ✅ גוש {result['gush']} חלקה {result['chelka']} (locator)")
                            return result
                    except Exception:
                        continue

            # ── שכבות fallback (body text / URL / JSON / attr) ──
            body_text   = await page.inner_text("body")
            current_url = page.url
            html_src    = await page.content()

            gush, chelka, source = _extract(body_text, current_url, html_src)

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

        finally:
            await page.wait_for_timeout(2_000)
            await browser.close()

    return result


# ════════════════════════════════════════════════════════════════
#  4 שכבות חילוץ
# ════════════════════════════════════════════════════════════════
def _extract(body: str, url: str, html: str):
    """מחזיר (gush, chelka, source) או (None, None, None)."""

    # Layer 1: Hebrew labels in visible text
    g = re.search(r'גוש[:\s\u00a0]+(\d+)', body)
    c = re.search(r'חלק[הא][:\s\u00a0]+(\d+)', body)
    if g and c:
        return g.group(1), c.group(1), "text"

    # Layer 2: URL params
    g2 = re.search(r'[Gg][Uu][Ss][Hh]=(\d+)', url)
    c2 = re.search(r'[Cc][Hh][Ee][Ll][KkQq][Aa]=(\d+)|[Hh][Ee][Ll][Kk][Aa]=(\d+)', url)
    if g2 and c2:
        return g2.group(1), (c2.group(1) or c2.group(2)), "url"

    # Layer 3: JSON in DOM source
    g3 = re.search(r'"GUSH"\s*:\s*"?(\d+)"?', html, re.I)
    c3 = re.search(r'"(?:CHELKA|HELKA|PARCEL)"\s*:\s*"?(\d+)"?', html, re.I)
    if g3 and c3:
        return g3.group(1), c3.group(1), "json"

    # Layer 4: JS variables / data attributes
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

    result = _run(_govmap_agent(city, street, number))
    return jsonify(result), (200 if not result["error"] else 422)


# ════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("━" * 52)
    print("  ⚖️  LegalFlow OS — GOVMAP Agent Server")
    print("  🌐  http://127.0.0.1:5000")
    print("━" * 52)
    app.run(debug=True, port=5000, use_reloader=False)