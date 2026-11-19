"""Tests that call the tool in process. Each docstring names the rule it guards."""
import sys

sys.dont_write_bytecode = True

import csv
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import night_shift

TOOL = os.path.join(ROOT, "night_shift.py")
PY = sys.executable
NIGHT = "2020-01-15"


def sid(tag):
    return "%s-1111-4111-8111-111111111111" % tag


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="night-shift-")
        self.projects = os.path.join(self.tmp, "projects")
        self.nights = os.path.join(self.tmp, "nights")
        os.makedirs(self.projects)
        self._cwd = os.getcwd()
        os.chdir(self.tmp)

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

    def env(self):
        merged = os.environ.copy()
        merged["PYTHONDONTWRITEBYTECODE"] = "1"
        merged.pop("NIGHT_SHIFT_DIR", None)
        merged.pop("CLAUDE_CONFIG_DIR", None)
        return merged

    def run_tool(self, args, nights=None):
        cmd = [PY, TOOL] + list(args) + ["--projects", self.projects, "--nights", nights or self.nights]
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=self.env(),
            cwd=self.tmp,
        )

    def run_in(self, argv, nights=None):
        out = io.StringIO()
        err = io.StringIO()
        full = list(argv) + ["--projects", self.projects, "--nights", nights or self.nights]
        with patch.object(night_shift.sys, "stdout", out), patch.object(night_shift.sys, "stderr", err):
            code = night_shift.run(full)
        return code, out.getvalue(), err.getvalue()

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

    def user(self, text, stamp, cwd="/work/shop"):
        return {"type": "user", "message": {"content": text}, "cwd": cwd, "timestamp": stamp}

    def snapshot(self):
        found = []
        for dirpath, _dirnames, filenames in os.walk(self.tmp):
            for name in filenames:
                found.append(os.path.join(dirpath, name))
        return sorted(found)

    def modes(self, path):
        return os.stat(path).st_mode & 0o777

    def test_shared_prefix_packet_still_counts(self):
        """source: a packet still counts after a later window makes the short id print whole."""
        first = "abcdefgh-one"
        second = "abcdefgh-two"
        made = self.run_tool(["desk", "--night", NIGHT, "--add", first, "first job"])
        self.assertEqual(made.returncode, 0, made.stderr)
        marked = self.run_tool(["reckon", first, "handover", "context is full", "--night", NIGHT])
        self.assertEqual(marked.returncode, 0, marked.stderr)
        packet = self.run_tool([
            "handover", first, "--goal", "ship it", "--done", "drafted",
            "--next", "review", "--evidence", "the notes", "--night", NIGHT,
        ])
        self.assertEqual(packet.returncode, 0, packet.stderr)
        again = self.run_tool(["desk", "--night", NIGHT, "--add", second, "second job"])
        self.assertEqual(again.returncode, 0, again.stderr)
        checked = self.run_tool(["check", "--night", NIGHT])
        self.assertIn("packets: 1 of 1", checked.stdout)
        self.assertNotIn("no packet:", checked.stdout)
        self.assertNotIn(first, checked.stdout.split("no packet:")[-1] if "no packet:" in checked.stdout else "")

    def test_packet_for_another_window_does_not_count(self):
        """source: a packet whose first line names a different window does not count."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page", "--add", "other", "other job"])
        self.run_tool(["reckon", "shop", "handover", "context is full", "--night", NIGHT])
        path = os.path.join(self.nights, NIGHT, "handover", "shop.md")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("Taking over from other, which was asked: rebuild the page\n")
            handle.write("Goal: ship it\nDone: drafted\nNext: review\nEvidence: the notes\n")
        checked = self.run_tool(["check", "--night", NIGHT])
        self.assertIn("no packet:", checked.stdout)
        self.assertIn("shop", checked.stdout)
        self.assertIn("packets: 0 of 1", checked.stdout)

    def test_packet_with_another_request_does_not_count(self):
        """source: a packet whose first line carries a different request does not count."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page"])
        self.run_tool(["reckon", "shop", "handover", "context is full", "--night", NIGHT])
        path = os.path.join(self.nights, NIGHT, "handover", "shop.md")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("Taking over from shop, which was asked: some other request\n")
            handle.write("Goal: ship it\nDone: drafted\nNext: review\nEvidence: the notes\n")
        checked = self.run_tool(["check", "--night", NIGHT])
        self.assertIn("no packet:", checked.stdout)
        self.assertIn("packets: 0 of 1", checked.stdout)

    def test_oversized_last_record_cannot_be_checked(self):
        """source: a session whose last record is larger than the tail the tool reads cannot be checked."""
        limit = night_shift.TAIL_BYTES
        ident = sid("aaaaaaaa")
        moment = datetime(2020, 6, 1, tzinfo=timezone.utc).timestamp()
        path = self.write_session("shop-api", ident, [self.user("placeholder", "2020-01-01T00:00:00Z")])
        self.run_tool(["desk", "--night", NIGHT])
        line = json.dumps(self.user("z" * (limit + 50), "2099-01-01T00:00:00Z")) + "\n"
        self.assertGreater(len(line.encode("utf-8")), limit)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(line)
        os.utime(path, (time.time(), time.time()))
        night = os.path.join(self.nights, NIGHT)
        rec = os.path.join(night, "reckoning.csv")
        with open(rec, "w", encoding="utf-8") as handle:
            handle.write("id,verdict,reason,you,at\n")
            handle.write("%s,close,merged,,%.6f\n" % (ident, moment))
        data = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(data["handover"]["cannot_check"], [ident])
        self.assertEqual(data["handover"]["used_after"], [])

    def test_large_file_with_only_earlier_records_is_quiet(self):
        """source: a long session whose tail holds only earlier records is quiet."""
        limit = night_shift.TAIL_BYTES
        ident = sid("bbbbbbbb")
        moment = datetime(2020, 6, 1, tzinfo=timezone.utc).timestamp()
        path = self.write_session("shop-api", ident, [self.user("placeholder", "2020-01-01T00:00:00Z")])
        self.run_tool(["desk", "--night", NIGHT])
        early = json.dumps(self.user("early", "2099-01-01T00:00:00Z")) + "\n"
        tail = json.dumps(self.user("tail", "2020-01-01T00:00:00Z")) + "\n"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(early)
            handle.write("x" * (limit + 100000))
            handle.write("\n")
            handle.write(tail)
        os.utime(path, (time.time(), time.time()))
        rec = os.path.join(self.nights, NIGHT, "reckoning.csv")
        with open(rec, "w", encoding="utf-8") as handle:
            handle.write("id,verdict,reason,you,at\n")
            handle.write("%s,close,merged,,%.6f\n" % (ident, moment))
        data = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(data["handover"]["used_after"], [])
        self.assertEqual(data["handover"]["cannot_check"], [])

    def test_fraction_of_a_second_counts(self):
        """source: a fraction of a second in a record time is compared with the verdict."""
        base = datetime(2020, 6, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        at = "%.6f" % (base + 0.25)
        self.assertTrue(at.endswith(".250000"))
        later = sid("cccccccc")
        earlier = sid("dddddddd")
        later_path = self.write_session("shop-api", later, [self.user("later", "2020-06-01T12:00:00.750Z")])
        earlier_path = self.write_session("data-jobs", earlier, [self.user("earlier", "2020-06-01T12:00:00.100Z")])
        self.run_tool(["desk", "--night", NIGHT])
        stamp = base + 0.25 + 100
        os.utime(later_path, (stamp, stamp))
        os.utime(earlier_path, (stamp, stamp))
        rec = os.path.join(self.nights, NIGHT, "reckoning.csv")
        with open(rec, "w", encoding="utf-8") as handle:
            handle.write("id,verdict,reason,you,at\n")
            handle.write("%s,close,merged,,%s\n" % (later, at))
            handle.write("%s,close,merged,,%s\n" % (earlier, at))
        data = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(data["handover"]["used_after"], [later])
        self.assertNotIn(earlier, data["handover"]["used_after"])
        self.assertNotIn(earlier, data["handover"]["cannot_check"])

    def test_symbolic_link_is_not_a_session(self):
        """source: a session file that is a symbolic link is skipped, and a linked source cannot be checked."""
        ident = sid("eeeeeeee")
        outside = os.path.join(self.tmp, "outside.jsonl")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.user("hidden", "2099-01-01T00:00:00Z")) + "\n")
        os.utime(outside, (time.time(), time.time()))
        folder = os.path.join(self.projects, "shop-api")
        os.makedirs(folder)
        link = os.path.join(folder, ident + ".jsonl")
        try:
            os.symlink(outside, link)
        except (OSError, AttributeError, NotImplementedError):
            self.skipTest("a symbolic link cannot be made")
        listed = self.run_tool(["desk", "--night", NIGHT, "--add", "billing", "draft the reply"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertNotIn(ident[:8], listed.stdout)
        self.assertNotIn(ident, listed.stdout)
        night = os.path.join(self.nights, NIGHT)
        desk = os.path.join(night, "desk.csv")
        with open(desk, "a", encoding="utf-8") as handle:
            handle.write("%s,hidden work,/work/shop,%s\n" % (ident, link))
        moment = datetime(2020, 6, 1, tzinfo=timezone.utc).timestamp()
        with open(os.path.join(night, "reckoning.csv"), "a", encoding="utf-8") as handle:
            handle.write("%s,close,merged,,%.6f\n" % (ident, moment))
        checked = self.run_tool(["check", "--json", "--night", NIGHT])
        self.assertTrue(checked.stdout.startswith("{"), checked.stderr)
        data = json.loads(checked.stdout)
        self.assertIn(ident, data["handover"]["cannot_check"])

    def test_created_folders_are_owner_only_under_a_wide_umask(self):
        """source: every folder created for a new nights path is owner-only even under a wide umask."""
        nights = os.path.join(self.tmp, "a", "b", "c")

        def open_umask():
            os.umask(0)

        result = subprocess.run(
            [PY, TOOL, "desk", "--night", NIGHT, "--add", "solo", "hello",
             "--projects", self.projects, "--nights", nights],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=self.env(),
            cwd=self.tmp,
            preexec_fn=open_umask,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (
            os.path.join(self.tmp, "a"),
            os.path.join(self.tmp, "a", "b"),
            nights,
            os.path.join(nights, NIGHT),
        ):
            self.assertEqual(self.modes(path), 0o700, path)

    def test_existing_folders_keep_their_mode(self):
        """source: a nights folder and a night folder that already existed keep their mode."""
        os.makedirs(self.nights)
        os.chmod(self.nights, 0o755)
        first = self.run_tool(["desk", "--night", NIGHT, "--add", "solo", "hello"])
        self.assertEqual(first.returncode, 0, first.stderr)
        night = os.path.join(self.nights, NIGHT)
        os.chmod(night, 0o750)
        second = self.run_tool(["desk", "--night", NIGHT, "--add", "other", "again"])
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.modes(self.nights), 0o755)
        self.assertEqual(self.modes(night), 0o750)

    def assert_could_not(self, result, names=None):
        self.assertEqual(result.returncode, 2)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertTrue(lines[0].startswith("could not: "), result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Traceback", result.stdout)
        self.assertEqual(result.stdout, "")
        if names:
            self.assertIn(names, lines[0])

    def test_unwritable_parent_is_reported(self):
        """source: a nights folder under a parent that cannot be written is reported without a traceback."""
        if os.geteuid() == 0:
            self.skipTest("the tests run as root, so a mode of 0555 does not block the write")
        parent = os.path.join(self.tmp, "locked")
        os.makedirs(parent)
        os.chmod(parent, 0o555)
        try:
            result = self.run_tool(
                ["desk", "--night", NIGHT, "--add", "solo", "hello"],
                nights=os.path.join(parent, "nights"),
            )
            self.assert_could_not(result)
        finally:
            os.chmod(parent, 0o700)

    def test_nights_path_that_is_a_file_is_reported(self):
        """source: a nights path that names a file is reported without a traceback."""
        path = os.path.join(self.tmp, "not-a-folder")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x")
        result = self.run_tool(["desk", "--night", NIGHT, "--add", "solo", "hello"], nights=path)
        self.assert_could_not(result)

    def test_handover_path_that_is_a_file_is_reported(self):
        """source: a handover path that is a file is reported without a traceback."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page"])
        self.run_tool(["reckon", "shop", "handover", "context is full", "--night", NIGHT])
        target = os.path.join(self.nights, NIGHT, "handover")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("x")
        result = self.run_tool([
            "handover", "shop", "--goal", "ship it", "--done", "drafted",
            "--next", "review", "--evidence", "the notes", "--night", NIGHT,
        ])
        self.assert_could_not(result)

    def test_brief_path_that_is_a_folder_is_reported(self):
        """source: a brief path that is a folder is reported without a traceback or a temp file."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page"])
        self.run_tool(["reckon", "shop", "close", "merged", "--night", NIGHT])
        night = os.path.join(self.nights, NIGHT)
        os.mkdir(os.path.join(night, "brief.md"))
        result = self.run_tool(["brief", "--night", NIGHT])
        self.assert_could_not(result, "brief.md")
        left = [name for name in os.listdir(night) if name.startswith(".ns-")]
        self.assertEqual(left, [])

    def test_oversized_cell_cannot_be_parsed(self):
        """source: a table cell the reader cannot parse is damage."""
        night = os.path.join(self.nights, NIGHT)
        os.makedirs(night)
        path = os.path.join(night, "desk.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("id,asked,folder,source\n")
            handle.write("shop,%s,,\n" % ("a" * 140000))
        result = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "could not: %s line 2: cannot be parsed\n" % path)
        self.assertEqual(result.stdout, "")

    def test_packet_that_is_not_utf8_is_no_packet(self):
        """source: a packet that is not text counts as no packet."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page"])
        self.run_tool(["reckon", "shop", "handover", "context is full", "--night", NIGHT])
        folder = os.path.join(self.nights, NIGHT, "handover")
        os.makedirs(folder)
        with open(os.path.join(folder, "shop.md"), "wb") as handle:
            handle.write(b"\xff\xfe not text\n")
        result = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 1)
        self.assertIn("no packet:", result.stdout)
        self.assertIn("shop", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Traceback", result.stdout)

    def test_reckoning_without_a_final_newline_can_grow(self):
        """source: a reckoning file with no final newline still accepts another verdict."""
        self.run_tool([
            "desk", "--night", NIGHT,
            "--add", "shop", "rebuild the page",
            "--add", "billing", "draft the reply",
        ])
        rec = os.path.join(self.nights, NIGHT, "reckoning.csv")
        with open(rec, "wb") as handle:
            handle.write(b"id,verdict,reason,you,at\n")
            handle.write(b"shop,close,merged,,1.000000")
        again = self.run_tool(["reckon", "billing", "close", "sent", "--night", NIGHT])
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("verdicts: 2 of 2", again.stdout)
        checked = self.run_tool(["check", "--night", NIGHT])
        self.assertNotEqual(checked.returncode, 2, checked.stderr)
        self.assertIn("verdicts: 2 of 2", checked.stdout)

    def _closed_pipe(self, args):
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        try:
            result = subprocess.run(
                [PY, TOOL] + args + ["--projects", self.projects, "--nights", self.nights],
                stdout=write_fd,
                stderr=subprocess.PIPE,
                env=self.env(),
                cwd=self.tmp,
            )
        finally:
            os.close(write_fd)
        return result

    def test_closed_pipe_on_a_broken_night_is_silent(self):
        """source: a closed output pipe does not print a broken pipe error."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page"])
        result = self._closed_pipe(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"")

    def test_closed_pipe_when_all_four_stand_is_silent(self):
        """source: a closed output pipe keeps the exit code of a night whose stages stand."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page"])
        self.run_tool(["reckon", "shop", "close", "merged", "--night", NIGHT])
        self.run_tool(["brief", "--night", NIGHT])
        result = self._closed_pipe(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")

    def test_interrupt_stops_without_a_temp_file(self):
        """source: an interrupt stops the run and leaves no temporary file."""
        def boom(*_args, **_kwargs):
            raise KeyboardInterrupt()

        err = io.StringIO()
        try:
            with patch.object(night_shift.os, "replace", boom), patch.object(night_shift.sys, "stderr", err):
                try:
                    code = night_shift.run([
                        "desk", "--night", NIGHT, "--add", "solo", "hello",
                        "--projects", self.projects, "--nights", self.nights,
                    ])
                except KeyboardInterrupt:
                    self.fail("interrupt was not handled")
        finally:
            pass
        self.assertEqual(code, 130)
        self.assertEqual(err.getvalue(), "stopped\n")
        night = os.path.join(self.nights, NIGHT)
        if os.path.isdir(night):
            left = [name for name in os.listdir(night) if name.startswith(".ns-")]
            self.assertEqual(left, [])

    def test_unexpected_error_is_one_line(self):
        """source: an unexpected error is one line on the error stream."""
        def boom(_args):
            raise RuntimeError("boom")

        err = io.StringIO()
        out = io.StringIO()
        with patch.object(night_shift, "cmd_check", boom), patch.object(night_shift.sys, "stderr", err), patch.object(night_shift.sys, "stdout", out):
            code = night_shift.run([
                "check", "--night", NIGHT, "--projects", self.projects, "--nights", self.nights,
            ])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "could not: unexpected error: RuntimeError: boom\n")
        self.assertEqual(out.getvalue(), "")

    def test_night_runs_from_noon_to_noon(self):
        """source: a night runs from noon to the next noon."""
        zone = timezone(timedelta(hours=-8))
        day = datetime(2020, 6, 2, tzinfo=zone)
        self.assertEqual(night_shift.night_name(day.replace(hour=23, minute=40)), "2020-06-02")
        self.assertEqual(night_shift.night_name(day.replace(hour=0, minute=10)), "2020-06-01")
        self.assertEqual(night_shift.night_name(day.replace(hour=11, minute=59)), "2020-06-01")
        self.assertEqual(night_shift.night_name(day.replace(hour=12, minute=0)), "2020-06-02")
        early = os.path.join(self.tmp, "early-nights")
        late = os.path.join(self.tmp, "late-nights")

        def at_0010():
            return day.replace(hour=0, minute=10)

        def at_1300():
            return day.replace(hour=13, minute=0)

        with patch.object(night_shift, "local_now", at_0010):
            code, _out, err = self.run_in(
                ["desk", "--add", "solo", "hello"], nights=early
            )
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(os.path.join(early, "2020-06-01")))
        self.assertFalse(os.path.isdir(os.path.join(early, "2020-06-02")))
        self.assertEqual(err, "note: before noon, so this desk belongs to the night of 2020-06-01\n")
        with patch.object(night_shift, "local_now", at_1300):
            code, _out, err = self.run_in(
                ["desk", "--add", "solo", "hello"], nights=late
            )
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(os.path.join(late, "2020-06-02")))
        self.assertNotIn("before noon", err)

    def test_long_text_is_refused_before_a_write(self):
        """source: a text past the length limit is refused before anything is written."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page"])
        long = "a" * 2001
        short = "b" * 2000
        before = self.snapshot()
        refused = [
            (["reckon", "shop", "close", long, "--night", NIGHT], "reason"),
            (["reckon", "shop", "close", "merged", "--you", long, "--night", NIGHT], "you"),
        ]
        for args, label in refused:
            result = self.run_tool(args)
            self.assertEqual(result.returncode, 2, label)
            self.assertEqual(result.stderr, "usage: %s is longer than 2000 characters\n" % label)
            self.assertEqual(result.stdout, "")
            self.assertEqual(self.snapshot(), before)
        accepted = self.run_tool(["reckon", "shop", "handover", short, "--night", NIGHT])
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        marked = self.snapshot()
        for label in ("goal", "done", "next", "evidence"):
            args = [
                "handover", "shop",
                "--goal", "ship it", "--done", "drafted", "--next", "review", "--evidence", "the notes",
                "--" + label, long, "--night", NIGHT,
            ]
            result = self.run_tool(args)
            self.assertEqual(result.returncode, 2, label)
            self.assertEqual(result.stderr, "usage: %s is longer than 2000 characters\n" % label)
            self.assertEqual(result.stdout, "")
            self.assertEqual(self.snapshot(), marked)

    def test_added_request_is_cut(self):
        """source: a request added by hand is cut the same way as a session request."""
        asked = "c" * 150
        result = self.run_tool(["desk", "--night", NIGHT, "--add", "solo", asked])
        self.assertEqual(result.returncode, 0, result.stderr)
        path = os.path.join(self.nights, NIGHT, "desk.csv")
        with open(path, "r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        stored = rows[1][1]
        self.assertEqual(len(stored), 100)
        self.assertTrue(stored.endswith("..."))

    def test_a_later_verdict_drops_the_you_text(self):
        """source: reckoning a window again without a you text drops the earlier you text."""
        self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "rebuild the page"])
        first = self.run_tool(["reckon", "shop", "close", "merged", "--you", "sign the form", "--night", NIGHT])
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_tool(["reckon", "shop", "close", "merged", "--night", NIGHT])
        self.assertEqual(second.returncode, 0, second.stderr)
        page = self.run_tool(["brief", "--night", NIGHT])
        self.assertIn("## Only you (0)", page.stdout)
        self.assertNotIn("sign the form", page.stdout)


if __name__ == "__main__":
    unittest.main()
