import uvicorn
import fitz
import json
import requests
import re
import time
import base64
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API_KEY = "AIzaSyBu-r-8uO_HZ5P-4HbyCd7EmQ3vZodhPLY"
BASE    = "https://generativelanguage.googleapis.com"
HEADERS = {'Content-Type': 'application/json'}

MAX_FILES = 6   # מספר מסמכים מקסימלי לסריקה במקביל

IMAGE_MIME = {
    '.jpg':  'image/jpeg', '.jpeg': 'image/jpeg',
    '.png':  'image/png',
    '.tiff': 'image/tiff', '.tif':  'image/tiff',
    '.bmp':  'image/bmp',  '.webp': 'image/webp',
}

MODELS = [
    (f"{BASE}/v1beta/models/gemini-3-flash-preview:generateContent", "gemini-3-flash-preview", True),
    (f"{BASE}/v1beta/models/gemini-2.0-flash:generateContent",       "gemini-2.0-flash",       True),
    (f"{BASE}/v1beta/models/gemini-2.0-flash-001:generateContent",   "gemini-2.0-flash-001",   True),
    (f"{BASE}/v1beta/models/gemini-1.5-flash-002:generateContent",   "gemini-1.5-flash-002",   True),
]

# סימנים בכותרת שמזהים נסח טאבו
TABU_TITLE_HINTS = [
    'הרשות לרישום', 'רישום מקרקעין', 'לשכת רישום', 'land registry',
    'נסח טאבו', 'נסח מס', 'נסח רישום', 'settlement of rights',
]

def _is_tabu_by_title(text: str) -> bool:
    """בודק אם הטקסט מכיל כותרת אופיינית לנסח טאבו"""
    sample = text[:600].lower()
    return any(h.lower() in sample for h in TABU_TITLE_HINTS)

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
  "encumbrances": "רלוונטי לטאבו בלבד. בדוק את כל הרשומות (בעלות, הערות אזהרה, עיקולים, משכנתאות). חפש עיקולים המתייחסים לבעל הנכס — בין אם רשום כבעלים ובין אם שמו מופיע בהערת אזהרה. אם יש — פרט: סוג (עיקול/משכנתא/הערת אזהרה), שם הגורם הרושם, וסכום אם מצוין. אם אין שעבוד או עיקול כלשהו — החזר 'לא'",
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
  "encumbrances": "רלוונטי לטאבו בלבד. בדוק את כל הרשומות (בעלות, הערות אזהרה, עיקולים, משכנתאות). חפש עיקולים המתייחסים לבעל הנכס — בין אם רשום כבעלים ובין אם שמו מופיע בהערת אזהרה. אם יש — פרט: סוג (עיקול/משכנתא/הערת אזהרה), שם הגורם הרושם, וסכום אם מצוין. אם אין שעבוד או עיקול כלשהו — החזר 'לא'",
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
    "permit_number": None, "permit_date": None, "permit_type": None
}

def fallback_result(reason: str) -> dict:
    print(f"\n❌ Fallback: {reason}\n")
    return {"status": "error", "message": reason, "ai_analysis": EMPTY_ANALYSIS}

def _parse_response(r) -> dict:
    raw   = r.json()['candidates'][0]['content']['parts'][0]['text']
    clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    return json.loads(clean)

def _call_models(payload: dict) -> dict:
    last_err = "לא נוסה אף מודל"
    for url, label, use_mime in MODELS:
        full_url = f"{url}?key={API_KEY}"
        gen_cfg  = {"temperature": 0}
        if use_mime:
            gen_cfg["responseMimeType"] = "application/json"
        payload["generationConfig"] = gen_cfg

        for attempt in range(2):
            try:
                print(f"   🔄 {'retry — ' if attempt else ''}{label}")
                r = requests.post(full_url, headers=HEADERS, json=payload, timeout=60)

                if r.status_code == 200:
                    data = _parse_response(r)
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

            except requests.exceptions.Timeout:
                last_err = f"Timeout: {label}"; break
            except Exception as e:
                last_err = str(e); break

    raise RuntimeError(f"כל המודלים נכשלו. שגיאה אחרונה: {last_err}")

def _safe_pixmap(page, target_dpi: int = 150) -> fitz.Pixmap:
    """
    ממיר עמוד PDF לתמונה עם DPI בטוח.
    אם התמונה גדולה מדי (>60,000 פיקסל בצד) — מוריד DPI אוטומטית.
    """
    MAX_PX = 60_000
    for dpi in [target_dpi, 120, 96, 72]:
        scale = dpi / 72.0
        mat   = fitz.Matrix(scale, scale)
        pix   = page.get_pixmap(matrix=mat, alpha=False)
        if pix.width <= MAX_PX and pix.height <= MAX_PX:
            return pix
        print(f"   ⚠️ {dpi} DPI → {pix.width}×{pix.height} גדול מדי, מנסה {dpi-24} DPI")
    # fallback אחרון — DPI 48
    mat = fitz.Matrix(48/72, 48/72)
    return page.get_pixmap(matrix=mat, alpha=False)

def scan_text(text: str) -> dict:
    payload = {"contents": [{"parts": [{"text": PROMPT_TEXT + "\n\nטקסט המסמך:\n" + text[:8000]}]}]}
    return _call_models(payload)

def scan_image_bytes(img_bytes: bytes, mime: str) -> dict:
    b64 = base64.b64encode(img_bytes).decode()
    payload = {"contents": [{"parts": [
        {"text": PROMPT_IMAGE},
        {"inline_data": {"mime_type": mime, "data": b64}}
    ]}]}
    return _call_models(payload)

def scan_scanned_pdf(pdf_bytes: bytes) -> dict:
    """PDF סרוק — ממיר עמודים לתמונות עם DPI בטוח"""
    doc   = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = min(3, doc.page_count)
    parts = [{"text": PROMPT_IMAGE}]
    for i in range(pages):
        pix = _safe_pixmap(doc[i], target_dpi=150)
        b64 = base64.b64encode(pix.tobytes("jpeg", jpg_quality=85)).decode()
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        print(f"   🖼️  עמוד {i+1}: {pix.width}×{pix.height} px")
    doc.close()
    return _call_models({"contents": [{"parts": parts}]})

@app.on_event("startup")
async def startup_event():
    print("\n" + "="*60)
    print(f"🚀 LegalFlow Server — עד {MAX_FILES} מסמכים | טאבו+ארנונה+היתר 🚀")
    print("="*60 + "\n")

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.post("/api/scan-documents")
async def scan_documents(files: list[UploadFile] = File(...)):
    print(f"\n{'='*50}\nהתקבלו {len(files)} קבצים")

    if len(files) > MAX_FILES:
        return fallback_result(f"ניתן לסרוק עד {MAX_FILES} קבצים בו-זמנית (התקבלו {len(files)})")

    results = {}  # document_type → ai_analysis

    for file in files:
        file_bytes = await file.read()
        fname      = file.filename.lower()
        ext        = '.' + fname.rsplit('.', 1)[-1] if '.' in fname else ''
        size_mb    = len(file_bytes) / 1_048_576
        print(f"\n📄 סורק: {file.filename} ({size_mb:.1f} MB) ext={ext}")

        try:
            if ext == '.pdf':
                doc  = fitz.open(stream=file_bytes, filetype="pdf")
                text = "".join(doc[i].get_text() for i in range(min(4, doc.page_count)))
                doc.close()
                char_count = len(text.strip())
                print(f"   📝 טקסט: {char_count} תווים")

                if char_count > 80:
                    print("   🔤 PDF טקסטואלי")
                    # זיהוי נסח לפי כותרת לפני שליחה ל-AI
                    if _is_tabu_by_title(text):
                        print("   📌 זוהה נסח טאבו לפי כותרת")
                    ai_data = scan_text(text)
                    # אם ה-AI לא זיהה כטאבו אבל הכותרת מורה — תקן
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
                    print("   🔄 TIFF → JPEG")
                ai_data = scan_image_bytes(file_bytes, mime)

            else:
                print(f"   ⚠️ פורמט לא נתמך: {ext or fname} — מדלג")
                continue

            doc_type = (ai_data.get("document_type") or "לא_ידוע").strip()
            print(f"   📦 סוג: {doc_type} | {json.dumps(ai_data, ensure_ascii=False)}")
            results[doc_type] = ai_data

        except Exception as e:
            print(f"   ❌ שגיאה ב-{file.filename}: {e}")
            continue

    if not results:
        return fallback_result("לא נסרק אף קובץ בהצלחה")

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
        "all_results": results
    }

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)