#!/bin/bash
# start.sh — מפעיל את שני השרתים במקביל
# app.py     → Flask/Gunicorn  port 5000  (GOVMAP + index.html)
# server_6.py → FastAPI/Uvicorn port 8000  (Gemini document scan)

cd "$(dirname "$0")"

# הפעל venv אם קיים
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

echo "🚀 מפעיל app.py (Flask) על port 5000..."
gunicorn app:app \
    --bind 0.0.0.0:5000 \
    --workers 2 \
    --timeout 120 \
    --limit-request-line 0 \
    --limit-request-field_size 0 \
    --daemon \
    --pid /tmp/gunicorn_flask.pid \
    --log-file /tmp/flask.log

echo "🚀 מפעיל server_6.py (FastAPI) על port 8000..."
nohup uvicorn server_6:app \
    --host 0.0.0.0 \
    --port 8000 \
    --timeout-keep-alive 120 \
    > /tmp/uvicorn.log 2>&1 &
echo $! > /tmp/uvicorn.pid

echo "✅ שני השרתים פועלים:"
echo "   http://185.241.6.63:5000  — Flask (GOVMAP + HTML)"
echo "   http://185.241.6.63:8000  — FastAPI (סריקת מסמכים)"
