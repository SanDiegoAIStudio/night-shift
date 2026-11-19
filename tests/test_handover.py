"""Handover command tests. Each docstring names the rule it guards."""
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


class HandoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="night-shift-")
        self.projects = os.path.join(self.tmp, "projects")
        self.nights = os.path.join(self.tmp, "nights")
        os.makedirs(self.projects)
        self.night = "2020-01-15"
        self.parts = ["--goal", "ship it", "--done", "drafted", "--next", "review", "--evidence", "the notes"]

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

    def seed(self, verdict=None):
        desk = self.run_tool([
            "desk", "--night", self.night,
            "--add", "shop", "rebuild the checkout page",
        ])
        self.assertEqual(desk.returncode, 0, desk.stderr)
        if verdict:
            marked = self.run_tool(["reckon", "shop", verdict, "because"])
            self.assertEqual(marked.returncode, 0, marked.stderr)

    def snapshot(self):
        found = []
        for dirpath, _dirnames, filenames in os.walk(self.tmp):
            for name in filenames:
                found.append(os.path.join(dirpath, name))
        return sorted(found)

    def test_refused_with_no_verdict(self):
        """source: a packet is refused when the window has no verdict yet."""
        self.seed()
        before = self.snapshot()
        result = self.run_tool(["handover", "shop"] + self.parts)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.strip(), "shop has no verdict yet")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.snapshot(), before)

    def test_refused_when_the_verdict_is_not_handover(self):
        """source: a packet is refused when the window is marked with another verdict."""
        for verdict in ("keep", "close"):
            self.seed(verdict)
            before = self.snapshot()
            result = self.run_tool(["handover", "shop"] + self.parts)
            self.assertEqual(result.returncode, 2, verdict)
            self.assertEqual(
                result.stderr.strip(),
                "shop is marked %s; give it the handover verdict first" % verdict,
            )
            self.assertEqual(result.stdout, "")
            self.assertEqual(self.snapshot(), before)
            shutil.rmtree(self.nights)

    def test_each_part_missing_or_empty_is_refused(self):
        """source: each packet part that is missing or empty is refused by name."""
        keys = ("goal", "done", "next", "evidence")
        filled = {"goal": "ship it", "done": "drafted", "next": "review", "evidence": "the notes"}
        self.seed("handover")
        for key in keys:
            args = ["handover", "shop"]
            for other, value in filled.items():
                if other != key:
                    args.extend(["--" + other, value])
            missing = self.run_tool(args)
            self.assertEqual(missing.returncode, 2, key)
            self.assertEqual(missing.stderr.strip(), "missing %s" % key)
            self.assertEqual(missing.stdout, "")
            blank = ["handover", "shop"]
            for other, value in filled.items():
                blank.extend(["--" + other, " \n\t " if other == key else value])
            empty = self.run_tool(blank)
            self.assertEqual(empty.returncode, 2, key)
            self.assertEqual(empty.stderr.strip(), "empty %s" % key)
            self.assertEqual(empty.stdout, "")
        packet = os.path.join(self.nights, self.night, "handover")
        self.assertFalse(os.path.isdir(packet))

    def test_five_lines_on_stdout_and_in_the_file(self):
        """source: a packet's printed lines are the lines stored in the file."""
        self.seed("handover")
        result = self.run_tool(["handover", "shop"] + self.parts)
        expected = "\n".join([
            "Taking over from shop, which was asked: rebuild the checkout page",
            "Goal: ship it",
            "Done: drafted",
            "Next: review",
            "Evidence: the notes",
            "",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(result.stderr.strip(), "handover: wrote handover/shop.md")
        path = os.path.join(self.nights, self.night, "handover", "shop.md")
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), expected)
        again = self.run_tool([
            "handover", "shop",
            "--goal", "ship the rewrite", "--done", "drafted", "--next", "review", "--evidence", "the notes",
        ])
        self.assertEqual(again.returncode, 0, again.stderr)
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), again.stdout)
        self.assertIn("ship the rewrite", again.stdout)
        self.assertNotIn("Goal: ship it\n", again.stdout)

    def test_slash_and_two_dots_in_an_id_are_damage(self):
        """source: an id that would escape a folder is damage and no file is written anywhere."""
        for ident in ("bad/id", "ab..cd"):
            night = os.path.join(self.nights, self.night)
            os.makedirs(night, exist_ok=True)
            desk = os.path.join(night, "desk.csv")
            with open(desk, "w", encoding="utf-8") as handle:
                handle.write("id,asked,folder,source\n%s,hello,,\n" % ident)
            before = self.snapshot()
            result = self.run_tool(["handover", "hello"] + self.parts + ["--night", self.night])
            self.assertEqual(result.returncode, 2, ident)
            self.assertIn("could not:", result.stderr)
            self.assertIn("line 2", result.stderr)
            self.assertIn("id does not match the form", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(len(result.stderr.strip().splitlines()), 1)
            self.assertEqual(self.snapshot(), before)
            shutil.rmtree(night)


if __name__ == "__main__":
    unittest.main()
