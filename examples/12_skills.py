"""Step 15 demo: ten skills cost ten lines, not ten documents."""

import shutil
from pathlib import Path

from aven.core.compact import estimate_tokens
from aven.core.skills import catalogue, find, read
from aven.tools import skill_tools

BOX = Path("/tmp/aven-demo/skilldemo")
shutil.rmtree(BOX, ignore_errors=True)

BODY = """---
name: {name}
description: {description}
---

# {name}

{steps}
"""

CATALOGUE = [
    ("报销整理", "把发票归档并生成清单。用户提到发票、报销、月度汇总时用这个。",
     "1. list_dir 看 Downloads\n2. 按 references/规则.md 的格式归档\n3. 生成 Markdown 清单,合计放最后\n" * 12),
    ("周报", "从笔记目录汇总这周做了什么。用户说周报、总结、汇报时用这个。",
     "1. 读 笔记/ 下本周的文件\n2. 按项目分组\n3. 每组三行以内\n" * 12),
    ("会议准备", "开会前把相关材料找齐。用户提到会议、准备、议程时用这个。",
     "1. list_events 看时间和参会人\n2. spotlight 搜相关文件\n3. 汇总成一页\n" * 12),
]

for name, description, steps in CATALOGUE:
    home = BOX / ".aven" / "skills" / name
    (home / "references").mkdir(parents=True)
    (home / "SKILL.md").write_text(BODY.format(name=name, description=description, steps=steps))
    (home / "references" / "规则.md").write_text("文件名格式:发票_商户_金额元.pdf\n")

found = find(BOX)
listed = catalogue(found)
bodies = sum(len(read(s)) for s in found)

print("═══ 每轮都发的(system prompt 里)═══\n")
print("\n".join("  " + l for l in listed.splitlines()))
print(f"\n  {len(listed)} 字符 ≈ {estimate_tokens([listed])} token")
print(f"\n═══ 全部技能的正文加起来 ═══\n  {bodies} 字符 ≈ {estimate_tokens([' ' * bodies])} token")
print(f"\n  省下 {(1 - len(listed) / bodies):.0%} —— 只有用到的那个才会被取")

load = skill_tools(found)[0]
print("\n═══ 模型决定用「周报」时才调 load_skill ═══\n")
print("\n".join("  " + l for l in load(name="周报").output.splitlines()[:8]))
print("  …")
