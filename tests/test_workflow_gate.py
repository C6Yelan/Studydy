import json
import tempfile
import unittest
from pathlib import Path

from scripts.workflow_gate import evaluate_result


class WorkflowGateTest(unittest.TestCase):
    def evaluate(self, checks):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps({"case_id": "test-case", "checks": checks}), encoding="utf-8")
            return evaluate_result(path)

    def test_passes_when_every_check_passes(self):
        result = self.evaluate([{"check_id": "one", "status": "pass"}])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["counts"]["pass"], 1)

    def test_reports_fail_without_critical_regression(self):
        result = self.evaluate(
            [
                {"check_id": "one", "status": "pass"},
                {"check_id": "two", "status": "fail"},
            ]
        )
        self.assertEqual(result["status"], "fail")

    def test_critical_regression_has_highest_priority(self):
        result = self.evaluate(
            [
                {"check_id": "one", "status": "fail"},
                {"check_id": "two", "status": "critical_regression"},
            ]
        )
        self.assertEqual(result["status"], "critical_regression")

    def test_rejects_unknown_status(self):
        with self.assertRaisesRegex(ValueError, "must be one of"):
            self.evaluate([{"check_id": "one", "status": "unknown"}])


if __name__ == "__main__":
    unittest.main()
