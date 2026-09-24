"""Step 12 demo: the conversation gets shorter, the file does not."""

from dataclasses import replace
from pathlib import Path

from aven.core.agent import run
from aven.core.compact import Compactor, estimate_tokens
from aven.core.events import MessageEnd
from aven.core.messages import (
    AssistantMessage, SummaryMessage, ToolCall, ToolResultMessage, UserMessage,
    new_id, to_llm,
)
from aven.core.session import Session
from aven.core.tools import tool


@tool()
def read_file(path: str) -> str:
    """读文件。"""
    return "内容" * 400


async def summariser(_):
    return AssistantMessage(
        text="用户在整理 ~/报销 目录。已归档 6 个月的发票,财务邮箱 finance@corp.com,"
             "合计 12,480 元。上个月的两张还没找到。"
    )


path = Path("/tmp/aven-demo/compact.jsonl")
path.unlink(missing_ok=True)
session = Session.open(path)

# 先攒出一段很长的历史
for n in range(8):
    session.append(UserMessage(text=f"看看 {n} 月的发票"))
    call = ToolCall(name="read_file", args={"path": f"报销/{n}月.md"})
    session.append(AssistantMessage(tool_calls=[call], stop_reason="tool_use"))
    session.append(ToolResultMessage(tool_call_id=call.id, tool_name="read_file",
                                     output="内容" * 400))
    session.append(AssistantMessage(text=f"{n} 月一共 1560 元。"))

before_path = len(session.history())
before_tokens = estimate_tokens(to_llm(session.history()))
print(f"压缩前:路径上 {before_path} 条消息 · 估算 {before_tokens:,} token")

reply = AssistantMessage(text="好的,我继续。")
events = []


async def main():
    async for event in run(
        session=session,
        prompt="继续看 8 月的",
        model=lambda _: replace(reply, id=new_id()),
        tools=[read_file],
        compactor=Compactor(model=summariser, limit=5_000, keep_turns=2),
    ):
        events.append(event)

    summary = next(
        e.message for e in events
        if isinstance(e, MessageEnd) and isinstance(e.message, SummaryMessage)
    )

    after_tokens = estimate_tokens(to_llm(session.history()))
    print(f"压缩后:路径上 {len(session.history())} 条消息 · 估算 {after_tokens:,} token"
          f"  ({after_tokens / before_tokens:.0%})")
    print(f"文件里:{len(path.read_text(encoding='utf-8').splitlines())} 行 —— 一条都没少")
    print(f"\n摘要覆盖了 {len(summary.covers)} 条消息:")
    print(f"  「{summary.text}」")

    print("\n发给模型的投影现在长这样:")
    for message in to_llm(session.history()):
        kinds = "+".join(b["type"] for b in message["content"])
        head = next((b.get("text", "") for b in message["content"] if "text" in b), "")
        print(f"  {message['role']:<10} [{kinds}]  {head[:52]}")


import asyncio
asyncio.run(main())
