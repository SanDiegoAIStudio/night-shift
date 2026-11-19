#!/bin/sh
# Sample night in a temp folder. The folder is removed on exit.
set -eu

tool="$(dirname "$0")/../night_shift.py"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/night-shift.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

projects="$tmp/projects"
export NIGHT_SHIFT_DIR="$tmp/nights"

fail() {
  printf '%s\n' "$1" >&2
  if [ -n "${last_err:-}" ] && [ -f "$last_err" ]; then
    cat "$last_err" >&2
  fi
  exit 1
}

id_a="11111111-1111-4111-8111-111111111111"
id_b="22222222-2222-4222-8222-222222222222"
id_c="33333333-3333-4333-8333-333333333333"
id_d="44444444-4444-4444-8444-444444444444"

mkdir -p "$projects/shop-api" "$projects/data-jobs" "$projects/shop-web" "$projects/old-jobs"

cat > "$projects/shop-api/$id_a.jsonl" << 'EOF'
{"type": "user", "message": {"content": "fix the invoice export"}, "cwd": "/work/shop-api", "timestamp": "2020-03-01T10:00:00Z"}
{"type": "assistant", "message": {"content": "looking at the export"}, "timestamp": "2020-03-01T10:05:00Z"}
EOF
cat > "$projects/data-jobs/$id_b.jsonl" << 'EOF'
{"type": "user", "message": {"content": "migrate the orders table"}, "cwd": "/work/data-jobs", "timestamp": "2020-03-01T11:00:00Z"}
{"type": "assistant", "message": {"content": "migration started"}, "timestamp": "2020-03-01T11:05:00Z"}
EOF
cat > "$projects/shop-web/$id_c.jsonl" << 'EOF'
{"type": "user", "message": {"content": "rebuild the checkout page"}, "cwd": "/work/shop-web", "timestamp": "2020-03-01T12:00:00Z"}
{"type": "assistant", "message": {"content": "layout drafted"}, "timestamp": "2020-03-01T12:05:00Z"}
EOF
cat > "$projects/old-jobs/$id_d.jsonl" << 'EOF'
{"type": "user", "message": {"content": "retire the old report"}, "cwd": "/work/old-jobs", "timestamp": "2020-01-01T09:00:00Z"}
{"type": "assistant", "message": {"content": "left as it was"}, "timestamp": "2020-01-01T09:05:00Z"}
EOF

hours_ago() {
  date -v-"$1"H +%Y%m%d%H%M.%S 2>/dev/null || date -d "$1 hours ago" +%Y%m%d%H%M.%S
}
days_ago() {
  date -v-"$1"d +%Y%m%d%H%M.%S 2>/dev/null || date -d "$1 days ago" +%Y%m%d%H%M.%S
}
touch -t "$(hours_ago 3)" "$projects/shop-api/$id_a.jsonl"
touch -t "$(hours_ago 2)" "$projects/data-jobs/$id_b.jsonl"
touch -t "$(hours_ago 1)" "$projects/shop-web/$id_c.jsonl"
touch -t "$(days_ago 3)" "$projects/old-jobs/$id_d.jsonl"

run() {
  _out=$1
  _err=$2
  last_err=$2
  shift 2
  set +e
  python3 "$tool" "$@" --projects "$projects" >"$_out" 2>"$_err"
  _code=$?
  set -e
  printf '%s\n' "$_code" > "$tmp/code"
}

code_of() {
  cat "$tmp/code"
}

last_line() {
  tail -n 1 "$1"
}

printf '%s\n' "part 1 desk"
run "$tmp/desk.out" "$tmp/desk.err" desk --night 2026-01-15 --add billing "draft the reply to the vendor"
cat "$tmp/desk.out"
[ "$(code_of)" -eq 0 ] || fail "part 1 desk failed"
grep -F -q "desk: 4 windows, 4 new" "$tmp/desk.out" || fail "part 1 did not list four windows"
grep -F -q "33333333  shop-web  rebuild the checkout page" "$tmp/desk.out" || fail "part 1 missed the checkout window"
grep -F -q "22222222  data-jobs  migrate the orders table" "$tmp/desk.out" || fail "part 1 missed the migration window"
grep -F -q "11111111  shop-api  fix the invoice export" "$tmp/desk.out" || fail "part 1 missed the invoice window"
grep -F -q "billing  -  draft the reply to the vendor" "$tmp/desk.out" || fail "part 1 missed the window added by hand"
grep -F -q "44444444" "$tmp/desk.out" && fail "part 1 listed the old session"
printf '\n'

printf '%s\n' "part 2 check"
run "$tmp/check.out" "$tmp/check.err" check
cat "$tmp/check.out"
[ "$(code_of)" -eq 1 ] || fail "part 2 check should fail"
grep -F -q "  no verdict: 33333333, 22222222, 11111111, billing" "$tmp/check.out" || fail "part 2 did not name every window under no verdict"
[ "$(last_line "$tmp/check.out")" = "BROKE AT: reckoning" ] || fail "part 2 last line was not the reckoning"
printf '\n'

printf '%s\n' "part 3 verdicts"
run "$tmp/r1.out" "$tmp/r1.err" reckon "$id_a" close "merged, nothing left"
[ "$(code_of)" -eq 0 ] || fail "part 3 close failed"
run "$tmp/r2.out" "$tmp/r2.err" reckon "$id_b" keep "the migration is mid-run" --you "approve the vendor quote"
[ "$(code_of)" -eq 0 ] || fail "part 3 keep failed"
run "$tmp/r3.out" "$tmp/r3.err" reckon "$id_c" handover "the context is full"
[ "$(code_of)" -eq 0 ] || fail "part 3 handover verdict failed"
run "$tmp/r4.out" "$tmp/r4.err" reckon billing close sent
[ "$(code_of)" -eq 0 ] || fail "part 3 hand-added close failed"
cat "$tmp/r1.out" "$tmp/r2.out" "$tmp/r3.out" "$tmp/r4.out"
run "$tmp/check.out" "$tmp/check.err" check
cat "$tmp/check.out"
[ "$(code_of)" -eq 1 ] || fail "part 3 check should fail"
grep -F -q "  no packet: 33333333" "$tmp/check.out" || fail "part 3 did not name the hand-over window under no packet"
[ "$(last_line "$tmp/check.out")" = "BROKE AT: handover" ] || fail "part 3 last line was not the handover"
printf '\n'

printf '%s\n' "part 4 handover"
run "$tmp/hand.out" "$tmp/hand.err" handover "$id_c" \
  --goal "ship the new checkout" \
  --done "the layout is in place" \
  --next "wire the new form to the API" \
  --evidence "notes in the shop repo"
cat "$tmp/hand.out"
[ "$(code_of)" -eq 0 ] || fail "part 4 handover failed"
cat > "$tmp/expect.txt" << 'EOF'
Taking over from 33333333, which was asked: rebuild the checkout page
Goal: ship the new checkout
Done: the layout is in place
Next: wire the new form to the API
Evidence: notes in the shop repo
EOF
cmp -s "$tmp/hand.out" "$tmp/expect.txt" || fail "part 4 did not print the five lines"
printf '\n'

printf '%s\n' "part 5 brief"
run "$tmp/brief.out" "$tmp/brief.err" brief
cat "$tmp/brief.out"
[ "$(code_of)" -eq 0 ] || fail "part 5 brief should stand"
grep -F -q "# Night shift 2026-01-15" "$tmp/brief.out" || fail "part 5 missed the page title"
run "$tmp/check.out" "$tmp/check.err" check
cat "$tmp/check.out"
[ "$(code_of)" -eq 0 ] || fail "part 5 check should stand"
[ "$(last_line "$tmp/check.out")" = "all four stages stand" ] || fail "part 5 last line did not say the stages stand"
printf '\n'

printf '%s\n' "part 6 next morning"
printf '%s\n' '{"type": "user", "message": {"content": "one more look"}, "timestamp": "2099-06-01T00:00:00Z"}' >> "$projects/shop-api/$id_a.jsonl"
run "$tmp/check.out" "$tmp/check.err" check
cat "$tmp/check.out"
[ "$(code_of)" -eq 1 ] || fail "part 6 check should fail"
grep -F -q "  used after: 11111111" "$tmp/check.out" || fail "part 6 did not name the closed window under used after"
[ "$(last_line "$tmp/check.out")" = "BROKE AT: handover" ] || fail "part 6 last line was not the handover"
run "$tmp/brief.out" "$tmp/brief.err" brief
cat "$tmp/brief.out"
[ "$(code_of)" -eq 1 ] || fail "part 6 brief should fail"
awk '
  index($0, "  used after: 11111111") { found = 1 }
  index($0, "## ") == 1 && !found { exit 1 }
  END { if (!found) exit 1 }
' "$tmp/brief.out" || fail "part 6 brief did not show used after at the top"
