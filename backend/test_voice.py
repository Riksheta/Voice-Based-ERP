"""Voice parser tests. Run from backend/:   python -m unittest test_voice -v"""
import unittest
from datetime import date

from voice import parse

NAMES = ["Rahul Sharma", "Priya Reddy", "Arjun Verma", "Sneha Patel", "Rahul Kumar",
         "Ananya Iyer", "Karthik Naidu", "Meera Nair", "Vikram Singh", "Divya Menon"]
STUDENTS = [{"id": i + 1, "roll_no": f"21CSE{i + 1:03d}", "name": n} for i, n in enumerate(NAMES)]
TODAY = date(2026, 10, 11)


def run(text):
    return parse(text, STUDENTS, TODAY)


def ids(action):
    return [s["id"] for s in action["students"]]


class VoiceParserTests(unittest.TestCase):
    def test_simple_absent(self):
        r = run("mark Priya absent")
        self.assertEqual(r["actions"], [{"type": "set_status", "status": "absent",
                                         "students": [{"id": 2, "roll_no": "21CSE002", "name": "Priya Reddy"}]}])

    def test_two_rahuls_is_ambiguous(self):
        r = run("mark Rahul absent")
        self.assertEqual(r["actions"], [])
        self.assertEqual(len(r["ambiguous"]), 1)
        self.assertEqual({o["id"] for o in r["ambiguous"][0]["options"]}, {1, 5})
        self.assertEqual(r["ambiguous"][0]["status"], "absent")

    def test_full_name_disambiguates(self):
        r = run("Rahul Kumar absent")
        self.assertEqual(ids(r["actions"][0]), [5])
        self.assertEqual(r["ambiguous"], [])

    def test_several_names_one_status(self):
        r = run("Rahul Sharma and Arjun late")
        self.assertEqual((r["actions"][0]["status"], ids(r["actions"][0])), ("late", [1, 3]))

    def test_two_statuses_in_one_sentence(self):
        r = run("Priya absent and Sneha late")
        got = {(a["status"], tuple(ids(a))) for a in r["actions"]}
        self.assertEqual(got, {("absent", (2,)), ("late", (4,))})

    def test_status_first_form(self):
        r = run("mark absent Meera and Divya")
        self.assertEqual((r["actions"][0]["status"], ids(r["actions"][0])), ("absent", [8, 10]))

    def test_everyone_except(self):
        r = run("everyone present except Priya and Karthik")
        self.assertEqual(r["actions"][0], {"type": "set_all", "status": "present"})
        self.assertEqual((r["actions"][1]["status"], ids(r["actions"][1])), ("absent", [2, 7]))

    def test_all_present_and_rest(self):
        self.assertEqual(run("mark everyone present")["actions"], [{"type": "set_all", "status": "present"}])
        r = run("Priya absent, rest present")
        self.assertEqual([a["type"] for a in r["actions"]], ["set_all", "set_status"])  # all first, so Priya stays absent

    def test_roll_numbers(self):
        r = run("roll number 4 and 9 absent")
        self.assertEqual(ids(r["actions"][0]), [4, 9])
        r = run("numbers four nine absent")
        self.assertEqual(ids(r["actions"][0]), [4, 9])
        r = run("number 4 9 absent")
        self.assertEqual(ids(r["actions"][0]), [4, 9])

    def test_period_and_date(self):
        r = run("period 3 mark Priya absent")
        self.assertEqual(r["actions"][0], {"type": "set_period", "period": 3})
        r = run("Priya absent second period")
        self.assertEqual(r["actions"][0], {"type": "set_period", "period": 2})
        r = run("yesterday Meera absent")
        self.assertEqual(r["actions"][0], {"type": "set_date", "date": "2026-10-10"})
        self.assertEqual(run("period 12 all present")["unresolved"], ["period 12"])

    def test_save_undo_report(self):
        self.assertEqual(run("save")["actions"], [{"type": "save"}])
        self.assertEqual(run("save attendance")["actions"], [{"type": "save"}])
        self.assertEqual(run("undo that")["actions"], [{"type": "undo"}])
        self.assertEqual(run("show defaulters")["actions"], [{"type": "show_report", "defaulters_only": True}])
        self.assertEqual(run("attendance report")["actions"], [{"type": "show_report", "defaulters_only": False}])

    def test_not_present_means_absent(self):
        self.assertEqual(run("Priya not present")["actions"][0]["status"], "absent")

    def test_spelling_variants(self):
        self.assertEqual(ids(run("Karthick Naidu absent")["actions"][0]), [7])
        self.assertEqual(ids(run("Wikram absent")["actions"][0]), [9])

    def test_unknown_name_and_gibberish(self):
        r = run("Zorro absent")
        self.assertEqual((r["actions"], r["unresolved"]), ([], ["zorro"]))
        r = run("hello there")
        self.assertEqual(r["actions"], [])
        self.assertTrue(r["unresolved"])

    def test_commas_and_then(self):
        r = run("Priya absent, then Arjun late")
        self.assertEqual({a["status"] for a in r["actions"]}, {"absent", "late"})


if __name__ == "__main__":
    unittest.main()