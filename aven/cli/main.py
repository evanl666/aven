"""The aven command.

    aven "把下载目录里的发票整理一下"      one task, then review
    aven                                     the full-screen app
    aven -c                                  the app, continuing the last session
    aven --plain                             keep talking, line by line

A client of the loop and nothing more. It owns the terminal; the loop owns the
work; the tray owns what actually takes effect.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import sys
import time
from pathlib import Path

from aven.cli.render import BOLD, DIM, RED, Renderer
from aven.cli.review import review
from aven.core.agent import run
from aven.core.compact import Compactor, estimate_tokens
from aven.core.context import find, read
from aven.core.skills import catalogue
from aven.core.toolbox import ToolBox
from aven.core.skills import find as find_skills
from aven.core.session import Session
from aven.model import Claude
from aven.actuators import mac_tools
from aven.tools import file_tools, memory_tools, skill_tools
from aven.tx import Tray, bulk, guard, protect

SESSIONS = Path.home() / ".aven" / "sessions"

# What each dormant group is for. The model reads this to decide whether a task
# needs the group, so it says when, not only what.
GROUPS = {
    "memory": "记住/忘掉关于用户的长期事实。用户说了下次还用得上的事时拿这组。",
    "calendar": "看日程、建日程。用户提到会议、日程、几号几点时拿这组。",
    "mail": "写草稿、发邮件。用户要发东西给别人时拿这组。",
    "search": "Spotlight 全盘搜索。要找的文件不在当前目录里时拿这组。",
}

SYSTEM = """你是 aven,一个运行在用户自己电脑上的个人助理。

你只能在一个目录范围内操作,路径都相对于它。越界的请求会被工具拒绝。

文件类工具的路径都相对于那个目录。日历、邮件和 Spotlight 由 macOS 管,
不受它限制,但 macOS 会自己向用户要授权。

你的工具分三类:
- 只读的,随时可以用
- 可撤销的,直接做就行,用户随时能退回去
- 不可逆的,不会立刻发生,会进入待确认队列

工具结果里出现 "staged" 就表示那件事【还没有发生】。不要当成已完成,
也不要围着它继续推理,把剩下能做的做完,然后告诉用户有什么在等他确认。

不是所有工具一开始就在。目录里列出的那些组,需要时用 use_tools 拿进来,
一次拿一组,拿了就一直在。

你有 remember / forget 两个工具(在 memory 组里),用来记住【下次还用得上】的事:人、地址、
文件夹约定、用户的偏好。不要用它记当前任务的细节——那些对话里已经有了。
用户纠正你的时候,先 forget 旧的再 remember 新的。

直接做事,不要反复请示。做完用一两句话说清楚你做了什么。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aven", description="本地优先的个人助理")
    parser.add_argument("prompt", nargs="?", help="要它做的事。省略则进入对话模式")
    parser.add_argument("-c", "--continue", dest="resume", action="store_true",
                        help="接着最近一次会话")
    parser.add_argument("--session", type=Path, help="指定会话文件")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="文件工具允许操作的目录,默认是当前目录")
    parser.add_argument("--mac", action="store_true",
                        help="打开日历 / 邮件 / Spotlight(macOS 会向你申请权限)")
    parser.add_argument("--model", default=os.environ.get("AVEN_MODEL"),
                        help="模型 id,也可用 AVEN_MODEL 环境变量设定")
    parser.add_argument("--no-cache", dest="cache", action="store_false",
                        help="关掉 prompt 缓存(调试用)")
    parser.add_argument("--no-instructions", dest="instructions", action="store_false",
                        help="不加载 AVEN.md / AGENTS.md")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--bulk", type=int, default=25,
                        help="一次运行改动超过这么多处,就先让你看一眼")
    parser.add_argument("--protect", action="append", default=[], metavar="片段",
                        help="路径里含这个片段就要确认,可以重复给")
    parser.add_argument("--reserve", type=int, default=24_000,
                        help="给回复和下一轮增长留出的 token 余量")
    parser.add_argument("--no-compact", dest="compact", action="store_false",
                        help="关掉自动压缩")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示每个工具的结果")
    parser.add_argument("--plain", action="store_true",
                        help="不用全屏界面,一行一行地对话")
    parser.add_argument("--yes", action="store_true",
                        help="跳过确认,直接提交(自动化用,慎用)")
    return parser


def pick_session(args: argparse.Namespace) -> Path:
    if args.session:
        return args.session
    SESSIONS.mkdir(parents=True, exist_ok=True)
    if args.resume:
        existing = sorted(SESSIONS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if existing:
            return existing[-1]
        print(DIM("没有找到历史会话,开一个新的"))
    return SESSIONS / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"


async def turn(*, session: Session, prompt: str, model: Claude, tools, compactor,
               policy, args) -> None:
    """One prompt: run it, then decide what takes effect."""
    tray = Tray(policy=policy)
    screen = Renderer(verbose=args.verbose)
    screen.waiting("思考中")

    async for event in run(
        session=session, prompt=prompt, model=model, tools=tools,
        tray=tray, compactor=compactor, max_turns=args.max_turns,
    ):
        screen.handle(event)

    print(DIM(f"\n  {model.usage}"))

    if args.yes:
        await asyncio.to_thread(tray.commit)
    else:
        await review(tray, session)


async def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(RED("需要先设置 ANTHROPIC_API_KEY"), file=sys.stderr)
        return 1

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(RED(f"不是一个目录:{root}"), file=sys.stderr)
        return 1

    policy = guard(bulk(limit=args.bulk), protect(*args.protect))

    session = Session.open(pick_session(args))

    found_skills = find_skills(root)

    # Files are what nearly every task touches, so they are always in play.
    # The rest wait to be asked for: a calendar is dead weight while sorting a
    # download folder, and its schema is charged for every turn it sits there.
    groups: dict[str, list] = {"memory": memory_tools(root)}

    # Opt-in, not opt-out. These are the tools that reach outside the root and
    # ask macOS for permission, and forgetting a flag should not be what
    # decides whether Mail is reachable.
    on_mac = args.mac and platform.system() == "Darwin"
    if on_mac:
        mac = {t.name: t for t in mac_tools()}
        groups["calendar"] = [mac["list_calendars"], mac["list_events"], mac["create_event"]]
        groups["mail"] = [mac["draft_mail"], mac["send_mail"]]
        groups["search"] = [mac["spotlight"]]

    box = ToolBox(core=file_tools(root) + skill_tools(found_skills), groups=groups)
    tools = box.active

    # Standing instructions are part of the system prompt, so they sit in the
    # cached prefix and cost nothing after the first turn.
    instruction_files = find(root) if args.instructions else []
    system = SYSTEM
    if found_skills:
        system += "\n\n" + catalogue(found_skills)
    waiting = box.catalogue(GROUPS)
    if waiting:
        system += "\n\n" + waiting
    if instruction_files:
        system = SYSTEM + "\n\n以下是用户自己写下的长期指示,优先于上面的通用说明:\n\n" + read(
            instruction_files
        )

    picked = {"model": args.model} if args.model else {}
    model = Claude(tools=tools, system=system, cache=args.cache, **picked)

    compactor = None
    if args.compact:
        # The threshold is the model's own window less what the reply and the
        # turn's tool results will add before we look again. Asking the Models
        # API beats hard-coding a number that is wrong for every model but one.
        limit = await model.context_window() - args.reserve

        # A separate instance with no tools and no system prompt: summarising
        # needs neither, and handing them over would only make the request
        # bigger and invite the model to call something. Caching is off too -
        # the prefix it sends is the conversation being retired, read once and
        # never again, so a cache write would be paid at 1.25x and never read.
        compactor = Compactor(
            model=Claude(cache=False, **picked),
            limit=limit,
            # What the provider counted for the last request, which is exact.
            # It is one turn stale, and the reserve is what covers the gap.
            measure=lambda _msgs: getattr(model.usage, "last_input", 0),
        )

    waiting_note = f" + {len(box.dormant())} 组待加载" if box.dormant() else ""
    print(DIM(f"aven · {root} · {len(box.active())} 个工具{waiting_note} · {session.path.name}"))
    for path in instruction_files:
        # Read from the user's disk into the prompt: say so, every time.
        print(DIM(f"  ↳ 已读取指示 {path}"))
    if found_skills:
        print(DIM(f"  ↳ {len(found_skills)} 个技能可用:{', '.join(s.name for s in found_skills)}"))

    if args.prompt:
        await turn(session=session, prompt=args.prompt, model=model, tools=tools,
                   compactor=compactor, policy=policy, args=args)
        return 0

    if not args.plain and sys.stdin.isatty() and sys.stdout.isatty():
        # Imported here so a one-shot `aven "..."` never pays for Textual.
        from aven.tui import AvenApp

        await AvenApp(
            session=session, model=model, tools=tools, root=root,
            compactor=compactor, policy=policy, max_turns=args.max_turns,
        ).run_async()
        return 0

    print(DIM("说点什么,Ctrl-D 退出\n"))
    while True:
        try:
            typed = await asyncio.to_thread(input, BOLD("› "))
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if typed.strip():
            await turn(session=session, prompt=typed.strip(), model=model, tools=tools,
                       compactor=compactor, policy=policy, args=args)


def main(argv: list[str] | None = None) -> int:
    """The console entry point. asyncio.run is the only place the loop starts."""
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
