"""
Backend tests. Run from the backend folder:   python -m unittest -v
Uses a throwaway database, so your real erp.db is never touched.
"""
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["ERP_DB"] = _tmp.name

import app as app_module  # noqa: E402  (must come after ERP_DB is set)
import db as db_module     # noqa: E402


def build_data():
    conn = sqlite3.connect(db_module.DB_PATH)
    conn.executescript(
        """
        DELETE FROM attendance; DELETE FROM courses; DELETE FROM students;
        DELETE FROM classes; DELETE FROM faculty; DELETE FROM command_log;
        """
    )
    pw = generate_password_hash("pw123")
    conn.execute("INSERT INTO faculty (id,name,email,password_hash) VALUES (1,'Anita','a@x.edu',?)", (pw,))
    conn.execute("INSERT INTO faculty (id,name,email,password_hash) VALUES (2,'Suresh','s@x.edu',?)", (pw,))
    conn.execute("INSERT INTO classes (id,name) VALUES (1,'CSE-A')")
    conn.execute("INSERT INTO classes (id,name) VALUES (2,'CSE-B')")
    for i in range(1, 5):
        conn.execute("INSERT INTO students (id,roll_no,name,class_id) VALUES (?,?,?,1)", (i, f"R{i}", f"Student {i}"))
    conn.execute("INSERT INTO students (id,roll_no,name,class_id) VALUES (9,'R9','Other Class',2)")
    conn.execute("INSERT INTO courses (id,code,name,faculty_id,class_id) VALUES (1,'C1','DBMS',1,1)")
    conn.execute("INSERT INTO courses (id,code,name,faculty_id,class_id) VALUES (2,'C2','OS',2,2)")
    conn.commit()
    conn.close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        build_data()
        self.c = app_module.app.test_client()

    def login(self, email="a@x.edu"):
        r = self.c.post("/api/login", json={"email": email, "password": "pw123"})
        self.assertEqual(r.status_code, 200)

    def mark(self, statuses, period=1, course=1, **extra):
        recs = [{"student_id": i + 1, "status": s} for i, s in enumerate(statuses)]
        return self.c.post("/api/attendance", json={"course_id": course, "period": period, "records": recs, **extra})

    def test_login_required(self):
        self.assertEqual(self.c.get("/api/courses").status_code, 401)

    def test_bad_password(self):
        r = self.c.post("/api/login", json={"email": "a@x.edu", "password": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_mark_and_remark_updates_not_duplicates(self):
        self.login()
        self.assertEqual(self.mark(["present", "absent", "late", "present"]).status_code, 200)
        self.assertEqual(self.mark(["present", "present", "late", "absent"]).status_code, 200)
        n = sqlite3.connect(db_module.DB_PATH).execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
        self.assertEqual(n, 4)
        roster = self.c.get("/api/courses/1/roster?period=1").get_json()
        self.assertEqual([s["status"] for s in roster["students"]], ["present", "present", "late", "absent"])
        self.assertTrue(roster["already_marked"])
        self.assertEqual(roster["marked_periods"], [1])

    def test_cannot_mark_other_faculty_course(self):
        self.login()
        r = self.mark(["present"], course=2)
        self.assertEqual(r.status_code, 403)

    def test_student_from_other_class_rejected(self):
        self.login()
        r = self.c.post("/api/attendance", json={"course_id": 1, "period": 1,
                        "records": [{"student_id": 9, "status": "present"}]})
        self.assertEqual(r.status_code, 400)

    def test_validation(self):
        self.login()
        bad_status = self.c.post("/api/attendance", json={"course_id": 1, "period": 1,
                                 "records": [{"student_id": 1, "status": "sleeping"}]})
        self.assertEqual(bad_status.status_code, 400)
        self.assertEqual(self.mark(["present"], period=99).status_code, 400)
        self.assertEqual(self.mark(["present"], date="2999-01-01").status_code, 400)
        self.assertEqual(self.c.post("/api/attendance", json={"course_id": 1, "period": 1, "records": []}).status_code, 400)

    def test_summary_percentages_and_defaulters(self):
        self.login()
        # Student 2 absent in 2 of 4 sessions, Student 3 late once (counts as attended)
        self.mark(["present", "absent", "late", "present"], period=1)
        self.mark(["present", "absent", "present", "present"], period=2)
        self.mark(["present", "present", "present", "present"], period=3)
        self.mark(["present", "present", "present", "present"], period=4)
        s = self.c.get("/api/courses/1/summary?threshold=75").get_json()
        self.assertEqual(s["sessions"], 4)
        by = {r["roll_no"]: r for r in s["students"]}
        self.assertEqual(by["R1"]["percent"], 100.0)
        self.assertEqual(by["R2"]["percent"], 50.0)
        self.assertTrue(by["R2"]["defaulter"])
        self.assertEqual(by["R3"]["percent"], 100.0)
        self.assertFalse(by["R3"]["defaulter"])

    def test_summary_with_no_sessions(self):
        self.login()
        s = self.c.get("/api/courses/1/summary").get_json()
        self.assertEqual(s["sessions"], 0)
        self.assertIsNone(s["students"][0]["percent"])
        self.assertFalse(s["students"][0]["defaulter"])

    def test_dashboard_and_csv_report(self):
        self.login()
        self.mark(["present", "absent", "present", "present"])
        d = self.c.get("/api/dashboard").get_json()
        self.assertEqual(d["courses"][0]["today_periods"], [1])
        self.assertEqual(d["courses"][0]["defaulters"], 1)
        r = self.c.get("/api/courses/1/report.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("roll_no,name,present", r.get_data(as_text=True))

    def test_csv_import(self):
        self.login()
        csv_text = ("roll_no,name,class\n"
                    "R20,New Kid,CSE-A\n"
                    "R1,Duplicate,CSE-A\n"
                    "R21,Wrong Class,CSE-B\n"
                    ",Missing Roll,CSE-A\n")
        r = self.c.post("/api/students/upload", data={"file": (io.BytesIO(csv_text.encode()), "s.csv")},
                        content_type="multipart/form-data")
        body = r.get_json()
        self.assertEqual(body["added"], 1)
        self.assertEqual(len(body["skipped"]), 3)

    def test_csv_import_bad_header(self):
        self.login()
        r = self.c.post("/api/students/upload", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "s.csv")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_students_scoped_to_taught_classes(self):
        self.login()
        names = [s["roll_no"] for s in self.c.get("/api/students").get_json()]
        self.assertNotIn("R9", names)
        self.assertEqual(self.c.get("/api/students?class_id=2").status_code, 403)

    def test_command_endpoint_logs_voice_source(self):
        self.login()
        r = self.c.post("/api/command", json={"action": "mark_attendance", "course_id": 1, "period": 2,
                        "records": [{"student_id": 1, "status": "absent"}], "source": "voice"})
        self.assertEqual(r.status_code, 200)
        src = sqlite3.connect(db_module.DB_PATH).execute("SELECT source FROM command_log").fetchone()[0]
        self.assertEqual(src, "voice")
        self.assertEqual(self.c.post("/api/command", json={"action": "fly"}).status_code, 400)

    def test_frontend_served(self):
        self.assertEqual(self.c.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()