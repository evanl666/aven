<h1 align="center">aven</h1>

<p align="center">
  <b>跑在你自己电脑上的本地优先个人助理。</b><br>
  你的文件、你的密钥、你的日历 —— 除了发给模型的那段 prompt,什么都不离开这台机器。
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#为什么">为什么</a> ·
  <a href="#它是怎么工作的">怎么工作</a> ·
  <a href="#六条设计承诺">设计</a> ·
  <a href="README.md">English</a>
</p>

---

```
$ cd ~/Downloads && aven "把发票归档到 报销/,写一份清单"

⠋ 思考中

我先看看 Downloads 里有什么。
  · list_dir(path='.')  ✓
  · move_file(src='发票_滴滴_38.50元.pdf', dst='报销/发票_滴滴_38.50元.pdf')  ✓
  · move_file(src='发票_美团_126.00元.pdf', dst='报销/发票_美团_126.00元.pdf')  ✓
  · write_file(path='报销/清单.md', content=<173 字符>)  ✓

三张发票已归档,合计 463.50 元,清单在 报销/清单.md。

  4 次请求 · 输入 5914(缓存读 5103 · 写 803) · 输出 568

4 项已执行(可撤销)
  ✓  发票_滴滴_38.50元.pdf → 报销/发票_滴滴_38.50元.pdf
  ✓  发票_美团_126.00元.pdf → 报销/发票_美团_126.00元.pdf
  ✓  发票_京东_299.00元.pdf → 报销/发票_京东_299.00元.pdf
  ✓  write 报销/清单.md
[u] 撤销已执行   [Enter] 保持现状
>
```

按一个键,全部退回去。

---

## 为什么

写代码的 agent 可以先斩后奏 —— 改坏了 `git checkout` 一下就回来了。但一个帮你动文件、订日程、发邮件的助理不行。

云端助理的答案是:把 agent 跑在某处的虚拟机里。于是它碰不到你的机器,于是它需要你的密码才能代替你行事。aven 反过来 —— 它跑在你的机器上,所以它问 macOS,而 macOS 问你。

其余的设计,全部由这一个选择推导出来。

| | 云端助理 | aven |
|---|---|---|
| 跑在哪 | 不属于你的虚拟机 | 你的笔记本 |
| 你的账号密码 | 存进保险库,让 agent 登录成你 | 完全不涉及 —— 系统本来就知道你是谁 |
| 你的文件 | 上传,或者根本够不着 | 原地读,且只在你指定的那一个目录内 |
| 权限 | 授予厂商 | 由 macOS 授予,可随时在系统设置里撤销 |
| 谁看得到你的数据 | 厂商 | 只有模型,而且只有你发过去的那部分 |

## 快速开始

```bash
git clone https://github.com/evanl666/aven && cd aven
python3 -m venv .venv && .venv/bin/pip install -e .
ln -s "$PWD/.venv/bin/aven" ~/.local/bin/aven

export ANTHROPIC_API_KEY=sk-ant-...
export AVEN_MODEL=claude-sonnet-5        # 可选,默认是 claude-opus-5

cd ~/某个目录
aven "帮我整理一下"
```

**`cd` 就是权限模型** —— 文件工具只能碰你启动时所在的那个目录。`../`、`/etc/passwd`、`~/.ssh`、指向外面的符号链接,全部在工具边界被拒绝,而不是靠 prompt 里叮嘱一句。

```bash
aven "一句话任务"      # 干完,给你看清单,退出
aven                   # 对话模式,Ctrl-D 退出
aven -c "接着说"        # 接上次会话 —— 有记忆,而且省约 60% 的钱
aven --mac "..."       # 加上日历、邮件、Spotlight(macOS 会向你申请权限)
aven -v "..."          # 显示每个工具的返回值
```

需要 Python 3.11+;`--mac` 那组工具需要 macOS,其余跨平台。

## 审批这一步

这是整个设计存在的理由。一次运行结束时:

```
1 项等待确认
  ⏸  发送邮件给 finance@corp.com:9月报销
4 项已执行(可撤销)
  ✓  发票_滴滴.pdf → 报销/发票_滴滴.pdf
  ...
[c] 提交待确认   [d] 丢弃待确认   [u] 撤销已执行   [Enter] 保持现状
```

两栏,因为两类动作该被区别对待:

- **✓ 已经做了** —— 可撤销的操作在回合里直接执行,并留下 undo。为一次「能移回来的移动文件」征求同意,只会训练人闭眼点确认。
- **⏸ 还在等** —— 不可逆的操作根本没有执行。模型被如实告知「已暂存」,于是它不会围着一个没发生的效果继续推理,而是把其余能做的做完,再把一份清单交给你。

按 `u` 会**倒序**回滚(后面的改动可能依赖前面的),同时把对话也一起退回去 —— 否则模型还以为那些活儿算数。

菜单只显示当下真能做的键。一个只剩已完成日历事件的清单,只会给你 `[u]`。

## 它是怎么工作的

```
aven/
  core/       消息 · 会话 · 事件 · 工具协议 · 主循环
  tx/         暂存区              ← risk 在这里变成行为
  model/      全项目唯一 import provider SDK 的文件
  tools/      文件工具,锁在一个 root 内
  actuators/  日历 / 邮件 / Spotlight,走 osascript
  cli/        渲染 · 审批 · 命令入口
```

**循环是一个生成器。** `run()` 产出事件,调用方用 `for` 驱动它,每一步都当场看到,不想继续就不要下一个。没有订阅者列表,没有「渲染代码崩了把 agent 一起带走」,而一个测试读起来就是一串「发生了什么」的列表。

**模型是注入的。** `core/` 里没有任何 provider import。106 个测试不用 key、不联网就能跑完,换 provider 也碰不到循环。

**工具在定义处声明四件事** —— JSON schema(从函数签名推导,不会和代码脱节)、风险等级、执行前怎么预览、执行后怎么撤销:

```python
@tool(risk="reversible", preview="{src} → {dst}")
def move_file(
    src: Annotated[str, "源路径,相对于 root"],
    dst: Annotated[str, "目标路径,相对于 root"],
) -> ToolResult:
    """移动或重命名一个文件。"""
    source, target = inside(src), inside(dst)      # 两个路径都过沙箱
    shutil.move(source, target)
    return ToolResult(
        output=f"moved {show(source)} → {show(target)}",
        undo=lambda: shutil.move(target, source),  # 闭包记住了它实际落在哪
    )
```

只有工具自己知道它**实际**做了什么 —— 重名后换的那个路径、Calendar 返回的那个 id —— 所以只有工具能造出正确的 undo。

**会话是只追加的 JSONL。** 一条消息一行,树由 `parent_id` 承载,任何一行都不会被改写。分支只是移动一个指针。正是这条纪律让撤销成为可能:回退从不擦除。

## 六条设计承诺

一开始就定下来的,因为其中任何一条事后补都很痛。

1. **副作用先暂存,不直接触发。** ✅ 已实现 —— 见上面的审批步骤。
2. **不可信内容不能授权动作。** 部分实现:路径在边界检查之前先 `resolve()`;AppleScript 的值全部走 `on run argv` 带外传递 —— 一个写着 `x" & (do shell script "rm -rf ~") & "` 的邮件主题,始终只是个邮件主题。
3. **分级执行。** 部分实现:目前是 原生 API → `osascript`;下面还有 Shortcuts、Accessibility、视觉点击几级。
4. **模型只拿到句柄,拿不到值。** 计划中。私密数据在 prompt 离开这台机器之前变成 `<<person:7>>`,只在工具执行器内部还原。被劫持的模型偷不走从未进入它上下文的东西。
5. **触发器优先,而不是对话优先。** 计划中。一个回合可以由 cron、一封新邮件、或者 `~/Downloads` 里多出的一个文件开启 —— `UserMessage.source` 从第一个 commit 起就带着这个字段。
6. **重复的任务沉淀成可读的小程序。** 计划中。干过两次的活变成一份你能读能改的例程,确定性地执行,只在真正有歧义的步骤才问模型。

## 开发

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q       # 106 个测试,不用 key,不联网
```

示例不需要 API key,每个演示一块:

```bash
.venv/bin/python examples/06_tray.py     # 暂存、提交、撤销
.venv/bin/python examples/08_mac.py      # osascript(调用被替换成假的)
.venv/bin/python examples/09_stream.py   # 流式输出和 spinner
```

## 许可

MIT
