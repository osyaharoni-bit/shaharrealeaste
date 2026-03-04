import os
import re
import asyncio
import requests
from flask import Flask, jsonify, request, send_from_directory
from playwright.async_api import async_playwright

# ניסיון טעינת stealth למניעת זיהוי בוטים בחיפוש גוגל
try:
    from playwright_stealth import stealth_async
except ImportError:
    async def stealth_async(page): pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR)

# הגדרות שרת
PORT = int(os.environ.get("PORT", 5000))
HEADLESS = True  # חובה להשאיר True בשרת לינוקס

# ════════════════════════════════════════════════════════════════
#  ROUTES - דף הבית ולוגו
# ════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index_19.html")

@app.route("/logo.png")
def logo():
    if os.path.exists(os.path.join(BASE_DIR, "logo.png")):
        return send_from_directory(BASE_DIR, "logo.png")
    return "", 404

# ════════════════════════════════════════════════════════════════
#  AGENT 1: גוש/חלקה (שימוש ב-API ממשלתי מהיר - חסכוני במשאבים)
# ════════════════════════════════════════════════════════════════
def _govmap_api_agent(city: str, street: str, number: str) -> dict:
    address_q = f"{street} {number}, {city}".strip()
    result = {"gush": None, "chelka": None, "source": "Gov-API", "error": None}
    
    try:
        api_url = "https://data.gov.il/api/3/action/datastore_search"
        params = {
            "resource_id": "be5b7935-3922-45d4-9638-08871b17ec95",
            "q": address_q,
            "limit": 1
        }
        resp = requests.get(api_url, params=params, timeout=10)
        data = resp.json()
        records = data.get('result', {}).get('records', [])
        
        if records:
            result["gush"] = str(records[0].get('GUSH', ''))
            result["chelka"] = str(records[0].get('HELKA', ''))
        else:
            result["error"] = "כתובת לא נמצאה במאגר הממשלתי."
    except Exception as e:
        result["error"] = f"שגיאת API: {str(e)}"
    return result

# ════════════════════════════════════════════════════════════════
#  AGENT 2: תיק בניין (שימוש ב-Playwright - סוכן חכם)
# ════════════════════════════════════════════════════════════════
async def _building_file_agent(city: str) -> dict:
    search_query = f"איתור תיק בניין ועדה מקומית {city}"
    result = {"url": None, "error": None}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-gpu"])
        page = await browser.new_page()
        await stealth_async(page)

        try:
            # חיפוש בגוגל למציאת דף הנדסה
            await page.goto(f"https://www.google.com/search?q={search_query}", timeout=30000)
            await page.wait_for_timeout(1500)

            # חיפוש לינקים של מערכות הנדסה מוכרות
            links = await page.locator("a").all()
            target_url = None
            for link in links:
                href = await link.get_attribute("href")
                if href and any(x in href for x in ["complot.co.il", "bar-vps.co.il", "arava.co.il", ".gov.il"]):
                    if "google" not in href:
                        target_url = href
                        break
            
            result["url"] = target_url or f"https://www.google.com/search?q={search_query}"
        except Exception as e:
            result["error"] = str(e)
        finally:
            await browser.close()
    return result

# ════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ════════════════════════════════════════════════════════════════
@app.route("/api/govmap", methods=["POST"])
def govmap_api():
    data = request.get_json(silent=True) or {}
    res = _govmap_api_agent(data.get("city",""), data.get("street",""), data.get("number",""))
    return jsonify(res)

@app.route("/api/building_file", methods=["POST"])
def building_file_api():
    data = request.get_json(silent=True) or {}
    city = (data.get("city") or "").strip()
    # הרצת הסוכן האסינכרוני בתוך Flask
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    res = loop.run_until_complete(_building_file_agent(city))
    loop.close()
    return jsonify(res)

if __name__ == "__main__":
    print(f"🚀 LegalFlow OS Active on http://0.0.0.0:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)