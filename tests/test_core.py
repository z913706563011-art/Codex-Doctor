import json
import unittest
from pathlib import Path

from codex_doctor.core import MIB, CheckReport, SessionFile, StateMetrics, recommendations


class AdviceTests(unittest.TestCase):
    def test_synthetic_check_cases(self):
        path = Path(__file__).with_name("fixtures") / "check_cases.json"
        for case in json.loads(path.read_text(encoding="utf-8")):
            with self.subTest(case=case["name"]):
                report = CheckReport(
                    sessions=tuple(SessionFile(Path(f"demo-{i}.jsonl"), n * MIB)
                                   for i, n in enumerate(case["mib"])),
                    state=StateMetrics(81, 1, {"updated_at_ms": ("demo_index",)},
                                       tuple(case["missing"]), case["unassigned"], case["broken"]),
                    log_bytes=case["log_mib"] * MIB,
                    log_wal_bytes=0,
                    old_bytes=case["old_mib"] * MIB if case["old_mib"] else None,
                    errors=tuple(case["errors"]),
                )
                self.assertEqual([action.code for action in recommendations(report)], case["actions"])
                if case["name"] == "mild":
                    self.assertEqual(report.counts, {"安全": 0, "轻度": 2, "重度": 0})
