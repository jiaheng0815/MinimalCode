# MinimalCode

[![CI](https://github.com/jiaheng0815/MinimalCode/blob/main/.github/workflows/tests.yml/badge.svg)](https://github.com/jiaheng0815/MinimalCode/blob/main/.github/workflows/tests.yml)

MinimalCode 是一个面向终端编码任务的轻量 Agent。它优先减少启动开销、交互层级和上下文浪费，不提供炫技式 TUI。

当前版本：`0.8.0`。运行时依赖只有 `httpx2`，Python 3.11+。

## 特性

- **Shell 优先使用 pwsh**：Windows 上按 `pwsh` → `powershell.exe` 选择，其他平台按 `pwsh` → `bash` → `sh` 选择；启动时解析一次，可在配置中固定路径。
- **聚焦工具集**：`read`、`write`、`edit`、`shell`、`grep`、`find`、`subagent` 七个基础工具，外加可选的 `read_image`、`web_search`、`web_fetch`。`read_image` 不绑定供应商，任何配置了 `vision` 的供应商都能使用。
- **搜索不依赖 shell**：`grep` / `find` 直接用 `rg` 子进程或内置 Python 遍历实现，不经过 pwsh，因此没有引号、通配符和对象管道语义的偏差。
- **纯文本输出**：流式打印，在交互终端中增量渲染 Markdown 高亮，不使用 Rich、Textual 或其他 UI 框架。
- **会话**：追加式 JSONL 自动保存，支持继续最近会话、按 ID 恢复、按工作区隔离。
- **上下文管理**：接近上限时自动压缩较早消息，也可手动 `/compact`。
- **规则文件**：读取全局和项目级 `AGENTS.md` / `CLAUDE.md`，可用 `SYSTEM.md` 替换默认系统提示词。
- **协议**：OpenAI Chat Completions 与 OpenAI Responses，其他兼容供应商只需配置。
- **脚本友好**：`--json` 输出机器可读结果，stdout 与工具活动分离。

## 安装

```powershell
python -m pip install -e .
minimalcode init
```

也可以用 pipx：

```powershell
pipx install .
minimalcode init
```

`init` 会在配置目录写入 `settings.json` 并创建 `sessions/`。首次正常运行如果发现配置缺失，也会自动生成，因此这一步是可选的。

要求 Python 3.11 及以上。安装 `ripgrep`（`rg`）是可选的，但会让 `grep` / `find` 更快，并获得更完整的 ripgrep 语义。

## 从 0.6 升级到 0.7

0.7.0 把 `tools`、`sandbox`、`provider`、`highlight` 四个大模块拆成了包，并在发布前移除了拆包过程中临时保留的旧名字回退。CLI、配置和工具行为没有变化；只有直接导入内部名字或 patch 内部函数的代码需要迁移：

- `minimalcode.tools`、`minimalcode.sandbox`、`minimalcode.provider`、`minimalcode.highlight` 现在只导出各自 `__all__` 中的公开名称，未知名字直接抛 `AttributeError`。
- 私有 helper 改为从实现模块导入，例如 `minimalcode.tools.search.run_python_grep`、`minimalcode.sandbox.execution._posix_limits`、`minimalcode.highlight.palette._PALETTES`。
- patch 目标要指向实际查找该名字的模块，例如 `minimalcode.tools.search_tools.shutil.which`、`minimalcode.provider.transport.time.sleep`、`minimalcode.highlight.color._supports_color`。

完整对照表见 [docs/compatibility.md](docs/compatibility.md)。

0.8.0 只补充文档与注释、补全供应商重试策略的测试，公开面与 0.7.0 一致，从 0.7 升级无需改代码。

## 快速开始

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
minimalcode
```

交互模式启动后会显示供应商/模型、当前工作区、shell 和会话文件路径。

单次任务：

```powershell
minimalcode "找出这个项目测试失败的原因并修好"
minimalcode -p "运行测试并总结" --json
```

`-p/--print` 表示非交互执行，必须同时给出提示词或通过标准输入管道传入；`--json` 隐含该模式。

## CLI 参考

```text
minimalcode [prompt ...] [options]
```

| 选项 | 说明 |
|---|---|
| `prompt ...` | 初始消息。给出后执行一轮任务并退出 |
| `-p`, `--print` | 非交互模式；缺少提示词时报错 |
| `--json` | 输出 JSON 并隐含 `--print`，同时隐藏工具活动 |
| `-c`, `--continue` | 继续当前目录最近一次会话 |
| `--session <id>` | 指定会话：完整 ID、前缀或 JSONL 路径 |
| `--no-session` | 本次运行不读写会话 |
| `--provider <name>` | 覆盖配置中的供应商 |
| `-m`, `--model <id>` | 覆盖模型 |
| `--thinking <effort>` | 覆盖推理强度：`none`（关闭）、`minimal`、`low`、`medium`、`high`、`xhigh`、`ultra`、`max` |
| `--max-turns <n>` | 覆盖最大工具轮次（1–10000） |
| `--cwd <dir>` | 指定工作区 |
| `--home <dir>` | 指定配置目录，默认 `~/.MinimalCode` |
| `--sandbox`, `--no-sandbox` | 开启/关闭沙箱执行层（默认关闭，等价于默认 `--no-sandbox`）。无论开关，文件工具的工作区与敏感文件边界都生效 |
| `--sandbox-backend <name>` | `auto`、`local`、`bwrap`、`firejail`、`sandbox-exec`、`docker` |
| `--sandbox-root <path>` | 额外允许的读写根，可重复 |
| `--sandbox-network <policy>` | `deny`（默认）、`allowlist`、`allow` |
| `--sandbox-allow-host <host>` | `allowlist` 允许的主机，可重复 |
| `--sandbox-timeout <sec>` | 单条命令的硬超时 |
| `--sandbox-max-output <chars>` | 单条命令返回的 stdout/stderr 上限 |
| `--sandbox-allow-sensitive` | 允许访问 `.env`、私钥等敏感文件 |
| `--quiet` | 隐藏工具活动 |
| `--no-highlight` | 关闭 Markdown 高亮 |
| `--version` | 打印版本 |

管理命令按第一个词识别，因此以这些词开头的提示词需要用 `--` 分隔：`minimalcode -- init config show` 发送的是提示词而不是执行子命令。

管理命令：

| 命令 | 说明 |
|---|---|
| `minimalcode init [--force]` | 创建配置目录与 `settings.json` |
| `minimalcode config [show]` | 打印合并后的配置，敏感字段显示为 `<redacted>` |
| `minimalcode config path` | 打印配置文件路径 |
| `minimalcode config set-model <id>` | 写入默认模型 |
| `minimalcode config set-provider <name>` | 写入默认供应商 |
| `minimalcode sessions` | 列出已保存会话（时间、ID、模型、工作区） |
| `minimalcode shell` | 显示探测到的 shell |
| `minimalcode sandbox` | 打印当前沙箱策略与各后端可用性 |
| `minimalcode import-pi` | 从 Pi 配置迁移供应商、模型和上下文窗口 |

`import-pi` 默认把 Pi 中已有的 API key 复制到权限受限的配置中，但不会在终端打印密钥。加 `--no-secrets` 则跳过密钥，改用环境变量；`--pi-home` 指定 Pi 目录，`--overwrite` 允许覆盖已有供应商。

退出码：`0` 任务完成，`1` 未在轮次上限内完成，`2` 配置或运行错误，`130` 被 Ctrl+C 中断。

JSON 输出字段：

```json
{"text":"...","completed":true,"turns":3,"usage":{...},"session":"...jsonl"}
```

## 交互命令

| 命令 | 作用 |
|---|---|
| `/new`、`/clear` | 新建会话 |
| `/compact` | 立即压缩历史 |
| `/session` | 显示会话文件路径 |
| `/effort [none\|minimal\|low\|medium\|high\|xhigh\|ultra\|max]` | 查看或修改本次运行的推理强度；`none` 关闭思考 |
| `/status` | 显示模型、推理强度、shell、上下文和 token 统计（JSON） |
| `/help` | 显示命令 |
| `/exit`、`/quit` | 退出 |

未知的 `/` 命令只报错，不会结束会话。

## 工具

| 工具 | 作用 | 主要参数 |
|---|---|---|
| `read` | 按行范围读取 UTF-8 文本，输出带行号 | `path`、`start_line`、`end_line` |
| `write` | 创建或整体覆盖文件 | `path`、`content` |
| `edit` | 精确文本替换，匹配不唯一时失败 | `path`、`old_text`、`new_text`、`replace_all` |
| `shell` | 在配置的 shell 中执行命令 | `command`、`timeout`、`cwd` |
| `grep` | 正则搜索文本，返回 `path:line:text` | `pattern`、`path`、`glob`、`max_results` |
| `find` | 按 glob 查找文件 | `pattern`、`path`、`max_results` |
| `subagent` | 在子 shell 中启动一个新的 `minimalcode`，只返回它的最终答复 | `task`、`cwd`、`timeout`、`max_turns` |
| `read_image` | 读取本地图片并交给配置的 OpenAI 兼容视觉端点，返回描述或对 `prompt` 的回答 | `path`、`prompt` |
| `web_search` | 通过供应商检索网页 | `query`、`count`、`need_content` |
| `web_fetch` | 通过供应商抓取 HTTP/HTTPS 页面 | `url` |

`web_search`、`web_fetch` 由供应商的 `web_tools` 配置启用；`read_image` 由供应商的 `vision` 配置启用。未启用的供应商既收不到对应的工具定义，直接调用也会被拒绝。

`subagent` 默认开启，但受 `subagent.enabled` 和 `subagent.max_depth` 约束；超过嵌套深度的子进程既收不到该工具定义，直接调用也会被拒绝。子进程就是一条命令，因此开启沙箱执行层时其超时还要再受 `sandbox.timeout_seconds` 约束：`subagent.timeout_seconds` 更大时会被压到该值，并在 `minimalcode sandbox` / `/status` 的 `notes` 中记录；执行层关闭时只受 `subagent.timeout_seconds` 约束。

### 图像理解

`read_image` 是通用工具，不绑定某个供应商，也不受供应商自身 `protocol` 限制：只要在供应商配置里加一个 `vision` 块，指向任意 OpenAI 兼容的 `/chat/completions` 端点，该供应商就能获得这个工具。`base_url`、`model` 和密钥默认沿用供应商的同名字段，也可以单独覆盖：

```json
"vision": {
  "enabled": true,
  "base_url": "https://api.example.com/v1",
  "model": "vision-model",
  "timeout_seconds": 120,
  "max_image_bytes": 33554432,
  "max_chars": 8000,
  "detail": "auto",
  "api_key_env": "EXAMPLE_API_KEY",
  "headers": {}
}
```

- 图片以 base64 data URL 内联发送，格式按文件内容判断，支持 JPEG、PNG、GIF 和 WebP，不依赖扩展名。
- `prompt` 可选；缺省时要求模型详细描述图片并逐字转写可见文字，结果超过 `max_chars` 时截断并标记 `<description truncated>`。
- `read_image` 和其他文件工具一样受工作区限制，读取工作区外的图片需要 `tools.allow_outside_workspace`。
- 内置默认配置只为 DeepSeek 打开了 `vision`（`deepseek-flash`），其他供应商默认关闭，需要时按上面的结构自行添加。

### 实现要点

- **工作区约束**：所有接受路径的工具都经同一处校验。相对路径以工作区为基准，解析后必须位于工作区内；`tools.allow_outside_workspace: true` 可放行外部路径，但仍受敏感文件策略约束。`shell` 与 `subagent` 同样经过沙箱入口，命令的工作目录和解析后的符号链接都在允许根内。`--no-sandbox` 只关闭执行层（后端包装、网络策略、资源限制），工作区与敏感文件边界照旧生效。
- **沙箱执行层**：`sandbox.enabled` 默认关闭（等同默认 `--no-sandbox`），`--sandbox` 或任意 `--sandbox-*` 选项会开启它；开启后默认后端 `local`（跨平台策略与子进程限制基线），可选 `auto`（Linux 依次尝试 `bwrap`、`firejail`，macOS 尝试 `sandbox-exec`，否则 `local`）以及 `bwrap`、`firejail`、`sandbox-exec`、`docker`。默认网络策略 `deny`，默认拒绝 `.env`、私钥、`.ssh/`、`.aws/` 等敏感路径。开启时路径逃逸、符号链接逃逸、敏感文件、网络命令、后端缺失都会 fail-closed 拒绝并记录原因。命令统一受 `sandbox.timeout_seconds`、`sandbox.max_output_chars` 和子进程资源上限约束，临时目录用后删除。详见 `docs/configuration.md` 的 Sandbox 一节。
- **grep / find**：检测到 `rg` 时直接以参数数组调用子进程（`rg --line-number --no-heading --color never --smart-case ...` 和 `rg --files --glob ...`），不拼接命令行字符串；没有 `rg` 时退回 Python 的 `re` + `os.walk`，并用独立子进程执行正则，超时会终止。两条路径跳过相同的忽略目录（`.git`、`node_modules`、`build`、`dist`、缓存和虚拟环境），支持相同的 smart-case 语义，且都不解析 `.gitignore`；显式把 `path` 指向 `build` 等目录时仍会搜索该目录。单行结果按 300 字符截断，`rg`/`find` 输出达到上限即停止读取。
- **shell**：自动探测会先用一条 `exit 0` 验证候选 shell 能否真正启动，`pwsh` 因缺少 libicu、.NET 无法预留内存或架构不符而崩溃时会继续尝试 `bash`/`sh`（Windows 为 `powershell`）；探测结果按可执行文件缓存，每个进程只付一次启动开销（pwsh 约 0.2 秒）。显式配置的 `shell.path` 同样会验证，启动失败会直接报出原因而不是静默换 shell。`pwsh`/`powershell` 以 `-NoLogo -NoProfile -NonInteractive -Command` 启动，并在命令前注入 bootstrap：关闭进度条、`$ErrorActionPreference='Stop'`、控制台输出编码设为无 BOM 的 UTF-8。bash/zsh/sh 分别使用 `-c` 及各自的 no-profile 参数。stdout/stderr 由后台线程持续读取，只在内存中保留首尾有界片段，因此超长输出不会占满磁盘或内存，超时后仍能返回已产生的部分输出。超时会终止整个进程树（Windows 用 `taskkill /T`，POSIX 用进程组）。
- **误报网络命令**：`local` 基线的 `deny` 基于命令字符串识别，只读的 `python -m pip show/list`、`pip --version` 会被放行，`pip install/download` 等会触网子命令才会被拒绝。
- **资源限制**：POSIX 下对子进程设置 `RLIMIT_CORE` 和按超时推算的 `RLIMIT_CPU`；Linux 的 `RLIMIT_AS`（`sandbox.max_memory_mb`）和 `RLIMIT_NPROC`（`sandbox.max_processes`）默认关闭，因为 `RLIMIT_AS` 限制的是预留地址空间而非实际占用，2 GB 的默认值会让 .NET（pwsh）、JVM、Go 子进程在真正运行前就报 `Out of memory`。
- **subagent**：没有新的子协议，就是一条 shell 命令 `<python> -m minimalcode -p --no-session --quiet`，透传父进程的 `--provider` / `-m` / `--thinking` / `--home` / 工作区。任务正文通过 stdin 传入，因此提示词不会出现在命令行里，也没有引号转义问题；PowerShell 使用调用运算符 `&`，并在末尾 `exit $LASTEXITCODE` 保留子进程真实退出码。子进程通过 `MINIMALCODE_SUBAGENT_DEPTH` 得知自己的层级，超过 `subagent.max_depth` 后不再获得 `subagent` 工具；`PYTHONPATH` 指向当前包所在目录，因此子进程跑的是同一份代码。
- **网络重试**：请求在拿到响应之前失败（`ConnectError`、`ConnectTimeout`，以及 `Server disconnected without sending a response.` 这类 `RemoteProtocolError`）会自动重试 10 次，退避从 0.5 秒翻倍到最多 8 秒；已经开始返回数据的流式中断不重试，避免重复输出或重复计费。HTTP 429/503 仍按 `Retry-After` 重试一次。
- **输出裁剪**：工具结果按首尾保留截断，超长输出中间以 `... <truncated N chars> ...` 标记，既保留开头上下文也保留结尾错误信息。

## 配置

默认目录：

```text
~/.MinimalCode/
├── settings.json   # 自动创建，与包内 defaults.json 深度合并
├── SYSTEM.md       # 可选，替换默认系统提示词
├── AGENTS.md       # 可选，全局规则
└── sessions/       # 按工作区分组的 JSONL 会话
```

配置目录可用 `MINIMALCODE_HOME` 或 `--home` 覆盖。

默认供应商是 DeepSeek，使用 Responses 协议和环境变量中的密钥：

```json
{
  "provider": "deepseek",
  "model": "deepseek-flash",
  "thinking": "high",
  "max_turns": 1000,
  "providers": {
    "deepseek": {
      "protocol": "openai_responses",
      "base_url": "https://api.deepseek.com/v1",
      "api_key_env": "DEEPSEEK_API_KEY",
      "context_window": 1000000,
      "timeout_seconds": 120,
      "stream": true,
      "include_usage": false,
      "send_reasoning_effort": true,
      "headers": {},
      "extra_body": {}
    }
  }
}
```

- `protocol` 取 `openai_chat` 或 `openai_responses`。
- 密钥优先用 `api_key_env` 指向环境变量；直接写 `api_key` 也支持，但要注意文件权限。密钥不会由 `init` 写入配置。
- 任意字符串值支持 `${VAR}` 环境变量占位符，未解析时会明确报出变量名，而不是把占位符发给供应商。
- `include_usage` 仅在服务端接受 `stream_options` 时开启；`send_reasoning_effort` 仅在供应商支持时开启。Responses 模式下的 `thinking` 只有配合 `send_reasoning_effort: true` 才会作为 `reasoning.effort` 发送；`none` 会发送 `reasoning.effort: "none"`，Chat 模式则发送 `thinking: {"type": "disabled"}` 关闭思考。
- 其他兼容供应商沿用同一结构，替换 `base_url`、模型和密钥环境变量即可。

沙箱执行层默认关闭（默认 `--no-sandbox`），需要时用 `--sandbox` 或把 `enabled` 设为 `true` 开启；开启后默认只允许工作区、默认禁网：

```json
"sandbox": {
  "enabled": false,
  "backend": "local",
  "allowed_roots": [],
  "network_policy": "deny",
  "allowed_hosts": [],
  "timeout_seconds": 120,
  "max_output_chars": 60000,
  "allow_sensitive_files": false,
  "max_memory_mb": 0,
  "max_processes": 0,
  "docker_image": "alpine:3"
}
```

`backend` 可选 `auto`、`local`、`bwrap`、`firejail`、`sandbox-exec`、`docker`；不可用的后端会拒绝执行而不是降级。命令行提供 `--sandbox/--no-sandbox`、`--sandbox-backend`、`--sandbox-root`、`--sandbox-network`、`--sandbox-allow-host`、`--sandbox-timeout`、`--sandbox-max-output` 和 `--sandbox-allow-sensitive`，`minimalcode sandbox` 可查看当前策略与后端可用性。

完整字段、默认值、Web 工具和视觉配置见 [docs/configuration.md](docs/configuration.md)；内置默认值以包内 `minimalcode/defaults.json` 为准。

## 终端高亮

当输出目标是支持 ANSI 的交互式终端时，模型输出中的标题、强调、行内代码、引用、列表、表格和围栏代码块会被着色，并按流式 delta 逐词显示，不等待整行或完整回复。未闭合的 Markdown 标记会立即按对应样式渲染且不打印语法字符；`#` 到 `######` 标题标记本身也不打印，`## 标题` 只输出着色的标题文字；链接显示为 `文本 (URL)`。正文不重排，会话中保存的仍是模型返回的原始 Markdown。模型输出、工具输出和错误信息在写到终端前会移除 ANSI/OSC 等控制序列，只保留换行和制表符，避免转义序列操纵终端。

主题通过配置切换：

```json
"output": {
  "markdown_highlight": true,
  "theme": "dark"
}
```

`theme` 支持 `dark` 和 `light`。以下情况保持纯文本：`--json`、stdout 被重定向、设置 `NO_COLOR`、`--no-highlight`、或配置中关闭高亮。

工具活动（`[shell]`、`[read]` 等标签）写入 stderr，助手正文写入 stdout；`--quiet` 和 `--json` 下不显示工具活动。长 shell 输出在终端只保留首尾各若干行（最多 12 行、4000 字符，省略部分标记 `… N lines omitted`），模型收到的完整输出不受影响。

## 安全边界

- MinimalCode 会执行模型生成的 shell 命令，所有命令统一经过沙箱入口。文件工具始终拒绝工作区外路径、符号链接逃逸和常见敏感文件。沙箱执行层默认关闭（默认 `--no-sandbox`），此时不施加网络策略、命令超时/输出上限和子进程资源限制；用 `--sandbox` 或 `sandbox.enabled: true` 开启后，疑似联网命令会被拒绝，后端不可用时 fail-closed 拒绝执行。
- 默认关闭的执行层意味着 `shell` 直接以当前用户身份运行，没有禁网推断、命令超时和输出上限；需要这些约束时请加 `--sandbox`（或在配置中开启）。开启后的 `local` 基线是策略、超时、输出和子进程资源限制层，不是操作系统级文件系统或网络隔离。需要硬隔离时请选择 `bwrap`/`firejail`/`sandbox-exec`/`docker`（`docker` 仅在 POSIX 宿主上可用，并以当前 `uid:gid` 运行容器，避免工作区出现 root 属主文件），或把 MinimalCode 放进容器、虚拟机或受限账户中运行。
- 执行层关闭时 `local` 的网络 `deny` 不生效；开启后 `deny` 基于命令字符串识别，无法阻止任意程序自行发起网络连接；`allowlist` 同样依赖命令中的主机可被识别。真正的禁网依赖支持网络命名空间的后端。
- 文件工具默认限制在工作区内；这不能替代操作系统级隔离，路径检查与真正打开文件之间存在 TOCTOU 窗口。
- `sandbox` 不清理子进程环境变量，`shell` 仍能看到当前用户的凭据环境变量；不要在不信任的仓库中运行不可信命令。
- `AGENTS.md` / `CLAUDE.md` / `SYSTEM.md` 会进入系统提示词。解析后的文件必须仍位于声明它的目录中，指向目录外的符号链接会被忽略，避免把工作区外文件带进模型上下文。
- `web_fetch` 的目标 URL 由配置的 provider 服务端抓取，而不是由 MinimalCode 直接访问。`web_tools.base_url` 及其凭据属于可信配置，不要指向不可信或能访问敏感内网的服务。
- `read_image` 同样受工作区限制，图片以 base64 data URL 内联发送，受 `max_image_bytes` 约束。请求由 MinimalCode 直接发往 `vision.base_url`（未设置时回退到供应商的 `base_url`），与主协议无关，因此该端点及其凭据属于可信配置。

## 开发

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m compileall -q minimalcode tests main.py
python -m ruff check minimalcode tests benchmarks main.py
python -m ruff format --check minimalcode tests benchmarks main.py
python benchmarks/deepseek_responses.py --runs 3
```

`benchmarks/coding_suite.py --validate-only` 用于在无网络、无密钥的情况下校验基准任务定义。

旧入口仍然可用：

```powershell
python main.py -p "inspect this repository"
```

## 项目结构

```text
minimalcode/
├── cli.py           # 参数解析、管理命令、交互循环
├── agent.py         # 工具循环、用量统计、上下文压缩
├── provider/        # OpenAI Chat / Responses 客户端
│   ├── client.py    #   共享客户端与协议分派
│   ├── chat.py      #   Chat Completions 载荷、流与解析
│   ├── responses.py #   Responses 载荷、事件流与解析
│   ├── transport.py #   HTTP 请求建立与连接重试
│   ├── common.py    #   usage / 内容 / 参数归一化
│   ├── types.py     #   ToolCall、ProviderReply
│   └── factory.py   #   create_provider
├── tools/           # 工作区工具
│   ├── runtime.py       #   共享状态与工具分派
│   ├── schemas.py       #   工具定义
│   ├── files.py         #   read / write / edit
│   ├── search.py        #   子进程搜索与输出解析
│   ├── search_tools.py  #   grep / find
│   ├── process.py       #   shell / subagent
│   ├── remote.py        #   vision / web 工具
│   └── validation.py    #   模型参数校验
├── sandbox/         # 沙箱策略、后端与有界执行
│   ├── runner.py    #   统一入口
│   ├── policy.py    #   路径与网络策略
│   ├── sensitive.py #   敏感文件规则
│   ├── network.py   #   联网命令识别与主机白名单
│   ├── backends.py  #   bwrap / firejail / sandbox-exec / docker
│   ├── execution.py #   子进程捕获、超时与资源限制
│   └── audit.py     #   拒绝与运行记录
├── highlight/       # 流式 Markdown 渲染
│   ├── markdown.py  #   增量渲染状态机
│   ├── code.py      #   围栏代码着色
│   ├── sanitize.py  #   终端控制序列清理
│   ├── color.py     #   ANSI 能力探测
│   └── palette.py   #   深色/浅色调色板
├── shell.py         # shell 探测与子进程执行
├── prompt.py        # 系统提示词与 AGENTS.md 发现
├── session.py       # JSONL 会话存储与检索
├── config.py        # 配置合并、校验、脱敏、原子写入
├── web.py           # 供应商 web_search / web_fetch 客户端
├── vision.py        # 供应商视觉客户端
├── subagent.py      # 子进程命令构建与深度传递
├── importer.py      # Pi 配置迁移
├── errors.py        # 统一异常类型
└── defaults.json    # 内置默认配置
```

四个包的 `__all__` 是唯一公开接口。0.7.0 起不再为拆分前的模块级名字提供回退；私有 helper 从定义它的子模块导入，详见 [docs/compatibility.md](docs/compatibility.md)。

请求链路：CLI → 配置与 shell 探测 → 会话加载/创建 → 供应商流式请求 → 工具调用与执行 → 结果追加到 JSONL → 下一轮模型请求 → 最终文本或 JSON。每次运行只有一个常驻主进程和一个复用的 HTTP 连接池，没有守护进程、嵌入式数据库或后台服务（`shell` 工具会按需启动子进程）。更多细节见 [docs/architecture.md](docs/architecture.md)。

## 文档

- [docs/architecture.md](docs/architecture.md)：请求链路、模块职责与性能取舍。
- [docs/configuration.md](docs/configuration.md)：全部配置字段、默认值与安全边界。
- [docs/compatibility.md](docs/compatibility.md)：0.6 → 0.7 的导入与 patch 迁移对照表。

## 后续工作

- 原生 Anthropic、Gemini 协议适配。
- RPC/SDK 集成。
- 扩展、Skills 和主题系统。
- 更细粒度的工具权限。

## 许可证

[Apache License 2.0](LICENSE)，Copyright 2026 jiaheng0815。
