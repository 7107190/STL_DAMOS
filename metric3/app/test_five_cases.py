import unittest

from m3_rules import rollup, select_cases


class FiveCases(unittest.TestCase):
    def setUp(self):
        self.cases = [{"case_id": f"test-{i:03}", "scenario": f"S{(i-1)%9+1}", "seed": i} for i in range(1, 101)]

    def test_first_five_keep_locked_case_objects(self):
        selected = select_cases(self.cases, "five", "S1")
        self.assertEqual(len(selected), 5)
        for current, original in zip(selected, self.cases):
            self.assertIs(current, original)

    def test_full_100_unchanged(self):
        self.assertIs(select_cases(self.cases, "full", "S1"), self.cases)

    def test_three_keeps_first_locked_cases(self):
        selected = select_cases(self.cases, "three", "S1")
        self.assertEqual(selected, self.cases[:3])
        self.assertTrue(all(a is b for a, b in zip(selected, self.cases)))

    def test_single_and_sweep_unchanged(self):
        self.assertEqual(select_cases(self.cases, "single", "S3"), [self.cases[2]])
        self.assertEqual(select_cases(self.cases, "sweep", "S1"), self.cases[:9])

    def test_numbering_uses_entire_plan(self):
        result = rollup(self.cases[:5], {})
        self.assertEqual([r["evaluation_number"] for r in result["cases"]], [1, 2, 3, 4, 5])
        self.assertEqual(result["planned"], 5)
        self.assertEqual(result["evaluated"], 0)
        self.assertFalse(result["criterion_pass"])

    def test_full_success_criterion_unchanged(self):
        results = {c["case_id"]: {"status": "success" if i < 70 else "failure"} for i, c in enumerate(self.cases)}
        self.assertTrue(rollup(self.cases, results)["criterion_pass"])
        results[self.cases[0]["case_id"]]["status"] = "failure"
        self.assertFalse(rollup(self.cases, results)["criterion_pass"])

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            select_cases(self.cases, "unknown", "S1")


if __name__ == "__main__":
    unittest.main()
