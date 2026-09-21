"""Step 9 demo: streaming text and a spinner while nothing is visible yet."""

import time
from dataclasses import replace
from pathlib import Path

from aven.cli.render import Renderer, render_tray
from aven.core.agent import run
from aven.core.messages import AssistantMessage as AM, ToolCall, new_id
from aven.core.session import Session
from aven.core.tools import ToolResult, tool
from aven.tx import Tray


@tool(risk="read")
def list_dir(path: str = ".") -> str:
    """列目录。"""
    time.sleep(1.2)                       # 假装它慢
    return "发票_滴滴.pdf, 发票_美团.pdf, 假期照片.jpg"


@tool(risk="irreversible", preview="发邮件给 {to}")
def send_mail(to: str, subject: str) -> str:
    """发邮件。"""
    return f"sent to {to}"


TURNS = [
    (2.0, ["我先", "看看 ", "Downloads ", "里有", "什么。"],
     AM(text="我先看看 Downloads 里有什么。",
        tool_calls=[ToolCall(name="list_dir", args={"path": "Downloads"})],
        stop_reason="tool_use")),
    (1.5, ["两张", "发票,", "一共 ", "164 ", "元。", "我把", "汇总", "发给", "财务。"],
     AM(text="两张发票,一共 164 元。我把汇总发给财务。",
        tool_calls=[ToolCall(name="send_mail", args={"to": "finance@corp.com", "subject": "9月报销"})],
        stop_reason="tool_use")),
    (1.0, ["邮件", "已经", "写好,", "等你", "确认。"],
     AM(text="邮件已经写好,等你确认。")),
]


def model(_):
    think, chunks, reply = TURNS.pop(0)
    time.sleep(think)                     # 模型在想,屏幕上什么都没有
    for chunk in chunks:
        time.sleep(0.09)
        yield chunk
    return replace(reply, id=new_id())


path = Path("/tmp/aven-demo/stream.jsonl")
path.unlink(missing_ok=True)
session, tray = Session.open(path), Tray()
screen = Renderer(verbose=True)
screen.waiting("思考中")

for event in run(session=session, prompt="整理发票并汇总给财务",
                 model=model, tools=[list_dir, send_mail], tray=tray):
    screen.handle(event)

render_tray(tray)
