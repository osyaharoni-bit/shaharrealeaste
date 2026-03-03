"""
app.py — LegalFlow OS · GOVMAP API Agent
=================================================
מריץ שרת Flask שמציג את index_19.html ומספק endpoint
המושך נתוני גוש/חלקה ישירות ממאגר הנתונים הממשלתי (Data.gov.il).

הרצה:
    python app.py
"""

import os
import requests
from flask import Flask, jsonify, request, send_from_directory

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
#  GOVMAP API AGENT — חילוץ גוש/חלקה ממאגר הנתונים הפתוח
# ════════════════════════════════════════════════════════════════
def _govmap_api_agent(city: str, street: str, number: str) -> dict:
    """
    פנייה ישירה למאגר הכתובות הממשלתי (resource_id: be5b7935-3922-45d4-9638-08871b17ec95)
    זהו מעקף ל-Scraping שחוסך את הצורך ב-Playwright וב-Proxy.
    """
    address_q = f"{street} {number}, {city}".strip()
    result = {
        "gush": None,
        "chelka": None,
        "address": address_q,
        "source": "Gov-API",
        "error": None
    }

    try:
        # פנייה ל-API של Data.gov.il
        api_url = "https://data.gov.il/api/3/action/datastore_search"
        params = {
            "resource_id": "be5b7935-3922-45d4-9638-08871b17ec95",
            "q": address_q,
            "limit": 1
        }
        
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        records = data.get('result', {}).get('records', [])
        
        if records:
            # שליפת גוש וחלקה מהרשומה הראשונה
            best_match = records[0]
            result["gush"] = str(best_match.get('GUSH', ''))
            result["chelka"] = str(best_match.get('HELKA', ''))
            
            if not result["gush"] or result["gush"] == 'None':
                result["error"] = "נמצאה כתובת, אך ללא נתוני גוש/חלקה במאגר הממשלתי."
                result["gush"] = None
        else:
            result["error"] = "הכתובת לא נמצאה במאגר הנתונים הממשלתי. וודא שהכתובת מדויקת."
            
    except Exception as e:
        result["error"] = f"שגיאה בתקשורת עם מאגר הנתונים: {str(e)}"
        
    return result

# ════════════════════════════════════════════════════════════════
#  ROUTE  POST /api/govmap
# ════════════════════════════════════════════════════════════════
@app.route("/api/govmap", methods=["POST"])
def govmap_api():
    data = request.get_json(silent=True) or {}
    city = (data.get("city") or "").strip()
    street = (data.get("street") or "").strip()
    number = (data.get("number") or "").strip()

    if not city and not street:
        return jsonify({"error": "נא לספק עיר ורחוב לפחות"}), 400

    # הפעלת הסוכן המבוסס API
    result = _govmap_api_agent(city, street, number)
    
    # החזרת סטטוס מתאים
    status_code = 200 if result["gush"] else 422
    return jsonify(result), status_code

# ════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════