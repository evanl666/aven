"""Step 2b demo: one file, two branches, no rewrites."""

from pathlib import Path

from aven.harness.messages import AssistantMessage, UserMessage
from aven.harness.session import Session

path = Path("/tmp/aven-demo/demo.jsonl")
path.unlink(missing_ok=True)

s = Session.open(path)

ask = s.append(UserMessage(text="整理一下我下载目录里的发票"))
plan_a = s.append(AssistantMessage(text="我直接把它们移到 ~/Documents/2026-报销/。"))

print("分支 A 的对话:")
for m in s.history():
    print(f"  {m.kind:<10} {m.text}")

# Go back to the question and answer it differently.
s.checkout(ask.id)
plan_b = s.append(AssistantMessage(text="我先列个清单给你看,你确认了我再动。"))

print("\n分支 B 的对话:")
for m in s.history():
    print(f"  {m.kind:<10} {m.text}")

print(f"\n树里一共 {len(s)} 条消息,但每条路径只有 2 条")
print(f"问题这个节点有 {len(s.children(ask.id))} 个孩子:")
for c in s.children(ask.id):
    print(f"  - {c.text}")

print(f"\n分支末端 (leaves): {[m.id for m in s.leaves()]}")

print("\n回到分支 A:")
s.checkout(plan_a.id)
print(f"  {s.history()[-1].text}")

print(f"\n文件里有 {len(path.read_text().splitlines())} 行 —— 一行都没被改写")
