# MinimalCode

[![CI](https://github.com/jiaheng0815/MinimalCode/actions/workflows/tests.yml/badge.svg)](https://github.com/jiaheng0815/MinimalCode/actions/workflows/tests.yml)

MinimalCode 是一个面向终端编码任务的轻量 Agent。它优先减少启动开销、交互层级和上下文浪费，不提供炫技式 TUI。

当前版本：`1.11.1`。运行时依赖只有 `httpx2`（`deepseek_web` 协议额外需要可选的 `wasmtime`），Python 3.11–3.13。

## 目录

- [特性](#特性)
- [安装](#安装)
- [快速开始](#快速开始)
- [网页界面](#网页界面)
- [CLI 参考](#cli-参考)
- [交互命令](#交互命令)
- [工具](#工具)
- [配置](#配置)
  - [同时使用多家 API](#同时使用多家-api)
  - [自定义接入点](#自定义接入点)
  - [内置供应商预设](#内置供应商预设)
  - [从 Claude Code 环境变量启动](#从-claude-code-环境变量启动)
- [终端高亮](#终端高亮)
- [安全边界](#安全边界)
- [桌宠集成（Clawd on Desk）](#桌宠集成clawd-on-desk)
- [开发](#开发)
- [项目结构](#项目结构)
- [升级指南](#升级指南)
- [后续工作](#后续工作)

## 特性

### 接入与协议

- **协议**：OpenAI Chat Completions、OpenAI Responses、Anthropic Messages 与 DeepSeek 网页版（`deepseek_web`），其他兼容供应商只需配置。Anthropic 供应商可加 `"prompt_cache": true`，把 system 提示与工具定义标记为一个缓存前缀，工具循环里每轮重发的大段静态内容按缓存读取计费；默认关闭，因为并非所有 Messages 兼容端点都接受 `cache_control`。
- **多供应商切换**：内置 38 个供应商预设（34 家托管服务 + `ollama` / `lmstudio` / `vllm` / `ninfer` 四个本地服务），各自的 API key 走对应厂商的环境变量；交互中用 `/model 供应商/模型` 随时切换，会话历史保留。
- **同时使用多家 API**：一个会话只用一个聊天接入点，但其它已配置的接入点仍可各司其职。只要配置里有一个 DeepSeek API 接入点（当前对话不在它上面也行），OCR、语音识别、语音朗读和默认的独立 Search/Fetch 联网工具就由那个接入点提供；服务端联网工具、文档解析、内容安全和会话标题这类「写在请求里」的能力，仍然要求当前聊天接入点就是 `api.deepseek.com`。详见[同时使用多家 API](#同时使用多家-api)。
- **自定义 API**：`minimalcode config add-provider` 或网页端的「自定义 API…」，写入任意 OpenAI 兼容、Anthropic 兼容或 DeepSeek 接入点。只新增 `providers.<name>`，内置预设保持不变。详见[自定义接入点](#自定义接入点)。

### 工具与执行

- **Shell 优先使用 pwsh**：Windows 上按 `pwsh` → `powershell.exe` → Git Bash（`bash`）选择，其他平台按 `pwsh` → `bash` → `zsh` → `sh` 选择；每个候选都会先探测能否启动，启动时解析一次，可在配置中固定路径。Windows 上会跳过 `C:\Windows\System32\bash.exe`（WSL 启动器），它看起来像 bash，但会把命令送进 Linux 命名空间。
- **工具白名单与只读模式**：`tools.allow` 列出允许的工具名，`--read-only` 只保留 `read`、`read_range`、`grep`、`find`、`read_image`、`web_search`、`web_fetch`、`ocr`、`transcribe`；两者取交集。被禁用的工具既不发给模型，直接在 `dispatch` 里调用也会被拒绝，因此恢复的旧会话或忽略 schema 的供应商都绕不过去。
- **搜索不依赖 shell**：`grep` / `find` 直接用 `rg` 子进程或内置 Python 遍历实现，不经过 pwsh，因此没有引号、通配符和对象管道语义的偏差。同一轮里相邻的只读工具会并发执行（上限 `tools.max_parallel`，默认 4），结果仍按请求顺序写回；只读调用旁边出现 `write` / `shell` / `subagent` 时保持严格顺序。
- **搜索语义一致**：有 `rg` 与没有 `rg` 时跳过相同的忽略目录，使用相同的 smart-case 与 glob 语义（`*` 不跨目录、`**/` 可匹配零层、大小写敏感），都不解析 `.gitignore`，也都不受 `RIPGREP_CONFIG_PATH` 影响。
- **编码可见**：`read` 用严格 UTF-8 先扫一遍请求范围，遇到非 UTF-8 字节时改用替换解码返回内容，并在 `warnings` 里说明 `edit` 会拒绝该文件；UTF-8 BOM 会被去掉，不再出现在第一行。
- **写入即校验**：`write`、`edit`、`append_file` 写入 Python 文件后先跑 `py_compile`，装有时再跑 `ruff`；出现新诊断就原子回滚并返回文件、行号和源码预览。改动前已经存在的 lint 问题不会阻塞无关编辑。

### 联网与多模态

- **联网工具**：DeepSeek 预设默认启用独立的 Search/Fetch API，`web_search` / `web_fetch` 作为普通函数工具参与 Agent 工具循环；这条路径适用于带自定义工具调用的 Agent，按 DeepSeek 的成功请求价格计费。`server_tools` 仍可显式打开 `deepseek_web_search` / `deepseek_read_url`，但它面向 Chatbot 会话，只在关闭独立 `web_tools` 时生效。
- **网页端 DeepSeek 能力**（请求侧仅 `api.deepseek.com`）：网页端有「联网搜索」「内容安全」开关、文档上传（Files API + `file_id`）、OCR 取字、语音输入（自拼 16 位 PCM WAV）与朗读回答。换成不支持的聊天接入点时，请求侧开关直接隐藏并给出原因，不会发出注定 400 的请求。
- **搜索质量**：结果按 `rerank_score` 排序后去重，镜像页只保留分数最高的一份；`min_rerank_score` 可过滤低质结果；`web_search` 的 query 支持 `site:`、`-site:`、`-词`、`"精确短语"`、`lang:zh,en`、`after:`、`before:` 等搜索语法。
- **文件与语音**（DeepSeek 工具 API）：`ocr` 逐页识别 PDF / Word / PowerPoint / 图片；`transcribe` 把音频转写成文字（语音输入）；`speech` 把文本合成为音频写入工作区（语音输出）；`/attach` 上传文档拿 `file_id`，随下一条消息交给模型按原始版式阅读（含文档里的图）；`/listen` 转写后直接作为消息发送，`/say` 朗读指定文本或上一条回答。图像、OCR、语音与网页四个客户端共用一套代理与重试策略。这些接口按页 / 按音频秒 / 按字符计费，所以**不做自动重试**，超限文件在离开本机前就被拒绝。
- **图像理解**：`read_image` 交给供应商 `vision` 配置指向的任意 OpenAI 兼容端点，格式按文件内容判断（JPEG / PNG / GIF / WebP）；交互模式下空回车可以直接读取剪贴板里的图片。详见[图像理解](#图像理解)。

### 会话与上下文

- **会话**：追加式 JSONL 自动保存，支持继续最近会话、按 ID 恢复、按工作区隔离。`--session` 只接受 `.jsonl` 文件；文件里记录的 workspace 只有在文件位于配置目录 `sessions/` 之下时才会被信任。压缩后如果文件比保留的状态大一倍以上（且超过 64 KiB），会原子重写成「meta + 压缩状态」，避免把同一段历史存两遍；小会话保持纯追加。
- **上下文管理**：接近上限时自动压缩较早消息，也可手动 `/compact`；`/summary` 让模型把当前会话总结成仓库笔记写入工作区 `AGENTS.md`，已存在时只更新标记块，其余内容不动。
- **推理进入上下文**：推理内容在终端按 `h6` 样式显示，不进入 `--json`。工具循环里它必须回传：Chat 把本轮的 `reasoning_content` 存到带 `tool_calls` 的助手消息上并在下一次请求原样发出（DeepSeek 的要求，缺了它思考模型会重复或推翻自己的计划），Anthropic Messages 回传带 `signature` 的 thinking 块。已经结束的回答不保留，避免把上一轮的推理塞进新请求。
- **会话标题与元信息**：DeepSeek 预设开启 `session_title` 后，会话的第一次请求会让供应商顺带生成一个短标题，写入独立的 `title` 事件，`minimalcode sessions` 会显示它；`reminder` 配置让每个用户回合前带上一条 `latest_reminder`（日期、星期、所在地、平台、语言），星期按该语言自身写法输出。`safety_check.level: high` 打开服务端内容风控，拒答会被当作回合提前结束上报。
- **规则文件**：读取全局和项目级 `AGENTS.md` / `CLAUDE.md`，每次运行开始前重新读取（文件不存在则跳过）；可用 `SYSTEM.md` 替换默认系统提示词。

### 界面与可观测性

- **网页界面**：`minimalcode --web` 在本地起一个聊天页面（一个 HTML + 一个 CSS + 一个 JS，随 wheel 一起安装，没有构建步骤与前端依赖）。页面里可以直接换 API 厂家、API 格式、模型和 API Key，也可以选一个本地文件夹作为 agent 的工作目录；工具仍走同一套 `ToolRuntime`、沙箱、白名单与只读模式，页面只是同一个 agent 的另一个前端。
- **纯文本输出**：流式打印，在交互终端中增量渲染 Markdown 高亮，不使用 Rich、Textual 或其他 UI 框架；stdout 只放回答，工具活动走 stderr。
- **脚本友好**：`--json` 输出机器可读结果，stdout 与工具活动分离。
- **用量可见**：每次任务结束在 stderr 打印本轮 token 用量（prompt、completion、total，以及非零的 cached/reasoning）。供应商没有返回 usage 时按字符宽度估算并标注 `(estimated)`，不会把猜出来的数字当成计费结果。

### 扩展

- **MCP 工具**：`mcp.servers` 里的 Model Context Protocol 服务器会以 `mcp__<服务器>__<工具>` 出现在工具列表里，支持 `stdio`、streamable HTTP 和旧版 HTTP+SSE 三种传输。启动失败的服务器只记录错误、不影响其余工具；`/mcp` 查看状态，`/mcp refresh` 重新发现工具。未配置服务器时不启动任何进程或线程。
- **子代理**：`subagent` 在子 shell 里启动一个新的 `minimalcode -p`，只把最终答复带回主会话；同一轮里相邻的多个调用并发执行，结果按请求顺序写回。

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

要求 Python 3.11–3.13。安装 `ripgrep`（`rg`）是可选的，但会让 `grep` / `find` 更快，并获得更完整的 ripgrep 语义。`deepseek_web` 协议需要可选的 wasm 运行时：`python -m pip install "minimalcode[deepseek-web]"`。

## 快速开始

推荐 API：[<img src="https://www.deepseek.com/favicon.ico" width="16" height="16" alt="DeepSeek 图标"> DeepSeek 开放平台](https://platform.deepseek.com)

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
minimalcode
```

交互模式启动后会显示供应商/模型、当前工作区、shell 和会话文件路径。

输入支持多行：行尾写 `\` 回车继续下一行（`... ` 提示符），行尾 `\\` 表示字面反斜杠；也可以直接粘贴整段长文本，粘贴块里的换行不会被当作多次提交，整段内容作为一条消息发送。交互式终端里长行自动换行后，退格删到下一行第一个字符时会回到上一行末尾继续删除；CJK 宽字符按实际终端宽度排版。`-p` 或管道输入仍按普通文本读取，不做交互式改写。

如果剪贴板里是图片，先按终端的粘贴快捷键（Windows 为 `Ctrl+V`，macOS 为 `Cmd+V`，多数 Linux 终端为 `Ctrl+Shift+V`）再回车即可发送：终端本身无法把图片粘进输入行，所以 MinimalCode 把空回车当作读取剪贴板的信号。随后可以输入要一起发送的问题再回车；直接回车则让视觉模型完整描述图片。图片不需要事先保存成文件，由供应商 `vision` 配置的视觉模型转成文字描述后随消息一起发给主模型；未配置 `vision` 时该功能不可用。

单次任务：

```powershell
minimalcode "找出这个项目测试失败的原因并修好"
minimalcode -p "运行测试并总结" --json
```

`-p/--print` 表示非交互执行，必须同时给出提示词或通过标准输入管道传入；`--json` 隐含该模式。

## 网页界面

```powershell
minimalcode --web
minimalcode --web --web-port 9000 --cwd D:\work\repo --provider deepseek -m deepseek-flash
```

打开 `http://127.0.0.1:8090/` 就是一个聊天页面，界面按 [deepseek-ai/chat-demo](https://github.com/deepseek-ai/chat-demo) 做：左侧 261px 侧边栏（品牌名、开启新对话、新建文件夹、文件夹 + 对话的两级会话列表、底部设置），中间 840px 消息列，用户消息是右对齐气泡、助手回答渲染 Markdown，底部是 24px 圆角输入框加圆形发送按钮，浅色 / 深色两套主题。它和终端里是同一个 agent：

- 侧边栏是「文件夹 / 对话」两级：先在「新建文件夹」建一个分类，再把对话移进去（对话悬停出现「移入文件夹」与「删除对话」两个按钮，文件夹标题悬停可以重命名或删除；删除文件夹只是把里面的对话放回「未分类」）。点一条对话就接着聊，点文件夹标题折叠或展开，折叠状态记在浏览器本地。文件夹与归属存在配置目录的 `webui/folders.json`，会话 JSONL 文件本身不动，所以终端里的 `--continue` 看到的还是同一份记录。
- 「删除对话」会真的把这条会话从 `sessions/` 里删掉（不可撤销），删掉的正好是当前打开的那条时页面回到新对话状态。
- 设置（左侧栏底部）分「模型设置」和「通用设置」两页：**API 厂家**、**API 格式**、**模型**、**API Key**，以及主题和工作目录。保存后写回 `settings.json`（只写你改动的键，`${VAR}` 占位符与内置预设保持原样），下一次请求立即生效，不用重启；关掉页面再打开也还在。
- 「API 厂家」列表末尾是「自定义 API…」：填一个名称（`[A-Za-z0-9][A-Za-z0-9._-]{0,31}`）和接入地址，选好格式与模型，保存后它就和内置预设一样出现在列表里并立即切换过去。远程接入点必须带 API Key，`127.0.0.1` / `localhost` 这类本地服务可以留空（会写成 `require_api_key: false`）。名称重复会被拒绝，内置预设不会被覆盖。
- 「联网搜索」在两条路任一可用时出现：默认是任一 DeepSeek 接入点启用的 `web_tools`（本进程调用独立 Search/Fetch API，工具名是 `web_search` / `web_fetch`）；显式关闭 `web_tools` 后，当前聊天接入点若是 DeepSeek API，则可用服务端 `deepseek_web_search` / `deepseek_read_url`。「内容安全」只在当前聊天接入点是 `api.deepseek.com` 时出现，打开后发送 `safety_check.level: high`，命中风险时拒答。两者都不再可用时按钮直接隐藏，悬停提示原因，不会发出注定 400 的请求。
- 输入框上方还有附件、OCR 与语音入口：附件走 Files API 上传，拿到的 `file_id` 随下一条消息交给模型按原始版式阅读（含文档里的图）；OCR 识别 PDF / Office / 图片里的文字后直接填进输入框；麦克风录一段话（前端自拼 16 kHz 单声道 WAV）转写成文字再发送；回答下方可以朗读。这些接口是独立的 HTTP 调用，所以当前对话换成别的厂家、而配置里仍有一个 DeepSeek API 接入点时，OCR / 语音 / 朗读照常可用（由那个接入点的 Key 与地址执行）；附件和内容安全因为要写在聊天请求里，仍然跟着当前接入点走。
- `Enter` 发送、`Shift+Enter` 换行；回答按 SSE 流式追加。每次模型请求在页面上是一个独立段落，段内固定「思考 → 正文 → 工具」，所以工具循环的多轮不会把思考和正文各堆成一大块，正文也不会跑进思考区。思考块默认展开、刷新后仍然在，点标题（或聚焦后按 `Enter`/空格）折叠成「已思考（用时 N 秒）」，再点一次展开；折叠过一次之后新页面也跟着折叠，这个偏好只存在浏览器本地。
- `--web-host` 默认只绑定 `127.0.0.1`，`Host`/`Origin` 不匹配的请求一律 403：页面能执行工具，所以不能被别的网页跨域驱动。确实要从别的机器访问时用 `--web-host 0.0.0.0`，并自己想清楚谁能打开这个端口。

`--web` 不能和提示词、`--session`、`--continue` 一起用；`--read-only`、`--sandbox-*`、`--no-session`、`--provider`、`--model` 仍然生效（它们只决定页面启动时的初始状态）。

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
| `--attach <path>` | 首轮之前上传并解析该文档，`file_id` 随第一条用户消息附带；可重复。适合非交互（`-p`）运行 |
| `--thinking <effort>` | 覆盖推理强度：`none`（关闭）、`minimal`、`low`、`medium`、`high`、`xhigh`、`ultra`、`max` |
| `--max-turns <n>` | 覆盖最大工具轮次（1–10000） |
| `--read-only` | 只保留 `read`、`read_range`、`grep`、`find`、`read_image`、`web_search`、`web_fetch`、`ocr`、`transcribe`，以及 MCP 服务器标记为只读的工具；拒绝任何可能改动工作区的工具 |
| `--cwd <dir>` | 指定工作区 |
| `--home <dir>` | 指定配置目录，默认 `~/.MinimalCode` |
| `--web` | 启动本地网页界面代替终端交互；不能与提示词、`--session`、`--continue` 同用 |
| `--web-host <host>` | 网页界面绑定地址，默认 `127.0.0.1`（只绑回环） |
| `--web-port <port>` | 网页界面端口，默认 `8090`；`0` 表示由系统分配 |
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
| `minimalcode config add-provider <name> --base-url URL [--protocol P] [--model M] [--api-key-env VAR\|--api-key KEY]` | 新增一个自定义接入点并设为默认供应商 |
| `minimalcode sessions` | 列出已保存会话（时间、ID、模型、工作区） |
| `minimalcode shell` | 显示探测到的 shell |
| `minimalcode sandbox` | 打印当前沙箱策略与各后端可用性 |
| `minimalcode mcp [--cwd DIR]` | 启动配置的 MCP 服务器并打印状态、协议版本与工具清单（JSON），不发起模型请求 |
| `minimalcode clawd [status\|install\|remove\|test]` | 查看、注册或撤回 Clawd on Desk 桌宠集成（`install` / `remove` 要在 Clawd 退出时执行） |
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
| `/status` | 显示模型、推理强度、shell、上下文、token 统计、工具列表与 MCP 服务器状态（JSON） |
| `/mcp [refresh]` | 显示每个 MCP 服务器的状态；`refresh` 重新拉取工具列表（并重试启动失败的服务器），重建本会话的工具定义 |
| `/attach <路径> [用途]` | 上传并解析文档（PDF / Word / PowerPoint / 图片 / 表格 / 电子书），`file_id` 随下一条消息发给模型；`rich_layout_recognition` 会把文档里的图按原位置一起交给模型 |
| `/listen <音频路径>` | 把音频转写成文字，并把转写结果作为本轮消息发送（语音输入） |
| `/say [文本]` | 把文本（省略时为上一条回答）合成为语音，写入工作区 `speech-<时间戳>.wav` |
| `/help` | 显示命令 |
| `/exit`、`/quit` | 退出 |

未知的 `/` 命令只报错，不会结束会话。多行消息的行间换行会原样保留在会话记录里。

## 工具

| 工具 | 作用 | 主要参数 |
|---|---|---|
| `read` | 按行范围读取 UTF-8 文本，输出带行号并报告 `line_ending`；非 UTF-8 内容会带 `warnings` 提示 | `path`、`start_line`、`end_line` |
| `read_range` | 读取显式行区间（带行号），用于行号编辑前确认目标 | `path`、`start_line`、`end_line` |
| `write` | 创建或整体覆盖文件 | `path`、`content` |
| `append_file` | 在 EOF 追加文本，文件不存在时创建，保留既有换行风格 | `path`、`content` |
| `edit` | 按行号替换区间，`end_line = start_line - 1` 表示在该行前插入；`old_text` 是精确文本兜底，两者不能混用 | `path`、`start_line`、`end_line`、`new_text`、`old_text`、`replace_all` |
| `shell` | 在配置的 shell 中执行命令 | `command`、`timeout`、`cwd` |
| `grep` | 正则搜索文本，返回 `path:line:text` | `pattern`、`path`、`glob`、`max_results` |
| `find` | 按 glob 查找文件 | `pattern`、`path`、`max_results` |
| `web_search` | DeepSeek 预设默认由 `web_tools` 提供，本进程调用独立 Search API；关闭 `web_tools` 后可退回服务端 `deepseek_web_search` | `query`、`count`、`need_content`、`ori_question`、`localization` |
| `web_fetch` | 默认由 `web_tools` 提供，本进程调用 Fetch API；关闭 `web_tools` 后可退回服务端 `deepseek_read_url` | `url` |
| `ocr` | 逐页识别 PDF / Word / PowerPoint / 图片中的文字 | `path`、`purpose` |
| `transcribe` | 把 16 位 PCM WAV 或 Ogg Opus 音频转写成文字 | `path` |
| `speech` | 把文本合成为音频并写入工作区 | `text`、`path`、`voice_id` |
| `read_image` | 读取本地图片并交给配置的 OpenAI 兼容视觉端点，返回描述或对 `prompt` 的回答 | `path`、`prompt` |
| `subagent` | 在子 shell 中启动一个新的 `minimalcode`，只返回它的最终答复 | `task`、`cwd`、`timeout`、`max_turns` |

联网默认走 Agent 路径：DeepSeek 预设带完整的 `web_tools` 块并设 `enabled: true`，因此 `web_search` / `web_fetch` 会出现在函数工具列表中。独立 Search/Fetch API 按成功请求计费，具体价格以 DeepSeek 定价页为准；启用它时即使旧配置还写着 `server_tools.enabled: true`，服务端工具也不会覆盖本地工具。要显式使用服务端 Chatbot 工具，请关闭 `web_tools`。

`web_search`、`web_fetch` 由供应商的 `web_tools` 配置启用；`read_image` 由供应商的 `vision` 配置启用；`ocr`、`transcribe`、`speech` 由 DeepSeek 接入点的 `media` 配置启用（当前供应商没有 `media` 时，会用配置里另一个可用的 DeepSeek 接入点），写哪个 `*_path` 就开哪项能力。未启用的供应商既收不到对应的工具定义，直接调用也会被拒绝。

`tools.allow` 给出允许的工具名列表（例如 `["read", "grep", "find"]`），`--read-only` 是它的一种预设。两者取交集，且被禁用的工具不会出现在请求的 `tools` 里；即使模型仍然发起调用（恢复的旧会话、忽略 schema 的供应商），`dispatch` 也会返回形如 `tool 'write' is disabled by --read-only` 的错误。只读模式下列表以外的工具一律不可用，包括 `subagent`（它会启动一个能写文件的子进程）。

`subagent` 默认开启，但受 `subagent.enabled` 和 `subagent.max_depth` 约束；超过嵌套深度的子进程既收不到该工具定义，直接调用也会被拒绝。同一轮里相邻的多个 `subagent` 调用会并发执行（上限 `subagent.max_parallel`，默认 4），结果仍按请求顺序写回会话；它们不会与相邻的其他工具重叠，因为 `write` 后接 `shell` 这类顺序是有意义的。相邻的只读工具（`read`、`read_range`、`grep`、`find`、`read_image`、`web_search`、`web_fetch`、`ocr`、`transcribe`）同样会并发执行，上限 `tools.max_parallel`。子进程就是一条命令，因此开启沙箱执行层时其超时还要再受 `sandbox.timeout_seconds` 约束：`subagent.timeout_seconds` 更大时会被压到该值，并在 `minimalcode sandbox` / `/status` 的 `notes` 中记录；执行层关闭时只受 `subagent.timeout_seconds` 约束。

### MCP 服务器

MinimalCode 可以作为 MCP 客户端，把外部服务器的工具并入同一套工具循环。服务器在 `settings.json` 的 `mcp.servers` 里声明，工具名是 `mcp__<服务器名>__<工具名>`：

```json
{
  "mcp": {
    "servers": {
      "files": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
      },
      "docs": {
        "transport": "http",
        "url": "https://mcp.example.com/mcp",
        "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
      }
    }
  }
}
```

- 传输：`stdio`（默认，子进程）、`http`（streamable HTTP，响应可以是 JSON 或 SSE）、`sse`（旧版 HTTP+SSE）。
- 发现：`tools/list` 会读完全部分页；缺少 `inputSchema` 的工具按无参数处理；非法字符、超长名字会被规范化，并用短哈希区分冲突。
- 结果：文本原样返回，图片/音频只报告类型与大小，`structuredContent` 在没有文本时转成 JSON，总长度按 `mcp.max_output_chars` 首尾截断。
- 失败隔离：某个服务器启动失败或调用超时，只返回该服务器的错误，其余工具和后续回合照常。
- 权限：`tools.allow` 支持 `mcp__<服务器>` / `mcp__<服务器>__*`；`--read-only` 只保留 `readOnlyHint: true` 或整台服务器 `read_only: true` 的工具。
- 生命周期：`/new` 和 `/model` 复用同一批服务器进程；`/mcp refresh` 重新发现工具；退出时统一关闭。
- 边界：`stdio` 服务器默认只拿到启动所需的环境变量；`cwd` 经过与文件工具相同的沙箱路径检查；服务器进程本身由你声明，不在沙箱后端内运行。

完整字段与安全说明见 [docs/configuration.md](docs/configuration.md)。

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
- 内置默认配置只为 DeepSeek 打开了 `vision`（`deepseek-flash`），其他供应商默认关闭，需要时按上面的结构自行添加。视觉请求始终发给当前聊天接入点的 `vision` 端点，不会借用另一个 DeepSeek 接入点。

### 实现要点

- **工作区约束**：所有接受路径的工具都经同一处校验。相对路径以工作区为基准，解析后必须位于工作区内；`tools.allow_outside_workspace: true` 可放行外部路径，但仍受敏感文件策略约束。`shell` 与 `subagent` 同样经过沙箱入口，命令的工作目录和解析后的符号链接都在允许根内。`--no-sandbox` 只关闭执行层（后端包装、网络策略、资源限制），工作区与敏感文件边界照旧生效。
- **沙箱执行层**：`sandbox.enabled` 默认关闭（等同默认 `--no-sandbox`），`--sandbox` 或任意 `--sandbox-*` 选项会开启它；开启后默认后端 `local`（跨平台策略与子进程限制基线），可选 `auto`（Linux 依次尝试 `bwrap`、`firejail`，macOS 尝试 `sandbox-exec`，否则 `local`）以及 `bwrap`、`firejail`、`sandbox-exec`、`docker`。默认网络策略 `deny`，默认拒绝 `.env`、私钥、`.ssh/`、`.aws/` 等敏感路径。开启时路径逃逸、符号链接逃逸、敏感文件、网络命令、后端缺失都会 fail-closed 拒绝并记录原因。命令统一受 `sandbox.timeout_seconds`、`sandbox.max_output_chars` 和子进程资源上限约束，临时目录用后删除。详见 `docs/configuration.md` 的 Sandbox 一节。
- **grep / find**：检测到 `rg` 时直接以参数数组调用子进程（`rg --no-config --line-number --no-heading --color never --smart-case ...` 和 `rg --files --glob ...`），不拼接命令行字符串；没有 `rg` 时退回 Python 的 `re` + `os.walk`，并用独立子进程执行正则，超时会终止。两条路径跳过相同的忽略目录（`.git`、`node_modules`、`build`、`dist`、缓存和虚拟环境），支持相同的 smart-case 语义和相同的 glob 语义（`*` 不跨目录、`**/` 可匹配零层、大小写敏感），都不解析 `.gitignore`，也都不受 `RIPGREP_CONFIG_PATH` 影响；显式把 `path` 指向 `build` 等目录时仍会搜索该目录。两条路径的输出都再经过一次敏感文件策略过滤，被沙箱截断产生的标记行不会作为匹配返回。单行结果按 300 字符截断，`rg`/`find` 输出达到上限即停止读取。
- **shell**：自动探测会先用一条 `exit 0` 验证候选 shell 能否真正启动，`pwsh` 因缺少 libicu、.NET 无法预留内存或架构不符而崩溃时会继续尝试 `bash` → `zsh` → `sh`（Windows 为 `powershell` → Git Bash）；探测结果按可执行文件缓存，每个进程只付一次启动开销（pwsh 约 0.2 秒）。Windows 上 `System32\bash.exe` 与 `WindowsApps\bash.exe`（WSL 或应用商店启动器）不会作为原生 shell 候选，`pwsh-preview` / `powershell-preview` 按对应的 PowerShell 处理。显式配置的 `shell.path` 同样会验证，启动失败会直接报出原因而不是静默换 shell；`cmd.exe` 会被直接拒绝，因为它不接受 `-c`，用 `-c` 启动只会打印横幅、退出 0 而什么都不执行。`pwsh`/`powershell` 以 `-NoLogo -NoProfile -NonInteractive -Command` 启动，并在命令前注入 bootstrap：关闭进度条、`$ErrorActionPreference='Stop'`、控制台输出编码设为无 BOM 的 UTF-8。bash/zsh/sh 分别使用 `-c` 及各自的 no-profile 参数。stdout/stderr 由后台线程持续读取，只在内存中保留首尾有界片段，因此超长输出不会占满磁盘或内存，超时后仍能返回已产生的部分输出。超时会终止整个进程树（Windows 用 `taskkill /T`，POSIX 用进程组）。
- **误报网络命令**：`local` 基线的 `deny` 基于命令字符串识别，只读的 `python -m pip show/list`、`pip --version` 会被放行，`pip install/download` 等会触网子命令才会被拒绝。
- **资源限制**：POSIX 下对子进程设置 `RLIMIT_CORE` 和按超时推算的 `RLIMIT_CPU`；Linux 的 `RLIMIT_AS`（`sandbox.max_memory_mb`）和 `RLIMIT_NPROC`（`sandbox.max_processes`）默认关闭，因为 `RLIMIT_AS` 限制的是预留地址空间而非实际占用，2 GB 的默认值会让 .NET（pwsh）、JVM、Go 子进程在真正运行前就报 `Out of memory`。
- **subagent**：没有新的子协议，就是一条 shell 命令 `<python> -m minimalcode -p --no-session --quiet`，透传父进程的 `--provider` / `-m` / `--thinking` / `--home` / 工作区。任务正文通过 stdin 传入，因此提示词不会出现在命令行里，也没有引号转义问题；PowerShell 使用调用运算符 `&`，并在末尾用空值安全的 `exit` 保留子进程真实退出码（无法启动时返回 1）。子进程通过 `MINIMALCODE_SUBAGENT_DEPTH` 得知自己的层级，超过 `subagent.max_depth` 后不再获得 `subagent` 工具；`PYTHONPATH` 指向当前包所在目录，因此子进程跑的是同一份代码。
- **网络重试**：请求在拿到响应之前失败（`ConnectError`、`ConnectTimeout`，以及 `Server disconnected without sending a response.` 这类 `RemoteProtocolError`）会自动重试 10 次，退避从 0.5 秒翻倍到最多 8 秒；已经开始返回数据的流式中断不重试，避免重复输出或重复计费。HTTP 429/503 仍按 `Retry-After` 重试一次。
- **输出裁剪**：工具结果按首尾保留截断，超长输出中间以 `... <truncated N chars> ...` 标记，既保留开头上下文也保留结尾错误信息。
- **编码与并发**：`read` 先用 `errors="strict"` 扫描请求范围，只有真正遇到非 UTF-8 字节时才退回替换解码并写 `warnings`，因此正常文件不多读一遍、也不会把有损内容当成干净内容。同一轮的只读调用走同一个分组器：结果按调用顺序回填，`[read]` 等活动行仍在主线程打印；`write` / `edit` / `shell` / `subagent` 不参与并发，紧挨着它们的只读调用也留在主线程。

## 配置

默认目录：

```text
~/.MinimalCode/
├── settings.json   # 自动创建，与包内 defaults.json 深度合并
├── SYSTEM.md       # 可选，替换默认系统提示词
├── AGENTS.md       # 可选，全局规则
├── sessions/       # 按工作区分组的 JSONL 会话
└── webui/          # 网页端专用：folders.json 记住文件夹归属，thinking.json 存刷新后仍在的推理块
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
      "extra_body": {},
      "web_tools": {
        "enabled": true,
        "base_url": "https://api.deepseek.com/v1/tools",
        "search_path": "search",
        "fetch_path": "fetch",
        "timeout_seconds": 60,
        "default_count": 10,
        "max_count": 50,
        "max_content_chars": 120000,
        "default_need_content": false,
        "min_rerank_score": 0,
        "headers": {}
      },
      "server_tools": {
        "enabled": false,
        "web_search": true,
        "read_url": true
      }
    }
  }
}
```

- `web_tools` 是默认联网路径，也是 Agent 的正确入口：本进程调用 DeepSeek 的独立 Search/Fetch API，`web_search` / `web_fetch` 作为普通函数工具参与工具循环。它按成功请求计费；预设默认启用，并在 provider 自己的 Key 或 `feature_source()` 找到的 DeepSeek Key 上执行。
- `server_tools` 是显式选择的 Chatbot 路径（仅 `api.deepseek.com`）：`enabled` 打开服务端工具，`web_search` / `read_url` 分别控制 `deepseek_web_search` 与 `deepseek_read_url`。只要 `web_tools.enabled: true`，服务端工具就留空；要使用它，请把 `web_tools.enabled` 设为 `false` 再打开 `server_tools.enabled`。
- `protocol` 取 `openai_chat`、`openai_responses`、`anthropic_messages` 或 `deepseek_web`（网页版对话接口，见 [docs/configuration.md](docs/configuration.md#built-in-provider-presets)）。
- 密钥优先用 `api_key_env` 指向环境变量；直接写 `api_key` 也支持，但要注意文件权限。密钥不会由 `init` 写入配置。
- 任意字符串值支持 `${VAR}` 环境变量占位符，未解析时会明确报出变量名，而不是把占位符发给供应商。
- `include_usage` 仅在服务端接受 `stream_options` 时开启（仅 Chat 使用）；`send_reasoning_effort` 仅在供应商支持时开启。Responses 模式下的 `thinking` 只有配合 `send_reasoning_effort: true` 才会作为 `reasoning.effort` 发送；`none` 会发送 `reasoning.effort: "none"`，Chat 模式则发送 `thinking: {"type": "disabled"}` 关闭思考。Anthropic 模式同样由 `send_reasoning_effort` 控制是否发送 `thinking`。
- 供应商可以自带默认推理强度：`providers.<名称>.thinking` 取 `EFFORT_LEVELS` 之一（`ninfer` 预设为 `xhigh`）。优先级是 `--thinking` / `/effort` → `providers.<名称>.thinking` → 顶层 `thinking`，所以显式参数永远优先，没写这个键的供应商继续用全局值。
- `max_tokens` 仅 Anthropic Messages 使用，默认 `8192`。官方 Anthropic API 要求该字段；`thinking` 的 `budget_tokens` 会按 `thinking` 等级映射，并保证小于 `max_tokens`。
- `prompt_cache: true` 只对 Anthropic Messages 生效：给 system 提示与工具定义打一个 `cache_control` 缓存断点，长工具循环里这段静态前缀按缓存读取计费。默认关闭，因为部分兼容端点不接受该字段。
- 其他兼容供应商沿用同一结构，替换 `base_url`、模型和密钥环境变量即可；不想手写 JSON 时用[自定义接入点](#自定义接入点)。
- 代理：供应商、`web_tools`、`vision` 默认沿用环境变量与系统代理设置。`base_url` 指向环回地址（`127.0.0.1`、`::1`、`localhost`）时自动忽略代理，否则本机运行的 Ollama、vLLM 或测试服务器会被 Clash 之类的代理劫走；需要强制时可在对应配置块里写 `"trust_env": true|false`。显式传入自建 transport 时默认不读环境代理，避免代理映射覆盖注入的连接。

### 同时使用多家 API

一个回合只用一个聊天接入点，不存在并发调用多家或自动故障转移。但 DeepSeek 那套能力按「是否必须写进聊天请求」分成两半，只有一半跟着聊天接入点走：

| 能力 | 由谁执行 | 当前聊天接入点必须是 `api.deepseek.com` 吗 |
|---|---|---|
| `deepseek_web_search` / `deepseek_read_url`（服务端联网） | 聊天接入点 | 是 |
| 文档附件（Files API 的 `file_id`） | 聊天接入点 | 是 |
| 内容安全（`safety_check`）、会话标题（`title`） | 聊天接入点 | 是 |
| `ocr`、`transcribe`、`speech` | 任一可用的 DeepSeek 接入点 | 否 |
| 独立 Search/Fetch API（`web_tools`） | 任一可用的 DeepSeek 接入点 | 否 |
| `read_image` | 当前聊天接入点的 `vision` | 否（跟随聊天接入点） |

选择顺序：当前接入点（它本身就是 DeepSeek API 时）→ `deepseek` → `deepseek-anthropic` → 其它供应商名（按字母序）。`${VAR}` 未展开、或声明了 `require_api_key` 却拿不到 Key 的块会被跳过，而不是让启动失败或给出一个必然 401 的按钮。

最常见的用法是拿 OpenAI / Ollama 当聊天、DeepSeek 只负责 OCR 与语音，这时不需要额外配置，只要那个 `deepseek` 块能拿到 `DEEPSEEK_API_KEY`：

```powershell
$env:DEEPSEEK_API_KEY = "your-deepseek-key"
$env:OPENAI_API_KEY = "your-openai-key"

minimalcode --provider openai -m <model-id>     # 或交互里 /model openai/<model-id>
```

切过去之后 `ocr`、`transcribe`、`speech` 与 `web_search` / `web_fetch` 仍在工具列表里，附件与「内容安全」开关会隐藏并说明原因。联网搜索由任一可用的 DeepSeek 预设通过 `web_tools` 执行，与聊天走哪家无关；它按成功请求计费。

怎么确认生效：CLI 里 `/status` 的 `tools` 列出 `ocr` / `transcribe` / `speech` 就说明借到了；网页端 `GET /api/state` 的 `deepseek` 字段中，`available` 表示存在可用的 DeepSeek 接入点，`active` 表示当前聊天就在它上面，`provider` 指出实际执行的是哪个块。

两个边界：这套混用需要 1.10.0（1.9.0 及以前切换供应商会把媒体工具一起丢掉）；`subagent` 子代理继承父进程的同一个供应商与模型，不会自己换一家。

### 自定义接入点

命令行新增一个接入点并设为默认供应商：

```powershell
minimalcode config add-provider my-gateway `
  --base-url https://gateway.example.com/v1 `
  --protocol openai_chat `
  --model house-model `
  --api-key-env MY_GATEWAY_KEY
```

```json
"providers": {
  "my-gateway": {
    "protocol": "openai_chat",
    "base_url": "https://gateway.example.com/v1",
    "model": "house-model",
    "api_key_env": "MY_GATEWAY_KEY"
  }
}
```

- `--protocol` 默认 `openai_chat`，可选 `openai_responses`、`anthropic_messages`、`deepseek_web`；`--model` 同时写进顶层 `model`。
- 名称按 `[A-Za-z0-9][A-Za-z0-9._-]{0,31}` 校验，重名直接报错——内置预设不会被覆盖，其余键也不会被改动。
- `--api-key-env` 与 `--api-key` 二选一；网页端保存远程接入点时要求填 Key，`127.0.0.1` / `localhost` 这类本地服务可以留空，会写入 `require_api_key: false`。
- 定义好之后用 `--provider my-gateway`、`/model my-gateway/<模型>` 或网页端的厂家下拉框切换。

网页端在「设置 → 模型设置 → API 厂家」下拉到最底部选「自定义 API…」，填名称、接入地址、格式、模型与 Key，保存前由服务端校验（`POST /api/settings` 的 `custom` / `name` / `base_url` 字段）。保存成功后会立即切换过去，无需重启。

### 内置供应商预设

`defaults.json` 已经为常用供应商准备好端点、协议和密钥环境变量。它们不会猜模型 ID：交互中用 `/model 供应商/模型` 时显式给出模型，例如：

```text
/model deepseek/deepseek-flash
/model openai/<model-id>
/model anthropic/<model-id>
/model moonshot/<model-id>
/model ninfer/<本机加载的模型>
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
| `ninfer` | 无需密钥 | `http://localhost:8080/v1`（默认窗口 262144，`thinking: xhigh`） |

本地服务默认免密钥（`require_api_key: false`），`base_url` 指向环回地址时自动忽略系统代理。`ninfer` 预设还固定了 `context_window: 262144` 与 `thinking: xhigh`：这是该接入点的默认推理强度，`--thinking` 和交互里的 `/effort` 仍然优先，其它供应商继续用全局 `thinking`（默认 `high`）。

预设只固定端点和密钥变量名，`context_window` 保持一个保守值（`ollama` / `lmstudio` / `vllm` 为 `32768`，`ninfer` 按 262144，其余按各家公开的主推窗口填写），换到更大或更小的模型时按需在 `settings.json` 里调整。区域或套餐端点不同时（例如 MiniMax 国内站 `https://api.minimax.cn/v1`、StepFun 国际站 `https://api.stepfun.ai/v1`、火山方舟 `step_plan` 端点），覆盖 `base_url` 即可；Cohere 的兼容端点不接受 `stream_options`，所以预设里 `include_usage` 为 `false`。

`/model` 切换的是当前会话，不会修改 `settings.json`；要改默认值仍用 `minimalcode config set-provider` / `set-model`。切换后子代理也会继承新的供应商和模型。新供应商如果没有配置 `web_tools` / `vision`，对应的 `web_search`、`web_fetch`、`read_image` 工具会从工具列表中移除，切回带这些配置的供应商后恢复；OCR、`transcribe`、`speech` 不同，它们由任一可用的 DeepSeek 接入点提供，切换聊天供应商时不会消失。

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

端点从 `base_url` 推导：`https://api.deepseek.com/anthropic` 会请求 `/anthropic/v1/messages`；`base_url` 已以 `/v1` 结尾时只补 `/messages`，已经是完整 `/messages` 地址时原样使用。鉴权发送 `x-api-key`，并附带 `anthropic-version: 2023-06-01`（自定义 `headers` 中的同名字段优先）。工具定义会转换为 Anthropic 的 `name` / `description` / `input_schema`，连续的工具结果会合并到同一条 user 消息，且 `tool_result` 块放在文本之前。思考模式下通过工具调用继续对话时，assistant 的 thinking 块（含 signature）会保存在会话中并原样回传，以保证 Anthropic 的多轮工具调用可以继续。需要复用系统提示与工具定义的缓存时，在该供应商里加上 `"prompt_cache": true`；内置预设默认不开，避免端点不支持 `cache_control` 时整轮请求失败。

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
- `web_fetch` 默认由本进程请求 `web_tools.base_url`；关闭 `web_tools` 并启用 `server_tools` 后才改为 provider 服务端抓取。该地址及其凭据属于可信配置，不要指向不可信或能访问敏感内网的服务。
- `read_image` 同样受工作区限制，图片以 base64 data URL 内联发送，受 `max_image_bytes` 约束。请求由 MinimalCode 直接发往 `vision.base_url`（未设置时回退到供应商的 `base_url`），与主协议无关，因此该端点及其凭据属于可信配置。
- 网页界面默认只绑定 `127.0.0.1`，并校验 `Host` / `Origin` 的主机名与端口。它能在工作区里执行工具，所以不要把 `--web-host` 改成 `0.0.0.0` 后暴露到不受信任的网络，也不要让反向代理改写这两个头。

## 桌宠集成（Clawd on Desk）

MinimalCode 可以把工作状态上报给 [Clawd on Desk](https://github.com/rullerzhou-afk/clawd-on-desk)：提问时它思考，工具执行时它打字，子代理时它杂耍，压缩上下文时它扫除，回合结束弹完成动画。

先退出 Clawd on Desk，再注册，然后重新打开它：

```bash
# 1. 退出 Clawd on Desk（托盘菜单 Quit，不是关窗口）
minimalcode clawd install   # 把 MinimalCode 注册成 Clawd 的「自定义 HTTP Agent」
minimalcode clawd status    # 查看 Clawd 是否在运行、agent id 与偏好文件版本
minimalcode clawd test      # 真发一条探测事件，看 Clawd 是否接受（200 / 204）
minimalcode clawd remove    # 撤回注册（同样要先退出 Clawd）
```

`install` 在 Clawd 仍在运行时会拒绝写入：Clawd 把偏好设置放在内存里，移动桌宠或改设置时会把整份快照写回文件，运行期间的注册会被它覆盖成"看起来注册成功、其实没生效"。注册后 Clawd 只在启动时读取偏好文件，所以重新打开它就生效。

`clawd status` 里的 `schema` 是 Clawd 偏好文件的 schema 版本（1.6.0 是 21）。它和 Clawd 自身版本不同步时可以据此判断手上的偏好文件是哪个构建写的。`clawd test` 会真的 POST 一条 `SessionStart`：200 表示 Clawd 接受了这个 agent，204 表示它在运行但这个 agent 未知或被关掉（常见原因是注册后没重启 Clawd），连不上表示 Clawd 没运行。

之后 MinimalCode 每次运行都会读 `~/.clawd/runtime.json` 找到 Clawd 当前端口，把生命周期事件 POST 到 `127.0.0.1` 的 `/state`；端口变了会自动重读，Clawd 没运行、集成被关闭或没有注册时完全静默，不会拖慢或打断任何一次工具调用。

上报内容只有生命周期元数据：agent id、会话 id、状态、事件名、工具名、工作目录和平台。提示词、工具参数、文件和密钥都不会发送。Clawd 只接受它认识的 agent id，未注册的 id 会被它并进 Claude Code 的子代理流，因此 MinimalCode 在注册之前什么也不发。

偏好文件位置：Windows `%APPDATA%\clawd-on-desk\clawd-prefs.json`，macOS `~/Library/Application Support/clawd-on-desk/`，Linux `${XDG_CONFIG_HOME:-~/.config}/clawd-on-desk/`。文件不存在时 `install` 会创建一个只含 `customApplications` 的文档，Clawd 启动时用自己的 schema 补齐其余字段——所以源码 checkout 在第一次 `npm start` 之前就能注册。

### 源码版与安装版

两套安装共用同一份偏好文件（`app.getPath("userData")` + `clawd-prefs.json`）和同一个 `~/.clawd/runtime.json`，所以注册一次、两边都生效；`npm start` 启动的源码版和安装版同时只会有其中一个在跑，端口以 `runtime.json` 为准。

源码版不需要打任何补丁。Clawd 侧的「自定义 HTTP Agent」实现在 `src/custom-applications.js`、`src/server-agent-id.js` 和 `src/server-route-state.js` 里，这些文件在 1.6.0 标签和当前源码 HEAD 之间没有改动，因此上报路径完全一致。注意 `package.json` 的 `version` 在发版前不会跟着提交走，源码 checkout 常常写着 1.6.0 但代码已经比标签新（用 `git describe --tags` 看真实位置）；判断依据请用 `minimalcode clawd status` 的 `schema`，别用版本号字符串。

配置项 `integrations.clawd`：`enabled`（默认 `true`）、`agent_id`（留空表示使用注册得到的 id；把 Clawd 打了原生 MinimalCode 支持时可填 `minimalcode`）、`runtime_file`（默认 `~/.clawd/runtime.json`）、`timeout_seconds`（默认 `1.0`）。

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
├── subagent.py      # 子代理的子进程构造与深度限制
├── provider/        # OpenAI Chat / Responses 与 Anthropic Messages 客户端
│   ├── client.py    #   共享客户端与协议分派
│   ├── chat.py      #   Chat Completions 载荷、流与解析、推理回传
│   ├── responses.py #   Responses 载荷、事件流与解析
│   ├── anthropic.py #   Anthropic Messages 载荷、流与解析
│   ├── transport.py #   HTTP 请求建立与连接重试
│   ├── common.py    #   usage / 内容 / 参数归一化
│   ├── server_tools.py  # 服务端联网块的解析、展示归一与 read_link 回传
│   ├── types.py     #   ToolCall、ProviderReply
│   └── factory.py   #   create_provider
├── tools/           # 工作区工具
│   ├── runtime.py       #   共享状态与工具分派
│   ├── schemas.py       #   工具定义
│   ├── files.py         #   read / read_range / write / append_file / edit
│   ├── search.py        #   子进程搜索与输出解析
│   ├── search_tools.py  #   grep / find
│   ├── process.py       #   shell / subagent
│   ├── remote.py        #   vision / web 工具
│   ├── media_tools.py   #   ocr / transcribe / speech
│   └── validation.py    #   模型参数校验
├── mcp/             # MCP 客户端
│   ├── protocol.py  #   JSON-RPC、内容与 schema 转换
│   ├── transport.py #   stdio / streamable HTTP / 旧版 HTTP+SSE
│   ├── client.py    #   握手、工具发现与调用
│   └── manager.py   #   服务器生命周期与工具索引
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
├── webui/           # 本地网页界面
│   ├── runtime.py   #   WebRuntime：设置写入、自定义接入点、工作区切换、会话、单轮流式回合
│   ├── server.py    #   ThreadingHTTPServer 路由、Host/Origin 校验、SSE 分帧
│   ├── library.py   #   文件夹与对话的归属（<home>/webui/folders.json）
│   ├── thinking.py  #   刷新后仍在的推理块（<home>/webui/thinking.json）
│   └── assets/      #   index.html / app.css / app.js（随 wheel 安装，无构建步骤）
├── shell.py         # shell 探测与子进程执行
├── terminal_input.py # 交互式多行输入、可视换行与跨行退格
├── httpclient.py    # HTTP 客户端构造、代理策略与连接重试
├── prompt.py        # 系统提示词与 AGENTS.md 发现
├── session.py       # JSONL 会话存储与检索
├── config.py        # 配置合并、校验、脱敏、原子写入、接入点命名规则
├── deepseek.py      # DeepSeek 能力集、Agent/Chatbot 联网路由、feature_source() 与准入判定
├── web.py           # Agent 默认使用的独立 Search/Fetch API 客户端
├── media.py         # 供应商 OCR / 语音识别 / 语音合成 / 文件解析客户端
├── vision.py        # 供应商视觉客户端
├── reminder.py      # latest_reminder 的内容（日期、星期、平台、语言）
├── clawd.py         # Clawd on Desk 桌宠的生命周期上报
├── clipboard.py     # 系统剪贴板图片读取
├── importer.py      # Pi 配置迁移
├── errors.py        # 统一异常类型
└── defaults.json    # 内置默认配置
```

五个包的 `__all__` 是唯一公开接口（`tools`、`sandbox`、`provider`、`highlight`、`webui`）。0.7.0 起不再为拆分前的模块级名字提供回退；私有 helper 从定义它的子模块导入，详见 [docs/compatibility.md](docs/compatibility.md)。

请求链路：CLI → 配置与 shell 探测 → 会话加载/创建 → 供应商流式请求 → 工具调用与执行 → 结果追加到 JSONL → 下一轮模型请求 → 最终文本或 JSON。每次运行只有一个常驻主进程和一个复用的 HTTP 连接池，没有守护进程、嵌入式数据库或后台服务（`shell` 工具会按需启动子进程）。更多细节见 [docs/architecture.md](docs/architecture.md)。

## 文档

- [docs/architecture.md](docs/architecture.md)：请求链路、模块职责与性能取舍。
- [docs/configuration.md](docs/configuration.md)：全部配置字段、默认值与安全边界。
- [docs/compatibility.md](docs/compatibility.md)：1.0 兼容承诺、公开面定义，以及 0.6 → 0.7 的导入与 patch 迁移对照表。
- [CHANGELOG.md](CHANGELOG.md)：逐版本的新增、修复与行为变化；每个版本的发布说明见 [.github/releases](.github/releases)。

## 升级指南

每个版本都保持向后兼容：1.x 内没有删除公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema。下面按从新到旧排列，只列升级时需要留意的行为变化；逐条明细见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.11.0 升级到 1.11.1

1.11.1 修复 DeepSeek 联网工具在 Agent 工具循环中不可用的问题，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema。

- DeepSeek 的 `deepseek_web_search` / `deepseek_read_url` 是 Chatbot 会话工具；MinimalCode 始终发送客户端函数工具，因此默认改为独立 Search/Fetch API。`deepseek` 与 `deepseek-anthropic` 预设重新带完整 `web_tools`，并把 `server_tools.enabled` 设为 `false`。
- 已有配置不需要手改：只要 `web_tools.enabled: true`，即使旧文件还写着 `server_tools.enabled: true`，本版本也让 `web_search` / `web_fetch` 生效。若你曾显式关闭 `web_tools`，启动提示会说明如何切回 Agent 路径。
- 独立 Search/Fetch 按成功请求计费，具体价格以 DeepSeek 定价页为准；网页端和服务端 Chatbot 工具不再被描述成免费的默认路径。
- 终端长文本改用一个多行编辑缓冲区：自动换行或反斜杠续行后，退格到行首会移到上一行末尾，不再卡在下一行第一个字符前。
- 未写 `--sandbox` / `--no-sandbox` 时不再覆盖 `settings.json` 里的 `sandbox.enabled`；显式参数仍然优先。

### 从 1.10.1 升级到 1.11.0

1.11.0 只新增能力，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，已有的 `settings.json` 不需要改动。

- 新增本地预设 `ninfer`：免密钥、`http://localhost:8080/v1`、262144 上下文、默认 `thinking: xhigh`。用法和其它本地服务一样，需要显式给出模型：`minimalcode --provider ninfer -m <模型>`，或在交互里 `/model ninfer/<模型>`。`/model` 不带参数时会把它列进「无需密钥」的可用供应商。
- 新增配置键 `providers.<name>.thinking`：让单个接入点固定默认推理强度。`ninfer` 用它把默认值设成 `xhigh`；你现有的供应商都没写这个键，所以仍按顶层 `thinking`（默认 `high`）运行。`--thinking` 与 `/effort` 的优先级更高，随时可以覆盖。
- 端口 8080 与网页界面的默认端口 8090 不冲突；真要同时跑，`--web-port` 换个端口即可。

### 从 1.10.0 升级到 1.10.1

1.10.1 把默认配置里最后的按次计费端点清掉了，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema。

- 预设不再带 `web_tools`：`deepseek` 与 `deepseek-anthropic` 里那块独立 Search/Fetch API（5 元/千次成功请求，`base_url` 指向 `/v1/tools`）已整块移除。`minimalcode init` 生成的 `settings.json` 与 `config show` 都不会再出现付费端口。
- 联网只剩一条默认路径：`server_tools.enabled: true` 声明 `deepseek_web_search` / `deepseek_read_url`，搜索与抓取都在 DeepSeek 服务端完成，只计模型 token，没有按次费用。网页端「联网搜索」开关照旧可用。
- 你现有的 `settings.json` 如果还留着那段（早期 `init` 写入了完整模板），把整个 `web_tools` 块删掉即可。留着也安全：`enabled: false` 的块不会发出任何请求、不产生费用，也不会在启动时提示。
- 真想用独立 Search/Fetch API 时仍然可以，但要自己写完整块并设 `enabled: true`，同时把 `server_tools.enabled` 设成 `false`。少写任何一个路径键都会被明确拒绝，不会半启用后偷偷计费。
- 顺带修掉 1.10.0 的一处预设残缺：`deepseek-anthropic.web_tools` 在删重复 `base_url` 时丢了 `search_path`，若打开该块会因缺路径报错；这块现在已随付费端点一起移除。

### 从 1.9.0 升级到 1.10.0

1.10.0 只新增能力，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，已有的 `settings.json` 不需要改动。下面的变化都是把原本会失败或静默的事说清楚：

- DeepSeek 专属能力可以来自另一个接入点：配置里只要有带 Key 的 `api.deepseek.com` provider（默认预设 `deepseek` / `deepseek-anthropic`），即使当前对话跑在 OpenAI、Ollama 或自定义网关上，OCR、语音识别、朗读与独立 Search/Fetch API 仍由它执行；服务端工具、文档、内容安全、会话标题仍要求当前接入点是 DeepSeek API。CLI 与网页端同一套判定（`minimalcode/deepseek.py` 的 `feature_source()`），切换供应商不再把这些工具一起丢掉。详见[同时使用多家 API](#同时使用多家-api)。
- 自定义接入点：新增 `minimalcode config add-provider` 与网页端「自定义 API…」，只新增 `providers.<name>` 块，不改内置预设、不改其它键。远程接入点必须带 Key，本地服务可以留空。详见[自定义接入点](#自定义接入点)。
- Chat 工具循环补上推理：模型带 `tool_calls` 的助手消息会把本轮 `reasoning_content` 存进会话并在下一次请求回传（DeepSeek 的要求，缺了它思考模型会重复或推翻自己的计划）；已经结束的回答仍然不保留，避免把上一轮的推理塞进新请求。Anthropic Messages 的签名块回传逻辑不变。
- 修掉 1.9.0 遗留的几处：`/model` 与 `/new` 重建工具运行时会带上当前 provider 的 `server_tools`；`server_tools.enabled: false` + `web_tools.enabled: true` 时「联网搜索」按钮不再隐藏；`_apply_switches()` 不再重复执行开关，也不会在切换失败时改动旧 agent；`defaults.json` 里重复的 `web_tools.base_url` 已删除。

### 从 1.8.1 升级到 1.9.0

1.9.0 只新增能力，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，已有的 `settings.json` 不需要改动。但**联网的默认路径变了**，这部分值得看一眼。

- 联网默认改走服务端工具：`deepseek` 与 `deepseek-anthropic` 预设的 `server_tools.enabled` 默认 `true`，模型在回答前直接调用 `deepseek_web_search` / `deepseek_read_url`，搜索与抓取都在 DeepSeek 服务端完成，只计模型 token。此时本地的 `web_search` / `web_fetch` 不再出现在工具列表里，`--read-only` 与 `tools.allow` 里的这两个名字仍然有效（不会误报未知工具），只是不生效。
- 独立的 Search/Fetch API 变成可选 fallback：`web_tools.enabled` 的预设默认值从 `true` 改成 `false`（5 元/千次成功请求）。你的 `settings.json` 里如果显式写了 `web_tools.enabled: true`，行为不变、也不会被改写，只是启动时会打印一行提示告诉你这条路径按次计费；想换回新默认就把它设成 `false`。
- 两条路互斥：`server_tools` 生效时，恢复的旧会话或模型误调本地 `web_search` / `web_fetch` 会被 `dispatch` 拒绝并说明原因，一轮里不会同时产生两种计费。确实想用独立 API 时把 `server_tools.enabled` 设成 `false`，同时打开 `web_tools.enabled`。
- 网页端「联网搜索」开关现在驱动服务端工具：打开后下一次请求才声明它们，摘要与压缩请求永不联网。状态里新增 `web_search_mode`（`server` / `local` / `off`）与 `web_search_available`。
- 非 `api.deepseek.com` 的接入点（含 `deepseek-web` 协议）不声明、也不接受这些服务端工具，网页上的相关按钮直接隐藏并给出原因。

### 从 1.8.0 升级到 1.8.1

1.8.1 只修缺陷，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，已有的 `settings.json` 不需要改动。

- 网页刷新后思考过程不会消失：思考块默认展开，标题就是折叠/展开的开关（鼠标点击、聚焦后按 `Enter` 或空格都可以），偏好存在浏览器 `localStorage` 的 `minimalcode-thinking-collapsed` 里，换浏览器或清掉本地存储会回到默认展开。
- 网页服务端对 `Origin` 的校验从「主机名是回环就放行」收紧为「主机名和端口都要与 `Host` 一致」。用 `http://localhost:8090/` 或 `http://127.0.0.1:8090/` 打开页面都不受影响；用反向代理或改了端口映射访问时需要保持一致。
- `minimalcode sessions` 与网页侧边栏的文件格式没变，`webui/thinking.json`、`webui/folders.json` 仍然是 `version: 1`，旧的记录可以继续读。

### 从 1.7.0 升级到 1.8.0

1.8.0 只新增能力，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，已有的 `settings.json` 不需要改动。

- 新增 `--web`、`--web-host`、`--web-port`；不加 `--web` 时终端行为完全不变。
- 网页设置面板只写 `provider`、`model` 与 `providers.<供应商>.{protocol,model,api_key}` 这几个键，其余配置保持原样。
- 想换 API 格式而不换厂家时，直接改 `providers.<供应商>.protocol`（`openai_chat` / `openai_responses` / `anthropic_messages` / `deepseek_web`）即可，`base_url` 不会被改动。

### 从 1.6.0 升级到 1.7.0

1.7.0 只新增能力，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema。已有的 `settings.json` 不需要改动：没有 `media` 块时不会创建媒体客户端，也不会给模型多出任何工具。

- OCR / 语音识别 / 语音合成 / 文档解析由 `providers.<供应商>.media` 开启。内置 `deepseek` 预设已经配好四个路径，用自己的端点时把 `base_url` 与各 `*_path` 指过去即可；少写一个路径就等于关掉那项能力。
- 会话标题：DeepSeek 预设默认开启 `session_title.enabled`。标题会多花最多 5 秒（只在会话第一个请求上），不想要就设成 `false`。`minimalcode sessions` 输出多一列标题，JSON 多一个 `title` 键。
- 用户元信息：`reminder` 默认开启，但只有 provider 预设里写了 `latest_reminder: true` 的端点才会真正发送（内置预设里只有 `deepseek-anthropic`）。所在地默认留空，按需填 `reminder.location` 与 `reminder.language`。
- 内容安全默认仍是 `"safety_check": {"level": "low"}`（与不传一致）。对外提供服务时改成 `high`，拒答会以「回合提前结束」上报。
- 网页搜索现在会按 `rerank_score` 排序并去重，启用 `min_rerank_score`（例如 `1.5`）可以过滤低质结果；默认 `0` 保留全部，只改变顺序与重复项。
- 工具名新增 `ocr`、`transcribe`、`speech`。如果 `tools.allow` 是白名单，需要显式加上；`--read-only` 会保留前两个，`speech` 会写文件因此不被保留。

### 从 1.5.4 升级到 1.6.0

1.6.0 只新增能力，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema。已有的 `settings.json` 不需要改动：没有 `mcp` 块时行为与 1.5.4 完全一致，也不会启动任何额外进程。

- 想接入 MCP 服务器时，在配置里加 `mcp.servers`。最简形式是一个 `stdio` 服务器：`{"mcp":{"servers":{"fs":{"command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","."]}}}}`。工具会以 `mcp__fs__<工具名>` 提供给模型。
- 远程服务器用 `"transport": "http"`（streamable HTTP）或 `"sse"`（旧版 HTTP+SSE），配 `url` 和可选的 `headers`；`${VAR}` 在读取配置时展开，`config show` 会把 `headers`/`env` 里的值打码。
- `stdio` 服务器默认只继承启动所需的基础环境变量。需要凭据时写进 `env`（例如 `{"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}`），或设 `"inherit_env": true` 全量继承。
- `tools.allow` 可以用 `mcp__<服务器>` 或 `mcp__<服务器>__*` 选择一台服务器的全部工具；`--read-only` 只保留服务器标注 `readOnlyHint: true` 的工具。
- 用 `minimalcode mcp` 先验证配置（不消耗模型额度），交互中 `/mcp` 查看状态、`/mcp refresh` 重新发现工具。

### 从 1.5.3 升级到 1.5.4


1.5.4 只修 bug，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，已有配置无需改动。

- 沙箱：`--` 被当成目录选项，`grep -rn . -- ~/.ssh`、`cp -a -- ~/.ssh /tmp` 能列出或复制整个凭据目录；组合短选项 `-ec`/`-xc` 后面的引号内容不被扫描；`pwsh -EncodedCommand` 的 base64 载荷不解码；POSIX 下 `cat \.env` 的反斜杠转义不还原。这四处都已堵上，`--home=<dir>` 这类合法的目录选项写法继续放行。
- 沙箱网络策略：`curl -X POST` 的 `-X` 被当成 `-x` 代理，指向白名单主机的请求会被拒绝；`python -c "…os.system('curl …')"` 这类进程启动器在 `deny` 下没被拦住；`npm run install`、`yarn run install` 这类同名脚本被误判成安装。
- 工具：`edit` 的 `lines_added` 在含 U+2028/U+2029 的补丁上多算行；`append_file` 在目标文件没有行尾符时把 CRLF 载荷改写成 LF；`write` 会报告 `line_ending: none` 却写入 CRLF。
- provider：Responses 协议把一次工具调用解析成两次（工具会执行两遍）；非流式请求返回 SSE 时思考增量丢失；空的 `finish_reason` 让已完成的回答被标记为未完成。

详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.5.2 升级到 1.5.3

1.5.3 只修 bug，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，已有配置无需改动。

- 沙箱：`Get-Content -Path:.env` 这类用冒号粘值的 PowerShell 写法绕过了敏感路径检查；现在 `-Path:值` / `-LiteralPath:值` 里的值同样参与判定。
- 沙箱：转义引号会让引号配对翻转，`printf "a\"b"; cat .env` 和 ``Write-Output "a`"b"; curl …`` 都被当成「后面全是引号内数据」，于是读出明文或直接出网。现在按 POSIX 与 PowerShell 两种转义各分析一遍并合并结果，只会多报不会漏报。
- 沙箱：行尾续行符会把命令名拆成两段（`cur\` 换行 `l`），`curl` 因此不再被识别；续行符现在先被合并。
- 沙箱网络策略：`npm --loglevel verbose install x` 这类「未知选项 + 独立取值」会把真正的子命令顶到第二位，`deny` 下直接放行；现在会继续探测后面的位置。
- 沙箱审计：`https://<token>@host` 这种只有用户名、没有冒号的凭据，以及 `AWS_SECRET_ACCESS_KEY=…` 这种关键字带后缀的键，之前以明文写进拒绝记录；现在都会被脱敏。
- `grep`：CRLF 文件上 `$` 锚点在 ripgrep 与 Python 回退之间结果相反；`^` 锚点在带 UTF-8 BOM 的文件上永远不匹配，BOM 还会进入结果文本；超过 `tools.max_file_bytes` 的文件在两种引擎下行为不一致。现在两边都用 `--crlf`、`utf-8-sig` 和同一套大小上限，显式搜索超大文件会得到明确的错误而不是空结果。
- `shell`：PowerShell 块注释 `<# … #>` 里的 `<<EOF` 被当成 bash heredoc，合法命令被拒绝执行。

详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.5.1 升级到 1.5.2

1.5.2 新增 `deepseek_web` 协议和 `deepseek-web` 预设，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，已有配置无需改动。要使用网页版协议才需要额外安装 `minimalcode[deepseek-web]`（wasmtime），并把浏览器 localStorage 里的 `userToken` 放进 `DEEPSEEK_WEB_TOKEN`。

- 新增 `protocol: "deepseek_web"`：直连 chat.deepseek.com 的网页接口，用本机的 wasmtime 解 `x-ds-pow-response` 工作量证明（挑战前缀为 `<salt>_<expire_at>_`，模块的 `status` 非零表示成功）；工具以文本块形式发给模型再解析回来，因此这条链路不需要函数调用支持。
- 认证用 `Authorization: Bearer <userToken>`；`DEEPSEEK_WEB_COOKIE` / `cookie` 只是可选的 WAF 兼容项，缺失不会报错。`-m` / `/model deepseek-web/<mode>` 选的 `model_type` 不再被预设的 `default` 覆盖，`Authorization` 里重复的 `Bearer` 前缀会被去掉。
- 会话 id 从 `biz_data.id` 读取（旧结构 `biz_data.chat_session.id` 仍兼容），补全请求带上 `stream: true` 和 `client_stream_id`，否则服务端只回一整段非 SSE JSON。
- 网页版流式回复按片段增量去重，思维链与答案分别投递，`response/content` 补丁同样计入正文；无标记的裸 JSON 只有带参数键时才当作工具调用，普通 JSON 回答原样显示，解析失败的参数会让模型自我纠正而不是把原文塞进工具参数。
- `tool_mode`、`cookie`、`cookie_env`、`model_type`、`web_search`、`pow_wasm_path`、`tool_parse_retries` 是新增的供应商键；`web_search` 与其它布尔键一样参与占位符展开和类型校验，`config show` 会脱敏内联的 `cookie`。
- `transport` 的响应读取现在把裸 `OSError`/TLS 错误也归入 `ProviderError`，握手请求（会话创建、挑战获取）沿用连接重试，但重试仍不覆盖已开始生成的补全。

详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.5.0 升级到 1.5.1

1.5.1 没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，升级不需要改代码；`minimalcode.shell.MAX_TIMEOUT_SECONDS` 是新增常量。这一版修的是 1.5.0 里仍然存在的口径不一致，其中几条会改变日常命令的结果：

- 引号不再能绕过敏感文件检查：`type ".env"`、`Get-Content '.ssh/id_rsa'` 与不加引号时一样被拒绝；`minimalcode --home <目录>` 这类“长选项 + 目录名”不算读凭据，子代理不会再被自己的配置目录挡住。
- 网络门禁补上四种写法：`curl -xhost:port`（粘连代理）、`\\?\UNC\host\share`（扩展长度 UNC）、直接把 `(New-Object Net.WebClient).DownloadString(...)` 输入 shell、以及 `py -3 -c` / `python3.12 -c` 的内联脚本。`allowlist` 下这些目标同样参与主机比对。
- 审计记录里带引号的 `-b` / `-H` / `--header` 值现在也被脱敏，`/status` 的 `recent_denials` 不再是凭证明文副本。
- Chat 工具调用的分片不再截断或串包：`id` 只随首片发送时的续片归入同一调用，服务端复用 `index` 时的续片跟随新调用。每次连接尝试也受 `timeout_seconds` 剩余预算约束。
- `edit` 越界提示只显示前 40 行，`write` 覆盖超过 `tools.max_file_bytes` 的既有文件会明确报错，`grep(path=<单个文件>, glob=…)` 在有无 `rg` 时结果一致，`subagent` 回报的超时不再超过实际的 3600 秒上限。
- 管道与子代理的文本固定走 UTF-8（子进程设 `PYTHONUTF8=1`），`settings.json` 里 `thinking` 大小写不敏感，交互模式以 `/` 开头的多行粘贴会发给模型而不是被当成未知命令丢弃。

详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.4.3 升级到 1.5.0

1.5.0 没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，升级不需要改代码；`SandboxPolicy` 多了一个只读属性 `isolates_network`。这一版把此前几处“工具拦截、shell 放行”的口子堵上，其中一些会改变日常命令的结果：

- `shell` 命令现在也受敏感文件策略约束。未加引号的 `.env`、`~/.ssh/id_rsa`、`.git/config` 等路径会被拒绝并记入审计，`cmd /c "type .env"` 这类引号内的内联命令同样会被扫描。这条规则在沙箱关闭（包括默认配置）时仍然生效，因为 `read` / `write` 本来就拒绝这些路径；需要放行时设 `sandbox.allow_sensitive_files: true`。`git commit -m "document .env"` 这种引号内的文字不会被误判。
- 敏感名单新增 `.envrc`、`secrets.yaml` / `.yml` / `.toml`、`credentials.yaml` / `.yml` / `.toml` 与 `.git/config`，`grep` / `find` 也不再返回它们。`secrets.py`、`credentials.py` 仍可读写。
- 使用 `bwrap` / `firejail` / `sandbox-exec` / `docker` 后端时，`allowlist` 现在与 `deny` 一样隔离网络：容器无法执行主机白名单，此前它把后端进程整个放行，只剩字符串门禁。需要在容器里联网就改用 `allow`。
- 解析器会先按空行组装多行 `data:` 事件再解析，跨行长 JSON 不再被丢弃；省略 `index` 的并行工具调用按 `id` 归位，不再串包；`arguments` 以对象形式到达时覆盖已收到的片段。
- 非 UTF-8 的 Windows 控制台输出按当前控制台/OEM 代码页解码，不再整段变成 `�`。

详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.4.2 升级到 1.4.3

1.4.3 是缺陷修复版本，没有新增或删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，升级不需要改代码。修复集中在安全闸门与搜索，其中几处会改变出错场景下的结果，需要留意：

- 沙箱网络策略补上四种此前完全看不见的写法：`env -S 'curl …'` / `--split-string`（该选项的值本身就是命令，旧版把它当普通选项值跳过）、`py -3 -m pip install …` / `python -mpip install …` / `python -m pip3 install …`（Windows 的 `py` 启动器与紧贴拼写）、PowerShell 内联的 `(New-Object Net.WebClient).DownloadString/DownloadFile`，以及 `\\host\share` 形式的 UNC 路径（SMB，不出现任何客户端名或 URL）。`deny` 下这些命令现在会被拒绝；`allowlist` 下 UNC 主机也会参与比对。
- 审计脱敏覆盖紧贴标志的凭据：`curl -uadmin:pw`、`--user=admin:pw`、`mysql -phunter2` 此前以明文写入拒绝记录，现在被标记为 `<redacted>`。`docker run -p 8080:80` 这类普通端口映射不受影响。
- `write` 保留文件原有的 UTF-8 BOM（此前只有 `edit` 保留，重写会静默改变整个文件的编码）。
- 校验器自身跑不起来（ruff 配置损坏、超时、工具缺失）时不再算作“内容有错”，新建的 Python 文件不会被回滚删除，改为在 `warnings` 里报告。
- `grep` / `find` 的 `glob` 现在按搜索根锚定：`find("deep/*.py", path="sub")`、`grep(..., glob="src/*.py")` 此前在装有 `rg` 时返回空结果（无 `rg` 的 Python 回退却能找到），模型会把“没找到”当成事实。含多次 `{a,b}` 的花括号展开也加了上限，不再指数级挂死。
- `grep` 结果不再被行内换页符、U+2028 或孤立 CR 切断并丢弃尾部文本。
- 供应商重试受 `timeout_seconds` 约束：此前连接退避会跑完整轮（1 秒超时实际可耗约 55 秒）。
- 兼容 Anthropic 的网关用 `data: [DONE]` 收尾时按正常结束处理，完整回答与工具调用不再被判为 `incomplete` 丢弃。

详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.4.1 升级到 1.4.2

1.4.2 是缺陷修复版本，没有新增或删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，升级不需要改代码。修复覆盖安全闸门、工具循环、文件编辑、渲染与协议解析，其中几处会改变出错场景下的结果，需要留意：

- 沙箱网络策略更准也更严。内联命令现在按词边界切片，`sh -c "curl …" arg0`、`pwsh -Command "iwr …" x` 这类带尾随参数的写法不再漏检；PowerShell 的 `-ec` / `-e` / `-en` 等 `-EncodedCommand` 缩写会解码检查；`npx` / `bunx` 与 `curl.cmd` / `wget.ps1` 计入联网命令；嵌套层数超出预算按 fail-closed 处理。反方向的误报也修掉了：`bash -c "rm -f ./curl"` 这类引号内提到命令名的普通命令不再被拒绝。用 `allowlist` 时可能发现此前被放行的命令现在被拒，那正是修复的目标。
- `tools.max_parallel` 或 `subagent.max_parallel` 设为 1 时，一组相邻工具调用现在会逐个全部执行；此前只执行第一个，其余既没执行也没有工具结果，下一轮请求会把未配对的 `tool_call` 发给供应商。
- 文件工具：`edit` 保留 UTF-8 BOM、拒绝 NUL 字节，替换没有尾随换行的末行时按文件主导换行补丁。
- 会话：`--session` 指向用户自己的目录时不再改动该目录权限；末行缺少换行的会话文件会先补换行再追加；含孤立代理对的内容转义写入而不是让追加失败。
- 显式把 `shell.path` 配成 `cmd.exe` 现在直接报错；docker 后端拒绝 PowerShell 命令，而不是把它交给 `/bin/sh` 静默误执行。
- `/summary` 走工具策略写 `AGENTS.md`，在 `--read-only` 或 `tools.allow` 未放行 `write` 时会被拒绝。

详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.4.0 升级到 1.4.1

1.4.1 是缺陷修复版本，同时补了两个工具，没有删除任何公开名字、CLI 参数、配置键、JSON 字段或工具 schema，升级不需要改代码。新增 `read_range`（按行号读取窗口）与 `append_file`（EOF 追加，保留既有换行风格），`edit` 增加行号参数：`path` + `new_text` 是必填项，`old_text` 作为精确文本兜底保留，两者不能混用。`write` / `edit` / `append_file` 写入 Python 文件后会运行 `py_compile`，装有时再跑 `ruff`，出现新诊断就回滚。可观察的行为变化都是修正错误行为：`read` 结果多出 `line_ending` 字段；`settings.json` 只保存用户改过的键，升级后新增的默认值不再被旧文件里的全量副本掩盖；`--read-only` 的只读集合包含 `read_range`。其余修复（沙箱网络策略识别、搜索窗口、渲染状态、流式工具调用、配置脱敏）只改变出错场景下的结果。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.3.2 升级到 1.4.0

1.4.0 没有删除任何公开名字、CLI 参数、配置键、JSON 字段或工具 schema，升级不需要改代码。新增的是五个可选项：`--read-only`、`tools.allow`、`tools.max_parallel`（相邻只读工具并发数）、供应商的 `prompt_cache`，以及压缩后重写会话文件。可观察的行为变化只有三处，且都是修正错误行为：`read` 遇到非 UTF-8 内容时会在结果里带 `warnings` 字段（此前只返回 U+FFFD 且没有任何提示），UTF-8 BOM 不再显示为第一行的 `\ufeff`；被 `tools.allow` / `--read-only` 禁用的工具既不出现在工具定义里，直接调用也会返回错误；压缩后的大会话文件会被原子重写为「meta + 压缩状态」，此前同一段历史在文件里保存两份并整段重放。`read` 的常规结果多出一个只在出错时出现的 `warnings` 键，`/status` 多出 `read_only` 与 `tools` 两个字段。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.3.1 升级到 1.3.2

1.3.2 是缺陷修复版本，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，升级不需要改代码。可观察的行为变化都是修正错误行为：`grep` / `find` 现在隐藏全部敏感路径（此前开启 ripgrep 时 `.env-prod`、`KEY.PEM`、`Secrets` 会漏出），并且不再读取 `RIPGREP_CONFIG_PATH`；两条搜索路径的 glob 统一按 ripgrep 语义匹配，`*` 不再跨目录、Windows 上不再忽略大小写；`write` 拒绝含 NUL 的内容；恢复会话时若记录的 provider 不在配置里会报错，而不是把历史发给另一个协议；Windows 不再自动选用 `C:\Windows\System32\bash.exe` 这个 WSL 启动器；压缩与 `/summary` 的 token 用量计入统计。其余修复（流式与非流式协议解析、超时预算、搜索截断、渲染状态、并发 subagent）只改变出错场景下的结果。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.3.0 升级到 1.3.1

1.3.1 是缺陷修复版本，没有删除任何公开名字、CLI 参数、配置键、JSON 字段、会话格式或工具 schema，升级不需要改代码。可观察的行为变化只有两处，且都是修正错误行为：stdout 读取端提前关闭（`minimalcode ... | head`）现在按 `0` 退出，不再在 Windows 上打印 `error: [Errno 22] Invalid argument` 并以 `2` 退出；`--home` 写在管理命令前面时（`minimalcode --home DIR sessions`）按管理命令执行，不再把命令词当成提示词发给模型。其余修复（协议解析、压缩边界、搜索回退语义、沙箱网络识别、`trust_env` 布尔转换）都只改变出错场景下的结果。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.2 升级到 1.3

1.3.0 没有删除任何公开名字、CLI 参数、配置键、JSON 字段或会话格式，升级不需要改代码。新增的是交互式剪贴板图片粘贴：空回车读取系统剪贴板里的图片，经供应商 `vision` 配置转成文字描述后随消息发送。可观察的行为变化有四处：JSON API 请求遇到 3xx 现在直接报错而不是跟随跳转（跳转会重发请求体与 `x-api-key` 等认证头）；新建文件按进程 umask 创建，不再固定 `0600`；`config set-model` 同时更新当前供应商的 `model`；恢复会话时沿用会话记录的供应商与模型，显式 `--provider` / `--model` 仍然优先。`requires-python` 收窄为 `>=3.11,<3.14`。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.1 升级到 1.2

1.2.0 没有删除任何公开名字、CLI 参数、配置键或 JSON 字段，升级不需要改代码。新增的是：`anthropic_messages` 协议、`--anthropic-env`（与 `--provider` 互斥）、交互命令 `/model [供应商/模型]`，以及 `defaults.json` 里的 33 家托管供应商加 `ollama`、`lmstudio`、`vllm` 三个本地预设。预设走常规的默认值合并，已有 `settings.json` 里同名的供应商保留自己设置的键；因此对未改动配置的用户，唯一可见的差异是 `config show`、`minimalcode sessions` 和 `/model` 列表里多出了 `deepseek-anthropic` 与这些供应商。`/model` 只切换当前会话，不写回配置。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 1.0 升级到 1.1

1.1.0 没有删除任何公开名字、CLI 参数、配置键或 JSON 字段，升级不需要改代码。可观察的变化只有两处：系统提示会在每次运行开始时重新读取 `AGENTS.md` / `CLAUDE.md`（此前在进程启动时缓存一次），以及新增的交互命令 `/summary`（只在交互模式下可用，`-p` 一次性运行不受影响）。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 0.8 升级到 1.0

1.0.0 是首个稳定版本。从 1.0 起，CLI 参数、配置键、JSON 输出字段、会话格式，以及 `tools`、`sandbox`、`provider`、`highlight`（1.6.0 起还有 `mcp`）的 `__all__` 公开面在 1.x 内保持兼容。0.8 → 1.0 没有删除公开名字或 CLI 参数，但修复项里有两处可观察行为变化：`--json` 的 `usage` 改为本次运行（累计值在 `session_usage`），`import-pi` 默认不再复制 API key。详见 [CHANGELOG.md](CHANGELOG.md)。

### 从 0.6 升级到 0.7

0.7.0 把 `tools`、`sandbox`、`provider`、`highlight` 四个大模块拆成了包，并在发布前移除了拆包过程中临时保留的旧名字回退。CLI、配置和工具行为没有变化；只有直接导入内部名字或 patch 内部函数的代码需要迁移：

- `minimalcode.tools`、`minimalcode.sandbox`、`minimalcode.provider`、`minimalcode.highlight` 现在只导出各自 `__all__` 中的公开名称，未知名字直接抛 `AttributeError`。
- 私有 helper 改为从实现模块导入，例如 `minimalcode.tools.search.run_python_grep`、`minimalcode.sandbox.execution._posix_limits`、`minimalcode.highlight.palette._PALETTES`。
- patch 目标要指向实际查找该名字的模块，例如 `minimalcode.tools.search_tools.shutil.which`、`minimalcode.provider.transport.time.sleep`、`minimalcode.highlight.color._supports_color`。

完整对照表见 [docs/compatibility.md](docs/compatibility.md)。

0.7 → 0.8 只补充文档与注释、补全供应商重试策略的测试，公开面与 0.7.0 一致，升级无需改代码。

## 后续工作

- 原生 Gemini 协议适配。
- RPC/SDK 集成。
- 扩展、Skills 和主题系统。
- 按路径/按命令的更细粒度工具权限（当前只有按工具名的白名单）。

## 许可证

[Apache License 2.0](LICENSE)，Copyright 2026 jiaheng0815。
