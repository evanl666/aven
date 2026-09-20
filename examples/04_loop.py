"""Step 3 demo: the loop running against a scripted model and real tools."""

from pathlib import Path

from aven.core.agent import run
from aven.core.events import AgentEnd, AgentStart, MessageEnd, ToolEnd, ToolStart, TurnStart
from aven.core.messages import AssistantMessage, ToolCall
from aven.core.session import Session

# --- the tools -------------------------------------------------------------

FILES = {"发票_滴滴.pdf": 1, "发票_美团.pdf": 1, "猫.jpg": 0}


def find_files(dir: str, glob: str = "*") -> str:
    hits = [n for n in FILES if n.endswith(glob.lstrip("*"))]
    return f"{len(hits)} 个: " + ", ".join(hits)


def read_calendar(range: str) -> str:
    raise RuntimeError("Calendar.app 没有授权")      # 故意让它失败


# --- a scripted model ------------------------------------------------------

SCRIPT = [
    AssistantMessage(
        text="我先看看有哪些文件,顺便看下日程。",
        tool_calls=[
            ToolCall(name="find_files", args={"dir": "~/Downloads", "glob": "*.pdf"}),
            ToolCall(name="read_calendar", args={"range": "week"}),
        ],
        stop_reason="tool_use",
    ),
    AssistantMessage(text="找到 2 张发票。日历没权限,我跳过了。"),
]


def fake_model(llm_messages):
    print(f"      [模型收到 {len(llm_messages)} 条消息]")
    return SCRIPT.pop(0)


# --- run -------------------------------------------------------------------

path = Path("/tmp/aven-demo/loop.jsonl")
path.unlink(missing_ok=True)
session = Session.open(path)

for ev in run(
    session=session,
    prompt="我下载目录里有几张发票?",
    model=fake_model,
    tools={"find_files": find_files, "read_calendar": read_calendar},
):
    match ev:
        case AgentStart():
            print(f"▶ 开始: {ev.prompt}")
        case TurnStart():
            print(f"  ── 第 {ev.index} 轮 ──")
        case MessageEnd():
            text = getattr(ev.message, "text", None)
            if text:
                print(f"    {ev.message.kind}: {text}")
        case ToolStart():
            print(f"    🔧 {ev.call.name}({ev.call.args})")
        case ToolEnd():
            mark = "✗" if ev.result.is_error else "✓"
            print(f"       {mark} {ev.result.output}")
        case AgentEnd():
            print(f"■ 结束: {ev.reason}")

print(f"\n会话文件 {len(path.read_text().splitlines())} 行")
