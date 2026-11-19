# night-shift

Night shift is a command line tool for the end of a day spent with several agent chats open. It gives the night four named stages, each leaving a file you can check, and when the morning goes wrong it names the first stage that broke.

## What you get

Four files for one night, plus a check that reads them:

- `desk.csv`, every window still open, with what it was asked.
- `reckoning.csv`, one verdict for each window: keep, close, or handover, with a written reason.
- `handover/ID.md`, a five-line packet for each window whose work continues in a fresh chat.
- `brief.md`, one page to read first thing in the morning.
- `check`, which reads those files and names the first stage that broke.

The tool reads session files and writes its own small files. It never runs a command, never calls a model, never opens or closes a window, and never touches the network.

## Quick start

```
sh examples/demo.sh
```

This is the output of that command:

```
part 1 desk
desk: 4 windows, 4 new
  33333333  shop-web  rebuild the checkout page
  22222222  data-jobs  migrate the orders table
  11111111  shop-api  fix the invoice export
  billing  -  draft the reply to the vendor

part 2 check
desk: ok, windows: 4
reckoning: BROKE, verdicts: 0 of 4
  no verdict: 33333333, 22222222, 11111111, billing
handover: ok, packets: 0 of 0, used after: 0, cannot check: 0
brief: BROKE, missing
BROKE AT: reckoning

part 3 verdicts
reckoning: 11111111 close, verdicts: 1 of 4
reckoning: 22222222 keep, verdicts: 2 of 4
reckoning: 33333333 handover, verdicts: 3 of 4
reckoning: billing close, verdicts: 4 of 4
desk: ok, windows: 4
reckoning: ok, verdicts: 4 of 4
handover: BROKE, packets: 0 of 1, used after: 0, cannot check: 1
  no packet: 33333333
brief: BROKE, missing
BROKE AT: handover

part 4 handover
Taking over from 33333333, which was asked: rebuild the checkout page
Goal: ship the new checkout
Done: the layout is in place
Next: wire the new form to the API
Evidence: notes in the shop repo

part 5 brief
# Night shift 2026-01-15

desk: ok, windows: 4
reckoning: ok, verdicts: 4 of 4
handover: ok, packets: 1 of 1, used after: 0, cannot check: 1

## Closed (2)
- 11111111 | shop-api | fix the invoice export | why: merged, nothing left | quiet
- billing | - | draft the reply to the vendor | why: sent | cannot check

## Continues (2)
- 33333333 | shop-web | rebuild the checkout page | handed over: the context is full | next: wire the new form to the API | quiet
- 22222222 | data-jobs | migrate the orders table | kept: the migration is mid-run

## Only you (1)
- 22222222 | data-jobs | approve the vendor quote
desk: ok, windows: 4
reckoning: ok, verdicts: 4 of 4
handover: ok, packets: 1 of 1, used after: 0, cannot check: 1
brief: ok
all four stages stand

part 6 next morning
desk: ok, windows: 4
reckoning: ok, verdicts: 4 of 4
handover: BROKE, packets: 1 of 1, used after: 1, cannot check: 1
  used after: 11111111
brief: ok
BROKE AT: handover
# Night shift 2026-01-15

desk: ok, windows: 4
reckoning: ok, verdicts: 4 of 4
handover: BROKE, packets: 1 of 1, used after: 1, cannot check: 1
  used after: 11111111

## Closed (2)
- 11111111 | shop-api | fix the invoice export | why: merged, nothing left | used after
- billing | - | draft the reply to the vendor | why: sent | cannot check

## Continues (2)
- 33333333 | shop-web | rebuild the checkout page | handed over: the context is full | next: wire the new form to the API | quiet
- 22222222 | data-jobs | migrate the orders table | kept: the migration is mid-run

## Only you (1)
- 22222222 | data-jobs | approve the vendor quote
```

## The four parts

| Part | What it is | File it leaves |
| --- | --- | --- |
| The Desk | Every chat or agent window still open tonight. The raw material | `desk.csv` |
| The Reckoning | Reading each one and folding it: what it finished, what is left, whether it should keep going | `reckoning.csv` |
| The Handover | Starting a successor for the work that continues, then closing the one it replaces | `handover/ID.md` |
| The Brief | One page you read first thing in the morning | `brief.md` |

## Try it tonight

Run the commands from a plain terminal, because a command typed inside an agent window is recorded in that window's session, so a window that reckons itself reads `used after` at the next check.

1. Before bed, count your open agent windows. That is your desk.

   ```
   python3 night_shift.py desk --projects "$HOME/.claude/projects" --nights nights
   ```

2. For each one, write a single line: keep, close or hand over, and why.

   ```
   python3 night_shift.py reckon ID keep|close|handover REASON --nights nights
   ```

   `ID` can be the full id or the first characters of one id, at least four of them. Add `--you TEXT` when the window needs something only you can do.

3. Close every window marked close.

   ```
   python3 night_shift.py check --nights nights
   ```

   The check names the stage. Closing the window is yours to do.

4. For each hand-over, paste a five-line packet into a fresh chat and close the old one.

   ```
   python3 night_shift.py handover ID --goal TEXT --done TEXT --next TEXT --evidence TEXT --nights nights
   ```

   The five lines are printed for you to paste. Then close the old window yourself.

5. In the morning, read your lines from step 2 before you open anything else. That is your brief.

   ```
   python3 night_shift.py brief --nights nights
   ```

## Windows from other tools

A window that has no session file can still be put on the desk:

```
python3 night_shift.py desk --add NAME ASKED --projects "$HOME/.claude/projects" --nights nights
```

`NAME` is a short id you choose: up to 40 letters, digits, `.`, `_` or `-`, starting with a letter or digit. The window has an empty folder and an empty source. Once it is marked close, or handed over with a packet, check cannot compare a session file with the time you finished it, so its state is `cannot check`. It is still listed.

## What check tells you

```
python3 night_shift.py check --nights nights
```

One line for each stage, then a last line. A detail line starts with two spaces and lists the short ids in desk order. Part 3 of the demo shows `no packet:`. Part 6 shows a window used after it was finished:

```
desk: ok, windows: 4
reckoning: ok, verdicts: 4 of 4
handover: BROKE, packets: 1 of 1, used after: 1, cannot check: 1
  used after: 11111111
brief: ok
BROKE AT: handover
```

`desk` is ok when `desk.csv` is there. It is `BROKE, missing` when the night or the file is missing, and the other three stages are then `not checked`.

`reckoning` is ok when every window has a verdict. Otherwise the detail line is `no verdict:`.

`handover` is judged over the windows that have a verdict. It is ok when every window marked handover has a five-line packet whose four parts are not empty, and no window was used after you finished it. `cannot check` does not by itself break the stage. A packet that is not in the five-line form counts as no packet.

`brief` is ok when `brief.md` exists and is not older than `desk.csv`, `reckoning.csv`, or the packet of a window marked handover. Equal times are fine, and any other file in the `handover` folder is not counted. Otherwise it is `missing` or `older than the files it reads`.

A window marked close, and a window marked handover that has a packet, is in one of three states:

- `quiet`: nothing typed in that session after you finished it.
- `used after`: a user or assistant record in the session is later than the time you finished it.
- `cannot check`: there is no session file, the file cannot be read, a user or assistant record has a time that cannot be read, or the session's last record is larger than the 2 MB the tool reads from the end of the file.

A window marked keep is never looked at.

The last line is `BROKE AT:` and the first stage that broke, or `all four stages stand` when all four are ok. Every stage that can be judged is judged, also after an earlier one broke. The command exits 0 when all four are ok, and 1 otherwise.

```
python3 night_shift.py check --json --nights nights
```

`--json` prints one JSON object and nothing else. `broke_at` is the stage name, or null when all four stand. Each stage object carries `state` and its own numbers and lists of full ids: `windows`; `verdicts` and `no_verdict`; `packets`, `handovers`, `no_packet`, `used_after`, and `cannot_check`; and `reason` on the brief. The exit codes are the same.

The short id printed for people is the first eight characters of the id. An id of eight characters or fewer is printed whole. When two ids share those eight characters, both are printed whole.

## Exit codes

- `0`. `desk` and `handover` finished their write. `reckon` stored a verdict, or it was run with no arguments and every window already had one. `check` found all four stages standing. `brief` found desk, reckoning, and handover standing.
- `1`. `check` found a stage that broke. `brief` found that desk, reckoning, or handover did not stand. `reckon` with no arguments found a window that has no verdict. The report is still printed.
- `2`. The command was used wrongly, a table could not be parsed, a path could not be used, or something unexpected failed. Standard error ends with one line that says what was wrong, and standard output is empty.
- `130`. The run was stopped. Standard error is the line `stopped`.

## The morning page

```
python3 night_shift.py brief --nights nights
```

`brief` writes `brief.md` and prints the same text. The page starts with the night's name, then the desk, reckoning, and handover lines exactly as `check` prints them, including the detail lines. Then it lists what closed, what continues, and what only you can do. A heading with no windows is still printed, with `(0)` and no lines under it. `No verdict` is printed only when a window has no verdict.

The page is written even when a stage broke, because a morning page that names the stage that broke is the point. The command exits 0 when desk, reckoning, and handover are ok, and 1 when one of them broke. When there is no night or no `desk.csv`, `brief` prints what `check` prints, writes nothing, and exits 1.

Part 6 of the demo is that morning: the closed window was used again, `check` names it under `used after:`, and the page shows the same line at the top and exits 1.

## When a window was used after you finished it

The session file gained a user or assistant record later than the time you finished that window. For a close, that time is the verdict. For a handover, it is the time the packet file was last modified. Reckon the window again with the verdict it now deserves:

```
python3 night_shift.py reckon ID close REASON --nights nights
```

The new line is appended. The newest line for that window is the verdict that counts, and the older line stays in the file. Reckoning the same window again replaces that verdict whole, its `--you` text included, so a later line with no `--you` text leaves the window off the only-you list. Writing the packet again moves the handover's time to the new file's. Write a new packet if the new verdict is handover, then run `brief` again.

## What it reads and what it writes

Session files are only read. A session file is a regular file one folder deep under the projects folder, named with a session id and `.jsonl`. A session file which is a symbolic link is skipped. The request and the folder are read from the first 2000 lines or 4,000,000 bytes of a session. The projects folder is `--projects`, or `$CLAUDE_CONFIG_DIR/projects` when that variable is set and not empty, or `$HOME/.claude/projects`.

The nights folder is `--nights`, or `NIGHT_SHIFT_DIR` when that variable is set and not empty, or `nights` in the current folder. One night is one folder inside it named `YYYY-MM-DD`. `desk` uses `--night` or the local date twelve hours before now, and creates that folder. A desk opened before noon belongs to the night of the date before, and a desk opened at noon or later starts that day's night. When `--night` is not given and that name is not today's local date, `desk` prints one note on standard error: `note: before noon, so this desk belongs to the night of NAME`. Every other command uses `--night` or the newest night folder. `--nights` and `--night` are accepted by every command. Options are written after the command name.

Every folder the tool creates is mode `0700`, and every file is mode `0600`, whatever the umask of the caller. A folder that was already there keeps its mode. `desk.csv`, `brief.md`, and packets are replaced whole. `reckoning.csv` only grows: each verdict is one appended line, and nothing in it is ever rewritten.

Text that came from outside (the request, the folder, the reason, the `--you` text, and the four packet parts) is cleaned before it is stored or printed. A control character becomes a space, a run of white space becomes one space, and the ends are trimmed. A cell is always one line.

## Why the names matter

Before these four parts had names, a closing step could report that it closed zero windows, and the only question available was why nothing closed. With names, the question is whether the Reckoning wrote a verdict, and whether the Handover acted on it, which you can check.

## Tests

From this folder, the checks are:

```
python3 -m unittest discover -s tests -v
python3 tests/mutate.py
```

They need `sh` and Python 3.9 or newer, and nothing to install.

`python3 tests/mutate.py` first runs each test file on an unchanged copy of `night_shift.py`, then plants one bug at a time in a fresh copy and runs the test file named for that bug. A bug counts as caught only when a test fails. A crash, or a run that passes the time limit, is not counted. A real run ends with:

```
caught 81 of 81
```

The run exits 0 when every planted bug is caught and 1 when the count is short. It exits 2 when a planted edit is not in `night_shift.py` exactly once or changes nothing, or when a test file fails on the unchanged copy.

## Limits

The tool cannot see windows, only session files, so the desk is every session whose file changed in the last 24 hours, sessions a script started included. `desk --hours N` sets that span to the last N hours, where N is a positive number. A window that sat open and untouched all day is not on it until it is added by hand.

It closes nothing and starts nothing, so closing a window and pasting the packet into a fresh chat are yours to do. A window left open and idle reads quiet. A window from another tool reads cannot check.

Session files are read in the form Claude Code writes today, and when that form changes a window is still listed and may show `(no typed request found)`. A request that starts with `<` is passed over, because Claude Code writes its own notes into a session in that form, so a window whose first request starts with `<` shows its next request on the desk.

The desk copies the first 100 characters of each window's first request, so a key pasted into a prompt lands in `desk.csv`, which is why the files are private to your user and `nights/` is in the `.gitignore`. Cells are stored as typed, and a spreadsheet may read a cell that starts with `=` as a formula.

Time comes from the local clock, so a clock that was changed between a verdict and a check can give a wrong state. `reckoning.csv` only grows. The private modes do nothing on Windows.

A reason, a `--you` text, and each packet part can hold at most 2000 characters. Under Python 3.9, `reckon` stops with exit 2 and writes nothing when an option sits between `ID`, the verdict and the reason, so write the options after the reason, as the examples do. A reason that is one word starting with `-` is read as an option and stops the command the same way.

A blank line in `desk.csv` or `reckoning.csv`, such as one a hand edit leaves at the end, makes each command that reads the file stop with exit 2 and `could not: PATH line N: wrong number of cells` until the line is removed. A write that the system cuts short, such as one past a file size limit, is not caught: `desk` still exits 0, and `desk.csv` can lose its last rows, a window added by hand among them.

The tool was tested on macOS with Python 3.9 and Python 3.14.

## License

Night shift is released under the MIT license. The terms are in the LICENSE file.

## Built at San Diego AI Studio

This tool comes out of San Diego AI Studio, the builders' community in San Diego, where builders show the systems they run. More projects: https://sandiegoaistudio.com/projects
