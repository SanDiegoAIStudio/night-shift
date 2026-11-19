#!/usr/bin/env python3
"""Decide what each open agent window becomes overnight, and name the stage that broke."""
import argparse
import csv
import io
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone

DESK_HEADER = ["id", "asked", "folder", "source"]
REC_HEADER = ["id", "verdict", "reason", "you", "at"]
VERDICTS = ("keep", "close", "handover")
FILE_MODE = 0o600
DIR_MODE = 0o700
HEAD_LINES, HEAD_BYTES, TAIL_BYTES, ASKED_LIMIT = 2000, 4000000, 2000000, 100
TEXT_LIMIT = 2000
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
ADD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z")
SESSION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
NIGHT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)
STAGES = ("desk", "reckoning", "handover", "brief")


def die(code, text):
    sys.stderr.write("%s\n" % text)
    raise SystemExit(code)


def usage(text):
    shown = text if str(text).startswith("usage:") else "usage: " + text
    die(2, shown)


def damaged(path, line, reason):
    sys.stderr.write("could not: %s line %d: %s\n" % (path, line, reason))
    raise SystemExit(2)


def clean(text):
    if not isinstance(text, str):
        return ""
    chars = [" " if unicodedata.category(ch) == "Cc" else ch for ch in text]
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def clip(text):
    return text[: ASKED_LIMIT - 3] + "..." if len(text) > ASKED_LIMIT else text


def reject_long(label, text):
    if len(text) > TEXT_LIMIT:
        usage("%s is longer than 2000 characters" % label)


def id_ok(value):
    return isinstance(value, str) and ID_RE.match(value) is not None and ".." not in value


def number_ok(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return False
    return not math.isnan(value) and not math.isinf(value)


def chmod_path(path, mode):
    if os.name != "nt":
        os.chmod(path, mode)


def ensure_dir(path):
    if os.path.isdir(path):
        return
    missing = []
    current = path
    while current and not os.path.isdir(current):
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    missing.reverse()
    for directory in missing[:-1]:
        os.mkdir(directory, DIR_MODE)
        chmod_path(directory, DIR_MODE)
    if missing:
        os.mkdir(missing[-1], DIR_MODE)
        chmod_path(missing[-1], DIR_MODE)


def write_atomic(path, data):
    directory = os.path.dirname(path)
    ensure_dir(directory)
    fd, tmp = tempfile.mkstemp(prefix=".ns-", dir=directory)
    try:
        os.write(fd, data)
        os.close(fd)
        fd = -1
        chmod_path(tmp, FILE_MODE)
        os.replace(tmp, path)
        chmod_path(path, FILE_MODE)
        tmp = ""
    except OSError as exc:
        raise OSError(exc.errno, exc.strerror, path) from None
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def csv_bytes(row):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(row)
    return buf.getvalue().encode("utf-8")


def append_bytes(path, data):
    fd = os.open(path, os.O_APPEND | os.O_WRONLY)
    try:
        os.write(fd, data)
    except OSError as exc:
        raise OSError(exc.errno, exc.strerror, path) from None
    finally:
        os.close(fd)
    chmod_path(path, FILE_MODE)


def append_row(path, data):
    size = os.path.getsize(path)
    prefix = b""
    if size:
        with open(path, "rb") as handle:
            handle.seek(size - 1)
            last = handle.read(1)
        if last != b"\n":
            prefix = b"\n"
    append_bytes(path, prefix + data)


def read_table(path):
    try:
        raw = open(path, "rb").read()
    except OSError:
        damaged(path, 1, "unreadable")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        damaged(path, 1, "not utf-8")
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = []
    try:
        for row in reader:
            rows.append((reader.line_num, row))
    except csv.Error:
        damaged(path, reader.line_num, "cannot be parsed")
    return raw, rows


def load_desk(path):
    raw, rows = read_table(path)
    if not rows or rows[0][1] != DESK_HEADER:
        damaged(path, rows[0][0] if rows else 1, "wrong header")
    seen, data = {}, []
    for lineno, row in rows[1:]:
        if len(row) != 4:
            damaged(path, lineno, "wrong number of cells")
        if not id_ok(row[0]):
            damaged(path, lineno, "id does not match the form")
        if row[0] in seen:
            damaged(path, lineno, "id appears twice")
        seen[row[0]] = True
        data.append(row)
    return raw, data


def load_reckoning(path, desk_ids):
    if not os.path.isfile(path):
        return {}
    _raw, rows = read_table(path)
    if not rows or rows[0][1] != REC_HEADER:
        damaged(path, rows[0][0] if rows else 1, "wrong header")
    known, latest = set(desk_ids), {}
    for lineno, row in rows[1:]:
        if len(row) != 5:
            damaged(path, lineno, "wrong number of cells")
        if row[0] not in known:
            continue
        if row[1] not in VERDICTS:
            damaged(path, lineno, "verdict is not keep, close or handover")
        if clean(row[2]) == "":
            damaged(path, lineno, "empty reason")
        if not number_ok(row[4]):
            damaged(path, lineno, "at is not a number")
        latest[row[0]] = (row[1], row[2], row[3], row[4])
    return latest


def nights_root(args):
    if args.nights:
        return args.nights
    env = os.environ.get("NIGHT_SHIFT_DIR")
    if env:
        return env
    return os.path.join(os.getcwd(), "nights")


def local_now():
    return datetime.now().astimezone()


def night_name(moment):
    return (moment - timedelta(hours=12)).date().isoformat()


def newest_night(root):
    if not os.path.isdir(root):
        return None
    names = [name for name in os.listdir(root) if NIGHT_RE.match(name) and os.path.isdir(os.path.join(root, name))]
    return max(names) if names else None


def select_night(args, root, use_today, moment=None):
    if args.night:
        if not NIGHT_RE.match(args.night):
            usage("night must be YYYY-MM-DD")
        name = args.night
    elif use_today:
        if moment is None:
            moment = local_now()
        name = night_name(moment)
    else:
        name = newest_night(root)
        if not name:
            return None, None
    return name, os.path.join(root, name)


def projects_dir(args):
    if args.projects:
        return args.projects, False
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return os.path.join(env, "projects"), True
    return os.path.join(os.path.expanduser("~"), ".claude", "projects"), True


def hours_value(text):
    if text is None:
        return 24
    try:
        value = float(text)
    except ValueError:
        usage("hours must be a positive number")
    if math.isnan(value):
        usage("hours must be a positive number")
    if math.isinf(value):
        usage("hours must be a positive number")
    if value == 0:
        usage("hours must be a positive number")
    if value < 0:
        usage("hours must be a positive number")
    return value


def is_regular(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def read_capped(path, limit, tail):
    try:
        with open(path, "rb") as handle:
            if tail:
                size = os.path.getsize(path)
                drop = size > limit
                if drop:
                    handle.seek(size - limit)
                lines = handle.read(limit).splitlines()
                return lines[1:] if drop and lines else lines
            lines, total = [], 0
            while len(lines) < HEAD_LINES and total < limit:
                line = handle.readline()
                if not line:
                    break
                if total + len(line) > limit:
                    lines.append(line[: limit - total])
                    break
                total += len(line)
                lines.append(line)
            return lines
    except OSError:
        return None


def parse_line(raw):
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def text_of(message):
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                got = item.get("text")
                if isinstance(got, str):
                    return got
        return None
    return None


def scan_session(path):
    lines = read_capped(path, HEAD_BYTES, False)
    if lines is None:
        return "(could not be read)", ""
    asked, folder = None, ""
    for raw in lines:
        obj = parse_line(raw)
        if obj is None:
            continue
        if folder == "":
            cwd = obj.get("cwd")
            if isinstance(cwd, str) and cwd != "":
                folder = clean(cwd)
        if asked is not None or obj.get("type") != "user":
            continue
        if obj.get("isMeta") is True:
            continue
        if obj.get("isSidechain") is True:
            continue
        text = text_of(obj.get("message"))
        if text is None:
            continue
        cleaned = clean(text)
        if cleaned == "" or cleaned.startswith("<"):
            continue
        asked = clip(cleaned)
    found = asked if asked is not None else "(no typed request found)"
    return found, folder


def session_candidates(folder_path):
    try:
        names = os.listdir(folder_path)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder_path, name)
        if is_regular(path):
            yield name, path


def collect_sessions(projects, hours, default_location):
    if not os.path.isdir(projects):
        if default_location:
            note = "note: no session folder at %s; only windows added with --add are listed\n"
            sys.stderr.write(note % projects)
            return []
        die(2, "not a folder: %s" % projects)
    found, cutoff = [], time.time() - hours * 3600
    try:
        folders = os.listdir(projects)
    except OSError:
        return []
    for folder_name in folders:
        folder_path = os.path.join(projects, folder_name)
        if not os.path.isdir(folder_path):
            continue
        for name, path in session_candidates(folder_path):
            if not name.endswith(".jsonl") or SESSION_RE.match(name[:-6]) is None:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime >= cutoff:
                found.append((mtime, name[:-6], path))
    found.sort(key=lambda item: (-item[0], item[1]))
    return found


def last_part(folder):
    trimmed = (folder or "").replace("\\", "/").rstrip("/")
    return trimmed.split("/")[-1] if trimmed else "-"


def short_map(ids):
    groups = {}
    for ident in ids:
        groups.setdefault(ident[:8], []).append(ident)
    return {ident: ident if len(ident) <= 8 or len(groups[ident[:8]]) > 1 else ident[:8] for ident in ids}


def window_line(short, folder, asked):
    return "  %s  %s  %s" % (short, last_part(folder), asked)


def resolve(token, ids):
    if token in ids:
        return token
    if len(token) >= 4:
        hits = [ident for ident in ids if ident.startswith(token)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            die(2, "%s matches %d windows: %s" % (token, len(hits), ", ".join(hits)))
    die(2, "no window matches %s" % token)


def packet_path(night_path, ident):
    return os.path.join(night_path, "handover", ident + ".md")


def packet_text(short, asked, goal, done, nxt, evidence):
    lines = [
        "Taking over from %s, which was asked: %s" % (short, asked),
        "Goal: %s" % goal,
        "Done: %s" % done,
        "Next: %s" % nxt,
        "Evidence: %s" % evidence,
    ]
    return "\n".join(lines) + "\n"


def packet_head_ok(line, ident, short, asked):
    prefix = "Taking over from "
    marker = ", which was asked: "
    if not line.startswith(prefix):
        return False
    split_at = line.find(marker)
    if split_at < len(prefix):
        return False
    name = line[len(prefix):split_at]
    got = line[split_at + len(marker):]
    if name != ident and name != ident[:8]:
        return False
    if got != asked:
        return False
    return True


def read_packet(path, ident, short, asked):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if len(lines) != 5 or not packet_head_ok(lines[0], ident, short, asked):
        return None
    labels = ("Goal: ", "Done: ", "Next: ", "Evidence: ")
    values = []
    for label, line in zip(labels, lines[1:]):
        if not line.startswith(label) or clean(line[len(label):]) == "":
            return None
        values.append(line[len(label):])
    return values


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    match = TS_RE.match(value)
    if not match:
        return None
    parts = [int(part) for part in match.group(1, 2, 3, 4, 5, 6)]
    frac = match.group(7) or ""
    extra = float(frac) / (10 ** len(frac)) if frac else 0.0
    off = match.group(8)
    sign = 1 if off[:1] == "+" else -1
    shift = 0 if off == "Z" else sign * (int(off[1:3]) * 3600 + int(off[4:6]) * 60)
    try:
        stamp = datetime(parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
    return stamp + extra - shift


def usage_state(source, instant):
    if not source:
        return "cannot check"
    if not is_regular(source):
        return "cannot check"
    try:
        mtime = os.path.getmtime(source)
    except OSError:
        return "cannot check"
    if mtime <= instant:
        return "quiet"
    lines = read_capped(source, TAIL_BYTES, True)
    if lines is None:
        return "cannot check"
    saw_record = False
    unreadable = False
    for raw in lines:
        obj = parse_line(raw)
        if obj is None or obj.get("type") not in ("user", "assistant"):
            continue
        saw_record = True
        parsed = parse_timestamp(obj.get("timestamp"))
        if parsed is None:
            unreadable = True
            continue
        if parsed > instant:
            return "used after"
    if not saw_record:
        try:
            size = os.path.getsize(source)
        except OSError:
            return "cannot check"
        if size > TAIL_BYTES:
            return "cannot check"
    if unreadable:
        return "cannot check"
    return "quiet"


def judge(name, night_path):
    report = {
        "night": name,
        "broke_at": None,
        "windows": [],
        "details": {stage: [] for stage in STAGES},
    }
    report["desk"] = {"state": "not checked", "windows": 0, "extra": ""}
    report["reckoning"] = {"state": "not checked", "verdicts": 0, "no_verdict": [], "extra": ""}
    report["handover"] = {
        "state": "not checked",
        "packets": 0,
        "handovers": 0,
        "no_packet": [],
        "used_after": [],
        "cannot_check": [],
        "extra": "",
    }
    report["brief"] = {"state": "not checked", "reason": None, "extra": ""}
    desk_path = os.path.join(night_path, "desk.csv") if night_path else ""
    if not night_path or not os.path.isdir(night_path) or not os.path.isfile(desk_path):
        report["desk"]["state"] = "BROKE"
        report["desk"]["extra"] = "missing"
        report["broke_at"] = "desk"
        return report
    _raw, rows = load_desk(desk_path)
    ids = [row[0] for row in rows]
    shorts = short_map(ids)
    rec_path = os.path.join(night_path, "reckoning.csv")
    latest = load_reckoning(rec_path, ids)
    no_verdict, no_packet, used_after, cannot_check = [], [], [], []
    handovers = packets = 0
    windows = []
    packet_paths = []
    for ident, asked, folder, source in rows:
        saved = latest.get(ident)
        verdict, reason, you, at = saved if saved else ("", "", "", "")
        nxt, state = "", ""
        if verdict == "":
            no_verdict.append(ident)
        elif verdict == "handover":
            handovers += 1
            path = packet_path(night_path, ident)
            if is_regular(path):
                packet_paths.append(path)
            values = read_packet(path, ident, shorts[ident], asked)
            if values is None:
                no_packet.append(ident)
            else:
                packets += 1
                nxt = values[2]
                packet_instant = os.path.getmtime(path)
                state = usage_state(source, packet_instant)
        elif verdict == "close":
            close_instant = float(at)
            state = usage_state(source, close_instant)
        if state == "used after":
            used_after.append(ident)
        elif state == "cannot check":
            cannot_check.append(ident)
        windows.append((ident, asked, folder, verdict, reason, you, state, nxt, shorts[ident]))
    counted = len(windows) - len(no_verdict)
    report["windows"] = windows
    report["desk"].update(state="ok", windows=len(windows), extra="windows: %d" % len(windows))
    reckoning_ok = len(no_verdict) == 0
    report["reckoning"].update(
        state="ok" if reckoning_ok else "BROKE", verdicts=counted, no_verdict=no_verdict
    )
    report["reckoning"]["extra"] = "verdicts: %d of %d" % (counted, len(windows))
    if no_verdict:
        report["details"]["reckoning"].append("no verdict: " + ", ".join(shorts[item] for item in no_verdict))
    handover_ok = len(no_packet) == 0 and len(used_after) == 0
    report["handover"].update(
        state="ok" if handover_ok else "BROKE",
        packets=packets,
        handovers=handovers,
        no_packet=no_packet,
        used_after=used_after,
        cannot_check=cannot_check,
    )
    report["handover"]["extra"] = "packets: %d of %d, used after: %d, cannot check: %d" % (
        packets, handovers, len(used_after), len(cannot_check)
    )
    if no_packet:
        report["details"]["handover"].append("no packet: " + ", ".join(shorts[item] for item in no_packet))
    if used_after:
        report["details"]["handover"].append("used after: " + ", ".join(shorts[item] for item in used_after))
    brief_path = os.path.join(night_path, "brief.md")
    newest = input_mtime(desk_path, rec_path, packet_paths)
    if not os.path.isfile(brief_path):
        report["brief"].update(state="BROKE", reason="missing", extra="missing")
    elif os.path.getmtime(brief_path) >= newest:
        report["brief"].update(state="ok", reason=None, extra="")
    else:
        report["brief"].update(
            state="BROKE",
            reason="older than the files it reads",
            extra="older than the files it reads",
        )
    for stage in STAGES:
        if report[stage]["state"] == "BROKE":
            report["broke_at"] = stage
            break
    return report


def input_mtime(desk_path, rec_path, packet_paths):
    times = [os.path.getmtime(path) for path in (desk_path, rec_path) if path and os.path.isfile(path)]
    for path in packet_paths:
        if is_regular(path):
            times.append(os.path.getmtime(path))
    return max(times) if times else 0


def stage_line(name, info):
    if info["state"] == "not checked":
        return "%s: not checked" % name
    if info["extra"]:
        return "%s: %s, %s" % (name, info["state"], info["extra"])
    return "%s: %s" % (name, info["state"])


def render_check(report):
    lines = []
    for name in STAGES:
        lines.append(stage_line(name, report[name]))
        lines.extend("  " + detail for detail in report["details"][name])
    broke = "BROKE AT: %s" % report["broke_at"] if report["broke_at"] else "all four stages stand"
    lines.append(broke)
    return "\n".join(lines) + "\n"


def render_json(report):
    desk, reckoning = report["desk"], report["reckoning"]
    handover, brief = report["handover"], report["brief"]
    payload = {
        "night": report["night"],
        "broke_at": report["broke_at"],
        "desk": {"state": desk["state"], "windows": desk["windows"]},
        "reckoning": {
            "state": reckoning["state"],
            "verdicts": reckoning["verdicts"],
            "no_verdict": reckoning["no_verdict"],
        },
        "handover": {
            "state": handover["state"],
            "packets": handover["packets"],
            "handovers": handover["handovers"],
            "no_packet": handover["no_packet"],
            "used_after": handover["used_after"],
            "cannot_check": handover["cannot_check"],
        },
        "brief": {"state": brief["state"], "reason": brief["reason"]},
    }
    return json.dumps(payload) + "\n"


def render_page(report):
    lines = ["# Night shift %s" % report["night"], ""]
    for name in ("desk", "reckoning", "handover"):
        lines.append(stage_line(name, report[name]))
        lines.extend("  " + detail for detail in report["details"][name])
    closed, continues, only_you, missing = [], [], [], []
    for _ident, asked, folder, verdict, reason, you, state, nxt, short in report["windows"]:
        place = last_part(folder)
        if verdict == "":
            missing.append("- %s | %s | %s" % (short, place, asked))
        elif verdict == "close":
            closed.append("- %s | %s | %s | why: %s | %s" % (short, place, asked, reason, state))
        elif verdict == "keep":
            continues.append("- %s | %s | %s | kept: %s" % (short, place, asked, reason))
        elif nxt == "" and state == "":
            handed = "- %s | %s | %s | handed over: %s | no packet" % (short, place, asked, reason)
            continues.append(handed)
        else:
            handed = "- %s | %s | %s | handed over: %s | next: %s | %s" % (
                short, place, asked, reason, nxt, state
            )
            continues.append(handed)
        if clean(you):
            only_you.append("- %s | %s | %s" % (short, place, clean(you)))
    lines += ["", "## Closed (%d)" % len(closed)] + closed
    lines += ["", "## Continues (%d)" % len(continues)] + continues
    lines += ["", "## Only you (%d)" % len(only_you)] + only_you
    if missing:
        lines += ["", "## No verdict (%d)" % len(missing)] + missing
    return "\n".join(lines) + "\n"


def check_add(name, asked, seen):
    if ADD_RE.match(name) is None or ".." in name:
        usage("add name does not match the form: %s" % name)
    if name in seen:
        usage("add name is already on the desk: %s" % name)
    if clean(asked) == "":
        usage("add asked is empty")


def cmd_desk(args):
    hours = hours_value(args.hours)
    projects, default_location = projects_dir(args)
    if not default_location and not os.path.isdir(projects):
        die(2, "not a folder: %s" % projects)
    root = nights_root(args)
    root_existed = os.path.isdir(root)
    moment = local_now()
    name, night_path = select_night(args, root, True, moment)
    if not args.night and name != moment.date().isoformat():
        sys.stderr.write("note: before noon, so this desk belongs to the night of %s\n" % name)
    desk_path = os.path.join(night_path, "desk.csv")
    existing, raw = [], b""
    if os.path.isfile(desk_path):
        raw, existing = load_desk(desk_path)
    seen = set(row[0] for row in existing)
    adds = args.add or []
    for pair in adds:
        check_add(pair[0], pair[1], seen)
        seen.add(pair[0])
    new_rows, known = [], set(row[0] for row in existing)
    add_names = set(pair[0] for pair in adds)
    for _mtime, ident, path in collect_sessions(projects, hours, default_location):
        if ident in add_names:
            usage("add name is already on the desk: %s" % ident)
        if ident in known:
            continue
        known.add(ident)
        asked, folder = scan_session(path)
        new_rows.append([ident, asked, folder, os.path.abspath(path)])
    for pair in adds:
        new_rows.append([pair[0], clip(clean(pair[1])), "", ""])
    if existing and new_rows:
        extra = b"".join(csv_bytes(row) for row in new_rows)
        if raw.endswith(b"\n"):
            merged = raw + extra
        else:
            merged = raw + b"\n" + extra
        write_atomic(desk_path, merged)
    elif existing:
        chmod_path(desk_path, FILE_MODE)
    else:
        body = [csv_bytes(DESK_HEADER)] + [csv_bytes(row) for row in new_rows]
        write_atomic(desk_path, b"".join(body))
    rec_path = os.path.join(night_path, "reckoning.csv")
    if os.path.isfile(rec_path):
        chmod_path(rec_path, FILE_MODE)
    else:
        write_atomic(rec_path, csv_bytes(REC_HEADER))
    if not root_existed:
        chmod_path(root, DIR_MODE)
    rows = existing + new_rows
    shorts = short_map([row[0] for row in rows])
    lines_out = ["desk: %d windows, %d new" % (len(rows), len(new_rows))]
    for row in rows:
        lines_out.append(window_line(shorts[row[0]], row[2], row[1]))
    return "\n".join(lines_out) + "\n", 0


def load_for_edit(args):
    _name, night_path = select_night(args, nights_root(args), False)
    desk_path = os.path.join(night_path, "desk.csv") if night_path else ""
    if not night_path or not os.path.isfile(desk_path):
        usage("no night yet")
    _raw, rows = load_desk(desk_path)
    ids = [row[0] for row in rows]
    return night_path, rows, ids, short_map(ids)


def cmd_reckon(args):
    listing = args.window is None and args.verdict is None and args.reason is None and args.you is None
    night_path, rows, ids, shorts = load_for_edit(args)
    rec_path = os.path.join(night_path, "reckoning.csv")
    if listing:
        latest = load_reckoning(rec_path, ids)
        missing = [row for row in rows if row[0] not in latest]
        lines_out = [window_line(shorts[row[0]], row[2], row[1]) for row in missing]
        lines_out.append("verdicts: %d of %d" % (len(rows) - len(missing), len(rows)))
        return "\n".join(lines_out) + "\n", (0 if not missing else 1)
    if not args.window or not args.verdict or args.reason is None:
        usage("reckon needs a window, a verdict and a reason")
    if args.verdict not in VERDICTS:
        usage("verdict must be keep, close or handover")
    reason = clean(args.reason)
    if reason == "":
        die(2, "a verdict needs a written reason")
    reject_long("reason", reason)
    you = clean(args.you) if args.you is not None else ""
    reject_long("you", you)
    ident = resolve(args.window, ids)
    if not os.path.isfile(rec_path):
        write_atomic(rec_path, csv_bytes(REC_HEADER))
    append_row(rec_path, csv_bytes([ident, args.verdict, reason, you, "%.6f" % time.time()]))
    latest = load_reckoning(rec_path, ids)
    have = len([row for row in rows if row[0] in latest])
    text = "reckoning: %s %s, verdicts: %d of %d\n" % (shorts[ident], args.verdict, have, len(rows))
    return text, 0


def cmd_handover(args):
    parts = (("goal", args.goal), ("done", args.done), ("next", args.next), ("evidence", args.evidence))
    cleaned = {}
    for key, value in parts:
        if value is None:
            die(2, "missing %s" % key)
        text = clean(value)
        if text == "":
            die(2, "empty %s" % key)
        reject_long(key, text)
        cleaned[key] = text
    night_path, rows, ids, shorts = load_for_edit(args)
    ident = resolve(args.window, ids)
    saved = load_reckoning(os.path.join(night_path, "reckoning.csv"), ids).get(ident)
    short = shorts[ident]
    if saved is None:
        die(2, "%s has no verdict yet" % short)
    if saved[0] != "handover":
        die(2, "%s is marked %s; give it the handover verdict first" % (short, saved[0]))
    asked = next(row[1] for row in rows if row[0] == ident)
    body = packet_text(
        short, asked, cleaned["goal"], cleaned["done"], cleaned["next"], cleaned["evidence"]
    )
    ensure_dir(os.path.join(night_path, "handover"))
    write_atomic(packet_path(night_path, ident), body.encode("utf-8"))
    sys.stderr.write("handover: wrote handover/%s.md\n" % ident)
    return body, 0


def cmd_check(args):
    name, night_path = select_night(args, nights_root(args), False)
    report = judge(name, night_path)
    text = render_json(report) if args.json else render_check(report)
    if report["broke_at"] is None:
        code = 0
    else:
        code = 1
    return text, code


def cmd_brief(args):
    name, night_path = select_night(args, nights_root(args), False)
    report = judge(name, night_path)
    if report["desk"]["state"] != "ok":
        return render_check(report), 1
    page = render_page(report)
    write_atomic(os.path.join(night_path, "brief.md"), page.encode("utf-8"))
    ready = report["reckoning"]["state"] == "ok" and report["handover"]["state"] == "ok"
    return page, (0 if ready else 1)


def build_parser():
    def error(message):
        die(2, "usage: %s" % message)

    parser = argparse.ArgumentParser(prog="night_shift.py")
    parser.error = error
    common = argparse.ArgumentParser(add_help=False)
    common.error = error
    for flag in ("--nights", "--night", "--projects"):
        common.add_argument(flag, default=None)
    sub = parser.add_subparsers(dest="command")
    desk = sub.add_parser("desk", parents=[common])
    desk.add_argument("--hours", default=None)
    desk.add_argument("--add", nargs=2, action="append", metavar=("NAME", "ASKED"))
    desk.set_defaults(func=cmd_desk)
    reckon = sub.add_parser("reckon", parents=[common])
    for name in ("window", "verdict", "reason"):
        reckon.add_argument(name, nargs="?")
    reckon.add_argument("--you", default=None)
    reckon.set_defaults(func=cmd_reckon)
    handover = sub.add_parser("handover", parents=[common])
    handover.add_argument("window")
    for key in ("goal", "done", "next", "evidence"):
        handover.add_argument("--" + key, default=None)
    handover.set_defaults(func=cmd_handover)
    check = sub.add_parser("check", parents=[common])
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)
    brief = sub.add_parser("brief", parents=[common])
    brief.set_defaults(func=cmd_brief)
    for item in (desk, reckon, handover, check, brief):
        item.error = error
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        usage("night_shift.py desk|reckon|handover|check|brief")
    return args.func(args)


def exit_code(exc):
    code = exc.code
    if isinstance(code, int):
        return code
    return 0 if code is None else 2


def silence_stdout():
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(devnull, 1)
        os.close(devnull)
    except OSError:
        pass


def write_out(text):
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        silence_stdout()


def report_oserror(exc):
    reason = exc.strerror if exc.strerror else str(exc)
    if exc.filename:
        sys.stderr.write("could not: %s: %s\n" % (exc.filename, reason))
    else:
        sys.stderr.write("could not: %s\n" % reason)


def run(argv):
    try:
        text, code = main(argv)
        write_out(text)
        return code
    except SystemExit as exc:
        return exit_code(exc)
    except KeyboardInterrupt:
        sys.stderr.write("stopped\n")
        return 130
    except OSError as exc:
        report_oserror(exc)
        return 2
    except Exception as exc:
        sys.stderr.write("could not: unexpected error: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
