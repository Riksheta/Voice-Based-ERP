"""
Roll Call: Flask backend.

Run:   python app.py        then open http://localhost:5000
Seed:  python seed.py       (sample faculty, classes, students, courses)
"""
import csv
import io
import os
from functools import wraps

from flask import Flask, Response, jsonify, request, session
from werkzeug.security import check_password_hash

import db
import services
import voice
from services import ServiceError

app = Flask(__name__, static_folder="../frontend", static_url_path="")
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,          # 1 MB upload limit
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SEND_FILE_MAX_AGE_DEFAULT=0,                 # always serve fresh frontend files while developing
)
db.init_app(app)


# ---------- plumbing ----------

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "faculty_id" not in session:
            return jsonify(error="Please sign in."), 401
        return fn(*args, **kwargs)
    return wrapper


def me():
    return session["faculty_id"]


@app.errorhandler(ServiceError)
def handle_service_error(e):
    return jsonify(error=e.message), e.code


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error="File is too large. The limit is 1 MB."), 413


@app.get("/")
def home():
    return app.send_static_file("index.html")


# ---------- auth ----------

@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    row = db.get_db().execute("SELECT * FROM faculty WHERE lower(email) = ?", (email,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], d.get("password") or ""):
        return jsonify(error="Email or password is incorrect."), 401
    session.clear()
    session["faculty_id"] = row["id"]
    return jsonify(id=row["id"], name=row["name"], email=row["email"])


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@login_required
def whoami():
    row = db.get_db().execute("SELECT id, name, email FROM faculty WHERE id = ?", (me(),)).fetchone()
    if row is None:
        session.clear()
        return jsonify(error="Please sign in."), 401
    return jsonify(dict(row))


# ---------- data ----------

@app.get("/api/dashboard")
@login_required
def dashboard():
    return jsonify(services.dashboard(me()))


@app.get("/api/courses")
@login_required
def courses():
    return jsonify(services.list_courses(me()))


@app.get("/api/classes")
@login_required
def classes():
    return jsonify(services.list_classes(me()))


@app.get("/api/students")
@login_required
def students():
    class_id = request.args.get("class_id", type=int)
    return jsonify(services.list_students(me(), class_id))


@app.post("/api/students/upload")
@login_required
def upload_students():
    f = request.files.get("file")
    if f is None or not f.filename:
        raise ServiceError("Choose a CSV file first.")
    return jsonify(services.import_students(me(), f.read()))


@app.get("/api/courses/<int:course_id>/roster")
@login_required
def roster(course_id):
    return jsonify(services.get_roster(
        me(), course_id, request.args.get("date"), request.args.get("period", 1)))


@app.post("/api/attendance")
@login_required
def post_attendance():
    d = request.get_json(silent=True) or {}
    result = services.mark_attendance(
        me(), d.get("course_id"), d.get("period"), d.get("records"), d.get("date"), source="form")
    return jsonify(ok=True, **result)


@app.get("/api/courses/<int:course_id>/summary")
@login_required
def summary(course_id):
    return jsonify(services.attendance_summary(me(), course_id, request.args.get("threshold", 75)))


@app.get("/api/courses/<int:course_id>/report.csv")
@login_required
def report_csv(course_id):
    s = services.attendance_summary(me(), course_id, request.args.get("threshold", 75))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["roll_no", "name", "present", "late", "absent", "sessions", "percent", "below_threshold"])
    for r in s["students"]:
        w.writerow([r["roll_no"], r["name"], r["present"], r["late"], r["absent"],
                    r["sessions"], "" if r["percent"] is None else r["percent"],
                    "yes" if r["defaulter"] else "no"])
    filename = f"attendance_{s['course']['code']}.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# ---------- the voice seam ----------
# Voice will turn speech into this same JSON, then POST it here.
# Because it calls the same service functions as the forms, it gets the same
# validation and permission checks, and it is logged with source = "voice".

@app.post("/api/command")
@login_required
def command():
    d = request.get_json(silent=True) or {}
    action = d.get("action")
    source = d.get("source", "voice")
    if source not in ("form", "voice"):
        raise ServiceError("Unknown source.")
    if action == "mark_attendance":
        result = services.mark_attendance(
            me(), d.get("course_id"), d.get("period"), d.get("records"), d.get("date"), source=source)
        return jsonify(ok=True, **result)
    if action == "get_summary":
        return jsonify(services.attendance_summary(me(), d.get("course_id"), d.get("threshold", 75)))
    raise ServiceError(f"Unknown action: {action}")


@app.post("/api/voice/parse")
@login_required
def voice_parse():
    """Understand a sentence for the attendance screen. Changes nothing; the UI confirms first."""
    d = request.get_json(silent=True) or {}
    text = (d.get("transcript") or "").strip()
    if not text:
        raise ServiceError("Say or type a command first.")
    if len(text) > 400:
        raise ServiceError("That command is too long. Try a shorter one.")
    roster = services.class_students(me(), d.get("course_id"))
    result = voice.parse(text, roster)
    # Keep what was heard, so misrecognised names can be reviewed later
    services.log_action(me(), "voice_parse", {"transcript": text, "actions": len(result["actions"])}, "voice")
    db.get_db().commit()
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)