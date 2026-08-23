from flask import Flask, render_template, request, jsonify, send_file
import os
import io
import pandas as pd
from datetime import date, datetime

app = Flask(__name__)

# PostgreSQL (production) or SQLite (local dev)
DATABASE_URL = os.environ.get("DATABASE_URL")
_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
DB_FILE = os.path.join(_DATA_DIR, "tracker.db")

# SQL placeholder: %s for PostgreSQL, ? for SQLite
_PH = "%s" if DATABASE_URL else "?"

import logging
if DATABASE_URL:
    logging.warning("DB mode: PostgreSQL (data will persist across deploys)")
else:
    logging.warning("DB mode: SQLite — set DATABASE_URL to persist data on Render!")

QUESTIONS = [
    # Morning
    {"id": "sleep",     "label": "Slept 6-8 Hours",          "expected": "yes"},
    {"id": "skincare",  "label": "Did Skincare",             "expected": "yes"},
    {"id": "breakfast", "label": "Balanced Breakfast",       "expected": "yes"},
    {"id": "protein",   "label": "Protein Intake",           "expected": "yes"},
    # Daytime
    {"id": "water",     "label": "Water 6-8 Glasses",        "expected": "yes"},
    {"id": "lunch",     "label": "Balanced Lunch",           "expected": "yes"},
    {"id": "fibre",     "label": "Fibre Intake",             "expected": "yes"},
    {"id": "sugar",     "label": "Sugar Intake",             "expected": "no"},
    {"id": "junk",      "label": "Junk Food",                "expected": "no"},
    {"id": "dairy",     "label": "Dairy Intake",             "expected": "no"},
    # Evening / Night
    {"id": "workout",   "label": "Workout (Gym / Badminton)","expected": "yes"},
    {"id": "study",     "label": "Study",                    "expected": "yes"},
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_conn():
    """Return a DB connection for the active backend."""
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    import sqlite3
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _run(sql, params=None, fetch=False):
    """Execute SQL on the active backend; return list of dicts when fetch=True."""
    conn = _get_conn()
    try:
        if DATABASE_URL:
            with conn:
                cur = conn.cursor()
                cur.execute(sql, params or [])
                return [dict(r) for r in cur.fetchall()] if fetch else []
        else:
            with conn:
                cur = conn.execute(sql, params or [])
                return [dict(r) for r in cur.fetchall()] if fetch else []
    finally:
        conn.close()


def init_db():
    cols = ", ".join(f'"{q["id"]}" TEXT' for q in QUESTIONS)
    _run(f"""
        CREATE TABLE IF NOT EXISTS entries (
            date TEXT PRIMARY KEY,
            {cols},
            score_pct REAL
        )
    """)


def save_entry(answers: dict, score_pct: float, for_date: str = None):
    entry_date = for_date or str(date.today())
    q_ids   = [q["id"] for q in QUESTIONS]
    cols    = ["date"] + q_ids + ["score_pct"]
    vals    = [entry_date] + [answers.get(qid, "") for qid in q_ids] + [score_pct]
    col_str = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join([_PH] * len(cols))
    update_str   = ", ".join(f'"{c}"=excluded."{c}"' for c in cols[1:])
    _run(
        f'INSERT INTO entries ({col_str}) VALUES ({placeholders}) '
        f'ON CONFLICT(date) DO UPDATE SET {update_str}',
        vals,
    )


def get_history():
    try:
        return _run("SELECT * FROM entries ORDER BY date ASC", fetch=True)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    # Resolve entry date — from query param (?date=YYYY-MM-DD) or form field
    raw = request.args.get("date") or request.form.get("entry_date", "")
    try:
        entry_date = datetime.strptime(raw, "%Y-%m-%d").date()
        if entry_date > date.today():   # block future dates
            entry_date = date.today()
    except (ValueError, TypeError):
        entry_date = date.today()

    entry_date_str = str(entry_date)
    is_today = (entry_date == date.today())

    result = None
    if request.method == "POST":
        answers = {q["id"]: request.form.get(q["id"], "no") for q in QUESTIONS}
        items, score = [], 0
        for q in QUESTIONS:
            passed = answers[q["id"]] == q["expected"]
            if passed:
                score += 1
            items.append({
                "id":       q["id"],
                "label":    q["label"],
                "answer":   answers[q["id"]],
                "expected": q["expected"],
                "passed":   passed,
            })
        score_pct = round((score / len(QUESTIONS)) * 100, 2)
        save_entry(answers, score_pct, entry_date_str)
        result = {"entries": items, "score": score, "total": len(QUESTIONS), "pct": score_pct}

    return render_template("index.html", questions=QUESTIONS, result=result,
                           entry_date=entry_date_str, is_today=is_today)


@app.route("/dashboard")
def dashboard():
    history = get_history()
    recorded_dates = [row["date"] for row in history]
    return render_template("dashboard.html", history=history, questions=QUESTIONS,
                           recorded_dates=recorded_dates)


@app.route("/export")
def export():
    """Download all data as an Excel file."""
    history = get_history()
    if not history:
        return "No data to export.", 404
    df = pd.DataFrame(history)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Daily Tracker")
    buf.seek(0)
    return send_file(
        buf,
        download_name="daily_tracker_export.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/delete/<entry_date>", methods=["DELETE"])
def delete_entry(entry_date):
    try:
        datetime.strptime(entry_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400
    try:
        _run(f"DELETE FROM entries WHERE date = {_PH}", [entry_date])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history")
def api_history():
    return jsonify(get_history())


# Run at import time so gunicorn also initialises the DB
init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
