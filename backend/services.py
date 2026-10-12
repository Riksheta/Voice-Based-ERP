"""
Business logic. Routes call these functions, and so will the voice layer later.
Nothing in here knows about HTTP, so a spoken command and a button tap
go through exactly the same validation and permission checks.
"""
import csv
import io
import json
from datetime import date, datetime

from db import get_db

STATUSES = ("present", "absent", "late")
MAX_PERIOD = 8


class ServiceError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------- helpers ----------

def parse_date(value):
    if not value:
        return date.today()
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ServiceError("Date must look like 2026-10-11.")
    if d > date.today():
        raise ServiceError("You can't mark attendance for a future date.")
    return d


def parse_period(value):
    try:
        p = int(value)
    except (TypeError, ValueError):
        raise ServiceError("Period must be a number.")
    if not 1 <= p <= MAX_PERIOD:
        raise ServiceError(f"Period must be between 1 and {MAX_PERIOD}.")
    return p


def own_course(faculty_id, course_id):
    """Return the course row if this faculty teaches it, else raise."""
    try:
        course_id = int(course_id)
    except (TypeError, ValueError):
        raise ServiceError("Choose a course first.")
    row = get_db().execute(
        "SELECT c.id, c.code, c.name, c.class_id, c.faculty_id, cl.name AS class_name "
        "FROM courses c JOIN classes cl ON cl.id = c.class_id WHERE c.id = ?",
        (course_id,),
    ).fetchone()
    if row is None:
        raise ServiceError("Course not found.", 404)
    if row["faculty_id"] != faculty_id:
        raise ServiceError("You don't teach this course.", 403)
    return row


def log_action(faculty_id, action, payload, source):
    get_db().execute(
        "INSERT INTO command_log (faculty_id, action, payload, source) VALUES (?,?,?,?)",
        (faculty_id, action, json.dumps(payload), source),
    )


# ---------- courses and classes ----------

def list_courses(faculty_id):
    rows = get_db().execute(
        "SELECT c.id, c.code, c.name, c.class_id, cl.name AS class_name, "
        "(SELECT COUNT(*) FROM students s WHERE s.class_id = c.class_id) AS student_count "
        "FROM courses c JOIN classes cl ON cl.id = c.class_id "
        "WHERE c.faculty_id = ? ORDER BY c.code",
        (faculty_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_classes(faculty_id):
    rows = get_db().execute(
        "SELECT DISTINCT cl.id, cl.name FROM classes cl "
        "JOIN courses c ON c.class_id = cl.id WHERE c.faculty_id = ? ORDER BY cl.name",
        (faculty_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_students(faculty_id, class_id=None):
    allowed = {c["id"] for c in list_classes(faculty_id)}
    if class_id is not None:
        if class_id not in allowed:
            raise ServiceError("You don't teach this class.", 403)
        ids = [class_id]
    else:
        ids = sorted(allowed)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = get_db().execute(
        f"SELECT s.id, s.roll_no, s.name, s.class_id, cl.name AS class_name "
        f"FROM students s JOIN classes cl ON cl.id = s.class_id "
        f"WHERE s.class_id IN ({marks}) ORDER BY cl.name, s.roll_no",
        ids,
    ).fetchall()
    return [dict(r) for r in rows]


def class_students(faculty_id, course_id):
    """Students of a course's class (used by the voice parser to match spoken names)."""
    course = own_course(faculty_id, course_id)
    rows = get_db().execute(
        "SELECT id, roll_no, name FROM students WHERE class_id = ? ORDER BY roll_no",
        (course["class_id"],),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- attendance ----------

def get_roster(faculty_id, course_id, on_date, period):
    course = own_course(faculty_id, course_id)
    d = parse_date(on_date).isoformat()
    p = parse_period(period)
    db = get_db()
    students = db.execute(
        "SELECT s.id, s.roll_no, s.name, a.status "
        "FROM students s LEFT JOIN attendance a "
        "  ON a.student_id = s.id AND a.course_id = ? AND a.date = ? AND a.period = ? "
        "WHERE s.class_id = ? ORDER BY s.roll_no",
        (course["id"], d, p, course["class_id"]),
    ).fetchall()
    marked = db.execute(
        "SELECT DISTINCT period FROM attendance WHERE course_id = ? AND date = ? ORDER BY period",
        (course["id"], d),
    ).fetchall()
    return {
        "course": {k: course[k] for k in ("id", "code", "name", "class_name")},
        "date": d,
        "period": p,
        "already_marked": any(s["status"] for s in students),
        "marked_periods": [m["period"] for m in marked],
        "students": [dict(s) for s in students],
    }


def mark_attendance(faculty_id, course_id, period, records, on_date=None, source="form"):
    course = own_course(faculty_id, course_id)
    d = parse_date(on_date).isoformat()
    p = parse_period(period)

    if not isinstance(records, list) or not records:
        raise ServiceError("No attendance records were sent.")

    db = get_db()
    valid_ids = {
        r["id"] for r in db.execute(
            "SELECT id FROM students WHERE class_id = ?", (course["class_id"],)
        )
    }
    rows = {}
    for r in records:
        try:
            sid = int(r["student_id"])
            status = r["status"]
        except (KeyError, TypeError, ValueError):
            raise ServiceError("Each record needs student_id and status.")
        if sid not in valid_ids:
            raise ServiceError(f"Student {sid} is not in {course['class_name']}.")
        if status not in STATUSES:
            raise ServiceError("Status must be present, absent or late.")
        rows[sid] = status  # last one wins if a student is repeated

    try:
        db.executemany(
            "INSERT INTO attendance (student_id, course_id, date, period, status, marked_by) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(student_id, course_id, date, period) "
            "DO UPDATE SET status = excluded.status, marked_by = excluded.marked_by",
            [(sid, course["id"], d, p, st, faculty_id) for sid, st in rows.items()],
        )
        log_action(
            faculty_id, "mark_attendance",
            {"course_id": course["id"], "date": d, "period": p, "count": len(rows)},
            source,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    counts = {s: sum(1 for v in rows.values() if v == s) for s in STATUSES}
    return {"saved": len(rows), "date": d, "period": p, **counts}


def attendance_summary(faculty_id, course_id, threshold=75):
    course = own_course(faculty_id, course_id)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        raise ServiceError("Threshold must be a number.")
    db = get_db()
    sessions = db.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM attendance WHERE course_id = ? GROUP BY date, period)",
        (course["id"],),
    ).fetchone()[0]
    rows = db.execute(
        "SELECT s.id, s.roll_no, s.name, "
        "  COALESCE(SUM(a.status = 'present'), 0) AS present, "
        "  COALESCE(SUM(a.status = 'late'), 0)    AS late, "
        "  COALESCE(SUM(a.status = 'absent'), 0)  AS absent "
        "FROM students s LEFT JOIN attendance a "
        "  ON a.student_id = s.id AND a.course_id = ? "
        "WHERE s.class_id = ? GROUP BY s.id ORDER BY s.roll_no",
        (course["id"], course["class_id"]),
    ).fetchall()

    students = []
    for r in rows:
        attended = r["present"] + r["late"]          # late still counts as attended
        pct = round(attended * 100 / sessions, 1) if sessions else None
        students.append({
            "id": r["id"], "roll_no": r["roll_no"], "name": r["name"],
            "present": r["present"], "late": r["late"], "absent": r["absent"],
            "attended": attended, "sessions": sessions, "percent": pct,
            "defaulter": pct is not None and pct < threshold,
        })
    return {
        "course": {k: course[k] for k in ("id", "code", "name", "class_name")},
        "sessions": sessions, "threshold": threshold, "students": students,
    }


def dashboard(faculty_id, today=None):
    today = (today or date.today()).isoformat()
    db = get_db()
    out = []
    for c in list_courses(faculty_id):
        periods = db.execute(
            "SELECT DISTINCT period FROM attendance WHERE course_id = ? AND date = ? ORDER BY period",
            (c["id"], today),
        ).fetchall()
        summary = attendance_summary(faculty_id, c["id"])
        pcts = [s["percent"] for s in summary["students"] if s["percent"] is not None]
        out.append({
            **c,
            "today_periods": [p["period"] for p in periods],
            "sessions": summary["sessions"],
            "average": round(sum(pcts) / len(pcts), 1) if pcts else None,
            "defaulters": sum(1 for s in summary["students"] if s["defaulter"]),
        })
    return {"date": today, "courses": out}


# ---------- CSV import ----------

def import_students(faculty_id, raw_bytes):
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ServiceError("Save the file as CSV (UTF-8) and try again.")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ServiceError("The file is empty.")
    reader.fieldnames = [(h or "").strip().lower() for h in reader.fieldnames]
    missing = {"roll_no", "name", "class"} - set(reader.fieldnames)
    if missing:
        raise ServiceError("Header row must be: roll_no, name, class. Missing: " + ", ".join(sorted(missing)))

    db = get_db()
    classes = {c["name"].lower(): c["id"] for c in list_classes(faculty_id)}
    added, skipped = 0, []
    for line, row in enumerate(reader, start=2):
        roll = (row.get("roll_no") or "").strip()
        name = (row.get("name") or "").strip()
        cls = (row.get("class") or "").strip()
        if not (roll and name and cls):
            skipped.append(f"Row {line}: roll_no, name and class are all required.")
            continue
        class_id = classes.get(cls.lower())
        if class_id is None:
            skipped.append(f"Row {line}: you don't teach a class named {cls}.")
            continue
        if db.execute("SELECT 1 FROM students WHERE roll_no = ?", (roll,)).fetchone():
            skipped.append(f"Row {line}: roll number {roll} already exists.")
            continue
        db.execute("INSERT INTO students (roll_no, name, class_id) VALUES (?,?,?)", (roll, name, class_id))
        added += 1
    log_action(faculty_id, "import_students", {"added": added, "skipped": len(skipped)}, "form")
    db.commit()
    return {"added": added, "skipped": skipped}