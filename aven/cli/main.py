"""The aven command.

    aven "把下载目录里的发票整理一下"      one task, then review
    aven                                     keep talking
    aven -c                                  continue the last session

A client of the loop and nothing more. It owns the terminal; the loop owns the
work; the tray owns what actually takes effect.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time
from pathlib import Path

from aven.cli.render import BOLD, DIM, RED, Renderer
from aven.cli.review import review
from aven.core.agent import run
from aven.core.session import Session
from aven.model import Claude
from aven.actuators import mac_tools
from aven.tools import file_tools
from aven.tx import Tray

SESSIONS = Path.home() / ".aven" / "sessions"

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
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("-v", "--verbose", action="store_true", help="显示每个工具的结果")
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


def turn(*, session: Session, prompt: str, model: Claude, tools, args) -> None:
    """One prompt: run it, then decide what takes effect."""
    tray = Tray()
    screen = Renderer(verbose=args.verbose)
    screen.waiting("思考中")

    for event in run(
        session=session, prompt=prompt, model=model, tools=tools,
        tray=tray, max_turns=args.max_turns,
    ):
        screen.handle(event)

    print(DIM(f"\n  {model.usage}"))

    if args.yes:
        tray.commit()
    else:
        review(tray, session)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(RED("需要先设置 ANTHROPIC_API_KEY"), file=sys.stderr)
        return 1

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(RED(f"不是一个目录:{root}"), file=sys.stderr)
        return 1

    session = Session.open(pick_session(args))

    tools = file_tools(root)
    # Opt-in, not opt-out. These are the tools that reach outside the root and
    # ask macOS for permission, and forgetting a --no-mac should not be what
    # decides whether Mail is reachable.
    on_mac = args.mac and platform.system() == "Darwin"
    if on_mac:
        tools = tools + mac_tools()

    model = Claude(
        tools=tools,
        system=SYSTEM,
        cache=args.cache,
        **({"model": args.model} if args.model else {}),
    )

    print(DIM(f"aven · {root} · {len(tools)} 个工具{' (含日历/邮件)' if on_mac else ''} · {session.path.name}"))

    if args.prompt:
        turn(session=session, prompt=args.prompt, model=model, tools=tools, args=args)
        return 0

    print(DIM("说点什么,Ctrl-D 退出\n"))
    while True:
        try:
            prompt = input(BOLD("› ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if prompt:
            turn(session=session, prompt=prompt, model=model, tools=tools, args=args)


if __name__ == "__main__":
    raise SystemExit(main())
