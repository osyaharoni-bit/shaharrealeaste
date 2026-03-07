"""
server.py — LegalFlow OS · Unified Server
==========================================
מאחד את שני השרתים לשרת FastAPI אחד:
  • /api/govmap          — Playwright agent לחילוץ גוש/חלקה מ-GOVMAP
  • /api/scan-documents  — Gemini AI לסריקת מסמכים (טאבו/ארנונה/היתר)
  • /api/health          — בדיקת תקינות
  • /*                   — קבצים סטטיים (index.html וכו')

שרת חיצוני: 185.241.6.63:5000

הרצה:
    python server.py

דרישות:
    pip install fastapi uvicorn python-multipart pymupdf requests playwright
    python -m playwright install chromium --with-deps
"""

import asyncio
import base64
import json
import os
import re
import time

import fitz
import requests as http_requests
import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

try:
    from playwright_stealth import stealth_async
except ImportError:
    async def stealth_async(page):
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  תצורה כללית
# ══════════════════════════════════════════════════════════════════════════════
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
HOST      = "0.0.0.0"
PORT      = 5000
SERVER_IP = "185.241.6.63"
HEADLESS  = True

# Gemini
GEMINI_KEY     = "AIzaSyBu-r-8uO_HZ5P-4HbyCd7EmQ3vZodhPLY"
GEMINI_BASE    = "https://generativelanguage.googleapis.com"
GEMINI_HEADERS = {'Content-Type': 'application/json'}
MAX_FILES      = 6

IMAGE_MIME = {
    '.jpg':  'image/jpeg', '.jpeg': 'image/jpeg',
    '.png':  'image/png',
    '.tiff': 'image/tiff', '.tif':  'image/tiff',
    '.bmp':  'image/bmp',  '.webp': 'image/webp',
}

GEMINI_MODELS = [
    (f"{GEMINI_BASE}/v1beta/models/gemini-3-flash-preview:generateContent", "gemini-3-flash-preview", True),
    (f"{GEMINI_BASE}/v1beta/models/gemini-2.0-flash:generateContent",       "gemini-2.0-flash",       True),
    (f"{GEMINI_BASE}/v1beta/models/gemini-2.0-flash-001:generateContent",   "gemini-2.0-flash-001",   True),
    (f"{GEMINI_BASE}/v1beta/models/gemini-1.5-flash-002:generateContent",   "gemini-1.5-flash-002",   True),
]

TABU_TITLE_HINTS = [
    'הרשות לרישום', 'רישום מקרקעין', 'לשכת רישום', 'land registry',
    'נסח טאבו', 'נסח מס', 'נסח רישום', 'settlement of rights',
]


# ══════════════════════════════════════════════════════════════════════════════
#  FastAPI app
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI — פרומפטים
# ══════════════════════════════════════════════════════════════════════════════
PROMPT_TEXT = """אתה עוזר משפטי ודיגיטלי ישראלי. לפניך טקסט שחולץ ממסמך ישראלי.
נסח טאבו מזוהה לפי כותרת: "הרשות לרישום והסדר זכויות מקרקעין" / "לשכת רישום מקרקעין" / "LAND REGISTRY".
תחילה קבע את סוג המסמך: טאבו / ארנונה / היתר_בניה / לא_ידוע.
לאחר מכן חלץ את הנתונים הרלוונטיים. עבור נתונים שאינם מופיעים החזר null.
החזר אך ורק JSON תקני, ללא שום טקסט לפני או אחרי, במבנה הבא:
{
  "document_type": "טאבו או ארנונה או היתר_בניה או לא_ידוע",
  "gush": "מספר הגוש בלבד",
  "helka": "מספר החלקה בלבד",
  "tat_helka": "מספר תת חלקה (אם קיים בכל סוג מסמך, כולל ארנונה)",
  "area": "שטח הנכס במ\"ר - מספר בלבד (בטאבו: ללא רכוש משותף. בארנונה: שטח לחיוב. בהיתר: שטח מורשה)",
  "owners_or_payers": "שמות הבעלים (טאבו) / מחזיק (ארנונה) / מגיש הבקשה (היתר), מופרדים בפסיק. בטופסי ארנונה: חפש שדות 'לכבוד המחזיק בנכס' / 'שם המחזיק' / 'לכבוד' — הם מכילים את שם המחזיק. חובה לחלץ גם אם מופיע כ'לכבוד [שם]'",
  "parking": "כן או לא או null",
  "storage": "כן או לא או null",
  "encumbrances": "רלוונטי לטאבו בלבד. בדוק את כל הרשומות (בעלות, הערות אזהרה, עיקולים, משכנתאות). אם יש — פרט: סוג (עיקול/משכנתא/הערת אזהרה), שם הגורם הרושם, וסכום אם מצוין. אם אין שעבוד — החזר 'לא'",
  "address": "כתובת הנכס",
  "total_to_pay": "סה\"כ לתשלום בש\"ח - מספר בלבד (רלוונטי לארנונה)",
  "permit_number": "מספר ההיתר (רלוונטי להיתר בניה)",
  "permit_date": "תאריך ההיתר (רלוונטי להיתר בניה)",
  "permit_type": "סוג ההיתר - למשל: בניה חדשה / תוספת / שיפוץ (רלוונטי להיתר בניה)"
}"""

PROMPT_IMAGE = """אתה עוזר משפטי ודיגיטלי ישראלי. לפניך תמונה של מסמך ישראלי.
נסח טאבו מזוהה לפי כותרת: "הרשות לרישום והסדר זכויות מקרקעין" / "לשכת רישום מקרקעין" / "LAND REGISTRY".
תחילה קבע את סוג המסמך: טאבו / ארנונה / היתר_בניה / לא_ידוע.
קרא את המסמך בקפידה וחלץ את הנתונים הרלוונטיים. עבור נתונים שאינם מופיעים החזר null.
החזר אך ורק JSON תקני, ללא שום טקסט לפני או אחרי, במבנה הבא:
{
  "document_type": "טאבו או ארנונה או היתר_בניה או לא_ידוע",
  "gush": "מספר הגוש בלבד",
  "helka": "מספר החלקה בלבד",
  "tat_helka": "מספר תת חלקה (אם קיים בכל סוג מסמך, כולל ארנונה)",
  "area": "שטח הנכס במ\"ר - מספר בלבד (בטאבו: ללא רכוש משותף. בארנונה: שטח לחיוב. בהיתר: שטח מורשה)",
  "owners_or_payers": "שמות הבעלים (טאבו) / מחזיק (ארנונה) / מגיש הבקשה (היתר), מופרדים בפסיק. בטופסי ארנונה: חפש שדות 'לכבוד המחזיק בנכס' / 'שם המחזיק' / 'לכבוד' — הם מכילים את שם המחזיק. חובה לחלץ גם אם מופיע כ'לכבוד [שם]'",
  "parking": "כן או לא או null",
  "storage": "כן או לא או null",
  "encumbrances": "רלוונטי לטאבו בלבד. בדוק את כל הרשומות (בעלות, הערות אזהרה, עיקולים, משכנתאות). אם יש — פרט: סוג (עיקול/משכנתא/הערת אזהרה), שם הגורם הרושם, וסכום אם מצוין. אם אין שעבוד — החזר 'לא'",
  "address": "כתובת הנכס",
  "total_to_pay": "סה\"כ לתשלום בש\"ח - מספר בלבד (רלוונטי לארנונה)",
  "permit_number": "מספר ההיתר (רלוונטי להיתר בניה)",
  "permit_date": "תאריך ההיתר (רלוונטי להיתר בניה)",
  "permit_type": "סוג ההיתר - למשל: בניה חדשה / תוספת / שיפוץ (רלוונטי להיתר בניה)"
}"""

EMPTY_ANALYSIS = {
    "document_type": "שגיאה",
    "gush": None, "helka": None, "tat_helka": None,
    "area": None, "owners_or_payers": None,
    "parking": None, "storage": None, "encumbrances": None,
    "address": None, "total_to_pay": None,
    "permit_number": None, "permit_date": None, "permit_type": None,
}


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI — פונקציות עזר
# ══════════════════════════════════════════════════════════════════════════════
def _is_tabu_by_title(text: str) -> bool:
    sample = text[:600].lower()
    return any(h.lower() in sample for h in TABU_TITLE_HINTS)


def _parse_gemini_response(r) -> dict:
    raw   = r.json()['candidates'][0]['content']['parts'][0]['text']
    clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    return json.loads(clean)


def _call_gemini(payload: dict) -> dict:
    last_err = "לא נוסה אף מודל"
    for url, label, use_mime in GEMINI_MODELS:
        full_url = f"{url}?key={GEMINI_KEY}"
        gen_cfg  = {"temperature": 0}
        if use_mime:
            gen_cfg["responseMimeType"] = "application/json"
        payload["generationConfig"] = gen_cfg

        for attempt in range(2):
            try:
                print(f"   🔄 {'retry — ' if attempt else ''}{label}")
                r = http_requests.post(full_url, headers=GEMINI_HEADERS, json=payload, timeout=60)

                if r.status_code == 200:
                    data = _parse_gemini_response(r)
                    print(f"   ✅ הצליח: {label}")
                    return data
                elif r.status_code == 429:
                    if attempt == 0:
                        m    = re.search(r'"(\d+(?:\.\d+)?)s"', r.text)
                        wait = int(float(m.group(1))) + 2 if m else 9
                        print(f"   ⏳ 429 — ממתין {wait}s")
                        time.sleep(wait)
                        continue
                    print(f"   ⚠️ 429 שוב — עובר הלאה")
                    last_err = f"429: {label}"; break
                elif r.status_code in (404, 400):
                    print(f"   ⚠️ {r.status_code} — {label}: {r.text[:100]}")
                    last_err = f"{r.status_code}: {label}"; break
                else:
                    print(f"   ⚠️ {r.status_code} — {r.text[:80]}")
                    last_err = f"HTTP {r.status_code}"; break

            except http_requests.exceptions.Timeout:
                last_err = f"Timeout: {label}"; break
            except Exception as e:
                last_err = str(e); break

    raise RuntimeError(f"כל המודלים נכשלו. שגיאה אחרונה: {last_err}")


def _safe_pixmap(page, target_dpi: int = 150) -> fitz.Pixmap:
    MAX_PX = 60_000
    for dpi in [target_dpi, 120, 96, 72]:
        scale = dpi / 72.0
        mat   = fitz.Matrix(scale, scale)
        pix   = page.get_pixmap(matrix=mat, alpha=False)
        if pix.width <= MAX_PX and pix.height <= MAX_PX:
            return pix
        print(f"   ⚠️ {dpi} DPI → {pix.width}×{pix.height} גדול מדי")
    mat = fitz.Matrix(48/72, 48/72)
    return page.get_pixmap(matrix=mat, alpha=False)


def scan_text(text: str) -> dict:
    payload = {"contents": [{"parts": [{"text": PROMPT_TEXT + "\n\nטקסט המסמך:\n" + text[:8000]}]}]}
    return _call_gemini(payload)


def scan_image_bytes(img_bytes: bytes, mime: str) -> dict:
    b64 = base64.b64encode(img_bytes).decode()
    payload = {"contents": [{"parts": [
        {"text": PROMPT_IMAGE},
        {"inline_data": {"mime_type": mime, "data": b64}}
    ]}]}
    return _call_gemini(payload)


def scan_scanned_pdf(pdf_bytes: bytes) -> dict:
    doc   = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = min(3, doc.page_count)
    parts = [{"text": PROMPT_IMAGE}]
    for i in range(pages):
        pix = _safe_pixmap(doc[i], target_dpi=150)
        b64 = base64.b64encode(pix.tobytes("jpeg", jpg_quality=85)).decode()
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        print(f"   🖼️  עמוד {i+1}: {pix.width}×{pix.height} px")
    doc.close()
    return _call_gemini({"contents": [{"parts": parts}]})


# ══════════════════════════════════════════════════════════════════════════════
#  GOVMAP — Playwright agent
# ══════════════════════════════════════════════════════════════════════════════
def _extract_gush_chelka(body: str, url: str, html: str):
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


async def _govmap_agent(city: str, street: str, number: str) -> dict:
    address_q = f"{street} {number}, {city}".strip().strip(",")
    result = {"gush": None, "chelka": None, "address": address_q, "source": None, "error": None}

    if not async_playwright:
        result["error"] = "Playwright לא מותקן"
        return result

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=80,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--start-maximized", "--disable-blink-features=AutomationControlled",
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
            await page.goto(
                "https://www.govmap.gov.il/?c=210000,610000&z=0",
                wait_until="domcontentloaded", timeout=40_000,
            )
            await page.wait_for_timeout(3_500)

            # סגור popup
            for btn_sel in [
                "button:has-text('אישור')", "button:has-text('סגור')",
                "button:has-text('אישור ושמירה')", "[class*='close']", "[aria-label='Close']",
            ]:
                try:
                    btn = page.locator(btn_sel).first
                    if await btn.is_visible(timeout=1_500):
                        await btn.click()
                        await page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            # מצא שדה חיפוש
            search_input = None
            for sel in [
                "input#searchInput", "input[placeholder*='חפש']",
                "input[placeholder*='הזן']", ".search-box input",
                "#topSearch input", "input[type='text']",
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

                try:
                    await page.wait_for_selector(
                        ".autocomplete-suggestions, .govmap-autocomplete, "
                        "[class*='autocomplete'], [class*='suggestion']",
                        timeout=5_000,
                    )
                    await page.wait_for_timeout(500)

                    clicked = False
                    for sel in [
                        "li:has-text('כתובת')", "div:has-text('כתובת') >> nth=0",
                        "[class*='suggestion']:has-text('כתובת')",
                        "[class*='result']:has-text('כתובת')",
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
                except Exception:
                    await page.keyboard.press("Enter")

                await page.wait_for_timeout(3_000)

                # איתור גוש/חלקה
                for sel in [
                    "text=איתור גוש/חלקה", "text=איתור גוש",
                    "a:has-text('איתור גוש')", "button:has-text('איתור גוש')",
                    "span:has-text('איתור גוש')", "[class*='search-result'] a",
                    "[class*='result-actions'] a:first-child",
                    "[class*='card'] a:has-text('גוש')",
                ]:
                    try:
                        btn = page.locator(sel).first
                        await btn.wait_for(state="visible", timeout=4_000)
                        await btn.click()
                        print(f"  ✅ איתור גוש/חלקה: {sel}")
                        break
                    except Exception:
                        continue

                await page.wait_for_timeout(3_500)

                # חילוץ מפאנל
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

                # locators ישירים
                for g_sel, h_sel in [
                    ("[class*='gush']",   "[class*='helka']"),
                    ("[class*='GUSH']",   "[class*='HELKA']"),
                    ("[data-field='GUSH']", "[data-field='HELKA']"),
                    (".gush-value",        ".helka-value"),
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
                            return result
                    except Exception:
                        continue

            # fallback
            body_text = await page.inner_text("body")
            gush, chelka, source = _extract_gush_chelka(body_text, page.url, await page.content())
            if gush and chelka:
                result.update({"gush": gush, "chelka": chelka, "source": source})
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


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════
@app.on_event("startup")
async def startup_event():
    print("\n" + "=" * 60)
    print(f"⚖️   LegalFlow OS — Unified Server  http://{SERVER_IP}:{PORT}")
    print(f"📄  /api/scan-documents  — Gemini AI (עד {MAX_FILES} מסמכים)")
    print(f"🗺️   /api/govmap          — Playwright GOVMAP Agent")
    print("=" * 60 + "\n")


@app.get("/api/health")
async def health():
    return {"status": "ok", "server": f"{SERVER_IP}:{PORT}"}


# ── סריקת מסמכים ─────────────────────────────────────────────────────────────
@app.post("/api/scan-documents")
async def scan_documents(files: list[UploadFile] = File(...)):
    print(f"\n{'='*50}\nהתקבלו {len(files)} קבצים")

    if len(files) > MAX_FILES:
        return {"status": "error", "message": f"ניתן לסרוק עד {MAX_FILES} קבצים", "ai_analysis": EMPTY_ANALYSIS}

    results = {}

    for file in files:
        file_bytes = await file.read()
        fname      = file.filename.lower()
        ext        = '.' + fname.rsplit('.', 1)[-1] if '.' in fname else ''
        size_mb    = len(file_bytes) / 1_048_576
        print(f"\n📄 סורק: {file.filename} ({size_mb:.1f} MB)")

        try:
            if ext == '.pdf':
                doc  = fitz.open(stream=file_bytes, filetype="pdf")
                text = "".join(doc[i].get_text() for i in range(min(4, doc.page_count)))
                doc.close()
                char_count = len(text.strip())
                print(f"   📝 טקסט: {char_count} תווים")

                if char_count > 80:
                    print("   🔤 PDF טקסטואלי")
                    if _is_tabu_by_title(text):
                        print("   📌 זוהה נסח טאבו לפי כותרת")
                    ai_data = scan_text(text)
                    if _is_tabu_by_title(text) and ai_data.get('document_type') != 'טאבו':
                        ai_data['document_type'] = 'טאבו'
                        print("   🔧 תוקן → טאבו (על פי כותרת)")
                else:
                    print("   🖼️  PDF סרוק → vision")
                    ai_data = scan_scanned_pdf(file_bytes)

            elif ext in IMAGE_MIME:
                mime = IMAGE_MIME[ext]
                print(f"   🖼️  תמונה ({mime})")
                if ext in ('.tiff', '.tif'):
                    doc        = fitz.open(stream=file_bytes, filetype="tiff")
                    pix        = _safe_pixmap(doc[0], target_dpi=150)
                    file_bytes = pix.tobytes("jpeg", jpg_quality=85)
                    mime       = "image/jpeg"
                    doc.close()
                ai_data = scan_image_bytes(file_bytes, mime)

            else:
                print(f"   ⚠️ פורמט לא נתמך: {ext or fname}")
                continue

            doc_type = (ai_data.get("document_type") or "לא_ידוע").strip()
            print(f"   📦 סוג: {doc_type}")
            results[doc_type] = ai_data

        except Exception as e:
            print(f"   ❌ שגיאה ב-{file.filename}: {e}")
            continue

    if not results:
        return {"status": "error", "message": "לא נסרק אף קובץ בהצלחה", "ai_analysis": EMPTY_ANALYSIS}

    tabu_data   = results.get("טאבו")
    arnona_data = results.get("ארנונה")
    permit_data = results.get("היתר_בניה")
    primary     = tabu_data or arnona_data or permit_data or list(results.values())[0]

    print(f"\n✅ סיכום: נסרקו {len(results)} סוגי מסמכים — {list(results.keys())}")
    return {
        "status":      "completed",
        "ai_analysis": primary,
        "arnona":      arnona_data,
        "permit":      permit_data,
        "all_results": results,
    }


# ── GOVMAP Agent ──────────────────────────────────────────────────────────────
@app.post("/api/govmap")
async def govmap_api(request: Request):
    data   = await request.json()
    city   = (data.get("city")   or "").strip()
    street = (data.get("street") or "").strip()
    number = (data.get("number") or "").strip()

    if not city and not street:
        return JSONResponse({"error": "נא לספק עיר ורחוב לפחות"}, status_code=400)

    print(f"\n🔍 GOVMAP: {street} {number}, {city}")
    result = await _govmap_agent(city, street, number)
    status = 200 if not result["error"] else 422
    return JSONResponse(result, status_code=status)


# ── קבצים סטטיים ─────────────────────────────────────────────────────────────
@app.get("/")
async def serve_index():
    index_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"error": "index.html לא נמצא"}, status_code=404)


@app.get("/{filename:path}")
async def serve_static(filename: str):
    file_path = os.path.join(BASE_DIR, filename)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
    return JSONResponse({"error": f"{filename} לא נמצא"}, status_code=404)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
