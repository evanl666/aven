"""A client, the way a Tauri sidecar will be one.

    ANTHROPIC_API_KEY=... python examples/13_rpc.py

Deliberately dumb, and deliberately importing nothing from aven: a subprocess,
bytes in, lines out. That is the whole surface a window, a phone or somebody
else's script has to work against, so this file is also the check that the
surface is enough - if the protocol changes and this stops working, the change
was a breaking one.

It walks the path the approvals pane exists for: ask for two writes, have both
held back, read what is waiting, approve exactly one, and see that the other
never happened.

--protect is what makes the writes irreversible. The file tools are reversible on
purpose (a write carries its undo), so a policy rule is the portable way to get
something staged without reaching for Mail.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

AVEN = shutil.which("aven") or ".venv/bin/aven"


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("set ANTHROPIC_API_KEY first", file=sys.stderr)
        return 1

    box = Path(tempfile.mkdtemp(prefix="aven-rpc-"))
    print(f"working in {box}\n")

    agent = subprocess.Popen(
        [AVEN, "--mode", "rpc", "--root", str(box), "--protect", "ledger"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # stderr is commentary, never protocol. Read it for logs, never parse it.
        stderr=subprocess.DEVNULL,
    )

    inbox: queue.Queue[dict] = queue.Queue()
    threading.Thread(target=read_lines, args=(agent, inbox), daemon=True).start()

    def send(**command: object) -> None:
        line = json.dumps(command, ensure_ascii=False)
        print(f"→ {line}")
        assert agent.stdin is not None
        agent.stdin.write(line.encode("utf-8") + b"\n")
        agent.stdin.flush()

    def until(*types: str) -> dict:
        """Drain until one of `types` arrives.

        A response is NOT ordered against the events - a prompt's run is started
        before its response is written, so `agent_start` can arrive first. Real
        clients correlate by id; this one is a straight line and can just wait.
        """
        while True:
            record = inbox.get()
            if record["type"] in types:
                return record

    # 1. What am I looking at? One command is enough to draw a whole interface.
    send(id="1", type="state")
    state = until("response")["data"]
    print(f"\n   connectors: {[c['name'] for c in state['connectors']]}")
    print(f"   tools:      {len(state['tools'])}\n")

    # 2. Ask for two writes. --protect raises both, so both are held back.
    send(id="2", type="prompt",
         message="Create two files under ledger/: 2026-09.md saying 'September' "
                 "and 2026-10.md saying 'October'.")
    until("settled")
    print("\n   settled - the turn is over, and the process is still here\n")

    # 3. What is waiting. `id` is the field this whole format exists for.
    send(id="3", type="pending")
    waiting = until("response")["data"]["pending"]
    print(f"   {len(waiting)} waiting:")
    for entry in waiting:
        print(f"     {entry['id']}  {entry['preview']}")
        if entry["detail"]:
            print(f"     {'':14}detail.kind = {entry['detail']['kind']}")

    if not waiting:
        print("\n   nothing was staged - did --protect match?")
        return 1

    # 4. Approve exactly one. `ids: []` would approve none, which is not the
    #    same as leaving ids out entirely.
    send(id="4", type="approve", ids=[waiting[0]["id"]])
    result = until("response")["data"]
    print(f"\n   committed {len(result['committed'])}, "
          f"{len(result['tray']['pending'])} still waiting\n")

    send(id="5", type="shutdown")
    until("response")
    assert agent.stdin is not None
    agent.stdin.close()
    agent.wait(timeout=10)

    print("   on disk:")
    for path in sorted(box.rglob("*")):
        if path.is_file():
            print(f"     {path.relative_to(box)}")
    print("\n   Only the approved one exists. The other really did not happen.")
    return 0


def read_lines(agent: subprocess.Popen, inbox: queue.Queue) -> None:
    """Split on LF and nothing else.

    Not a generic line reader: some of them also split on U+2028 and U+2029,
    which are legal inside a JSON string. Node's `readline` is one.
    """
    assert agent.stdout is not None
    for raw in agent.stdout:
        line = raw.decode("utf-8").strip()
        if line:
            inbox.put(json.loads(line))


if __name__ == "__main__":
    raise SystemExit(main())
