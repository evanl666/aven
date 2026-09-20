"""Step 5 demo: the agent finishes its work without sending anything."""

import shutil
from dataclasses import replace
from pathlib import Path

from aven.core.agent import run
from aven.core.events import MessageEnd, ToolEnd
from aven.core.messages import AssistantMessage, AssistantMessage as AM, ToolCall, new_id
from aven.core.session import Session
from aven.core.tools import ToolResult, tool
from aven.tx import Tray

BOX = Path("/tmp/aven-demo/tray")
shutil.rmtree(BOX, ignore_errors=True)
(BOX / "Downloads").mkdir(parents=True)
(BOX / "报销").mkdir()
for n in ("发票_滴滴.pdf", "发票_美团.pdf"):
    (BOX / "Downloads" / n).write_text("PDF")

SENT: list[str] = []


@tool(risk="reversible", preview="{src} → {dst}")
def move_file(src: str, dst: str) -> ToolResult:
    """Move a file to another folder."""
    source, target = BOX / src, BOX / dst
    shutil.move(source, target)
    return ToolResult(output=f"moved {src}", undo=lambda: shutil.move(target, source))


@tool(risk="irreversible", preview="发邮件给 {to}:{subject}")
def send_mail(to: str, subject: str) -> str:
    """Send an email. There is no taking this back."""
    SENT.append(to)
    return f"sent to {to}"


TOOLS = [move_file, send_mail]

calls = [
    ToolCall(name="move_file", args={"src": "Downloads/发票_滴滴.pdf", "dst": "报销/发票_滴滴.pdf"}),
    ToolCall(name="move_file", args={"src": "Downloads/发票_美团.pdf", "dst": "报销/发票_美团.pdf"}),
    ToolCall(name="send_mail", args={"to": "finance@corp.com", "subject": "9 月报销"}),
]
script = [
    AM(text="我归档发票并发给财务。", tool_calls=calls, stop_reason="tool_use"),
    AM(text="两张发票已归档。邮件我准备好了,等你确认再发。"),
]

path = Path("/tmp/aven-demo/tray.jsonl")
path.unlink(missing_ok=True)
session = Session.open(path)
tray = Tray()

print("═══ agent 跑起来 ═══")
for ev in run(
    session=session,
    prompt="把发票归档,然后发给财务",
    model=lambda _: replace(script.pop(0), id=new_id()),
    tools=TOOLS,
    tray=tray,
):
    match ev:
        case ToolEnd():
            print(f"  {'⏸ 暂存' if ev.staged else '✓ 已做'}  {ev.result.output}")
        case MessageEnd() if getattr(ev.message, "text", ""):
            if ev.message.kind == "assistant":
                print(f"  助理: {ev.message.text}")

print(f"\n邮件发出去了吗? {SENT or '一封都没有'}")
print(f"报销/ = {sorted(p.name for p in (BOX / '报销').iterdir())}")

print(f"\n═══ 给用户看的「生活的 diff」 ═══\n{tray.diff()}")

print("\n═══ 路径 A:用户点【全部提交】 ═══")
tray.commit()
print(f"  邮件发出去了吗? {SENT}")
print(f"  {tray.diff()}")

print("\n═══ 路径 B:用户改主意,点【撤销】 ═══")
rolled = tray.undo()
print(f"  回滚了 {len(rolled)} 项(倒序)")
print(f"  报销/     = {sorted(p.name for p in (BOX / '报销').iterdir())}")
print(f"  Downloads/ = {sorted(p.name for p in (BOX / 'Downloads').iterdir())}")
print(f"\n  对话也要一起回退 → session.checkout({tray.rewind_point()[:6]}…)")
