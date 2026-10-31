# MinimalCode

[![CI](https://github.com/jiaheng0815/MinimalCode/actions/workflows/tests.yml/badge.svg)](https://github.com/jiaheng0815/MinimalCode/actions/workflows/tests.yml)

MinimalCode 是一个面向终端编码任务的轻量 Agent。它优先减少启动开销、交互层级和上下文浪费，不提供炫技式 TUI。

当前版本：`1.3.1`。运行时依赖只有 `httpx2`，Python 3.11–3.13。

## 特性

- **Shell 优先使用 pwsh**：Windows 上按 `pwsh` → `powershell.exe` → Git Bash（`bash`）选择，其他平台按 `pwsh` → `bash` → `zsh` → `sh` 选择；每个候选都会先探测能否启动，启动时解析一次，可在配置中固定路径。
- **聚焦工具集**：`read`、`write`、`edit`、`shell`、`grep`、`find`、`subagent` 七个基础工具，外加可选的 `read_image`、`web_search`、`web_fetch`。`read_image` 不绑定供应商，任何配置了 `vision` 的供应商都能使用。
- **搜索不依赖 shell**：`grep` / `find` 直接用 `rg` 子进程或内置 Python 遍历实现，不经过 pwsh，因此没有引号、通配符和对象管道语义的偏差。
- **纯文本输出**：流式打印，在交互终端中增量渲染 Markdown 高亮，不使用 Rich、Textual 或其他 UI 框架。
- **会话**：追加式 JSONL 自动保存，支持继续最近会话、按 ID 恢复、按工作区隔离。`--session` 只接受 `.jsonl` 文件；文件里记录的 workspace 只有在文件位于配置目录 `sessions/` 之下时才会被信任。
- **上下文管理**：接近上限时自动压缩较早消息，也可手动 `/compact`。
- **会话总结**：`/summary` 让模型把当前会话总结成仓库笔记写入工作区 `AGENTS.md`，已存在时只更新标记块，其余内容不动。
- **规则文件**：读取全局和项目级 `AGENTS.md` / `CLAUDE.md`，每次运行开始前重新读取（文件不存在则跳过）；可用 `SYSTEM.md` 替换默认系统提示词。
- **协议**：OpenAI Chat Completions、OpenAI Responses 与 Anthropic Messages，其他兼容供应商只需配置。
- **多供应商切换**：内置 36 个供应商预设（33 家托管服务 + `ollama` / `lmstudio` / `vllm`），各自的 API key 走对应厂商的环境变量；交互中用 `/model 供应商/模型` 随时切换，会话历史保留。
- **脚本友好**：`--json` 输出机器可读结果，stdout 与工具活动分离。
- **用量可见**：每次任务结束在 stderr 打印本轮 token 用量（prompt、completion、total，以及非零的 cached/reasoning）。供应商没有返回 usage 时按字符宽度估算并标注 `(estimated)`，不会把猜出来的数字当成计费结果。

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

要求 Python 3.11–3.13。安装 `ripgrep`（`rg`）是可选的，但会让 `grep` / `find` 更快，并获得更完整的 ripgrep 语义。

## 升级指南

### 从 1.3.0 升级到 1.3.1

1.3.1 是缺陷修复版本，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，升级不需要改代码。可观察的行为变化只有两处，且都是修正错误行为：stdout 读取端提前关闭（`minimalcode ... | head`）现在按 `0` 退出，不再在 Windows 上打印 `error: [Errno 22] Invalid argument` 并以 `2` 退出；`--home` 写在管理命令前面时（`minimalcode --home DIR sessions`）按管理命令执行，不再把命令词当成提示词发给模型。其余修复（协议解析、压缩边界、搜索回退语义、沙箱网络识别、`trust_env` 布尔转换）都只改变出错场景下的结果。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.2 升级到 1.3

1.3.0 没有删除任何公开名字、CLI 参数、配置键、JSON 字段或会话格式，升级不需要改代码。新增的是交互式剪贴板图片粘贴：空回车读取系统剪贴板里的图片，经供应商 `vision` 配置转成文字描述后随消息发送。可观察的行为变化有四处：JSON API 请求遇到 3xx 现在直接报错而不是跟随跳转（跳转会重发请求体与 `x-api-key` 等认证头）；新建文件按进程 umask 创建，不再固定 `0600`；`config set-model` 同时更新当前供应商的 `model`；恢复会话时沿用会话记录的供应商与模型，显式 `--provider` / `--model` 仍然优先。`requires-python` 收窄为 `>=3.11,<3.14`。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.1 升级到 1.2

1.2.0 没有删除任何公开名字、CLI 参数、配置键或 JSON 字段，升级不需要改代码。新增的是：`anthropic_messages` 协议、`--anthropic-env`（与 `--provider` 互斥）、交互命令 `/model [供应商/模型]`，以及 `defaults.json` 里的 33 家托管供应商加 `ollama`、`lmstudio`、`vllm` 三个本地预设。预设走常规的默认值合并，已有 `settings.json` 里同名的供应商保留自己设置的键；因此对未改动配置的用户，唯一可见的差异是 `config show`、`minimalcode sessions` 和 `/model` 列表里多出了 `deepseek-anthropic` 与这些供应商。`/model` 只切换当前会话，不写回配置。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.0 升级到 1.1

1.1.0 没有删除任何公开名字、CLI 参数、配置键或 JSON 字段，升级不需要改代码。可观察的变化只有两处：系统提示会在每次运行开始时重新读取 `AGENTS.md` / `CLAUDE.md`（此前在进程启动时缓存一次），以及新增的交互命令 `/summary`（只在交互模式下可用，`-p` 一次性运行不受影响）。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 0.8 升级到 1.0

1.0.0 是首个稳定版本。从 1.0 起，CLI 参数、配置键、JSON 输出字段、会话格式，以及 `tools`、`sandbox`、`provider`、`highlight` 四个包的 `__all__` 公开面在 1.x 内保持兼容。0.8 → 1.0 没有删除公开名字或 CLI 参数，但修复项里有两处可观察行为变化：`--json` 的 `usage` 改为本次运行（累计值在 `session_usage`），`import-pi` 默认不再复制 API key。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 0.6 升级到 0.7

0.7.0 把 `tools`、`sandbox`、`provider`、`highlight` 四个大模块拆成了包，并在发布前移除了拆包过程中临时保留的旧名字回退。CLI、配置和工具行为没有变化；只有直接导入内部名字或 patch 内部函数的代码需要迁移：

- `minimalcode.tools`、`minimalcode.sandbox`、`minimalcode.provider`、`minimalcode.highlight` 现在只导出各自 `__all__` 中的公开名称，未知名字直接抛 `AttributeError`。
- 私有 helper 改为从实现模块导入，例如 `minimalcode.tools.search.run_python_grep`、`minimalcode.sandbox.execution._posix_limits`、`minimalcode.highlight.palette._PALETTES`。
- patch 目标要指向实际查找该名字的模块，例如 `minimalcode.tools.search_tools.shutil.which`、`minimalcode.provider.transport.time.sleep`、`minimalcode.highlight.color._supports_color`。

完整对照表见 [docs/compatibility.md](docs/compatibility.md)。

0.7 → 0.8 只补充文档与注释、补全供应商重试策略的测试，公开面与 0.7.0 一致，升级无需改代码。

## 快速开始

推荐 API：[<img src="https://www.deepseek.com/favicon.ico" width="16" height="16" alt="DeepSeek 图标"> DeepSeek 开放平台](https://platform.deepseek.com)

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
minimalcode
```

交互模式启动后会显示供应商/模型、当前工作区、shell 和会话文件路径。

输入支持多行：行尾写 `\` 回车继续下一行（`... ` 提示符），行尾 `\\` 表示字面反斜杠；也可以直接粘贴整段长文本，粘贴块里的换行不会被当作多次提交，整段内容作为一条消息发送。`-p` 或管道输入时不做改写。

如果剪贴板里是图片，先按终端的粘贴快捷键（Windows 为 `Ctrl+V`，macOS 为 `Cmd+V`，多数 Linux 终端为 `Ctrl+Shift+V`）再回车即可发送：终端本身无法把图片粘进输入行，所以 MinimalCode 把空回车当作读取剪贴板的信号。随后可以输入要一起发送的问题再回车；直接回车则让视觉模型完整描述图片。图片不需要事先保存成文件，由供应商 `vision` 配置的视觉模型转成文字描述后随消息一起发给主模型；未配置 `vision` 时该功能不可用。

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
| `--session <id>` | 指定会话：完整 ID、前缀或 `.jsonl` 路径 |
| `--no-session` | 本次运行不读写会话 |
| `--provider <name>` | 覆盖配置中的供应商 |
| `--anthropic-env` | 用 Claude Code 风格的 `ANTHROPIC_*` / `CLAUDE_CODE_*` 环境变量直接启动 Anthropic Messages 协议，不修改配置文件；不能与 `--provider` 同用 |
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
| `--quiet` | 隐藏工具活动和 token 用量摘要 |
| `--no-highlight` | 关闭 Markdown 高亮 |
| `--version` | 打印版本 |

管理命令按第一个词识别，`--home` 可以写在命令前面（`minimalcode --home DIR sessions` 等价于 `minimalcode sessions --home DIR`）；其他选项写在前面时会被当成普通提示词。以命令词开头的提示词需要用 `--` 分隔：`minimalcode -- init config show` 发送的是提示词而不是执行子命令。

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

`import-pi` 默认**不**复制 API key，而是写入指向环境变量的 `api_key_env`；确实要把密钥复制进权限受限的配置时加 `--secrets`（密钥不会在终端打印）。`--pi-home` 指定 Pi 目录，`--overwrite` 允许覆盖已有供应商。

退出码：`0` 任务完成，`1` 未在轮次上限内完成，`2` 配置或运行错误，`130` 被 Ctrl+C 中断。stdout 的读取端提前关闭（例如 `minimalcode ... | head`）按 `0` 退出，不会打印错误或 `Exception ignored`。

JSON 输出字段：

```json
{"text":"...","completed":true,"turns":3,"usage":{},"session_usage":{},"usage_estimated":false,"session":"...jsonl"}
```

`usage` 是本次运行新增的用量，`session_usage` 是整个会话的累计值（`-c` 继续会话时两者不同）；`usage_estimated` 为 `true` 表示至少有一轮没有供应商返回的 usage，数字来自本地估算。

## 交互命令

| 命令 | 作用 |
|---|---|
| `/new`、`/clear` | 新建会话 |
| `/compact` | 立即压缩历史 |
| `/summary` | 让模型总结全部上下文并写入工作区 `AGENTS.md`；文件不存在则创建，已存在则只更新 `<!-- minimalcode:summary:start/end -->` 之间的块，其余内容原样保留 |
| `/session` | 显示会话文件路径 |
| `/effort [none\|minimal\|low\|medium\|high\|xhigh\|ultra\|max]` | 查看或修改本次运行的推理强度；`none` 关闭思考 |
| `/model [供应商/模型]` | 查看当前模型和可用供应商（只列出已检测到凭据的，含免密钥的本地预设）；给出 `供应商/模型` 时切换本次会话，例如 `/model deepseek/deepseek-flash`。只切换当前会话，不写回 `settings.json` |
| `/status` | 显示模型、推理强度、shell、上下文和 token 统计（JSON） |
| `/help` | 显示命令 |
| `/exit`、`/quit` | 退出 |

未知的 `/` 命令只报错，不会结束会话。多行消息的行间换行会原样保留在会话记录里。

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
- 交互模式下也可以直接从剪贴板粘贴图片：按终端的粘贴快捷键后再回车（Windows `Ctrl+V`、macOS `Cmd+V`、多数 Linux 终端 `Ctrl+Shift+V`），不需要先保存文件。MinimalCode 调用同一个 `vision` 配置读取剪贴板图片并生成描述；图片超过 `max_image_bytes`、剪贴板里没有图片或读取失败时会报错并继续会话。
- `prompt` 可选；缺省时要求模型详细描述图片并逐字转写可见文字，结果超过 `max_chars` 时截断并标记 `<description truncated>`。
- `read_image` 和其他文件工具一样受工作区限制，读取工作区外的图片需要 `tools.allow_outside_workspace`。
- 内置默认配置只为 DeepSeek 打开了 `vision`（`deepseek-flash`），其他供应商默认关闭，需要时按上面的结构自行添加。

### 实现要点

- **工作区约束**：所有接受路径的工具都经同一处校验。相对路径以工作区为基准，解析后必须位于工作区内；`tools.allow_outside_workspace: true` 可放行外部路径，但仍受敏感文件策略约束。`shell` 与 `subagent` 同样经过沙箱入口，命令的工作目录和解析后的符号链接都在允许根内。`--no-sandbox` 只关闭执行层（后端包装、网络策略、资源限制），工作区与敏感文件边界照旧生效。
- **沙箱执行层**：`sandbox.enabled` 默认关闭（等同默认 `--no-sandbox`），`--sandbox` 或任意 `--sandbox-*` 选项会开启它；开启后默认后端 `local`（跨平台策略与子进程限制基线），可选 `auto`（Linux 依次尝试 `bwrap`、`firejail`，macOS 尝试 `sandbox-exec`，否则 `local`）以及 `bwrap`、`firejail`、`sandbox-exec`、`docker`。默认网络策略 `deny`，默认拒绝 `.env`、私钥、`.ssh/`、`.aws/` 等敏感路径。开启时路径逃逸、符号链接逃逸、敏感文件、网络命令、后端缺失都会 fail-closed 拒绝并记录原因。命令统一受 `sandbox.timeout_seconds`、`sandbox.max_output_chars` 和子进程资源上限约束，临时目录用后删除。详见 `docs/configuration.md` 的 Sandbox 一节。
- **grep / find**：检测到 `rg` 时直接以参数数组调用子进程（`rg --line-number --no-heading --color never --smart-case ...` 和 `rg --files --glob ...`），不拼接命令行字符串；没有 `rg` 时退回 Python 的 `re` + `os.walk`，并用独立子进程执行正则，超时会终止。两条路径跳过相同的忽略目录（`.git`、`node_modules`、`build`、`dist`、缓存和虚拟环境），支持相同的 smart-case 语义，且都不解析 `.gitignore`；显式把 `path` 指向 `build` 等目录时仍会搜索该目录。单行结果按 300 字符截断，`rg`/`find` 输出达到上限即停止读取。
- **shell**：自动探测会先用一条 `exit 0` 验证候选 shell 能否真正启动，`pwsh` 因缺少 libicu、.NET 无法预留内存或架构不符而崩溃时会继续尝试 `bash` → `zsh` → `sh`（Windows 为 `powershell` → Git Bash）；探测结果按可执行文件缓存，每个进程只付一次启动开销（pwsh 约 0.2 秒）。显式配置的 `shell.path` 同样会验证，启动失败会直接报出原因而不是静默换 shell。`pwsh`/`powershell` 以 `-NoLogo -NoProfile -NonInteractive -Command` 启动，并在命令前注入 bootstrap：关闭进度条、`$ErrorActionPreference='Stop'`、控制台输出编码设为无 BOM 的 UTF-8。bash/zsh/sh 分别使用 `-c` 及各自的 no-profile 参数。stdout/stderr 由后台线程持续读取，只在内存中保留首尾有界片段，因此超长输出不会占满磁盘或内存，超时后仍能返回已产生的部分输出。超时会终止整个进程树（Windows 用 `taskkill /T`，POSIX 用进程组）。
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

- `protocol` 取 `openai_chat`、`openai_responses` 或 `anthropic_messages`。
- 密钥优先用 `api_key_env` 指向环境变量；直接写 `api_key` 也支持，但要注意文件权限。密钥不会由 `init` 写入配置。
- 任意字符串值支持 `${VAR}` 环境变量占位符，未解析时会明确报出变量名，而不是把占位符发给供应商。
- `include_usage` 仅在服务端接受 `stream_options` 时开启（仅 Chat 使用）；`send_reasoning_effort` 仅在供应商支持时开启。Responses 模式下的 `thinking` 只有配合 `send_reasoning_effort: true` 才会作为 `reasoning.effort` 发送；`none` 会发送 `reasoning.effort: "none"`，Chat 模式则发送 `thinking: {"type": "disabled"}` 关闭思考。Anthropic 模式同样由 `send_reasoning_effort` 控制是否发送 `thinking`。
- `max_tokens` 仅 Anthropic Messages 使用，默认 `8192`。官方 Anthropic API 要求该字段；`thinking` 的 `budget_tokens` 会按 `thinking` 等级映射，并保证小于 `max_tokens`。
- 其他兼容供应商沿用同一结构，替换 `base_url`、模型和密钥环境变量即可。
- 代理：供应商、`web_tools`、`vision` 默认沿用环境变量与系统代理设置。`base_url` 指向环回地址（`127.0.0.1`、`::1`、`localhost`）时自动忽略代理，否则本机运行的 Ollama、vLLM 或测试服务器会被 Clash 之类的代理劫走；需要强制时可在对应配置块里写 `"trust_env": true|false`。显式传入自建 transport 时默认不读环境代理，避免代理映射覆盖注入的连接。

### 内置供应商预设

`defaults.json` 已经为常用供应商准备好端点、协议和密钥环境变量。它们不会猜模型 ID：交互中用 `/model 供应商/模型` 时显式给出模型，例如：

```text
/model deepseek/deepseek-flash
/model openai/<model-id>
/model anthropic/<model-id>
/model moonshot/<model-id>
```

| 供应商 | API key 环境变量 | 端点 |
|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1`（Responses） |
| `deepseek-anthropic` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/anthropic`（Anthropic Messages） |
| `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1`（Responses） |
| `anthropic` | `ANTHROPIC_API_KEY` | `https://api.anthropic.com/v1`（Anthropic Messages） |
| `google` | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| `groq` | `GROQ_API_KEY` | `https://api.groq.com/openai/v1` |
| `mistral` | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` |
| `xai` | `XAI_API_KEY` | `https://api.x.ai/v1` |
| `moonshot` | `MOONSHOT_API_KEY` | `https://api.moonshot.cn/v1` |
| `zhipu` | `ZHIPUAI_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` |
| `dashscope` | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `siliconflow` | `SILICONFLOW_API_KEY` | `https://api.siliconflow.cn/v1` |
| `together` | `TOGETHER_API_KEY` | `https://api.together.ai/v1` |
| `fireworks` | `FIREWORKS_API_KEY` | `https://api.fireworks.ai/inference/v1` |
| `perplexity` | `PERPLEXITY_API_KEY` | `https://api.perplexity.ai` |
| `cerebras` | `CEREBRAS_API_KEY` | `https://api.cerebras.ai/v1` |
| `sambanova` | `SAMBANOVA_API_KEY` | `https://api.sambanova.ai/v1` |
| `nebius` | `NEBIUS_API_KEY` | `https://api.studio.nebius.com/v1` |
| `deepinfra` | `DEEPINFRA_TOKEN` | `https://api.deepinfra.com/v1/openai` |
| `novita` | `NOVITA_API_KEY` | `https://api.novita.ai/openai/v1` |
| `cohere` | `COHERE_API_KEY` | `https://api.cohere.ai/compatibility/v1` |
| `github` | `GITHUB_TOKEN` | `https://models.github.ai/inference` |
| `volcengine` | `ARK_API_KEY` | `https://ark.cn-beijing.volces.com/api/v3` |
| `minimax` | `MINIMAX_API_KEY` | `https://api.minimax.io/v1` |
| `hunyuan` | `HUNYUAN_API_KEY` | `https://api.hunyuan.cloud.tencent.com/v1` |
| `qianfan` | `QIANFAN_API_KEY` | `https://qianfan.baidubce.com/v2` |
| `modelscope` | `MODELSCOPE_API_KEY` | `https://api-inference.modelscope.cn/v1` |
| `baichuan` | `BAICHUAN_API_KEY` | `https://api.baichuan-ai.com/v1` |
| `yi` | `YI_API_KEY` | `https://api.lingyiwanwu.com/v1` |
| `stepfun` | `STEP_API_KEY` | `https://api.stepfun.com/v1` |
| `spark` | `SPARK_API_PASSWORD` | `https://spark-api-open.xf-yun.com/v1` |
| `ppio` | `PPIO_API_KEY` | `https://api.ppio.com/openai/v1` |
| `ollama` | 无需密钥 | `http://localhost:11434/v1` |
| `lmstudio` | 无需密钥 | `http://localhost:1234/v1` |
| `vllm` | 无需密钥 | `http://localhost:8000/v1` |

预设只固定端点和密钥变量名，`context_window` 保持一个保守值（本地三家为 `32768`，其余按各家公开的主推窗口填写），换到更大或更小的模型时按需在 `settings.json` 里调整。区域或套餐端点不同时（例如 MiniMax 国内站 `https://api.minimax.cn/v1`、StepFun 国际站 `https://api.stepfun.ai/v1`、火山方舟 `step_plan` 端点），覆盖 `base_url` 即可；Cohere 的兼容端点不接受 `stream_options`，所以预设里 `include_usage` 为 `false`。

`/model` 切换的是当前会话，不会修改 `settings.json`；要改默认值仍用 `minimalcode config set-provider` / `set-model`。切换后子代理也会继承新的供应商和模型。新供应商如果没有配置 `web_tools` / `vision`，对应的 `web_search`、`web_fetch`、`read_image` 工具会从工具列表中移除，切回带这些配置的供应商后恢复。

Anthropic Messages 模式可以直接接入 DeepSeek 的 Anthropic 兼容端点，内置预设 `deepseek-anthropic`：

```json
"deepseek-anthropic": {
  "protocol": "anthropic_messages",
  "base_url": "https://api.deepseek.com/anthropic",
  "api_key_env": "DEEPSEEK_API_KEY",
  "model": "deepseek-flash",
  "context_window": 1000000,
  "timeout_seconds": 120,
  "stream": true,
  "send_reasoning_effort": true,
  "max_tokens": 8192,
  "headers": {},
  "extra_body": {}
}
```

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
minimalcode --provider deepseek-anthropic "运行测试并总结"
```

端点从 `base_url` 推导：`https://api.deepseek.com/anthropic` 会请求 `/anthropic/v1/messages`；`base_url` 已以 `/v1` 结尾时只补 `/messages`，已经是完整 `/messages` 地址时原样使用。鉴权发送 `x-api-key`，并附带 `anthropic-version: 2023-06-01`（自定义 `headers` 中的同名字段优先）。工具定义会转换为 Anthropic 的 `name` / `description` / `input_schema`，连续的工具结果会合并到同一条 user 消息，且 `tool_result` 块放在文本之前。思考模式下通过工具调用继续对话时，assistant 的 thinking 块（含 signature）会保存在会话中并原样回传，以保证 Anthropic 的多轮工具调用可以继续。

### 从 Claude Code 环境变量启动

如果本机已经按 [DeepSeek 的 Claude Code 接入文档](https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/claude_code)配好了环境变量，可以跳过配置文件直接用：

```powershell
$env:ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
$env:ANTHROPIC_AUTH_TOKEN="<你的 DeepSeek API Key>"
$env:ANTHROPIC_MODEL="deepseek-flash[1m]"
$env:CLAUDE_CODE_EFFORT_LEVEL="max"
$env:CLAUDE_CODE_AUTO_COMPACT_WINDOW="786432"

minimalcode --anthropic-env
```

`--anthropic-env` 只在本进程内合成一个名为 `anthropic-env` 的供应商，不读写 `settings.json`，因此普通启动（不带该参数）仍然使用文件配置；它也不能与 `--provider` 同时使用。

| 环境变量 | 作用 |
|---|---|
| `ANTHROPIC_BASE_URL` | 端点根，默认 `https://api.deepseek.com/anthropic` |
| `ANTHROPIC_AUTH_TOKEN` | Bearer 凭据，作为 `Authorization: Bearer` 发送；与 `ANTHROPIC_API_KEY` 同时存在时优先 |
| `ANTHROPIC_API_KEY` | 作为 `x-api-key` 发送（两者至少需要一个） |
| `ANTHROPIC_MODEL` | 模型名；缺省时回退到 `ANTHROPIC_DEFAULT_SONNET_MODEL`，再缺省则报错。`--model` 优先级最高 |
| `CLAUDE_CODE_EFFORT_LEVEL` | 推理强度，取值同 `--thinking`；`--thinking` 优先 |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | 自动压缩阈值（token），换算成 `context.compact_at`，只在本次运行生效 |
| `ANTHROPIC_CONTEXT_WINDOW` | 覆盖上下文窗口（MinimalCode 扩展项） |
| `ANTHROPIC_MAX_TOKENS` | 覆盖输出上限，默认 `8192`（MinimalCode 扩展项） |

模型名里的 `[1m]` / `[200k]` 这类 Claude Code 后缀只用于选择上下文窗口，发送请求前会被去掉（`deepseek-flash[1m]` → `deepseek-flash`，窗口 1000000）；不带后缀时默认按 Anthropic 标准的 200000。`--anthropic-env` 不读取配置文件里的 `web_tools` / `vision`，因此这次运行没有 `web_search`、`web_fetch` 和 `read_image` 工具；需要这些工具时仍用 `--provider deepseek-anthropic`。

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
- 网络策略 `deny` / `allowlist` 只检查命令位置上的可执行文件名与少量已知联网子命令（`pip install`、`git clone`、`npm install` 等）。因此 `rg curl`、`git log --grep=curl` 这类把网络命令名当参数或搜索词的命令会被放行，引号内的 `|` / `;` 也不会被拆成多条命令（`rg 'nc|netcat'` 同样放行）；而 `echo x | curl ...`、`pwsh -Command "iwr ..."`、`sudo wget ...`、`busybox wget ...`、`sh -c "sudo curl ..."` 仍会被拒绝。它不是网络防火墙。
- `AGENTS.md` / `CLAUDE.md` / `SYSTEM.md` 会进入系统提示词，并在每次运行开始前重新读取（文件不存在则跳过）。解析后的文件必须仍位于声明它的目录中，指向目录外的符号链接会被忽略，避免把工作区外文件带进模型上下文。`/summary` 写入 `AGENTS.md` 的路径同样经过这层校验。
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

`benchmarks/coding_suite.py --validate-only` 用于在无网络、无密钥的情况下校验基准任务定义。结果报告只列出实际运行过的 agent：只跑了 MinimalCode 时不会输出与 Pi 的对比列。

旧入口仍然可用：

```powershell
python main.py -p "inspect this repository"
```

## 项目结构

```text
minimalcode/
├── cli.py           # 参数解析、管理命令、交互循环
├── agent.py         # 工具循环、用量统计、上下文压缩
├── provider/        # OpenAI Chat / Responses 与 Anthropic Messages 客户端
│   ├── client.py    #   共享客户端与协议分派
│   ├── chat.py      #   Chat Completions 载荷、流与解析
│   ├── responses.py #   Responses 载荷、事件流与解析
│   ├── anthropic.py #   Anthropic Messages 载荷、流与解析
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
├── httpclient.py    # HTTP 客户端构造、代理策略与连接重试
├── prompt.py        # 系统提示词与 AGENTS.md 发现
├── session.py       # JSONL 会话存储与检索
├── config.py        # 配置合并、校验、脱敏、原子写入
├── web.py           # 供应商 web_search / web_fetch 客户端
├── vision.py        # 供应商视觉客户端
├── clipboard.py     # 系统剪贴板图片读取
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
- [docs/compatibility.md](docs/compatibility.md)：1.0 兼容承诺、公开面定义，以及 0.6 → 0.7 的导入与 patch 迁移对照表。

## 后续工作

- 原生 Gemini 协议适配。
- RPC/SDK 集成。
- 扩展、Skills 和主题系统。
- 更细粒度的工具权限。

## 许可证

[Apache License 2.0](LICENSE)，Copyright 2026 jiaheng0815。
