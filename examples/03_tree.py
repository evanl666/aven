"""Step 2b demo: where each Session method actually gets used."""

from pathlib import Path

from aven.harness.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage, to_llm
from aven.harness.session import Session

path = Path("/tmp/aven-demo/tree.jsonl")
path.unlink(missing_ok=True)


def tree(s: Session, node_id=None, prefix=""):
    """Draw the branch picker. This is the only reason children() exists."""
    kids = s.children(node_id)
    for i, m in enumerate(kids):
        last = i == len(kids) - 1
        text = getattr(m, "text", "") or f"[{m.tool_name}] {m.output}"
        mark = "  ← 你在这" if m.id == s.head else ""
        print(f"{prefix}{'└─' if last else '├─'}{m.kind[:1]} {text[:34]}{mark}")
        tree(s, m.id, prefix + ("   " if last else "│  "))


s = Session.open(path)                                    # ① 打开对话

ask = s.append(UserMessage(text="整理一下我下载目录里的发票"))   # ② 用户说话
call = ToolCall(name="find_files", args={"dir": "~/Downloads"})
s.append(AssistantMessage(text="先看看有哪些。", tool_calls=[call]))
s.append(ToolResultMessage(tool_call_id=call.id, tool_name="find_files", output="3 个 PDF"))
bad = s.append(AssistantMessage(text="我把这 3 个文件删掉了。"))   # ③ 助理干了蠢事

print("═══ ③ 之后,发给模型的是什么 ═══")
for m in to_llm(s.history()):                             # ④ history() 的主用途
    print(" ", str(m)[:76])

print("\n═══ 用户:不对,重来 ═══")
s.checkout(ask.id)                                        # ⑤ 退回到问题那一步
s.append(AssistantMessage(text="我先列个清单给你确认。"))      # ⑥ 走另一条路

print("现在发给模型的变成:")
for m in to_llm(s.history()):
    print(" ", str(m)[:76])
print("→ 注意:删文件那 3 条完全不在里面了,但它们还在文件里")

print("\n═══ 用户想回头看:『我刚才试过哪几种做法?』 ═══")
tree(s)                                                   # ⑦ children() 画树

print("\n═══ 有几条分支可选? ═══")
for i, leaf in enumerate(s.leaves(), 1):                  # ⑧ leaves() 列分支
    print(f"  [{i}] ...{getattr(leaf, 'text', '')[:30]}")

print(f"\n文件 {len(path.read_text().splitlines())} 行 · 树里 {len(s)} 条 · 当前路径 {len(s.history())} 条")
