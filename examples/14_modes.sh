#!/bin/sh
# The four interfaces, given the same task, so the differences are visible.
#
#     ANTHROPIC_API_KEY=... sh examples/14_modes.sh
#
# They all use stdin and stdout, which is exactly why the difference is easy to
# miss. It is not the pipe - it is who reads stdin, what is in it, and how long
# the process lives.
#
#   text        a person types sentences        output for eyes      until Ctrl-D
#   -p          nothing (or piped material)     the final answer     one shot
#   --mode json NOTHING - stdin is ignored      events as JSONL      one shot
#   --mode rpc  another program, JSONL commands responses + events   until stdin closes
#
# The one that matters for a window is the third column of the last two rows.
# Both speak JSONL, but only rpc listens - and only rpc is still running
# afterwards, which is the whole point: a staged action carries an unfired
# closure over live objects. It cannot be serialised and it cannot outlive its
# process. In json mode "1 pending" is an obituary. In rpc it is a button.

set -e
AVEN=${AVEN:-.venv/bin/aven}
TASK="Create ledger/tally.md containing the word September."

[ -n "$ANTHROPIC_API_KEY" ] || { echo "set ANTHROPIC_API_KEY first" >&2; exit 1; }

box() { rm -rf "$1"; mkdir -p "$1"; }
shown() {
    if [ -n "$(find "$1" -type f 2>/dev/null)" ]; then
        find "$1" -type f | sed "s|$1/||" | sed 's/^/     /'
    else
        echo "     (nothing - the write never happened)"
    fi
}

# --- -p ----------------------------------------------------------------------
# One shot, and only the answer on stdout. What a cron line or a shortcut wants.
echo "=== -p ======================================================="
box /tmp/aven-p
$AVEN -p "$TASK" --root /tmp/aven-p --protect ledger 2>/dev/null
echo "   on disk:"; shown /tmp/aven-p

# --- --mode json -------------------------------------------------------------
# One shot, every event as a line. Note the last record: it reports what is
# waiting, and then the process exits and the staged call is gone.
echo
echo "=== --mode json =============================================="
box /tmp/aven-json
# Deliberately feeding it a command, to show it is ignored.
echo '{"type":"approve"}' |
    $AVEN --mode json "$TASK" --root /tmp/aven-json --protect ledger 2>/dev/null |
    while IFS= read -r line; do
        printf '%s\n' "$line" | python3 -c '
import json, sys
r = json.load(sys.stdin)
if r["type"] == "tool_end":
    print(f"   tool_end  {r[\"call\"][\"name\"]}  staged={r[\"staged\"]}")
elif r["type"] == "tray":
    print(f"   tray      pending={len(r[\"pending\"])}")
elif r["type"] in ("agent_start", "agent_end"):
    print(f"   {r[\"type\"]}")
'
    done
echo "   on disk:"; shown /tmp/aven-json
echo "   (the {\"type\":\"approve\"} we piped in was never read)"

# --- --mode rpc --------------------------------------------------------------
# The same task, but a client can answer. See examples/13_rpc.py for the client.
echo
echo "=== --mode rpc ==============================================="
python3 examples/13_rpc.py

echo
echo "The difference is not the pipe. It is that rpc is still there to be"
echo "answered, and that the tray it is holding is still real."
