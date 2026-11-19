"""Demo script tests. Each docstring names the rule it guards."""
import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEMO = os.path.join(ROOT, "examples", "demo.sh")


class DemoTests(unittest.TestCase):
    def test_demo_parts(self):
        """source: the sample run prints each labelled part and succeeds only when every part holds."""
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("NIGHT_SHIFT_DIR", None)
        env.pop("CLAUDE_CONFIG_DIR", None)
        result = subprocess.run(
            ["/bin/sh", DEMO],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        for label in (
            "part 1 desk",
            "part 2 check",
            "part 3 verdicts",
            "part 4 handover",
            "part 5 brief",
            "part 6 next morning",
        ):
            self.assertIn(label, result.stdout)
        self.assertIn("desk: 4 windows, 4 new", result.stdout)
        self.assertIn("  no verdict: 33333333, 22222222, 11111111, billing", result.stdout)
        self.assertIn("BROKE AT: reckoning", result.stdout)
        self.assertIn("  no packet: 33333333", result.stdout)
        self.assertIn("BROKE AT: handover", result.stdout)
        self.assertIn("Goal: ship the new checkout", result.stdout)
        self.assertIn("Next: wire the new form to the API", result.stdout)
        self.assertIn("all four stages stand", result.stdout)
        self.assertIn("  used after: 11111111", result.stdout)
        self.assertNotIn("44444444", result.stdout)
        self.assertIn("# Night shift 2026-01-15", result.stdout)


if __name__ == "__main__":
    unittest.main()
