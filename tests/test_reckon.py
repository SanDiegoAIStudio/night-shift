"""Reckon command tests. Each docstring names the rule it guards."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "night_shift.py")
PY = sys.executable


class ReckonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="night-shift-")
        self.projects = os.path.join(self.tmp, "projects")
        self.nights = os.path.join(self.tmp, "nights")
        os.makedirs(self.projects)
        self.night = "2020-01-15"

    def tearDown(self):
        for dirpath, dirnames, filenames in os.walk(self.tmp):
            for name in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o700)
                except OSError:
                    pass
            for name in filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o600)
                except OSError:
                    pass
        shutil.rmtree(self.tmp)

    def run_tool(self, args):
        merged = os.environ.copy()
        merged["PYTHONDONTWRITEBYTECODE"] = "1"
        merged.pop("NIGHT_SHIFT_DIR", None)
        merged.pop("CLAUDE_CONFIG_DIR", None)
        return subprocess.run(
            [PY, TOOL] + list(args) + ["--projects", self.projects, "--nights", self.nights],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=merged,
            cwd=self.tmp,
        )

    def seed(self):
        result = self.run_tool([
            "desk", "--night", self.night,
            "--add", "exact-longer", "the long one",
            "--add", "exact", "the short one",
            "--add", "solo-window", "a solo job",
            "--add", "wind-one", "first wind",
            "--add", "wind-two", "second wind",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def reckoning_text(self):
        path = os.path.join(self.nights, self.night, "reckoning.csv")
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_exact_id_beats_a_longer_prefix(self):
        """source: an exact id is chosen even when it is also the start of another id."""
        self.seed()
        result = self.run_tool(["reckon", "exact", "keep", "it is this one"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reckoning: exact keep", result.stdout)
        text = self.reckoning_text()
        self.assertIn("exact,keep,it is this one,", text)
        self.assertNotIn("exact-longer,keep", text)

    def test_prefix_of_four_characters(self):
        """source: a token of the minimum prefix length names the one window it starts."""
        self.seed()
        result = self.run_tool(["reckon", "solo", "close", "finished"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("solo-window,close,finished,", self.reckoning_text())

    def test_prefix_shorter_than_the_minimum_is_refused(self):
        """source: a token shorter than the minimum prefix length matches no window."""
        self.seed()
        before = self.reckoning_text()
        result = self.run_tool(["reckon", "sol", "close", "finished"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.strip(), "no window matches sol")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.reckoning_text(), before)

    def test_prefix_that_fits_two_windows_is_refused(self):
        """source: a prefix that fits more than one window is refused and names each full id."""
        self.seed()
        before = self.reckoning_text()
        result = self.run_tool(["reckon", "wind", "close", "finished"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.strip(), "wind matches 2 windows: wind-one, wind-two")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.reckoning_text(), before)

    def test_empty_reason_is_refused(self):
        """source: a verdict with an empty reason is refused and the file is left unchanged."""
        self.seed()
        before = self.reckoning_text()
        result = self.run_tool(["reckon", "exact", "close", "  \n\t "])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.strip(), "a verdict needs a written reason")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.reckoning_text(), before)

    def test_verdict_word_outside_the_allowed_set_is_refused(self):
        """source: a verdict word outside the allowed set is refused and not stored."""
        self.seed()
        before = self.reckoning_text()
        result = self.run_tool(["reckon", "exact", "drop", "because"])
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("usage:"), result.stderr)
        self.assertIn("verdict", result.stderr)
        self.assertNotIn("drop", self.reckoning_text())
        self.assertEqual(self.reckoning_text(), before)

    def test_newest_verdict_wins_and_the_older_line_stays(self):
        """source: the newest verdict is the one that counts and the older line stays in the file."""
        self.seed()
        first = self.run_tool(["reckon", "exact", "handover", "first pass"])
        self.assertEqual(first.returncode, 0, first.stderr)
        saved = self.reckoning_text()
        second = self.run_tool(["reckon", "exact", "keep", "changed my mind"])
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("reckoning: exact keep", second.stdout)
        text = self.reckoning_text()
        self.assertTrue(text.startswith(saved))
        self.assertIn("exact,handover,first pass,", text)
        self.assertIn("exact,keep,changed my mind,", text)
        refused = self.run_tool([
            "handover", "exact", "--goal", "g", "--done", "d", "--next", "n", "--evidence", "e",
        ])
        self.assertEqual(refused.returncode, 2)
        self.assertIn("marked keep", refused.stderr)

    def test_listing_missing_verdicts_uses_both_exit_codes(self):
        """source: listing verdicts names only the windows still missing, and the exit follows whether any are missing."""
        self.seed()
        self.run_tool(["reckon", "exact", "keep", "stays"])
        self.run_tool(["reckon", "wind-one", "close", "done"])
        missing = self.run_tool(["reckon"])
        self.assertEqual(missing.returncode, 1)
        self.assertIn("  exact-lo  -  the long one", missing.stdout)
        self.assertIn("  solo-win  -  a solo job", missing.stdout)
        self.assertIn("  wind-two  -  second wind", missing.stdout)
        self.assertNotIn("the short one", missing.stdout)
        self.assertNotIn("first wind", missing.stdout)
        self.assertTrue(missing.stdout.strip().endswith("verdicts: 2 of 5"))
        self.run_tool(["reckon", "exact-longer", "close", "done"])
        self.run_tool(["reckon", "solo-window", "close", "done"])
        self.run_tool(["reckon", "wind-two", "keep", "stays"])
        done = self.run_tool(["reckon"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "verdicts: 5 of 5")

    def test_shared_short_prefix_is_printed_whole(self):
        """source: ids that share a short prefix are printed whole."""
        result = self.run_tool([
            "desk", "--night", self.night,
            "--add", "abcdefgh-one", "first job",
            "--add", "abcdefgh-two", "second job",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("  abcdefgh-one  -  first job", result.stdout)
        self.assertIn("  abcdefgh-two  -  second job", result.stdout)
        reckoned = self.run_tool(["reckon", "abcdefgh-one", "close", "done", "--night", self.night])
        self.assertEqual(reckoned.returncode, 0, reckoned.stderr)
        self.assertIn("reckoning: abcdefgh-one close", reckoned.stdout)

    def test_bad_night_form_is_a_wrong_use(self):
        """source: a night value that is not a date form is a wrong use."""
        self.seed()
        result = self.run_tool(["reckon", "--night", "yesterday"])
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("usage:"), result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
