"""Step 14 demo: told once in one session, known at the start of the next."""

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path

from aven.harness.agent import run
from aven.harness.context import find, read
from aven.harness.messages import AssistantMessage, ToolCall, new_id
from aven.harness.session import Session
from aven.toolkit import file_tools, memory_tools

BOX = Path("/tmp/aven-demo/memory")
shutil.rmtree(BOX, ignore_errors=True)
(BOX / "Downloads").mkdir(parents=True)
(BOX / "AVEN.md").write_text("# 我的规矩\n\n- 邮件写短一点\n")

TOOLS = file_tools(BOX) + memory_tools(BOX)

SCRIPT = [
    ("好的,我记下来。", [
        ToolCall(name="remember", args={"fact": "财务邮箱是 finance@corp.com", "scope": "here"}),
        ToolCall(name="remember", args={"fact": "发票归到 报销/YYYY-MM/", "scope": "here"}),
    ]),
    ("记住了:财务是 finance@corp.com,发票归到 报销/YYYY-MM/。", []),
]


async def model(_):
    text, calls = SCRIPT.pop(0)
    yield text
    yield AssistantMessage(id=new_id(), text=text,
                           tool_calls=[replace(c, id=new_id()) for c in calls],
                           stop_reason="tool_use" if calls else "end_turn")


async def main():
    print("═══ 第一次会话:告诉它两件事 ═══")
    print("👤 我们财务是 finance@corp.com,发票以后归到 报销/YYYY-MM/\n")

    path = Path("/tmp/aven-demo/memory.jsonl")
    path.unlink(missing_ok=True)
    async for _event in run(
        session=Session.open(path),
        prompt="我们财务是 finance@corp.com,发票以后归到 报销/YYYY-MM/",
        model=model,
        tools=TOOLS,
    ):
        pass

    print("AVEN.md 现在是:\n")
    print("\n".join("  " + line for line in (BOX / "AVEN.md").read_text().splitlines()))

    print("\n\n═══ 第二次会话(全新的,没有 -c)═══")
    print("启动时读到的指示,直接进 system prompt:\n")
    print("\n".join("  " + line for line in read(find(BOX)).splitlines()))
    print("\n👤 把发票整理一下  →  它已经知道归到哪、发给谁了")


asyncio.run(main())
