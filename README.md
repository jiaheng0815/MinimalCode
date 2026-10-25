# MinimalCode

MinimalCode 是一个面向终端编码任务的轻量 Agent。它优先减少启动开销、交互层级和上下文浪费，不提供炫技式 TUI。

当前版本：`0.3.0`。

## 核心特性

- 默认使用 PowerShell 7（`pwsh`）；Windows 缺失时回退到 Windows PowerShell 5.1，其他平台缺失时回退到 `bash`，最后回退到 `sh`。
- 六个精简工具：`read`、`write`、`edit`、`shell`、`grep`、`find`。
- DeepSeek 默认启用 `read_image`、`web_search` 和 `web_fetch`；其他供应商默认不暴露这些工具。
- 流式输出，在交互终端中渲染 Markdown/README，不依赖 Markdown、Rich 或 UI 框架。
- 会话自动保存，支持继续和指定会话。
- 接近上下文上限时自动压缩较早消息。
- 读取全局和项目级 `AGENTS.md` / `CLAUDE.md`。
- 支持 OpenAI Chat Completions 和 Responses 兼容供应商。
- JSON 非交互输出，便于脚本集成。
- 工作区边界默认开启。

## 安装

要求 Python 3.10+。

```powershell
python -m pip install -e .
minimalcode init
```

首次正式运行也会自动创建缺失的配置文件。

也可以使用 pipx：

```powershell
pipx install .
minimalcode init
```

## 配置

配置默认位于：

```text
~/.MinimalCode/
├── settings.json
├── SYSTEM.md       # 可选，替换默认系统提示词
├── AGENTS.md       # 可选，全局项目规则
└── sessions/       # 自动生成
```

初始化后设置 DeepSeek API key：

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
minimalcode
```

查看配置：

```powershell
minimalcode config show
minimalcode config path
minimalcode config set-model deepseek-flash
minimalcode config set-provider deepseek
minimalcode shell
```

终端高亮默认开启，可按主题调整：

```json
"output": {
  "markdown_highlight": true,
  "theme": "dark"
}
```

`theme` 支持 `dark` 和 `light`。

从现有 Pi 配置迁移供应商、默认模型和上下文窗口：

```powershell
minimalcode import-pi
```

该命令默认把 Pi 中已有的 API key 复制到权限受限的 MinimalCode 配置中，但不会在终端输出密钥。若不希望复制密钥，使用 `--no-secrets`，然后设置输出的对应环境变量。

密钥不会自动写入 `settings.json`。推荐使用 `api_key_env` 指定环境变量名；直接配置 `api_key` 也受支持，但应确保配置文件权限。

## DeepSeek 配置

默认供应商是 DeepSeek。下面示例使用 OpenAI Responses 协议、`deepseek-flash` 模型和环境变量中的 API key：

```json
{
  "provider": "deepseek",
  "model": "deepseek-flash",
  "thinking": "high",
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

协议值为 `openai_chat` 或 `openai_responses`。Responses 模式下，`thinking` 只有同时设置 `send_reasoning_effort: true` 才会作为 `reasoning.effort` 发送。其他兼容供应商可沿用相同结构并替换 `base_url`、模型和密钥环境变量。

### Web 工具

Web 能力由供应商的 `web_tools` 配置启用，运行时代码不包含供应商主机或端点判断。默认 DeepSeek 配置通过 `/v1/tools/search` 和 `/v1/tools/fetch` 启用以下工具：

- `web_search`：搜索网页，可返回标题、链接和可选正文。
- `web_fetch`：抓取指定 HTTP/HTTPS 页面。

其他供应商默认不会收到这两个工具定义，直接调用也会被工具层拒绝。需要时应显式添加完整 `web_tools` 配置。

### 图像理解

`read_image` 由供应商的 `vision` 配置启用，通过 OpenAI 兼容的 `/chat/completions` 端点把本地图片以 base64 data URL 发送给视觉模型，返回文字描述。默认 DeepSeek 配置已启用：

```json
"vision": {
  "enabled": true,
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-flash",
  "timeout_seconds": 120,
  "max_image_bytes": 33554432,
  "max_chars": 8000,
  "detail": "auto",
  "headers": {}
}
```

- 支持 JPEG、PNG、GIF 和 WebP，格式按文件内容判断，不依赖扩展名。
- `read_image` 和文件工具一样受工作区限制；读取工作区外的图片需要同时设置 `tools.allow_outside_workspace`。
- `prompt` 可选，默认要求模型详细描述图片并逐字转写可见文字。
- 返回内容超过 `max_chars` 时截断并标记 `<description truncated>`。

未配置 `vision` 的供应商不会收到该工具定义，直接调用也会被拒绝。

完整默认值位于包内 `minimalcode/defaults.json`，运行时会与用户配置深度合并。

环境变量可以写在值中：

```json
"headers": {
  "X-API-Key": "${CUSTOM_PROVIDER_KEY}"
}
```

多数兼容服务接受默认配置。若服务拒绝 `stream_options`，将 `include_usage` 设为 `false`；只有供应商支持时才将 `send_reasoning_effort` 设为 `true`。

## 使用

交互模式：

```powershell
minimalcode
```

单次任务：

```powershell
minimalcode -p "找出这个项目测试失败的原因并修好"
```

继续当前目录最近一次会话：

```powershell
minimalcode -c
```

指定会话：

```powershell
minimalcode --session 1a2b3c4d
minimalcode sessions
```

JSON 输出：

```powershell
minimalcode --json -p "运行测试并总结"
```

关闭 Markdown 高亮：

```powershell
minimalcode --no-highlight
```

切换模型或供应商：

```powershell
minimalcode --provider deepseek --model deepseek-v4-pro
```

交互命令：

- `/new`、`/clear`：新建会话。
- `/compact`：立即压缩历史。
- `/session`：显示会话文件。
- `/status`：显示模型、shell、上下文和 token 状态。
- `/help`：显示命令。
- `/exit`、`/quit`：退出。

## 终端 Markdown 高亮

当输出目标是支持 ANSI 的交互式终端时，MinimalCode 会为模型输出中的标题、强调、行内代码、引用、列表、表格和围栏代码块着色，并按模型返回的 delta 逐词显示，不等待整行或完整回复。

行内 Markdown 采用与网页 AI 相同的“未闭合块”处理思路：收到 `**` 后立即把后续文字按粗体显示，且不打印语法标记；即使模型只输出到 `**未完成的粗体`，文字也会先正常渲染。真正的闭合标记到达后只结束样式，不会补打 `**`。相同规则适用于斜体、删除线、行内代码和链接文本。链接会显示为 `文本 (URL)`，URL 以灰色随流式输出打印。正文不会重排，会话中保存的仍是模型返回的原始 Markdown。

以下情况保持纯文本原样输出：

- `--json` 输出；
- stdout 被管道或文件重定向；
- 设置了 `NO_COLOR`；
- 使用 `--no-highlight`；
- 配置中设置 `"markdown_highlight": false`。

## Shell 选择

选择顺序固定且启动时只解析一次：

| 平台 | 第一选择 | 回退 |
|---|---|---|
| Windows | `pwsh` | `powershell.exe` |
| macOS | `pwsh` | `bash`，然后 `sh` |
| Linux | `pwsh` | `bash`，然后 `sh` |

可以在配置中指定 shell 路径：

```json
"shell": {
  "path": "C:\\Program Files\\PowerShell\\7\\pwsh.exe"
}
```

显式指定的 shell 不存在时直接报错，不会静默换成另一个 shell。

`shell` 工具执行完成后，终端活动区会显示退出码、stdout 和 stderr。为避免长命令刷屏，活动区只保留首尾各若干行（最多 12 行、4000 字符），被省略的行数以 `… N lines omitted` 标出；模型收到的完整输出不受影响。工具活动开头的 `[shell]`、`[read]` 等标签在支持 ANSI 的终端中显示为绿色。shell 输出写入 stderr，助手正文仍保存在 stdout；使用 `--quiet` 或 `--json` 时不会显示工具活动。

## 安全和边界

MinimalCode 会执行模型生成的 shell 命令。当前默认将文件工具限制在工作区内，但 `shell` 子进程仍继承当前用户权限。敏感项目应在容器、虚拟机或权限受限的账户中运行。

`web_fetch` 的目标 URL 由配置的 provider 服务端抓取，而不是由 MinimalCode 直接访问。`web_tools.base_url` 及其凭据属于可信配置；不要把 provider endpoint 指向不可信或可访问敏感内网的服务。

当前实现 OpenAI Chat Completions 和 OpenAI Responses 两种协议。使用 Anthropic、Gemini 时，需要通过兼容网关或在后续版本增加原生协议适配器。

## 开发

```powershell
python -m unittest discover -s tests -v
python -m compileall -q minimalcode tests main.py
python benchmarks/deepseek_responses.py --runs 3
```

旧入口仍然可用：

```powershell
python main.py -p "inspect this repository"
```

## 与 Pi 的定位

MinimalCode 的第一目标不是复制 Pi 的扩展生态，而是用更小的运行面完成高频终端编码任务。当前已覆盖文件编辑、命令执行、搜索、会话恢复、上下文压缩和自定义兼容供应商。

以下能力仍是明确的后续工作：

- 原生 Anthropic、Gemini 协议。
- RPC/SDK 集成。
- 扩展、Skills 和主题系统。
- 更细粒度的工具权限。
