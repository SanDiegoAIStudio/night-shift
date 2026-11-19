#!/usr/bin/env python3
"""Plant one bug at a time in a copy of the tool and record which tests catch it."""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "night_shift.py")
PY = sys.executable
LIMIT_SECONDS = 120

# name, old text, new text, test module
MUTATIONS = [
    ("hours_scale", "time.time() - hours * 3600", "time.time() - hours * 1", "test_desk"),
    ("hours_nan", "if math.isnan(value):", "if False and math.isnan(value):", "test_desk"),
    ("hours_inf", "if math.isinf(value):", "if False and math.isinf(value):", "test_desk"),
    ("hours_zero", "if value == 0:", "if value == 0 and False:", "test_desk"),
    ("hours_negative", "if value < 0:", "if False:", "test_desk"),
    ("session_name", "SESSION_RE.match(name[:-6]) is None", "False", "test_desk"),
    (
        "session_depth",
        "        if is_regular(path):\n            yield name, path",
        "        if os.path.isdir(path):\n"
        "            for inner, inner_path in session_candidates(path): yield inner, inner_path\n"
        "        elif is_regular(path): yield name, path",
        "test_desk",
    ),
    (
        "session_directory",
        "        if is_regular(path):\n            yield name, path",
        "        if True: yield name, path",
        "test_desk",
    ),
    (
        "skip_meta",
        '        if obj.get("isMeta") is True:\n            continue',
        "        if False:\n            continue",
        "test_desk",
    ),
    (
        "skip_sidechain",
        '        if obj.get("isSidechain") is True:\n            continue',
        "        if False:\n            continue",
        "test_desk",
    ),
    ("skip_bracket", 'cleaned.startswith("<")', 'cleaned.startswith("\\x00")', "test_desk"),
    ("skip_tool_result", 'item.get("type") == "text"', 'item.get("type") != "missing"', "test_desk"),
    ("list_form", "isinstance(content, list)", "isinstance(content, tuple)", "test_desk"),
    ("cut_at_limit", "2000000, 100", "2000000, 10000", "test_desk"),
    ("clean_controls", '== "Cc"', '== "Zz"', "test_desk"),
    (
        "desk_grows",
        '        if raw.endswith(b"\\n"):\n'
        "            merged = raw + extra\n"
        "        else:\n"
        '            merged = raw + b"\\n" + extra\n'
        "        write_atomic(desk_path, merged)",
        "        write_atomic(desk_path, extra)",
        "test_desk",
    ),
    ("add_form", 'if ADD_RE.match(name) is None or ".." in name:', "if False:", "test_desk"),
    ("add_duplicate", "if name in seen:", "if False:", "test_desk"),
    ("add_empty", 'if clean(asked) == "":', "if False:", "test_desk"),
    ("add_session_name", "if ident in add_names:", "if False:", "test_desk"),
    ("private_file", "FILE_MODE = 0o600", "FILE_MODE = 0o644", "test_desk"),
    ("private_dir", "DIR_MODE = 0o700", "DIR_MODE = 0o755", "test_desk"),
    ("newest_night", "return max(names)", "return min(names)", "test_desk"),
    ("prefix_length", "if len(token) >= 4:", "if len(token) >= 5:", "test_reckon"),
    (
        "exact_match_first",
        "    if token in ids:\n        return token",
        "    if False:\n        return token",
        "test_reckon",
    ),
    (
        "prefix_two",
        '        if len(hits) > 1:\n'
        '            die(2, "%s matches %d windows: %s" % (token, len(hits), ", ".join(hits)))',
        "        if len(hits) > 1:\n            return hits[0]",
        "test_reckon",
    ),
    ("verdict_words", "if args.verdict not in VERDICTS:", "if False:", "test_reckon"),
    ("empty_reason", 'if reason == "":', "if False:", "test_reckon"),
    (
        "newest_verdict",
        "latest[row[0]] = (row[1], row[2], row[3], row[4])",
        "latest.setdefault(row[0], (row[1], row[2], row[3], row[4]))",
        "test_reckon",
    ),
    ("handover_verdict", 'if saved[0] != "handover":', "if False:", "test_handover"),
    ("handover_no_verdict", "if saved is None:", "if False:", "test_handover"),
    (
        "packet_missing",
        '        if value is None:\n            die(2, "missing %s" % key)',
        '        if False:\n            die(2, "missing %s" % key)',
        "test_handover",
    ),
    (
        "packet_empty",
        '        if text == "":\n            die(2, "empty %s" % key)',
        '        if False:\n            die(2, "empty %s" % key)',
        "test_handover",
    ),
    ("id_form", "if not id_ok(row[0]):", "if False:", "test_handover"),
    ("close_instant", "close_instant = float(at)", "close_instant = 0.0", "test_check"),
    ("packet_instant", "packet_instant = os.path.getmtime(path)", "packet_instant = float(at)", "test_check"),
    ("record_user_only", '("user", "assistant")', '("user",)', "test_check"),
    ("record_counts_system", '("user", "assistant")', '("user", "assistant", "system")', "test_check"),
    ("unreadable_time", "unreadable = True", "unreadable = False", "test_check"),
    (
        "missing_source",
        '    if not source:\n        return "cannot check"',
        '    if not source:\n        return "quiet"',
        "test_check",
    ),
    (
        "gone_source",
        '    if not is_regular(source):\n        return "cannot check"',
        '    if not is_regular(source):\n        return "quiet"',
        "test_check",
    ),
    (
        "mtime_shortcut",
        '    if mtime <= instant:\n        return "quiet"',
        '    if False:\n        return "quiet"',
        "test_check",
    ),
    ("reckoning_always_ok", "reckoning_ok = len(no_verdict) == 0", "reckoning_ok = True", "test_check"),
    ("reckoning_always_broke", "reckoning_ok = len(no_verdict) == 0", "reckoning_ok = False", "test_check"),
    (
        "handover_always_ok",
        "handover_ok = len(no_packet) == 0 and len(used_after) == 0",
        "handover_ok = True",
        "test_check",
    ),
    (
        "handover_always_broke",
        "handover_ok = len(no_packet) == 0 and len(used_after) == 0",
        "handover_ok = False",
        "test_check",
    ),
    ("desk_always_broke", 'state="ok", windows=len(windows)', 'state="BROKE", windows=len(windows)', "test_check"),
    (
        "desk_missing_skipped",
        "if not night_path or not os.path.isdir(night_path) or not os.path.isfile(desk_path):",
        "if False:",
        "test_check",
    ),
    ("brief_missing_ok", 'state="BROKE", reason="missing"', 'state="ok", reason="missing"', "test_check"),
    ("brief_equal_is_stale", "os.path.getmtime(brief_path) >= newest", "os.path.getmtime(brief_path) > newest", "test_check"),
    ("brief_older_passes", "os.path.getmtime(brief_path) >= newest", "os.path.getmtime(brief_path) <= newest", "test_check"),
    (
        "first_broke_stage",
        '            report["broke_at"] = stage\n            break',
        '            report["broke_at"] = stage\n            continue',
        "test_check",
    ),
    (
        "exit_always_zero",
        '    if report["broke_at"] is None:\n        code = 0\n    else:\n        code = 1\n    return text, code',
        "    return text, 0",
        "test_check",
    ),
    (
        "exit_always_one",
        '    if report["broke_at"] is None:\n        code = 0\n    else:\n        code = 1\n    return text, code',
        "    return text, 1",
        "test_check",
    ),
    ("exit_damage_zero", "raise SystemExit(2)", "raise SystemExit(0)", "test_check"),
    (
        "keep_is_judged",
        '        elif verdict == "close":\n            close_instant = float(at)',
        '        elif verdict == "close" or verdict == "keep":\n            close_instant = float(at)',
        "test_check",
    ),
    (
        "packet_name_skipped",
        "    if name != ident and name != ident[:8]:",
        "    if False:",
        "test_run",
    ),
    (
        "packet_asked_skipped",
        "    if got != asked:",
        "    if False:",
        "test_run",
    ),
    (
        "packet_name_short_only",
        "    if name != ident and name != ident[:8]:",
        "    if name != short:",
        "test_run",
    ),
    (
        "cut_tail_quiet",
        '        if size > TAIL_BYTES:\n            return "cannot check"',
        '        if size > TAIL_BYTES:\n            return "quiet"',
        "test_run",
    ),
    (
        "drop_fraction",
        "extra = float(frac) / (10 ** len(frac))",
        "extra = 0.0",
        "test_run",
    ),
    ("follow_link", "os.lstat", "os.stat", "test_run"),
    (
        "root_mode_reset",
        "    if not root_existed:",
        "    if True:",
        "test_run",
    ),
    (
        "upper_umask",
        "    for directory in missing[:-1]:\n"
        "        os.mkdir(directory, DIR_MODE)\n"
        "        chmod_path(directory, DIR_MODE)",
        "    for directory in missing[:-1]:\n"
        "        os.mkdir(directory)",
        "test_run",
    ),
    (
        "oserror_exit_one",
        "        report_oserror(exc)\n        return 2",
        "        report_oserror(exc)\n        return 1",
        "test_run",
    ),
    (
        "csv_error_raised",
        'damaged(path, reader.line_num, "cannot be parsed")',
        "raise",
        "test_run",
    ),
    (
        "packet_utf8_raised",
        "except (OSError, UnicodeDecodeError):",
        "except OSError:",
        "test_run",
    ),
    (
        "reckon_no_newline",
        "append_bytes(path, prefix + data)",
        "append_bytes(path, data)",
        "test_run",
    ),
    (
        "broken_pipe_printed",
        "    except BrokenPipeError:\n        silence_stdout()",
        "    except BrokenPipeError:\n        raise",
        "test_run",
    ),
    (
        "interrupt_raised",
        '    except KeyboardInterrupt:\n        sys.stderr.write("stopped\\n")\n        return 130',
        "    except KeyboardInterrupt:\n        raise",
        "test_run",
    ),
    (
        "unexpected_exit_one",
        '        sys.stderr.write("could not: unexpected error: %s: %s\\n" % (type(exc).__name__, exc))\n'
        "        return 2",
        '        sys.stderr.write("could not: unexpected error: %s: %s\\n" % (type(exc).__name__, exc))\n'
        "        return 1",
        "test_run",
    ),
    (
        "night_is_today",
        "(moment - timedelta(hours=12)).date().isoformat()",
        "moment.date().isoformat()",
        "test_run",
    ),
    (
        "long_text_kept",
        "    if len(text) > TEXT_LIMIT:",
        "    if False:",
        "test_run",
    ),
    (
        "add_not_cut",
        "clip(clean(pair[1]))",
        "clean(pair[1])",
        "test_run",
    ),
    (
        "brief_lists_handover_folder",
        "    for path in packet_paths:\n"
        "        if is_regular(path):\n"
        "            times.append(os.path.getmtime(path))",
        "    handover_dir = os.path.join(os.path.dirname(desk_path), \"handover\")\n"
        "    if os.path.isdir(handover_dir):\n"
        "        for name in os.listdir(handover_dir):\n"
        "            path = os.path.join(handover_dir, name)\n"
        "            if is_regular(path):\n"
        "                times.append(os.path.getmtime(path))",
        "test_check",
    ),
    (
        "brief_counts_no_packet",
        "            if is_regular(path):\n"
        "                packet_paths.append(path)",
        "            if False:\n"
        "                packet_paths.append(path)",
        "test_check",
    ),
    (
        "brief_counts_every_verdict",
        "        elif verdict == \"close\":\n"
        "            close_instant = float(at)\n"
        "            state = usage_state(source, close_instant)",
        "        elif verdict == \"close\":\n"
        "            close_instant = float(at)\n"
        "            state = usage_state(source, close_instant)\n"
        "        if verdict in VERDICTS:\n"
        "            extra_packet = packet_path(night_path, ident)\n"
        "            if is_regular(extra_packet):\n"
        "                packet_paths.append(extra_packet)",
        "test_check",
    ),
    (
        "id_re_dollar",
        'ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}\\Z")',
        'ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")',
        "test_check",
    ),
    (
        "add_re_dollar",
        'ADD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}\\Z")',
        'ADD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")',
        "test_desk",
    ),
    (
        "session_re_dollar",
        'SESSION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\Z")',
        'SESSION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")',
        "test_desk",
    ),
    (
        "night_re_dollar",
        'NIGHT_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}\\Z")',
        'NIGHT_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")',
        "test_desk",
    ),
]


def check_swaps(source):
    bad = False
    for name, old, new, _module in MUTATIONS:
        count = source.count(old)
        if count != 1 or old == new:
            sys.stderr.write("%s: swap matches %d times\n" % (name, count))
            bad = True
    return not bad


def stop_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def run_one(source, module, old=None, new=None):
    tmp = tempfile.mkdtemp(prefix="night-shift-mut-")
    try:
        os.makedirs(os.path.join(tmp, "tests"))
        text = source
        if old is not None:
            text = source.replace(old, new, 1)
        with open(os.path.join(tmp, "night_shift.py"), "w", encoding="utf-8") as handle:
            handle.write(text)
        shutil.copy(os.path.join(HERE, module + ".py"), os.path.join(tmp, "tests", module + ".py"))
        init_path = os.path.join(tmp, "tests", "__init__.py")
        with open(init_path, "w", encoding="utf-8") as handle:
            handle.write("")
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("NIGHT_SHIFT_DIR", None)
        env.pop("CLAUDE_CONFIG_DIR", None)
        proc = subprocess.Popen(
            [PY, "-m", "unittest", "tests." + module],
            cwd=tmp,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=LIMIT_SECONDS)
        except subprocess.TimeoutExpired:
            stop_group(proc)
            return "timed out"
        output = stdout + "\n" + stderr
        found = re.search(r"failures=(\d+)", output)
        if found and int(found.group(1)) > 0:
            return "caught"
        if proc.returncode != 0:
            return "crashed"
        return "survived"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    with open(TOOL, "r", encoding="utf-8") as handle:
        source = handle.read()
    if not check_swaps(source):
        return 2
    modules = []
    for _name, _old, _new, module in MUTATIONS:
        if module not in modules:
            modules.append(module)
    for module in modules:
        status = run_one(source, module)
        if status != "survived":
            sys.stderr.write("%s %s\n" % (module, status))
            sys.stdout.write("the clean copy does not pass\n")
            return 2
    caught = 0
    for name, old, new, module in MUTATIONS:
        status = run_one(source, module, old, new)
        sys.stdout.write("%s %s\n" % (name, status))
        sys.stdout.flush()
        if status == "caught":
            caught += 1
    total = len(MUTATIONS)
    sys.stdout.write("caught %d of %d\n" % (caught, total))
    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
