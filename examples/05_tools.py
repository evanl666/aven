"""Step 4 demo: schema from the signature, preview before, undo after."""

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Annotated

from aven.core.agent import run
from aven.core.events import ToolEnd, ToolStart
from aven.core.messages import AssistantMessage, ToolCall, new_id
from aven.core.session import Session
from aven.core.tools import ToolResult, tool
from aven.tx import Tray

# --- a sandbox with real files ---------------------------------------------

BOX = Path("/tmp/aven-demo/box")
shutil.rmtree(BOX, ignore_errors=True)
(BOX / "Downloads").mkdir(parents=True)
(BOX / "报销").mkdir()
for n in ("发票_滴滴.pdf", "发票_美团.pdf"):
    (BOX / "Downloads" / n).write_text("PDF")


# --- three tools, three risk levels ----------------------------------------


@tool(risk="read")
def list_files(
    folder: Annotated[str, "Folder to list, relative to the home directory"],
) -> str:
    """List the files in a folder."""
    return ", ".join(sorted(p.name for p in (BOX / folder).iterdir()))


@tool(risk="reversible", preview="{src} → {dst}")
def move_file(
    src: Annotated[str, "Current path of the file"],
    dst: Annotated[str, "Where to move it"],
) -> ToolResult:
    """Move a file to another folder."""
    source, target = BOX / src, BOX / dst
    shutil.move(source, target)
    return ToolResult(output=f"moved {src}", undo=lambda: shutil.move(target, source))


@tool(risk="irreversible", preview="发邮件给 {to}:{subject}")
def send_mail(to: str, subject: str, body: str = "") -> str:
    """Send an email. There is no taking this back."""
    return f"sent to {to}"


TOOLS = [list_files, move_file, send_mail]

# --- ① what the model is told ----------------------------------------------

print("═══ ① 发给模型的工具定义(从函数签名自动生成)═══")
print(json.dumps(move_file.for_model(), indent=2, ensure_ascii=False))
print("\n风险等级:")
for t in TOOLS:
    print(f"  {t.name:<12} {t.risk}")

# --- ② run it --------------------------------------------------------------

calls = [
    ToolCall(name="move_file", args={"src": "Downloads/发票_滴滴.pdf", "dst": "报销/发票_滴滴.pdf"}),
    ToolCall(name="move_file", args={"src": "Downloads/发票_美团.pdf", "dst": "报销/发票_美团.pdf"}),
]
script = [
    AssistantMessage(text="我把两张发票归档。", tool_calls=calls, stop_reason="tool_use"),
    AssistantMessage(text="归档完成。"),
]
model = lambda _: replace(script.pop(0), id=new_id())

path = Path("/tmp/aven-demo/tools.jsonl")
path.unlink(missing_ok=True)
session = Session.open(path)

print(f"\n═══ ② 执行前 报销/ = {sorted(p.name for p in (BOX / '报销').iterdir())} ═══")

tray = Tray()
by_name = {t.name: t for t in TOOLS}
for ev in run(session=session, prompt="把发票归档", model=model, tools=TOOLS, tray=tray):
    match ev:
        case ToolStart():
            t = by_name[ev.call.name]
            print(f"  [{t.risk}] {t.preview(ev.call.args)}")     # ← 执行之前
        case ToolEnd():
            print(f"      ✓ {ev.result.output}")

print(f"\n执行后 报销/    = {sorted(p.name for p in (BOX / '报销').iterdir())}")
print(f"       Downloads/ = {sorted(p.name for p in (BOX / 'Downloads').iterdir())}")

# --- ③ undo ----------------------------------------------------------------

print(f"\n═══ ③ 用户点了撤销({len(tray.undoable())} 个可撤销操作)═══")
tray.undo()

print(f"撤销后 报销/    = {sorted(p.name for p in (BOX / '报销').iterdir())}")
print(f"       Downloads/ = {sorted(p.name for p in (BOX / 'Downloads').iterdir())}")
