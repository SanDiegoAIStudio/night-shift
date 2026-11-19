"""Desk command tests. Each docstring names the rule it guards."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "night_shift.py")
PY = sys.executable


def sid(tag):
    return "%s-1111-4111-8111-111111111111" % tag


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="night-shift-")
        self.projects = os.path.join(self.tmp, "projects")
        self.nights = os.path.join(self.tmp, "nights")
        os.makedirs(self.projects)
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
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

    def run_tool(self, args, env=None, projects=True, nights=True):
        cmd = [PY, TOOL] + list(args)
        if projects:
            cmd.extend(["--projects", self.projects])
        if nights:
            cmd.extend(["--nights", self.nights])
        merged = os.environ.copy()
        merged["PYTHONDONTWRITEBYTECODE"] = "1"
        merged.pop("NIGHT_SHIFT_DIR", None)
        merged.pop("CLAUDE_CONFIG_DIR", None)
        if env:
            merged.update(env)
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=merged,
            cwd=self.tmp,
        )

    def write_session(self, folder, ident, records, mtime=None):
        folder_path = os.path.join(self.projects, folder)
        os.makedirs(folder_path, exist_ok=True)
        path = os.path.join(folder_path, ident + ".jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def user(self, text, cwd=None, **extra):
        record = {"type": "user", "message": {"content": text}}
        if cwd is not None:
            record["cwd"] = cwd
        record.update(extra)
        return record

    def test_hours_window(self):
        """source: a session changed within the window is listed and an older one is not."""
        recent = sid("aaaaaaaa")
        older = sid("bbbbbbbb")
        now = time.time()
        self.write_session("shop-api", recent, [self.user("fix the export", "/work/shop-api")], now - 2 * 3600)
        self.write_session("old-jobs", older, [self.user("old work", "/work/old-jobs")], now - 30 * 3600)
        result = self.run_tool(["desk", "--night", "2020-01-15", "--hours", "24"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("aaaaaaaa", result.stdout)
        self.assertNotIn("bbbbbbbb", result.stdout)

    def test_not_sessions(self):
        """source: a nested file, a badly named file, and a folder named like a session are not sessions."""
        good = sid("aaaaaaaa")
        deep = sid("cccccccc")
        self.write_session("shop-api", good, [self.user("real work", "/work/shop-api")])
        nested = os.path.join(self.projects, "shop-api", "nested")
        os.makedirs(nested)
        with open(os.path.join(nested, deep + ".jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.user("too deep")) + "\n")
        with open(os.path.join(self.projects, "shop-api", "notes.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.user("not a session")) + "\n")
        os.makedirs(os.path.join(self.projects, "shop-api", sid("dddddddd") + ".jsonl"))
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("aaaaaaaa", result.stdout)
        self.assertNotIn("cccccccc", result.stdout)
        self.assertNotIn("notes", result.stdout)
        self.assertNotIn("dddddddd", result.stdout)
        self.assertIn("desk: 1 windows, 1 new", result.stdout)

    def test_first_typed_request_skips(self):
        """source: the first typed request skips a bracket note, a meta record, a sidechain record, and a tool result."""
        ident = sid("aaaaaaaa")
        records = [
            self.user("<local-note>", "/work/shop-api"),
            self.user("meta text", isMeta=True),
            self.user("side text", isSidechain=True),
            {"type": "user", "message": {"content": [{"type": "tool_result", "text": "tool only", "content": "tool only"}]}},
            self.user("fix the invoice export", "/work/shop-api"),
        ]
        self.write_session("shop-api", ident, records)
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fix the invoice export", result.stdout)
        self.assertNotIn("meta text", result.stdout)
        self.assertNotIn("side text", result.stdout)
        self.assertNotIn("tool only", result.stdout)
        self.assertNotIn("<local-note>", result.stdout)

    def test_string_and_list_forms(self):
        """source: a typed request is read from a string and from the first text part of a list."""
        self.write_session("shop-api", sid("aaaaaaaa"), [self.user("string form", "/work/shop-api")])
        listed = {"type": "user", "message": {"content": [{"type": "text", "text": "list form"}]}, "cwd": "/work/data-jobs"}
        self.write_session("data-jobs", sid("bbbbbbbb"), [listed])
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertIn("string form", result.stdout)
        self.assertIn("list form", result.stdout)
        self.assertIn("shop-api", result.stdout)
        self.assertIn("data-jobs", result.stdout)

    def test_cut_at_limit(self):
        """source: a request longer than the stored limit is cut, and one at the limit is kept whole."""
        self.write_session("shop-api", sid("aaaaaaaa"), [self.user("x" * 101, "/work/shop-api")])
        self.write_session("data-jobs", sid("bbbbbbbb"), [self.user("y" * 100, "/work/data-jobs")])
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertIn(("x" * 97) + "...", result.stdout)
        self.assertNotIn("x" * 101, result.stdout)
        self.assertIn("y" * 100, result.stdout)
        self.assertNotIn(("y" * 97) + "...", result.stdout)

    def test_no_typed_request(self):
        """source: a session with no typed request is still listed with a stand-in."""
        self.write_session("shop-api", sid("aaaaaaaa"), [
            {"type": "user", "message": {"content": [{"type": "tool_result", "text": "only a tool", "content": "only a tool"}]}},
        ])
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertIn("(no typed request found)", result.stdout)
        self.assertNotIn("only a tool", result.stdout)

    def test_could_not_be_read(self):
        """source: a session file that cannot be read is still listed."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("the tests run as root, so a mode of 0 does not block the read")
        path = self.write_session("shop-api", sid("aaaaaaaa"), [self.user("secret work", "/work/shop-api")])
        os.chmod(path, 0)
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("(could not be read)", result.stdout)
        self.assertNotIn("secret work", result.stdout)

    def test_control_code_is_cleaned(self):
        """source: a control code in a request never reaches the output or the file."""
        self.write_session("shop-api", sid("aaaaaaaa"), [self.user("fix\u0001the\ninvoice", "/work/shop-api")])
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertNotIn("\u0001", result.stdout)
        self.assertNotIn("\nfix", result.stdout)
        self.assertIn("fix the invoice", result.stdout)
        raw = open(os.path.join(self.nights, "2020-01-15", "desk.csv"), "rb").read()
        self.assertNotIn(b"\x01", raw)
        self.assertIn(b"fix the invoice", raw)

    def test_folder_from_cwd(self):
        """source: the folder is taken from the first record that names a working directory."""
        records = [
            {"type": "system", "cwd": "/work/shop-api"},
            self.user("do the thing"),
        ]
        self.write_session("anywhere", sid("aaaaaaaa"), records)
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertIn("  aaaaaaaa  shop-api  do the thing", result.stdout)

    def test_desk_only_grows(self):
        """source: a second desk keeps the old rows byte for byte and adds only the new window."""
        self.write_session("shop-api", sid("aaaaaaaa"), [self.user("first request", "/work/shop-api")], time.time() - 50)
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertEqual(result.returncode, 0, result.stderr)
        desk = os.path.join(self.nights, "2020-01-15", "desk.csv")
        before = open(desk, "rb").read()
        reckoning = open(os.path.join(self.nights, "2020-01-15", "reckoning.csv"), "rb").read()
        self.write_session("shop-api", sid("aaaaaaaa"), [self.user("changed request", "/work/shop-api")])
        self.write_session("data-jobs", sid("bbbbbbbb"), [self.user("brand new", "/work/data-jobs")])
        again = self.run_tool(["desk", "--night", "2020-01-15"])
        after = open(desk, "rb").read()
        self.assertTrue(after.startswith(before))
        self.assertIn("brand new", again.stdout)
        self.assertIn("first request", again.stdout)
        self.assertNotIn("changed request", again.stdout)
        self.assertEqual(open(os.path.join(self.nights, "2020-01-15", "reckoning.csv"), "rb").read(), reckoning)
        lines = [line for line in again.stdout.splitlines() if line.startswith("  ")]
        self.assertTrue(lines[0].startswith("  aaaaaaaa"))
        self.assertTrue(lines[1].startswith("  bbbbbbbb"))

    def test_add_wrong_uses(self):
        """source: a bad added name, a repeated name, and an empty request are refused and nothing is written."""
        self.write_session("shop-api", sid("aaaaaaaa"), [self.user("keep me", "/work/shop-api")])
        first = self.run_tool(["desk", "--night", "2020-01-15", "--add", "billing", "draft the reply"])
        self.assertEqual(first.returncode, 0, first.stderr)
        desk = os.path.join(self.nights, "2020-01-15", "desk.csv")
        snap = open(desk, "rb").read()
        bad_name = self.run_tool(["desk", "--night", "2020-01-15", "--add", "bad name", "hello"])
        self.assertEqual(bad_name.returncode, 2)
        self.assertTrue(bad_name.stderr.startswith("usage:"))
        self.assertIn("bad name", bad_name.stderr)
        self.assertEqual(open(desk, "rb").read(), snap)
        empty = self.run_tool(["desk", "--night", "2020-01-15", "--add", "vendor", "  \n\t "])
        self.assertEqual(empty.returncode, 2)
        self.assertIn("empty", empty.stderr)
        self.assertEqual(open(desk, "rb").read(), snap)
        duplicate = self.run_tool(["desk", "--night", "2020-01-15", "--add", "billing", "again"])
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("already", duplicate.stderr)
        self.assertEqual(open(desk, "rb").read(), snap)
        fresh = os.path.join(self.nights, "2020-08-08")
        refused = self.run_tool(["desk", "--night", "2020-08-08", "--add", "-nope", "hello"])
        self.assertEqual(refused.returncode, 2)
        self.assertFalse(os.path.isdir(fresh))

    def test_add_name_of_a_session_is_refused(self):
        """source: an added name that belongs to a session being listed is refused and nothing is written."""
        ident = sid("aaaaaaaa")
        self.write_session("shop-api", ident, [self.user("keep me", "/work/shop-api")])
        result = self.run_tool(["desk", "--night", "2020-07-07", "--add", ident, "by hand"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("already", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(os.path.isdir(os.path.join(self.nights, "2020-07-07")))

    def test_default_projects_home(self):
        """source: the default session folder follows the home directory when no config dir is set."""
        home = os.path.join(self.tmp, "home")
        ident = sid("aaaaaaaa")
        folder = os.path.join(home, ".claude", "projects", "shop-api")
        os.makedirs(folder)
        with open(os.path.join(folder, ident + ".jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.user("from home", "/work/shop-api")) + "\n")
        result = self.run_tool(["desk", "--night", "2020-01-15"], env={"HOME": home}, projects=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("from home", result.stdout)

    def test_default_projects_config_dir(self):
        """source: the default session folder follows the config dir when that variable is set."""
        home = os.path.join(self.tmp, "empty-home")
        config = os.path.join(self.tmp, "config")
        os.makedirs(home)
        ident = sid("bbbbbbbb")
        folder = os.path.join(config, "projects", "data-jobs")
        os.makedirs(folder)
        with open(os.path.join(folder, ident + ".jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.user("from config", "/work/data-jobs")) + "\n")
        result = self.run_tool(
            ["desk", "--night", "2020-01-15"],
            env={"HOME": home, "CLAUDE_CONFIG_DIR": config},
            projects=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("from config", result.stdout)
        self.assertNotIn("from home", result.stdout)

    def test_missing_default_folder_note(self):
        """source: a missing default session folder is noted and windows added by hand are still listed."""
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        result = self.run_tool(
            ["desk", "--night", "2020-01-15", "--add", "billing", "draft the reply"],
            env={"HOME": home},
            projects=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = os.path.join(home, ".claude", "projects")
        self.assertIn("note: no session folder at %s; only windows added with --add are listed" % expected, result.stderr)
        self.assertIn("billing", result.stdout)

    def test_projects_not_a_folder(self):
        """source: a projects path that is not a folder is a wrong use and nothing is written."""
        missing = os.path.join(self.tmp, "missing-projects")
        result = self.run_tool(
            ["desk", "--night", "2020-02-02", "--projects", missing, "--add", "billing", "hello"],
            projects=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.strip(), "not a folder: %s" % missing)
        self.assertEqual(result.stdout, "")
        self.assertFalse(os.path.isdir(os.path.join(self.nights, "2020-02-02")))

    def test_projects_flag_must_be_a_folder(self):
        """source: a projects path given on the command line that is not a folder is a wrong use."""
        missing = os.path.join(self.tmp, "missing-projects")
        result = subprocess.run(
            [PY, TOOL, "desk", "--projects", missing, "--nights", self.nights, "--night", "2020-02-02"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, cwd=self.tmp,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.strip(), "not a folder: %s" % missing)
        self.assertFalse(os.path.isdir(os.path.join(self.nights, "2020-02-02")))
        self.assertEqual(result.stdout, "")

    def test_hours_refused(self):
        """source: a hours value that is not a positive finite number is refused."""
        cases = (
            ["--hours", "nan"],
            ["--hours", "inf"],
            ["--hours=-inf"],
            ["--hours", "0"],
            ["--hours", "-3"],
            ["--hours", "NaN"],
        )
        for extra in cases:
            result = self.run_tool(["desk", "--night", "2020-01-15"] + extra)
            self.assertEqual(result.returncode, 2, " ".join(extra))
            self.assertTrue(result.stderr.startswith("usage:"), result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertFalse(os.path.isdir(os.path.join(self.nights, "2020-01-15")))

    def test_unnamed_desk_matches_night_name(self):
        """source: a desk with no night named uses the night of the moment read just before or just after the run."""
        sys.dont_write_bytecode = True
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import night_shift
        before = night_shift.local_now()
        result = self.run_tool(["desk", "--add", "solo", "hello"])
        after = night_shift.local_now()
        self.assertEqual(result.returncode, 0, result.stderr)
        names = {night_shift.night_name(before), night_shift.night_name(after)}
        made = [name for name in os.listdir(self.nights) if os.path.isdir(os.path.join(self.nights, name))]
        self.assertTrue(set(made) & names, made)

    def test_newest_night_and_nights_dir(self):
        """source: other commands use the newest night, and the nights folder follows the flag or the environment."""
        self.run_tool(["desk", "--night", "2020-01-02", "--add", "early", "first"])
        self.run_tool(["desk", "--night", "2020-05-09", "--add", "later", "second"])
        os.makedirs(os.path.join(self.nights, "notes"))
        checked = self.run_tool(["check", "--json"])
        payload = json.loads(checked.stdout)
        self.assertEqual(payload["night"], "2020-05-09")
        other = os.path.join(self.tmp, "other-nights")
        via_env = self.run_tool(
            ["desk", "--night", "2020-03-03", "--add", "envwin", "hello"],
            env={"NIGHT_SHIFT_DIR": other},
            nights=False,
        )
        self.assertEqual(via_env.returncode, 0, via_env.stderr)
        self.assertTrue(os.path.isdir(os.path.join(other, "2020-03-03")))
        overridden = self.run_tool(
            ["desk", "--night", "2020-04-04", "--add", "flagged", "hello"],
            env={"NIGHT_SHIFT_DIR": other},
        )
        self.assertTrue(os.path.isdir(os.path.join(self.nights, "2020-04-04")))
        self.assertFalse(os.path.isdir(os.path.join(other, "2020-04-04")))

    def test_private_modes(self):
        """source: every folder and file the tool creates is owner-only under a wide umask."""
        old = os.umask(0)
        try:
            desk = self.run_tool(["desk", "--night", "2020-01-15", "--add", "solo", "hello there"])
            self.assertEqual(desk.returncode, 0, desk.stderr)
            reckon = self.run_tool(["reckon", "solo", "handover", "because", "--you", "sign it"])
            self.assertEqual(reckon.returncode, 0, reckon.stderr)
            handed = self.run_tool([
                "handover", "solo", "--goal", "ship it", "--done", "drafted",
                "--next", "review", "--evidence", "the notes",
            ])
            self.assertEqual(handed.returncode, 0, handed.stderr)
            brief = self.run_tool(["brief", "--night", "2020-01-15"])
            self.assertEqual(brief.returncode, 0, brief.stderr)
        finally:
            os.umask(old)
        for dirpath, dirnames, filenames in os.walk(self.nights):
            self.assertEqual(os.stat(dirpath).st_mode & 0o777, 0o700, dirpath)
            for name in filenames:
                path = os.path.join(dirpath, name)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, path)

    def test_equal_mtime_orders_by_id(self):
        """source: sessions changed at the same time are listed by id after newer ones."""
        stamp = time.time() - 10
        self.write_session("shop-api", sid("bbbbbbbb"), [self.user("second", "/work/shop-api")], stamp)
        self.write_session("data-jobs", sid("aaaaaaaa"), [self.user("first", "/work/data-jobs")], stamp)
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        lines = [line for line in result.stdout.splitlines() if line.startswith("  ")]
        self.assertTrue(lines[0].startswith("  aaaaaaaa"))
        self.assertTrue(lines[1].startswith("  bbbbbbbb"))

    def test_add_name_ending_with_a_newline_is_refused(self):
        """source: an added name that ends with a newline is refused and nothing is written."""
        first = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertEqual(first.returncode, 0, first.stderr)
        desk = os.path.join(self.nights, "2020-01-15", "desk.csv")
        with open(desk, "rb") as handle:
            before = handle.read()
        result = self.run_tool(["desk", "--night", "2020-01-15", "--add", "tool\n", "fix the export"])
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("usage: add name does not match the form: "))
        self.assertEqual(result.stdout, "")
        with open(desk, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_night_ending_with_a_newline_is_refused(self):
        """source: a night value that ends with a newline is refused and no folder is created."""
        self.assertFalse(os.path.exists(self.nights))
        result = self.run_tool(["desk", "--night", "2020-06-01\n"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "usage: night must be YYYY-MM-DD\n")
        self.assertEqual(result.stdout, "")
        if os.path.exists(self.nights):
            self.assertEqual(os.listdir(self.nights), [])

    def test_session_file_with_a_newline_before_the_suffix_is_not_listed(self):
        """source: a session file whose name carries a newline before the suffix is not put on the desk."""
        good = sid("aaaaaaaa")
        bad = sid("bbbbbbbb")
        self.write_session("shop-api", good, [self.user("fix the export", "/work/shop-api")])
        folder = os.path.join(self.projects, "shop-api")
        odd_name = bad + "\n.jsonl"
        odd = os.path.join(folder, odd_name)
        try:
            with open(odd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(self.user("leave this off the desk", "/work/shop-api")) + "\n")
        except OSError as exc:
            self.skipTest("a file whose name contains a newline cannot be made: %s" % exc)
        if odd_name not in os.listdir(folder):
            self.skipTest("a file whose name contains a newline cannot be made")
        result = self.run_tool(["desk", "--night", "2020-01-15"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("desk: 1 windows, 1 new", result.stdout)
        self.assertNotIn(bad, result.stdout)
        desk = os.path.join(self.nights, "2020-01-15", "desk.csv")
        with open(desk, "r", encoding="utf-8") as handle:
            text = handle.read()
        rows = [line for line in text.splitlines() if line and not line.startswith("id,")]
        self.assertEqual(len(rows), 1)
        self.assertIn(good, rows[0])
        self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
