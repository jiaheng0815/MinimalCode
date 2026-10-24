# MinimalCode

MinimalCode 是一个面向终端编码任务的轻量 Agent。它优先减少启动开销、交互层级和上下文浪费，不提供炫技式 TUI。

当前版本：`0.1.0`。

## 核心特性

- 默认使用 PowerShell 7（`pwsh`）；Windows 缺失时回退到 Windows PowerShell 5.1，其他平台缺失时回退到 `bash`，最后回退到 `sh`。
- 六个精简工具：`read`、`write`、`edit`、`shell`、`grep`、`find`。
- 流式输出，不依赖 Markdown、Rich 或 UI 框架。
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
minimalcode config set-model my-model
minimalcode config set-provider local
minimalcode shell
```

从现有 Pi 配置迁移供应商、默认模型和上下文窗口：

```powershell
minimalcode import-pi
```

该命令默认把 Pi 中已有的 API key 复制到权限受限的 MinimalCode 配置中，但不会在终端输出密钥。若不希望复制密钥，使用 `--no-secrets`，然后设置输出的对应环境变量。

密钥不会自动写入 `settings.json`。推荐使用 `api_key_env` 指定环境变量名；直接配置 `api_key` 也受支持，但应确保配置文件权限。

## 自定义供应商

编辑 `~/.MinimalCode/settings.json`。下面示例假设服务兼容 OpenAI `/v1/chat/completions`：

```json
{
  "provider": "local",
  "model": "my-model",
  "providers": {
    "local": {
      "protocol": "openai_chat",
      "base_url": "http://127.0.0.1:11434/v1",
      "api_key_env": null,
      "context_window": 32768,
      "timeout_seconds": 120,
      "stream": true,
      "include_usage": true,
      "send_reasoning_effort": false,
      "headers": {},
      "extra_body": {}
    }
  }
}
```

使用 OpenAI Responses 协议时，只需修改协议：

```json
{
  "protocol": "openai_responses",
  "base_url": "http://127.0.0.1:8080/v1",
  "api_key_env": "NINFER_API_KEY",
  "context_window": 262144
}
```

协议值为 `openai_chat` 或 `openai_responses`。Responses 模式下，`thinking` 只有同时设置 `send_reasoning_effort: true` 才会作为 `reasoning.effort` 发送。

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

切换模型或供应商：

```powershell
minimalcode --provider local --model qwen-coder
```

交互命令：

- `/new`、`/clear`：新建会话。
- `/compact`：立即压缩历史。
- `/session`：显示会话文件。
- `/status`：显示模型、shell、上下文和 token 状态。
- `/help`：显示命令。
- `/exit`、`/quit`：退出。

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

## 安全和边界

MinimalCode 会执行模型生成的 shell 命令。当前默认将文件工具限制在工作区内，但 `shell` 子进程仍继承当前用户权限。敏感项目应在容器、虚拟机或权限受限的账户中运行。

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

- 原生 Anthropic、Gemini 和 OpenAI Responses 协议。
- RPC/SDK 集成。
- 扩展、Skills 和主题系统。
- 图片输入和更细粒度的工具权限。
