<h1 align="center">aven</h1>

<p align="center">
  <b>跑在你自己电脑上的本地优先个人助理。</b><br>
  你的文件和日历留在这里。不可逆的事,不经过你不会发生。
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

一个帮你动文件、订日程、发邮件的助理,手里握着的是**按一下键盘撤不回来**的东西。所以 aven 是围着一个问题设计的:**在不可逆的事情发生之前的那一刻,发生了什么?**

三个答案。其余的设计全部由它们推导出来。

**它跑在你的机器上。** 文件工具只能碰你指定的那几个目录,别的碰不到 —— `../`、`/etc/passwd`、`~/.ssh`、以及指向外面的符号链接,都在**工具边界**被拒绝,不是靠 prompt 里叮嘱。日历、邮件、Spotlight 走 macOS,由系统自己向你申请权限,你随时能在「系统设置」里收回。aven **不存任何账号密码**,因为它不需要 —— 操作系统本来就知道你是谁。

**不可逆的事,不经过你不会发生。** 可撤销的操作当轮就执行,并且随身带着撤销 —— 为一个"移回去就行"的文件移动去请示,只会训练人不读就点同意。而**撤不回来的操作从不执行**:它被**暂存**下来,只是被描述而没有发生,排在一个列表里等你**逐条**批准,每一条下面带着 diff、订单明细、或者那封邮件的正文。

**它做过的每一步你都能读。** 每一条消息、每一次工具调用、每一个参数,都是你自己文件里的一行 JSON。`aven --tree` 能走分支,`--mode json` 能把整轮交给别的程序,而且**压缩掉的东西原文都还在**,随时能翻回去看。

有一句话我放在明面上而不是藏在脚注里:**你的对话文本会发给你自己配置的模型服务商。** 文件在本地读、在本地动;对话不是。这是唯一离开这台机器的东西,而且只去你自己选的那个地址。

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
aven "一句话任务"      # 干完,给你看清单,退出 —— 可以重定向、管道
aven                   # 全屏界面:暂存区实时更新,Esc 中断当前任务
aven -c                # 全屏界面,接着上次会话(每轮省约 60%)
aven --plain           # 一行一行地对话,不用全屏
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

## 连接一个服务

一个 connector 就是一组工具,在被需要之前不占位置。每个工具的 schema 每轮都要随请求发出,把用不到的收起来能省下大约 40% 的每轮开销 —— 而这个形状恰好也适合做权限。关掉的服务不是一个变灰的按钮,而是模型根本看不见、因此叫不动的一组工具。

服务写在 `~/.aven/connectors.toml`:

```toml
[google]
client_id = "....apps.googleusercontent.com"
client_secret = "...."

[mcp.notes]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/Notes"]
about = "读写笔记文件夹"

[mcp.notes.risk]
read_text_file = "read"      # 你的判断,不是服务器的
```

**令牌存在 macOS 登录钥匙串里**,走 `security`,和 Safari 存自己那些的是同一个地方。没有钥匙串的平台上存成只有你本人可读的文件,并且 Connections 面板会写明是哪一种 —— 在某个平台上悄悄用更弱的方案,就是在用沉默撒谎。aven 自己不加密:加密需要密钥,密钥需要一个存放的地方,而"存放的地方"正是这里要解决的问题。

### Google Calendar

你需要自己的 OAuth client,因为公开仓库里附带的 client id 等于所有人共用一个。去
[console.cloud.google.com](https://console.cloud.google.com) 做一次:启用 Google
Calendar API,然后 **凭据 → 创建凭据 → OAuth 客户端 ID → 桌面应用**,把两个值填进上面。

登录会打开你自己的浏览器。aven 全程看不到你的密码 —— 它只在 `127.0.0.1` 上等那个
回调,而回调里的 code 绑定了只有本进程知道的一次性密钥(PKCE),所以光截获它也花不掉。

然后注意每个工具带的风险等级:

| | |
|---|---|
| 读日历 | `read` —— 直接执行,从不询问 |
| 加一个只有你看得见的日程 | `reversible` —— 直接执行,撤销就是删掉它 |
| 加一个会发邀请的日程 | `irreversible` —— **等你确认** |

同一个工具,每次调用的答案不同。删掉日程能让你的日历回到原样,但收不回那封通知
四个人到场的邮件。

### 任何 MCP server

一个 server 就是一个 connector。这是"想连什么就连什么"的答案:手写一个 connector
要为每个服务做一遍 OAuth 和 API 封装,而指向一个 MCP server 只要四行。

`aven --connectors <关键词>` 会搜索公开的 MCP 注册表(约九千个 server),并打印
可以直接粘贴的配置:

```
$ aven --connectors stripe

com.stripe/mcp
published by stripe.com
MCP server integrating with Stripe - tools for customers, products, payments.

[mcp.stripe]
url = "https://mcp.stripe.com"
```

**它只说明是谁发布的,不下判断。** 名字是一个注册表验证过归属的命名空间,所以
`stripe.com` 是事实;那是不是你想要的那一方,由你判断。搜 `stripe` 同样会返回
陌生人发布的 server,区别摆在那里,而不是被一个标签盖住。

两种连法:

```toml
[mcp.files]                                  # 本机的一个进程
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/Notes"]

[mcp.github]                                 # 别处的一个服务
url = "https://api.githubcopilot.com/mcp/"
[mcp.github.headers]
Authorization = "Bearer ghp_..."
```

远程 url 必须是 `https`(回环地址除外),否则工具的参数和授权用的 token 都会明文
走在网上。

**每个 MCP 工具在你开口之前一律按 `irreversible` 处理**,也就是暂存等待确认。这是
故意的。MCP 允许 server 声明自己的工具是只读的 —— 这个提示恰恰来自行为本身存疑的
那一方,一个会删你文件的 server 完全可以自称人畜无害。**对远程 server 这一点更重要
而不是更不重要**:本地的至少还跑在你自己机器上、以你的身份受系统约束。两个出口都得
是你自己的话:`trust = true` 表示采信这个 server 的声明,或者在 `[…risk]` 里点名
某个工具、自己给它定级。**你写的永远赢。**

工具名前面会加上 server 名(`files_read_file`),所以 MCP 工具永远不可能顶替掉
aven 自己那些 —— 外面套着文件夹沙箱的那些。

一个好的上手方式:第一轮什么风险都别配。每次调用都会进托盘,你能看清每个工具到底
想干什么、带什么参数,再决定哪些确实是只读的。

**关于 Google 这类大厂。** 去包仓库里搜,返回的是一打陌生人写的一打 server,连上
其中一个等于把账号交给那个陌生人。aven 的清单里只收**由被连接方自己发布**的 server。
Google 请用上面 aven 自己的 connector:你自己注册的 OAuth client,直连 Google,
中间没有第三方。

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

**模型是注入的。** `harness/` 里没有任何 provider import。551 个测试不用 key、不联网就能跑完,换 provider 也碰不到循环。

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
.venv/bin/python -m pytest -q       # 551 个测试,不用 key,不联网
```

示例不需要 API key,每个演示一块:

```bash
.venv/bin/python examples/06_tray.py     # 暂存、提交、撤销
.venv/bin/python examples/08_mac.py      # osascript(调用被替换成假的)
.venv/bin/python examples/09_stream.py   # 流式输出和 spinner
```

## 许可

MIT
