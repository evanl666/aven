"""Step 6 demo: the same loop, now against the real Claude.

Everything below except the `Claude(...)` line existed and was tested before any
provider SDK was installed.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

from aven.harness.agent import run
from aven.harness.events import AgentEnd, MessageEnd, ToolEnd, ToolStart
from aven.harness.session import Session
from aven.harness.tools import ToolResult, tool
from aven.model import Claude
from aven.harness.tx import Tray

if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
    sys.exit("需要先设置 ANTHROPIC_API_KEY,例如:\n  export ANTHROPIC_API_KEY=sk-ant-...")

BOX = Path("/tmp/aven-demo/real")
shutil.rmtree(BOX, ignore_errors=True)
(BOX / "Downloads").mkdir(parents=True)
(BOX / "报销").mkdir()
for n in ("发票_滴滴_38元.pdf", "发票_美团_126元.pdf", "假期照片.jpg"):
    (BOX / "Downloads" / n).write_text("x")

SYSTEM = """你是 aven,一个跑在用户自己电脑上的个人助理。

你手上的工具分三类:只读的、可撤销的、不可逆的。不可逆的操作不会立刻发生,
它们会进入待批准队列,由用户最后统一确认。工具结果里说「staged」就表示这件事
还没发生,不要假装它已经完成。

做事直接,不要反复确认。做完后用一两句话说明你做了什么、还有什么在等用户批准。"""


@tool(risk="read")
def list_files(folder: Annotated[str, "要列出的文件夹,例如 Downloads"]) -> str:
    """列出一个文件夹里的所有文件。"""
    return ", ".join(sorted(p.name for p in (BOX / folder).iterdir())) or "(空)"


@tool(risk="reversible", preview="{src} → {dst}")
def move_file(
    src: Annotated[str, "源文件路径,例如 Downloads/发票.pdf"],
    dst: Annotated[str, "目标路径,例如 报销/发票.pdf"],
) -> ToolResult:
    """把一个文件移到别的文件夹。"""
    source, target = BOX / src, BOX / dst
    shutil.move(source, target)
    return ToolResult(output=f"moved {src} → {dst}", undo=lambda: shutil.move(target, source))


@tool(risk="irreversible", preview="发邮件给 {to}:{subject}")
def send_mail(
    to: Annotated[str, "收件人邮箱"],
    subject: Annotated[str, "邮件主题"],
    body: Annotated[str, "邮件正文"] = "",
) -> str:
    """发一封邮件。发出去就收不回来了。"""
    return f"sent to {to}"


TOOLS = [list_files, move_file, send_mail]

session = Session.open("/tmp/aven-demo/real.jsonl")
tray = Tray()
model = Claude(tools=TOOLS, system=SYSTEM)

PROMPT = "看看我 Downloads 里有哪些发票,归档到 报销/,然后给 finance@corp.com 发邮件汇总一下金额。"
print(f"👤 {PROMPT}\n")

for ev in run(session=session, prompt=PROMPT, model=model, tools=TOOLS, tray=tray, max_turns=8):
    match ev:
        case MessageEnd() if ev.message.kind == "assistant" and ev.message.text:
            print(f"🤖 {ev.message.text}")
        case ToolStart():
            print(f"   🔧 {ev.call.name}({ev.call.args})")
        case ToolEnd():
            print(f"      {'⏸' if ev.staged else '✓'} {ev.result.output[:90]}")
        case AgentEnd():
            print(f"\n■ {ev.reason} · {model.usage}")

print(f"\nDownloads/ = {sorted(p.name for p in (BOX / 'Downloads').iterdir())}")
print(f"报销/      = {sorted(p.name for p in (BOX / '报销').iterdir())}")
print(f"\n{tray.diff()}")
print("\n→ 邮件还没发。commit 才会发:tray.commit()")
