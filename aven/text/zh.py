"""The Chinese catalogue.

Only keys that differ usefully from English need to be here; anything left out
falls back to `en`. This is the one file below `apps/` allowed to hold text a
person reads, because holding it is the whole job.
"""

from __future__ import annotations

TEXT: dict[str, str] = {
    # --- the staging tray --------------------------------------------------
    "tray.empty": "没有任何改动",
    "tray.summary": "待处理:{pending} 项需批准 / {applied} 项已执行可撤销",
    "tray.state.pending": "需要批准",
    "tray.state.applied": "可撤销",
    "tray.staged": "已暂存",
    "tray.staged.long": "已暂存,等你确认",
    "tray.none_yet": "还没有改动",
    "tray.title": "暂存区",
    "tray.pending_count": "{n} 项等待确认\n",
    "tray.undoable_count": "{n} 项已执行(可撤销)\n",
    "tray.button.commit": "提交待确认",
    "tray.button.discard": "丢弃",
    "tray.button.undo": "撤销已执行",
    # --- policy verdicts ---------------------------------------------------
    "policy.bulk": "这次运行已经改了 {n} 处,再往下建议你先看一眼",
    "policy.protect": "路径里有「{pattern}」,这类东西改之前想一下",
    # --- bringing tools in -------------------------------------------------
    "toolbox.load": "加载 {group} 这组工具",
    # --- the session tree --------------------------------------------------
    "tree.empty": "这个会话还是空的",
    "tree.footer": "{nodes} 个节点,{branches} 个分支。/tree <id> 回到某一处,/fork <id> 另存为新会话",
    "tree.who.user": "你",
    "tree.who.assistant": "aven",
    "tree.who.summary": "摘要",
    # --- listing sessions --------------------------------------------------
    "sessions.untitled": "(空会话)",
    "sessions.none": "没有会话记录",
    "sessions.line": "{when} · {messages} 条 · {file}",
    "ago.second": "{n} 秒前",
    "ago.minute": "{n} 分钟前",
    "ago.hour": "{n} 小时前",
    "ago.day": "{n} 天前",
    # --- usage -------------------------------------------------------------
    "usage.nocache": "无缓存",
    "usage.cache": "缓存读 {read} · 写 {written}",
    "usage.line": "{requests} 次请求 · 输入 {input}({cache}) · 输出 {output}",
    # --- tool previews -----------------------------------------------------
    "files.edit": "改 {path}:{old}",
    "files.delete": "删除 {path}",
    "memory.where.global": "(所有地方)",
    "memory.where.here": "(这个目录)",
    "memory.remember": "记住{where}:{fact}",
    "memory.forget": "忘掉{where}:{fact}",
    "skills.read": "读技能 {name}",
    "mac.event": "日历「{calendar}」新建:{title} @ {start}",
    "mac.draft": "写草稿给 {to}:{subject}",
    "mac.send": "发送邮件给 {to}:{subject}",
    # --- what the line-by-line renderer says -------------------------------
    "render.compacted": "对话太长了,早先的部分已压缩成摘要(原文都还在会话文件里)",
    "render.max_turns": "达到轮次上限,任务没有做完",
    "render.truncated": "回复太长被截断了。让它接着说,或者把任务拆小一点",
    "render.waiting": "思考中",
    "render.pending_header": "{n} 项等待确认",
    "render.undoable_header": "{n} 项已执行(可撤销)",
    "render.outcome": "  {verb} {n} 项",
    "render.no_change": "  没有变化",
    "render.clipped": "<{n} 字符>",
    # --- the review step ---------------------------------------------------
    "review.commit": "[c] 提交待确认",
    "review.discard": "[d] 丢弃待确认",
    "review.undo": "[u] 撤销已执行",
    "review.keep": "[Enter] 保持现状",
    "review.unknown": "  这里没有 [{choice}] 这个选项",
    "review.committed": "提交了",
    "review.discarded": "丢弃了",
    "review.undone": "撤销了",
    "review.rewound": "  对话也回退到了改动之前",
    # --- the print and json sinks ------------------------------------------
    "stream.staged": "未执行(等待确认):{preview}",
    # --- the full-screen shell ---------------------------------------------
    "shell.help": """\
/session   这个会话的基本情况
/name x    给这次会话起个名字,下次 -r 好找
/tree      看这个会话的分支树;/tree <id> 回到某一处
/fork      把当前分支另存成新会话;/fork <id> 从某处截断
/undo      撤销已执行的改动(对话也一起回退)
/commit    执行等待确认的操作
/discard   丢弃等待确认的操作
/cost      这次会话用了多少 token
/clear     清屏(不影响会话记录)
/quit      退出

Esc 中断当前任务 · Ctrl+T 显示/隐藏暂存区 · Ctrl+Q 退出""",
    "shell.bind.interrupt": "中断",
    "shell.bind.tray": "暂存区",
    "shell.bind.clear": "清屏",
    "shell.bind.quit": "退出",
    "shell.placeholder": "说点什么 · /help 看命令",
    "shell.queued": "排队中({n} 条),这一轮结束就送进去",
    "shell.no_usage": "没有用量信息",
    "shell.unknown_command": "没有 {name} 这个命令,/help 看看有哪些",
    "shell.interrupted": "已中断。已做的可撤销改动还在暂存区里。",
    "shell.error": "出错了:{kind}: {problem}",
    "shell.busy": "任务还在跑,先按 Esc 中断",
    "shell.moved": "回到 {id}。接着说就会从这里分出一条新的分支。",
    "shell.carrying": "正在把刚才那条分支的结论带过来…",
    "shell.carry_failed": "没能带过来({kind}),分支已经切好了",
    "shell.carried": "已带过来:{gist}",
    "shell.unnamed": "这个会话还没有名字",
    "shell.named": "这次会话叫「{name}」了",
    "shell.describe.name": "名字:{name}",
    "shell.describe.none": "(没起名)",
    "shell.describe.file": "文件:{path}",
    "shell.describe.messages": "消息:{n} 条 · {branches} 个分支",
    "shell.describe.head": "当前:{id}",
    "shell.describe.usage": "用量:{usage}",
    "shell.forked": "已分出新会话 {file}({n} 条)。原来那个一个字没动。",
    "shell.committed": "提交了 {n} 项",
    "shell.commit_failed": ",{preview} 失败:{output}",
    "shell.discarded": "丢弃了 {n} 项,它们没有发生过",
    "shell.undone": "撤销了 {n} 项,对话也回到了改动之前",
    "shell.tools": "{n} 个工具",
    # --- the command line --------------------------------------------------
    "cli.description": "本地优先的个人助理",
    "cli.prompt": "要它做的事。省略则进入对话模式",
    "cli.continue": "接着最近一次会话",
    "cli.resume": "列出会话让你挑一个",
    "cli.name": "给这次会话起个名字,下次好找",
    "cli.session": "指定会话文件",
    "cli.root": "文件工具允许操作的目录,默认是当前目录",
    "cli.mac": "打开日历 / 邮件 / Spotlight(macOS 会向你申请权限)",
    "cli.model": "模型 id,也可用 AVEN_MODEL 环境变量设定",
    "cli.nocache": "关掉 prompt 缓存(调试用)",
    "cli.noinstructions": "不加载 AVEN.md / AGENTS.md",
    "cli.bulk": "一次运行改动超过这么多处,就先让你看一眼",
    "cli.protect": "路径里含这个片段就要确认,可以重复给",
    "cli.protect_metavar": "片段",
    "cli.reserve": "给回复和下一轮增长留出的 token 余量",
    "cli.nocompact": "关掉自动压缩",
    "cli.print": "跑完就退出,只把最后一句回答写到 stdout(给脚本用)",
    "cli.mode": "text 给人看;json 把每个事件按 JSONL 写到 stdout",
    "cli.tree": "打印这个会话的消息树然后退出",
    "cli.fork": "把会话另存为一个新文件,可以指定从哪个节点截断",
    "cli.verbose": "显示每个工具的结果",
    "cli.plain": "不用全屏界面,一行一行地对话",
    "cli.yes": "跳过确认,直接提交(自动化用,慎用)",
    "cli.lang": "界面语言,比如 en 或 zh",
    "cli.no_sessions": "没有找到历史会话,开一个新的",
    "cli.no_catalogue": "没有会话记录,开一个新的",
    "cli.pick": "挑一个(回车 = 最近的,q = 退出):",
    "cli.no_such": "没有第 {typed} 项,用最近的那个",
    "cli.not_a_dir": "不是一个目录:{path}",
    "cli.empty_fork": "这个会话是空的,没有东西可以 fork",
    "cli.forked": "  {n} 条消息,原来的会话没有动过",
    "cli.no_key": "需要先设置 ANTHROPIC_API_KEY",
    "cli.instructions_header": "以下是用户自己写下的长期指示,优先于上面的通用说明:",
    "cli.banner": "aven · {root} · {tools} 个工具{waiting} · {session}",
    "cli.waiting_groups": " + {n} 组待加载",
    "cli.read_instructions": "  ↳ 已读取指示 {path}",
    "cli.skills": "  ↳ {n} 个技能可用:{names}",
    "cli.needs_prompt": "-p / --mode json 需要一个 prompt(参数或管道)",
    "cli.talk": "说点什么,Ctrl-D 退出\n",
    # --- the coding app ----------------------------------------------------
    "code.description": "同一个 harness 上的写代码 agent",
    "code.banner": "aven-code · {root} · {tools} 个工具{waiting} · {session}",
    "code.allow": "把这个命令当成只读的,不必确认就能跑,可以重复给",
    "code.run": "执行:{command}",
    "code.grep": "搜索「{pattern}」",
    "code.glob": "列出匹配「{pattern}」的文件",
}
