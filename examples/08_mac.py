"""Step 8 demo: the tray finally matters.

Runs with osascript faked out, so nothing reaches Mail or Calendar. Pass --real
to let it through - it will ask macOS for permission, and the send still waits
for you.
"""

import sys
from dataclasses import replace
from pathlib import Path

from aven.actuators import mac, mac_tools
from aven.cli.render import Renderer, render_tray
from aven.core.agent import run
from aven.core.messages import AssistantMessage as AM, ToolCall, new_id
from aven.core.session import Session
from aven.tx import Tray

REAL = "--real" in sys.argv

if not REAL:
    FAKES = {"CALENDARS": "工作\n个人", "DRAFT": "4242", "DROP_DRAFT": "deleted", "SEND": "sent"}

    def fake(command, timeout):
        script = command[2]
        for name, reply in FAKES.items():
            if getattr(mac, name, None) == script:
                print(f"       [假装执行 {name}] ← 参数: {command[4:]}")
                return reply
        return ""

    mac._run = fake
    print("── osascript 已被替换成假的,不会碰你的邮件 ──\n")

TOOLS = mac_tools()
calls = [
    ToolCall(name="list_calendars", args={}),
    ToolCall(name="draft_mail", args={"to": "finance@corp.com", "subject": "9月报销",
                                      "body": "滴滴 38,美团 126,合计 164。"}),
    ToolCall(name="send_mail", args={"to": "finance@corp.com", "subject": "9月报销",
                                     "body": "滴滴 38,美团 126,合计 164。"}),
]
script = [
    AM(text="我先看看有哪些日历,然后把报销邮件写好。", tool_calls=calls, stop_reason="tool_use"),
    AM(text="草稿已经在 Drafts 里了。发送在等你确认。"),
]

path = Path("/tmp/aven-demo/mac.jsonl")
path.unlink(missing_ok=True)
session = Session.open(path)
tray = Tray()
screen = Renderer(verbose=True)

for ev in run(
    session=session,
    prompt="把 9 月报销汇总发给财务",
    model=lambda _: replace(script.pop(0), id=new_id()),
    tools=TOOLS,
    tray=tray,
):
    screen.handle(ev)

render_tray(tray)
print("\n→ 草稿已经写了(可撤销),发送还没发生(等确认)")
print("→ tray.commit() 才会真的发出去;tray.discard() 则从未发生过")
