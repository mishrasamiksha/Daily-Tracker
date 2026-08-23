from flask import Flask, render_template, request, jsonify, send_file
import sqlite3
import pandas as pd
from datetime import date, datetime
import os
import io

app = Flask(__name__)

# On Render, use /data/tracker.db (persistent disk mount point) if available,
# otherwise fall back to the local directory (for development).
_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
DB_FILE = os.path.join(_DATA_DIR, "tracker.db")

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

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    cols = ", ".join(f'"{q["id"]}" TEXT' for q in QUESTIONS)
    with get_db() as conn:
        conn.execute(f"""
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
    placeholders = ", ".join("?" * len(cols))
    update_str   = ", ".join(f'"{c}"=excluded."{c}"' for c in cols[1:])
    with get_db() as conn:
        conn.execute(
            f'INSERT INTO entries ({col_str}) VALUES ({placeholders}) '
            f'ON CONFLICT(date) DO UPDATE SET {update_str}',
            vals,
        )


def get_history():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM entries ORDER BY date ASC").fetchall()
            return [dict(r) for r in rows]
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


@app.route("/api/history")
def api_history():
    return jsonify(get_history())


# Run at import time so gunicorn also initialises the DB
init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
