"""Check and brief tests. Each docstring names the rule it guards."""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "night_shift.py")
PY = sys.executable
NIGHT = "2020-01-15"


def sid(tag):
    return "%s-1111-4111-8111-111111111111" % tag


def epoch(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="night-shift-")
        self.projects = os.path.join(self.tmp, "projects")
        self.nights = os.path.join(self.tmp, "nights")
        os.makedirs(self.projects)
        os.makedirs(self.nights)

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

    def user(self, text, cwd=None, stamp=None, kind="user"):
        record = {"type": kind, "message": {"content": text}}
        if cwd is not None:
            record["cwd"] = cwd
        if stamp is not None:
            record["timestamp"] = stamp
        return record

    def plant_desk(self, rows):
        night = os.path.join(self.nights, NIGHT)
        os.makedirs(night, exist_ok=True)
        path = os.path.join(night, "desk.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("id,asked,folder,source\n")
            for row in rows:
                handle.write(",".join(row) + "\n")
        os.chmod(path, 0o600)
        return path

    def plant_reckoning(self, rows):
        path = os.path.join(self.nights, NIGHT, "reckoning.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["id", "verdict", "reason", "you", "at"])
            for ident, verdict, reason, you, at in rows:
                writer.writerow([ident, verdict, reason, you, "%.6f" % float(at)])
        os.chmod(path, 0o600)
        return path

    def add_window(self, name, asked):
        result = self.run_tool(["desk", "--night", NIGHT, "--add", name, asked])
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_missing_desk_stops_before_the_other_stages(self):
        """source: a missing desk is broken and the other stages are not checked."""
        result = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "desk: BROKE, missing\nreckoning: not checked\nhandover: not checked\nbrief: not checked\nBROKE AT: desk\n",
        )
        self.assertFalse(os.path.isfile(os.path.join(self.nights, NIGHT, "brief.md")))

    def test_later_stage_is_judged_after_an_earlier_one_broke(self):
        """source: a later stage is still judged after an earlier one broke, and the last line names the first."""
        self.add_window("shop", "fix the export")
        result = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 1)
        self.assertIn("desk: ok, windows: 1\n", result.stdout)
        self.assertIn("reckoning: BROKE, verdicts: 0 of 1\n", result.stdout)
        self.assertIn("  no verdict: shop\n", result.stdout)
        self.assertIn("handover: ok, packets: 0 of 0, used after: 0, cannot check: 0\n", result.stdout)
        self.assertIn("brief: BROKE, missing\n", result.stdout)
        self.assertTrue(result.stdout.endswith("BROKE AT: reckoning\n"))

    def test_stage_lines_details_and_both_exit_codes(self):
        """source: check prints each stage line, the detail lines, the last line, and both exit codes."""
        ident = sid("aaaaaaaa")
        path = self.write_session("shop-api", ident, [
            self.user("fix the invoice export", "/work/shop-api", "2020-01-01T00:00:00Z"),
        ])
        hand = sid("bbbbbbbb")
        self.write_session("shop-web", hand, [
            self.user("rebuild the checkout page", "/work/shop-web", "2020-01-01T00:00:00Z"),
        ])
        listed = self.run_tool(["desk", "--night", NIGHT, "--add", "billing", "draft the reply"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        moment = epoch(2020, 6, 1)
        os.utime(path, (moment - 10, moment - 10))
        self.plant_reckoning([
            (ident, "close", "merged", "", moment),
            (hand, "handover", "context is full", "", moment),
            ("billing", "close", "sent", "", moment),
        ])
        broken = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(broken.returncode, 1)
        self.assertIn("handover: BROKE, packets: 0 of 1, used after: 0, cannot check: 1\n", broken.stdout)
        self.assertIn("  no packet: bbbbbbbb\n", broken.stdout)
        self.assertNotIn("\n  used after:", broken.stdout)
        self.assertTrue(broken.stdout.endswith("BROKE AT: handover\n"))
        packet = self.run_tool([
            "handover", hand, "--goal", "ship it", "--done", "drafted",
            "--next", "wire the form", "--evidence", "the notes", "--night", NIGHT,
        ])
        self.assertEqual(packet.returncode, 0, packet.stderr)
        os.utime(os.path.join(self.nights, NIGHT, "handover", hand + ".md"), (moment + 5, moment + 5))
        page = self.run_tool(["brief", "--night", NIGHT])
        self.assertEqual(page.returncode, 0, page.stderr)
        stood = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(stood.returncode, 0, stood.stderr)
        self.assertEqual(
            stood.stdout,
            "\n".join([
                "desk: ok, windows: 3",
                "reckoning: ok, verdicts: 3 of 3",
                "handover: ok, packets: 1 of 1, used after: 0, cannot check: 1",
                "brief: ok",
                "all four stages stand",
                "",
            ]),
        )

    def test_json_holds_full_ids_and_the_same_states(self):
        """source: the json report holds full ids and the same states as the text report."""
        ident = sid("aaaaaaaa")
        path = self.write_session("shop-api", ident, [
            self.user("fix the export", "/work/shop-api", "2099-01-01T00:00:00Z"),
        ])
        os.utime(path, (time.time(), time.time()))
        other = sid("bbbbbbbb")
        self.write_session("shop-web", other, [self.user("rebuild it", "/work/shop-web", "2020-01-01T00:00:00Z")])
        self.run_tool(["desk", "--night", NIGHT, "--add", "billing", "draft the reply", "--add", "keepme", "leave it"])
        moment = epoch(2020, 6, 1)
        self.plant_reckoning([
            (ident, "close", "merged", "", moment),
            (other, "handover", "full", "", moment),
            ("billing", "close", "sent", "sign the letter", moment),
            ("keepme", "keep", "mid run", "", moment),
        ])
        text = self.run_tool(["check", "--night", NIGHT])
        data = self.run_tool(["check", "--json", "--night", NIGHT])
        self.assertEqual(text.returncode, 1)
        self.assertEqual(data.returncode, 1)
        self.assertEqual(data.stderr, "")
        payload = json.loads(data.stdout)
        self.assertEqual(payload["night"], NIGHT)
        self.assertEqual(payload["broke_at"], "handover")
        self.assertEqual(payload["desk"]["state"], "ok")
        self.assertEqual(payload["desk"]["windows"], 4)
        self.assertEqual(payload["reckoning"]["state"], "ok")
        self.assertEqual(payload["reckoning"]["verdicts"], 4)
        self.assertEqual(payload["reckoning"]["no_verdict"], [])
        self.assertEqual(payload["handover"]["state"], "BROKE")
        self.assertEqual(payload["handover"]["packets"], 0)
        self.assertEqual(payload["handover"]["handovers"], 1)
        self.assertEqual(payload["handover"]["no_packet"], [other])
        self.assertEqual(payload["handover"]["used_after"], [ident])
        self.assertEqual(payload["handover"]["cannot_check"], ["billing"])
        self.assertEqual(payload["brief"]["state"], "BROKE")
        self.assertEqual(payload["brief"]["reason"], "missing")
        self.assertIn("  no packet: bbbbbbbb\n", text.stdout)
        self.assertIn("  used after: aaaaaaaa\n", text.stdout)

    def test_closed_window_states(self):
        """source: a finished window is quiet, used after, or cannot check, and a kept window is never judged."""
        moment = epoch(2020, 6, 1)
        quiet_id = sid("aaaaaaaa")
        used_id = sid("bbbbbbbb")
        blind_id = sid("cccccccc")
        later_id = sid("dddddddd")
        keep_id = sid("eeeeeeee")
        paths = {
            quiet_id: self.write_session("shop-api", quiet_id, [
                self.user("quiet work", "/work/shop-api", "2020-01-01T00:00:00Z"),
                self.user("program note", "/work/shop-api", "2099-01-01T00:00:00Z", kind="system"),
            ]),
            used_id: self.write_session("data-jobs", used_id, [
                self.user("old note", "/work/data-jobs", "2020-01-01T00:00:00Z"),
                self.user("still going", "/work/data-jobs", "2099-01-01T00:00:00Z", kind="assistant"),
            ]),
            blind_id: self.write_session("shop-web", blind_id, [
                self.user("no clock", "/work/shop-web"),
            ]),
            later_id: self.write_session("old-jobs", later_id, [
                self.user("hidden", "/work/old-jobs", "2099-01-01T00:00:00Z"),
            ]),
            keep_id: self.write_session("keep-jobs", keep_id, [
                self.user("leave this", "/work/keep-jobs", "2099-01-01T00:00:00Z"),
            ]),
        }
        listed = self.run_tool(["desk", "--night", NIGHT, "--add", "billing", "draft the reply"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        os.remove(paths[blind_id])
        os.utime(paths[quiet_id], (time.time(), time.time()))
        os.utime(paths[used_id], (time.time(), time.time()))
        os.utime(paths[keep_id], (time.time(), time.time()))
        self.plant_reckoning([
            (quiet_id, "close", "merged", "", moment),
            (used_id, "close", "merged", "", moment),
            (blind_id, "close", "gone", "", moment),
            (later_id, "close", "shortcut", "", moment),
            (keep_id, "keep", "mid run", "", moment),
            ("billing", "close", "sent", "", moment),
        ])
        # File changed at the verdict instant still holds a later record, so only the shortcut can call it quiet.
        os.utime(paths[later_id], (moment, moment))
        # blind_id was removed after the desk listing, so the row still names a file that is no longer regular and the window reads cannot check.
        data = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(data["handover"]["used_after"], [used_id])
        self.assertEqual(data["handover"]["cannot_check"], [blind_id, "billing"])
        self.assertNotIn(quiet_id, data["handover"]["used_after"])
        self.assertNotIn(quiet_id, data["handover"]["cannot_check"])
        self.assertNotIn(later_id, data["handover"]["used_after"])
        self.assertNotIn(keep_id, data["handover"]["used_after"])
        self.assertNotIn(keep_id, data["handover"]["cannot_check"])
        page = self.run_tool(["brief", "--night", NIGHT])
        self.assertIn("quiet work", page.stdout)
        self.assertIn("| quiet", page.stdout)
        self.assertIn("| used after", page.stdout)
        self.assertIn("| cannot check", page.stdout)
        self.assertNotIn("leave this | kept: mid run |", page.stdout)

    def test_user_record_with_no_time_cannot_be_checked(self):
        """source: a user record whose time cannot be read makes the window cannot check."""
        moment = epoch(2020, 6, 1)
        missing = sid("aaaaaaaa")
        zoneless = sid("bbbbbbbb")
        now = time.time()
        self.write_session("shop-api", missing, [self.user("no stamp", "/work/shop-api")], now)
        self.write_session("data-jobs", zoneless, [
            self.user("bare clock", "/work/data-jobs", "2020-01-01T00:00:00"),
        ], now - 30)
        listed = self.run_tool(["desk", "--night", NIGHT])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.plant_reckoning([
            (missing, "close", "merged", "", moment),
            (zoneless, "close", "merged", "", moment),
        ])
        data = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(data["handover"]["cannot_check"], [missing, zoneless])
        self.assertEqual(data["handover"]["used_after"], [])

    def test_record_before_the_tail_is_not_read(self):
        """source: a record that lies before the tail of a long session file is not read."""
        moment = epoch(2020, 6, 1)
        ident = sid("aaaaaaaa")
        path = self.write_session("shop-api", ident, [self.user("placeholder", "/work/shop-api", "2020-01-01T00:00:00Z")])
        self.run_tool(["desk", "--night", NIGHT])
        sys.dont_write_bytecode = True
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import night_shift
        early = '{"type": "user", "message": {"content": "early"}, "timestamp": "2099-01-01T00:00:00Z"}\n'
        tail = '{"type": "user", "message": {"content": "tail"}, "timestamp": "2020-01-01T00:00:00Z"}\n'
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(early)
            handle.write("x" * (night_shift.TAIL_BYTES + 100000))
            handle.write("\n")
            handle.write(tail)
        os.utime(path, (time.time(), time.time()))
        self.plant_reckoning([(ident, "close", "merged", "", moment)])
        data = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(data["handover"]["used_after"], [])
        self.assertEqual(data["handover"]["cannot_check"], [])
        self.assertEqual(data["handover"]["state"], "ok")

    def test_offset_and_long_fraction(self):
        """source: a time with an offset is applied, and a long fraction of a second still counts."""
        moment = epoch(2020, 6, 1, 12, 0, 0)
        offset_id = sid("aaaaaaaa")
        frac_id = sid("bbbbbbbb")
        # 13:00 at +02:00 is 11:00 UTC, which is before the verdict only when the offset is applied.
        self.write_session("shop-api", offset_id, [
            self.user("shifted", "/work/shop-api", "2020-06-01T13:00:00+02:00"),
        ])
        self.write_session("data-jobs", frac_id, [
            self.user("fine clock", "/work/data-jobs", "2020-06-01T12:00:01.1234567Z"),
        ])
        self.run_tool(["desk", "--night", NIGHT])
        now = time.time()
        for folder, ident in (("shop-api", offset_id), ("data-jobs", frac_id)):
            os.utime(os.path.join(self.projects, folder, ident + ".jsonl"), (now, now))
        self.plant_reckoning([
            (offset_id, "close", "merged", "", moment),
            (frac_id, "close", "merged", "", moment),
        ])
        data = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(data["handover"]["used_after"], [frac_id])
        self.assertNotIn(offset_id, data["handover"]["used_after"])
        self.assertNotIn(offset_id, data["handover"]["cannot_check"])

    def test_handover_is_judged_against_the_packet_time(self):
        """source: a handed-over window is judged against the packet time, so use between the verdict and the packet is quiet."""
        ident = sid("aaaaaaaa")
        path = self.write_session("shop-web", ident, [
            self.user("rebuild the checkout page", "/work/shop-web", "2020-06-01T00:00:00Z"),
        ])
        self.run_tool(["desk", "--night", NIGHT])
        verdict_at = epoch(2020, 1, 1)
        self.plant_reckoning([(ident, "handover", "context is full", "", verdict_at)])
        packet = self.run_tool([
            "handover", ident, "--goal", "ship it", "--done", "drafted",
            "--next", "wire the form", "--evidence", "the notes", "--night", NIGHT,
        ])
        self.assertEqual(packet.returncode, 0, packet.stderr)
        packet_path = os.path.join(self.nights, NIGHT, "handover", ident + ".md")
        packet_time = epoch(2021, 1, 1)
        os.utime(packet_path, (packet_time, packet_time))
        os.utime(path, (packet_time + 10, packet_time + 10))
        data = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(data["handover"]["used_after"], [])
        self.assertEqual(data["handover"]["cannot_check"], [])
        self.assertEqual(data["handover"]["packets"], 1)
        # A record after the packet is used after, while the same record would be the only proof the file was opened.
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.user("one more", stamp="2099-01-01T00:00:00Z")) + "\n")
        os.utime(path, (packet_time + 20, packet_time + 20))
        later = json.loads(self.run_tool(["check", "--json", "--night", NIGHT]).stdout)
        self.assertEqual(later["handover"]["used_after"], [ident])

    def test_each_kind_of_damage_names_its_line(self):
        """source: a file that is not in its form names the line and the reason, and writes nothing."""
        cases = [
            ("desk.csv", "wrong,header,row,here\n", 1, "wrong header"),
            ("desk.csv", "id,asked,folder,source\nalpha,only,two\n", 2, "wrong number of cells"),
            ("desk.csv", "id,asked,folder,source\nbad/id,hello,,\n", 2, "id does not match the form"),
            ("desk.csv", "id,asked,folder,source\nab..cd,hello,,\n", 2, "id does not match the form"),
            ("desk.csv", "id,asked,folder,source\nalpha,one,,\nalpha,two,,\n", 3, "id appears twice"),
        ]
        for name, text, line, reason in cases:
            shutil.rmtree(self.nights)
            os.makedirs(self.nights)
            night = os.path.join(self.nights, NIGHT)
            os.makedirs(night)
            path = os.path.join(night, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            before = self.walk_files()
            result = self.run_tool(["check", "--night", NIGHT])
            self.assertEqual(result.returncode, 2, reason)
            self.assertEqual(result.stderr, "could not: %s line %d: %s\n" % (path, line, reason))
            self.assertEqual(result.stdout, "")
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(self.walk_files(), before)
        shutil.rmtree(self.nights)
        os.makedirs(self.nights)
        self.add_window("alpha", "hello")
        reckoning = os.path.join(self.nights, NIGHT, "reckoning.csv")
        rec_cases = [
            ("alpha,drop,because,,1.0\n", 2, "verdict is not keep, close or handover"),
            ("alpha,close,,,1.0\n", 2, "empty reason"),
            ("alpha,close,because,,nope\n", 2, "at is not a number"),
            ("alpha,close,because\n", 2, "wrong number of cells"),
            ("other,close\n", 2, "wrong number of cells"),
        ]
        with open(reckoning, "r", encoding="utf-8") as handle:
            handle.read()
        for body, line, reason in rec_cases:
            with open(reckoning, "w", encoding="utf-8") as handle:
                handle.write("id,verdict,reason,you,at\n")
                handle.write(body)
            before = self.walk_files()
            result = self.run_tool(["brief", "--night", NIGHT])
            self.assertEqual(result.returncode, 2, reason)
            self.assertEqual(result.stderr, "could not: %s line %d: %s\n" % (reckoning, line, reason))
            self.assertEqual(result.stdout, "")
            self.assertFalse(os.path.isfile(os.path.join(self.nights, NIGHT, "brief.md")))
            self.assertEqual(self.walk_files(), before)

    def test_unknown_reckoning_id_is_ignored(self):
        """source: a reckoning line whose id is not on the desk is ignored."""
        self.add_window("alpha", "hello")
        path = os.path.join(self.nights, NIGHT, "reckoning.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("id,verdict,reason,you,at\nother,drop,,,nope\nalpha,close,because,,10.000000\n")
        result = self.run_tool(["check", "--json", "--night", NIGHT])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["reckoning"]["state"], "ok")
        self.assertEqual(payload["reckoning"]["verdicts"], 1)

    def test_brief_page_names_each_heading(self):
        """source: the morning page lists each heading, a missing packet, and the windows with no verdict."""
        ident = sid("aaaaaaaa")
        path = self.write_session("shop-api", ident, [
            self.user("fix the invoice export", "/work/shop-api", "2020-01-01T00:00:00Z"),
        ])
        handed = sid("bbbbbbbb")
        self.write_session("shop-web", handed, [
            self.user("rebuild the checkout page", "/work/shop-web", "2020-01-01T00:00:00Z"),
        ])
        self.run_tool([
            "desk", "--night", NIGHT,
            "--add", "billing", "draft the reply",
            "--add", "keepme", "migrate the orders table",
            "--add", "loose", "still open",
        ])
        moment = epoch(2020, 6, 1)
        os.utime(path, (moment - 5, moment - 5))
        self.plant_reckoning([
            (ident, "close", "merged, nothing left", "", moment),
            ("billing", "close", "sent", "", moment),
            ("keepme", "keep", "the migration is mid-run", "approve the vendor quote", moment),
            (handed, "handover", "the context is full", "", moment),
        ])
        # loose has no verdict. handed has no packet.
        page = self.run_tool(["brief", "--night", NIGHT])
        self.assertEqual(page.returncode, 1)
        self.assertTrue(os.path.isfile(os.path.join(self.nights, NIGHT, "brief.md")))
        text = page.stdout
        self.assertTrue(text.startswith("# Night shift %s\n" % NIGHT))
        self.assertIn("\n## Closed (2)\n", text)
        self.assertIn("- aaaaaaaa | shop-api | fix the invoice export | why: merged, nothing left | quiet\n", text)
        self.assertIn("- billing | - | draft the reply | why: sent | cannot check\n", text)
        self.assertIn("\n## Continues (2)\n", text)
        self.assertIn("- keepme | - | migrate the orders table | kept: the migration is mid-run\n", text)
        self.assertIn("- bbbbbbbb | shop-web | rebuild the checkout page | handed over: the context is full | no packet\n", text)
        self.assertIn("\n## Only you (1)\n", text)
        self.assertIn("- keepme | - | approve the vendor quote\n", text)
        self.assertNotIn("approve the vendor quote | migrate", text)
        self.assertIn("\n## No verdict (1)\n", text)
        self.assertIn("- loose | - | still open\n", text)
        with open(os.path.join(self.nights, NIGHT, "brief.md"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)

    def test_empty_headings_are_printed(self):
        """source: a heading with no windows is still printed, with nothing under it."""
        self.add_window("shop", "fix the export")
        self.run_tool(["reckon", "shop", "close", "merged", "--night", NIGHT])
        page = self.run_tool(["brief", "--night", NIGHT])
        self.assertEqual(page.returncode, 0, page.stderr)
        self.assertIn("\n## Closed (1)\n- shop | - | fix the export | why: merged | cannot check\n", page.stdout)
        self.assertIn("\n## Continues (0)\n\n## Only you (0)\n", page.stdout)
        self.assertNotIn("No verdict", page.stdout)

    def test_brief_older_than_the_files_it_reads(self):
        """source: a brief older than a newer verdict is older than the files it reads, and equal times still stand."""
        self.add_window("shop", "fix the export")
        self.run_tool(["reckon", "shop", "close", "merged", "--night", NIGHT])
        page = self.run_tool(["brief", "--night", NIGHT])
        self.assertEqual(page.returncode, 0, page.stderr)
        brief = os.path.join(self.nights, NIGHT, "brief.md")
        reckoning = os.path.join(self.nights, NIGHT, "reckoning.csv")
        desk = os.path.join(self.nights, NIGHT, "desk.csv")
        os.utime(desk, (2000, 2000))
        os.utime(reckoning, (2000, 2000))
        os.utime(brief, (2000, 2000))
        equal = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(equal.returncode, 0, equal.stderr)
        self.assertIn("brief: ok\n", equal.stdout)
        os.utime(brief, (1000, 1000))
        stale = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(stale.returncode, 1)
        self.assertIn("brief: BROKE, older than the files it reads\n", stale.stdout)
        self.assertTrue(stale.stdout.endswith("BROKE AT: brief\n"))

    def test_brief_with_no_desk_writes_nothing(self):
        """source: a brief with no desk prints the check report, writes nothing, and fails."""
        checked = self.run_tool(["check", "--night", NIGHT])
        briefed = self.run_tool(["brief", "--night", NIGHT])
        self.assertEqual(briefed.returncode, 1)
        self.assertEqual(briefed.stdout, checked.stdout)
        self.assertIn("BROKE AT: desk", briefed.stdout)
        for dirpath, _dirnames, filenames in os.walk(self.tmp):
            for name in filenames:
                self.assertFalse(name.endswith(".md"), os.path.join(dirpath, name))

    def stand_closed_and_handover(self):
        handed = sid("bbbbbbbb")
        session = self.write_session(
            "shop-web",
            handed,
            [self.user("rebuild the checkout page", "/work/shop-web", "2020-01-01T00:00:00Z")],
        )
        added = self.run_tool(["desk", "--night", NIGHT, "--add", "shop", "fix the export"])
        self.assertEqual(added.returncode, 0, added.stderr)
        closed = self.run_tool(["reckon", "shop", "close", "merged", "--night", NIGHT])
        self.assertEqual(closed.returncode, 0, closed.stderr)
        marked = self.run_tool(["reckon", handed, "handover", "context is full", "--night", NIGHT])
        self.assertEqual(marked.returncode, 0, marked.stderr)
        packet = self.run_tool([
            "handover", handed, "--goal", "ship it", "--done", "drafted",
            "--next", "wire the form", "--evidence", "the notes", "--night", NIGHT,
        ])
        self.assertEqual(packet.returncode, 0, packet.stderr)
        page = self.run_tool(["brief", "--night", NIGHT])
        self.assertEqual(page.returncode, 0, page.stderr)
        night = os.path.join(self.nights, NIGHT)
        paths = {
            "handed": handed,
            "session": session,
            "brief": os.path.join(night, "brief.md"),
            "desk": os.path.join(night, "desk.csv"),
            "reckoning": os.path.join(night, "reckoning.csv"),
            "packet": os.path.join(night, "handover", handed + ".md"),
            "handover": os.path.join(night, "handover"),
        }
        stood = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(stood.returncode, 0, stood.stdout + stood.stderr)
        self.assertIn("all four stages stand\n", stood.stdout)
        return paths

    def pin(self, path, when):
        os.utime(path, (when, when))

    def test_stray_files_in_handover_do_not_age_the_brief(self):
        """source: a file in the handover folder that the brief does not read does not make the brief older."""
        paths = self.stand_closed_and_handover()
        when = 1700000000
        later = when + 100
        for key in ("desk", "reckoning", "packet", "brief"):
            self.pin(paths[key], when)
        for name in (".DS_Store", "notes.txt"):
            path = os.path.join(paths["handover"], name)
            with open(path, "wb") as handle:
                handle.write(b"\n")
            self.pin(path, later)
        result = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("brief: ok\n", result.stdout)
        self.assertTrue(result.stdout.endswith("all four stages stand\n"))

    def test_packets_outside_a_handover_verdict_do_not_age_the_brief(self):
        """source: a packet for a closed window, or for an id off the desk, does not make the brief older."""
        paths = self.stand_closed_and_handover()
        when = 1700000000
        later = when + 100
        close_text = "\n".join([
            "Taking over from shop, which was asked: fix the export",
            "Goal: ship it",
            "Done: drafted",
            "Next: review",
            "Evidence: the notes",
        ]) + "\n"
        other_text = "\n".join([
            "Taking over from other, which was asked: something else",
            "Goal: ship it",
            "Done: drafted",
            "Next: review",
            "Evidence: the notes",
        ]) + "\n"
        close_packet = os.path.join(paths["handover"], "shop.md")
        other_packet = os.path.join(paths["handover"], "other.md")
        with open(close_packet, "w", encoding="utf-8") as handle:
            handle.write(close_text)
        with open(other_packet, "w", encoding="utf-8") as handle:
            handle.write(other_text)
        for key in ("desk", "reckoning", "packet", "brief"):
            self.pin(paths[key], when)
        self.pin(close_packet, later)
        self.pin(other_packet, later)
        result = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("brief: ok\n", result.stdout)
        self.assertTrue(result.stdout.endswith("all four stages stand\n"))

    def test_newer_handover_packet_makes_the_brief_older(self):
        """source: a brief older than the packet of a window marked handover is older than the files it reads."""
        paths = self.stand_closed_and_handover()
        when = 1700000000
        later = when + 100
        for key in ("desk", "reckoning", "brief"):
            self.pin(paths[key], when)
        self.pin(paths["packet"], later)
        self.pin(paths["session"], later + 50)
        result = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertIn("brief: BROKE, older than the files it reads\n", result.stdout)
        self.assertTrue(result.stdout.endswith("BROKE AT: brief\n"))

    def test_night_folder_ending_with_a_newline_is_not_newest(self):
        """source: a folder whose name is a night with a trailing newline is not the newest night."""
        self.add_window("shop", "fix the export")
        odd_name = "2099-01-01\n"
        odd = os.path.join(self.nights, odd_name)
        try:
            os.makedirs(odd)
        except OSError as exc:
            self.skipTest("a folder whose name ends with a newline cannot be made: %s" % exc)
        if odd_name not in os.listdir(self.nights):
            self.skipTest("a folder whose name ends with a newline cannot be made")
        path = os.path.join(odd, "desk.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["id", "asked", "folder", "source"])
            writer.writerow(["alpha", "one", "", ""])
            writer.writerow(["beta", "two", "", ""])
            writer.writerow(["gamma", "three", "", ""])
        result = self.run_tool(["check"])
        self.assertIn("desk: ok, windows: 1\n", result.stdout)
        self.assertNotIn("windows: 3", result.stdout)

    def test_desk_id_ending_with_a_newline_is_damage(self):
        """source: an id that ends with a newline is damage, the same as an id that lacks the form."""
        night = os.path.join(self.nights, NIGHT)
        os.makedirs(night)
        path = os.path.join(night, "desk.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["id", "asked", "folder", "source"])
            writer.writerow(["tool\n", "fix the export", "", ""])
        result = self.run_tool(["check", "--night", NIGHT])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("could not: "))
        self.assertTrue(result.stderr.endswith("id does not match the form\n"))

    def walk_files(self):
        found = []
        for dirpath, _dirnames, filenames in os.walk(self.tmp):
            for name in filenames:
                found.append(os.path.join(dirpath, name))
        return sorted(found)


if __name__ == "__main__":
    unittest.main()
