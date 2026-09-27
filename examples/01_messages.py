"""Step 1 demo: what aven stores vs what the model sees."""

import json

from aven.harness.messages import (
    AssistantMessage,
    NoteMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    to_llm,
)

# A turn opened by a trigger, not by a human typing.
turn = UserMessage(text="整理一下我下载目录里的发票", source="trigger:cron")

note = NoteMessage(parent_id=turn.id, text="matched routine: weekly-expenses", level="info")

call_a = ToolCall(name="find_files", args={"dir": "~/Downloads", "glob": "*.pdf"})
call_b = ToolCall(name="read_calendar", args={"range": "this_week"})
reply = AssistantMessage(
    parent_id=note.id,
    text="先看看有哪些文件。",
    tool_calls=[call_a, call_b],
    stop_reason="tool_use",
)

res_a = ToolResultMessage(
    parent_id=reply.id, tool_call_id=call_a.id, tool_name="find_files", output="3 files"
)
res_b = ToolResultMessage(
    parent_id=res_a.id, tool_call_id=call_b.id, tool_name="read_calendar", output="no events"
)

stored = [turn, note, reply, res_a, res_b]

print(f"stored in session : {len(stored)} messages")
for m in stored:
    print(f"  {m.kind:<12} {m.id}  parent={m.parent_id}")

seen = to_llm(stored)
print(f"\nsent to the model : {len(seen)} messages")
print(json.dumps(seen, indent=2, ensure_ascii=False))
