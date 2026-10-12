# backend/seed.py
import sqlite3
import os
from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "..", "database", "erp.db")

def seed_db():
    if not os.path.exists(DB_PATH):
        print("Database does not exist. Run app.py or initialize schema first.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")

    # Insert sample faculty matching the demo account
    hashed_password = generate_password_hash('test123')
    cursor.execute("""
        INSERT OR REPLACE INTO faculty (id, name, email, password_hash)
        VALUES (1, 'Dr. Anita', 'anita@college.edu', ?)
    """, (hashed_password,))

    # Insert sample class
    cursor.execute("""
        INSERT OR IGNORE INTO classes (id, name)
        VALUES (1, 'Computer Science Year 3')
    """)

    # Insert sample students
    students = [
        (1, 'CS301', 'Alice Johnson', 1),
        (2, 'CS302', 'Bob Smith', 1),
        (3, 'CS303', 'Charlie Brown', 1)
    ]
    cursor.executemany("""
        INSERT OR IGNORE INTO students (id, roll_no, name, class_id)
        VALUES (?, ?, ?, ?)
    """, students)

    # Insert sample course
    cursor.execute("""
        INSERT OR IGNORE INTO courses (id, code, name, faculty_id, class_id)
        VALUES (1, 'CS3010', 'Database Systems', 1, 1)
    """)

    conn.commit()
    conn.close()
    print("Database seeded successfully with demo credentials!")

if __name__ == "__main__":
    seed_db()