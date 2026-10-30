# 更新日志

## 1.2.0 - 2026-10-30

### 新增

- 新增交互命令 `/model [供应商/模型]`：无参数时显示当前模型、内置供应商、各自的默认模型与密钥状态；给出 `供应商/模型`（如 `deepseek/deepseek-flash`）时切换当前会话的供应商和模型，不写回 `settings.json`。切换会重建供应商客户端、Web/视觉客户端和工具运行时，保留会话历史，子代理随之继承新的 `--provider` / `--model`；缺少对应 `XXX_API_KEY` 时保持原供应商并报出环境变量名。
- 内置供应商预设新增 `openai`（`OPENAI_API_KEY`）、`anthropic`（`ANTHROPIC_API_KEY`）、`google`（`GEMINI_API_KEY`）、`openrouter`（`OPENROUTER_API_KEY`）、`groq`（`GROQ_API_KEY`）、`mistral`（`MISTRAL_API_KEY`）、`xai`（`XAI_API_KEY`）、`moonshot`（`MOONSHOT_API_KEY`）、`zhipu`（`ZHIPUAI_API_KEY`）、`dashscope`（`DASHSCOPE_API_KEY`）、`siliconflow`（`SILICONFLOW_API_KEY`）、`together`（`TOGETHER_API_KEY`）、`fireworks`（`FIREWORKS_API_KEY`）、`perplexity`（`PERPLEXITY_API_KEY`）、`cerebras`（`CEREBRAS_API_KEY`）、`sambanova`（`SAMBANOVA_API_KEY`）、`nebius`（`NEBIUS_API_KEY`）、`deepinfra`（`DEEPINFRA_TOKEN`）、`novita`（`NOVITA_API_KEY`）、`cohere`（`COHERE_API_KEY`）、`github`（`GITHUB_TOKEN`）、`volcengine`（`ARK_API_KEY`）、`minimax`（`MINIMAX_API_KEY`）、`hunyuan`（`HUNYUAN_API_KEY`）、`qianfan`（`QIANFAN_API_KEY`）、`modelscope`（`MODELSCOPE_API_KEY`）、`baichuan`（`BAICHUAN_API_KEY`）、`yi`（`YI_API_KEY`）、`stepfun`（`STEP_API_KEY`）、`spark`（`SPARK_API_PASSWORD`）、`ppio`（`PPIO_API_KEY`），以及本地 `ollama`、`lmstudio`、`vllm`（无需密钥）。预设只绑定端点和密钥变量，不猜模型 ID；本地三家的 `context_window` 取保守的 `32768`。
- 新增 `--anthropic-env`：直接读取 Claude Code 风格的 `ANTHROPIC_*` / `CLAUDE_CODE_*` 环境变量启动 Anthropic Messages 协议，不读写 `settings.json`，普通启动仍走文件配置。支持 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`（`Authorization: Bearer`）、`ANTHROPIC_API_KEY`（`x-api-key`）、`ANTHROPIC_MODEL`（回退 `ANTHROPIC_DEFAULT_SONNET_MODEL`）、`CLAUDE_CODE_EFFORT_LEVEL`、`CLAUDE_CODE_AUTO_COMPACT_WINDOW`，以及扩展项 `ANTHROPIC_CONTEXT_WINDOW`、`ANTHROPIC_MAX_TOKENS`。模型名里的 `[1m]` / `[200k]` 后缀用于选择上下文窗口并在发送前去掉；该参数与 `--provider` 互斥。
- 新增 `anthropic_messages` 供应商协议，可直接接入 Anthropic Messages API 及其兼容实现。请求会拆分 system 文本、把历史消息转换为 content blocks、把 OpenAI 风格的工具定义转换为 `name` / `description` / `input_schema`，并把连续的工具结果合并为一条 user 消息且保证 `tool_result` 位于文本之前。
- 新增内置 `deepseek-anthropic` 预设，指向 `https://api.deepseek.com/anthropic`，复用 `DEEPSEEK_API_KEY`。
- 新增 Anthropic 流式解析：`message_start`、content block 的 text/thinking/tool_use 增量、`message_delta` 的 stop reason 与累计 usage、`message_stop`、错误事件，以及未收到 `message_stop` 时标记为 `incomplete`。
- Anthropic 思考模式下，工具调用轮的 thinking 块（含 signature）会保存在 assistant 消息的 `provider_content` 中并在下一轮原样回传；已完成的回答不保留思考块。Chat 请求会剥离该字段，避免切换协议后把 Anthropic 私有字段发给 OpenAI 兼容服务。
- 供应商配置新增 `max_tokens`（Anthropic 默认 `8192`）。`send_reasoning_effort: true` 时按思考等级发送 `thinking`，`none` 发送 `{"type":"disabled"}`。

### 变更

- 版本号升级到 1.2.0。本次发布没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段或会话格式；公开面的变化全部是新增。
- `/new` 改为复用当前活动的供应商与模型，而不是启动时的那一份。切换模型后新建会话不会退回旧模型，同时清零上一会话的累计用量。

### 文档

- README 增加“内置供应商预设”表、`/model` 用法和 1.1 → 1.2 升级说明；`docs/configuration.md` 记录预设、密钥变量和区域端点覆盖方式；`docs/compatibility.md` 增加 1.2.0 的兼容性说明。

### 测试

- 新增 `/model` 回归测试：切换后活动供应商、模型和工具运行时同步更新，旧客户端关闭，会话历史保留；密钥缺失时保持原供应商并报出 `OPENAI_API_KEY`；非法选择器（未知供应商、无默认模型的供应商、空模型）不会改动当前运行时；`/new` 使用切换后的模型。
- 新增内置供应商预设测试：逐个断言 `api_key_env` 指向对应厂商的环境变量，本地三家不要求密钥且指向环回地址；并按真实配置构造客户端，断言 36 个预设的 `_endpoint()` 都拼出完整的 `/chat/completions`、`/responses` 或 `/messages` 地址。
- 新增 Anthropic 协议回归测试：非流式载荷、header 与用量归一化，工具定义转换，工具结果合并及 `is_error`，流式 text/thinking/tool_use 分片，thinking signature 回传，SSE 错误，缺少 `message_stop`，端点推导，`provider_content` 不完整时回退到 `tool_calls`，`thinking: none`，以及 Chat 对 `provider_content` 的剥离。
- 新增 Anthropic 端到端测试：本地 HTTP 服务以 SSE 返回 thinking + `tool_use`，验证真实文件写入、第二轮请求里 thinking 块（含 signature）按原样回传、`tool_result` 位于 user 消息首位，以及 CLI 的 JSON 输出。
- 配置测试覆盖 `anthropic_messages` 协议与非法 `max_tokens`。
- 新增 `--anthropic-env` 回归测试：环境变量合成供应商、Bearer 与 `x-api-key` 两种凭据、`[1m]` 后缀与窗口换算、`CLAUDE_CODE_*` 覆盖、非法数值报错、`--provider` 互斥，以及 CLI 运行时 `settings.json` 逐字节不变。

## 1.1.0 - 2026-10-29

### 新增

- 新增交互命令 `/summary`：把当前会话（系统提示与全部消息）交给模型总结成仓库笔记，写入工作区 `AGENTS.md`。文件不存在时创建；已存在时只改写 `<!-- minimalcode:summary:start -->` / `<!-- minimalcode:summary:end -->` 之间的标记块，标记块外的内容逐字保留，重复执行是替换而不是追加，并沿用原文件的换行符。写入路径仍经沙箱解析，符号链接指到工作区外或命中敏感文件时拒绝写入；读到非 UTF-8 内容时不动原文件。总结内容不进入会话历史，该文件在下次运行时作为仓库指令载入。
- 每次运行开始前重新读取全局与项目级 `AGENTS.md` / `CLAUDE.md`（文件不存在则跳过），而不是在进程启动时缓存一次。`/summary` 写入的新内容、以及外部编辑器在会话期间改动的规则，都会在下一轮生效；同一轮内的多次模型请求仍复用同一份系统提示，避免工具中途改写规则导致上下文前后不一致。

### 变更

- 供应商返回的思考内容现在会显示：Chat 的 `reasoning_content`/`reasoning` 增量与 Responses 的 reasoning summary 事件/条目都会实时输出，样式取自 Markdown 六级标题（调色板 `h6`）。思考内容仅用于显示，不进入 assistant 消息、会话文件或 `--json`；无 ANSI 支持时（重定向输出、`--json`、`NO_COLOR`、`--no-highlight`）不输出。

### 测试

- 新增回归测试：Chat 流式与非流式的思考内容解析、Responses reasoning summary 增量与 reasoning 条目回退、同一段思考不重复输出、agent 的 h6 样式渲染及无 ANSI 时不输出。
- 新增回归测试：`AGENTS.md` 合并（新建、追加、替换标记块、CRLF 保留、模型回显标记不嵌套）、`/summary` 写入文件与失败后会话继续、空总结被拒绝且不落盘。
- 新增回归测试：每次运行重建系统提示、运行间改动 `AGENTS.md` / `CLAUDE.md` 在下一轮生效，以及没有规则文件时照常开始。

## 1.0.0 - 2026-10-28

首个稳定版本。从 1.0.0 起，1.x 系列保持 CLI 参数、配置键、JSON 输出字段、
会话格式，以及 `minimalcode.tools`、`minimalcode.sandbox`、
`minimalcode.provider`、`minimalcode.highlight` 四个包的 `__all__` 公开面兼容；
内部模块与私有 helper 仍可能移动，迁移方式见
[docs/compatibility.md](docs/compatibility.md)。

### 破坏性变更

- `--json` 的 `usage` 从会话累计值改为本次运行的用量，累计值移到 `session_usage`，并新增 `usage_estimated`。按旧语义读取 `usage` 的脚本需要改用 `session_usage`。
- `import-pi` 不再默认把 Pi 里的 API key 复制进 `settings.json`：默认写入指向环境变量的 `api_key_env`，需要复制时显式加 `--secrets`。

### 修复

- 本机供应商被环境代理劫持：三个 HTTP 客户端都继承 `trust_env`，`base_url` 指向环回地址时请求会被 Clash 之类的系统代理接管，`python -m unittest discover -s tests` 在配置了代理的机器上直接返回 502。新增 `minimalcode/httpclient.py` 统一客户端构造：环回地址和显式注入的 transport 默认不读环境代理，可用 `trust_env` 覆盖。三份重复的 header 构造与连接重试合并到这里，`vision` 因此获得与 Web 工具相同的重试。
- `--session <path>` 会把任意文件当成会话并追加 JSONL（`minimalcode --session README.md "hi"` 会污染 README）：现在只接受 `.jsonl` 文件；会话文件里记录的 workspace 只有在该文件位于 `sessions/` 目录下时才被信任，避免任意 JSONL 把工具根目录指到别处。
- token 估算按 `len(text) // 4` 计算，中文内容约少算 4 倍，而它同时是压缩阈值和无 usage 时的唯一数据源。现在按字符宽度加权（CJK/emoji 约 1 token/字符），估算结果在 CLI 标注 `(estimated)`，`--json` 里是 `usage_estimated`。
- 网络策略把命令里所有 token 都当作可执行名，`rg curl`、`git log --grep=curl`、`grep -c curl file` 都会被判定为联网命令而拒绝。现在只检查命令位置（含管道、`sudo`、`VAR=1` 前缀和 `pwsh -Command` / `bash -c` 的内联命令），同时仍能识别这些形式中的真实网络命令。
- 基准报告在只有 MinimalCode 运行时仍输出 `Pi 0/0` 对比表格，读起来像对比结果。现在只列出实际运行过的 agent，缺少 Pi 时明确写“未运行”，并按新格式重新生成了 2026-10-26 的结果文件。

### 变更

- 删除 `minimalcode.shell._prefix_command` 兼容别名，与 0.7 起“不为内部名字保留兼容层”的约定一致。
- 清理本地 `build/` 与 `minimalcode.egg-info/` 残留。
- 版本号升级到 1.0.0。公开 `__all__`、CLI 参数和配置键与 0.8.0 一致；行为变化见“破坏性变更”和上文的修复项。
- 开发工具升级到 `ruff==0.17.3`，现有检查与格式化结果不变。
- CI 增加 wheel 构建，并校验包内 `defaults.json` 确实进入 wheel，避免 1.0 的打包元数据或包数据回归。

### 测试

- 新增 `tests/test_httpclient.py`：环回地址绕过环境代理、注入的 transport 不被代理映射遮蔽、退避上限。
- 新增回归测试：非 `.jsonl` 会话文件被拒绝、会话记录的外部 workspace 不被信任、命令参数中的网络命令名不误报、CJK 与 ASCII 的估算差异、无 usage 时的估算标记、vision 连接重试、`--json` 的 usage 字段。
- 修复共享 runner 上超时测试的偶发失败：pwsh 在当前 1 秒窗口内还没启动完时，部分输出断言会看到空 stdout；现在先用 1 秒、再用 4 秒窗口重试，启动延迟不再被误判为输出丢失。

## 0.8.0 - 2026-10-26

### 变更

- 为 `tools`、`sandbox`、`provider`、`highlight` 四个包补齐包级 docstring：每个包说明它解决什么问题、`__all__` 里的公开名字、与相邻包的分工边界，以及必须遵守的不变量。
- `provider` 与 `highlight` 的实现补齐「为什么」注释：重试预算与退避、`Retry-After` 的上下界、流式工具调用分片累积、Responses 的 `item_id`/`output_index` 双寻址、增量渲染器为何要暂扣不完整标记、终端转义序列的识别与上限。
- `provider.responses` 的 arguments 合并规则抽成具名 helper `merge_arguments`，`update_call` 与 `function_call_arguments.done` 共用同一份实现；行为不变。
- `provider.transport` 的重试计数器改名为 `connection_failures`，并注明状态重试不计入连接预算。
- 版本号升级到 0.8.0。

### 测试

- 新增供应商重试策略测试：429/503 只重试一次、`Retry-After` 的 0.2 秒下界与 5 秒上界、无法解析的 `Retry-After` 回退 1 秒、5xx 不重试，以及连接预算耗尽后状态重试仍然可用（单次调用最多 `_CONNECTION_ATTEMPTS + 1` 个请求）。

本版本没有 CLI、配置或工具行为变化；`__all__` 公开面与 0.7.0 一致。

## 0.7.0 - 2026-10-26

### 破坏性变更

- 移除拆包过程中临时保留的 facade 兼容属性（原计划 0.7.0 移除，实际在发布前删除）。`minimalcode.tools`、`minimalcode.sandbox`、`minimalcode.provider`、`minimalcode.highlight` 现在只导出各自 `__all__` 中的公开名称；访问拆分前的其他名字会直接抛 `AttributeError`，不再经过 `__getattr__` 回退，也不再发出 `DeprecationWarning`。
- 旧的 patch 目标不再改变实现。`unittest.mock.patch("minimalcode.tools.shutil.which")`、`minimalcode.provider.time.sleep`、`minimalcode.highlight._supports_color` 等必须改为实际查找该名字的模块，例如 `minimalcode.tools.search_tools.shutil.which`、`minimalcode.provider.transport.time.sleep`、`minimalcode.highlight.color._supports_color`。
- 私有 helper 改为从定义它的子模块导入，例如 `minimalcode.tools.search.run_python_grep`、`minimalcode.sandbox.execution._posix_limits`、`minimalcode.highlight.palette._PALETTES`。完整的导入与 patch 对照表见 [docs/compatibility.md](docs/compatibility.md)。

### 变更

- 将 `tools.py`、`sandbox.py`、`provider.py`、`highlight.py` 拆分为同名包：`tools/`（schema、文件、搜索、进程、远程工具、参数校验、分派）、`sandbox/`（策略、敏感路径、网络、后端、执行、审计、入口）、`provider/`（客户端、Chat、Responses、传输、公共解析、类型、工厂）、`highlight/`（Markdown 状态机、代码着色、终端清理、颜色探测、调色板）。
- 测试中的 patch 目标全部迁移到实现模块，并新增 `tests/test_package_surface.py` 锁定 `__all__` 公开面、防止兼容回退重新出现。
- README 的项目结构与开发命令、`docs/architecture.md` 的模块与包布局、`docs/configuration.md` 的版本说明按 0.7.0 更新；`docs/compatibility.md` 重写为 0.6 → 0.7 的迁移指南。
- 版本号升级到 0.7.0。
- 沙箱执行层默认关闭（默认等价 `--no-sandbox`）：`sandbox.enabled` 与 `minimalcode/defaults.json` 的默认值改为 `false`，需要时用 `--sandbox` 开启。文件工具的工作区约束与敏感文件过滤不受影响，仍然始终生效。
- 任意 `--sandbox-*` 调优参数会隐式开启执行层，避免 `--sandbox-timeout 30` 之类的参数成为静默无效项；显式的 `--no-sandbox` 优先。执行层关闭时 `subagent` 只受 `subagent.timeout_seconds` 约束。
- `should_compact` 优先使用供应商返回的 prompt token 数，不再每轮序列化整个历史。

### 修复

- 修复 `--no-sandbox` 同时放开了工作区约束和敏感文件过滤的 fail-open 问题：`sandbox.enabled=false` 现在只关闭执行层（后端包装、网络策略、子进程资源限制），文件工具仍受允许根、符号链接逃逸检查和敏感文件策略约束，工作区外的读写需要使用 `tools.allow_outside_workspace`。
- 修复 `subagent.timeout_seconds` 被 `sandbox.timeout_seconds` 静默截断到 120 秒的问题：构造工具时就按策略上限收敛，工具结果返回实际生效的 `timeout_seconds`，原因写入沙箱 notes 并可在 `minimalcode sandbox` / `/status` 中看到。
- 修复超长行导致行号错位的问题：`read` 的 Python 搜索后备实现（子进程与内联两套）现在按物理行计数，超过单行上限的行会先消费剩余片段再继续，`read(start_line=...)` 不再返回被切成多行的片段。
- 修复 `docker` 后端在 Windows 上被报告为可用却必然失败的问题（Docker Desktop 无法消费 `C:\ws:C:\ws` 形式挂载），并在 POSIX 上以当前 `uid:gid` 运行容器，避免工作区出现 root 属主文件。
- 修复 `local` 网络策略把只读的 `python -m pip show/list`、`pip --version` 误判为联网命令的问题：只有 `install`、`download`、`search`、`index` 等子命令会被拒绝。
- 修复工具历史修复逻辑：孤立的 `tool` 消息和重复的 `tool_call_id` 结果会被丢弃，避免严格供应商直接 400；工具处理函数抛出非预期异常时也转为工具错误结果，不再中断整轮并让用户消息悬空。
- 修复上下文压缩在历史缺少 `user` 消息时永远无法推进的问题：新增回退边界，保证保留的尾部不以孤立的工具结果开头。
- 修复 `subagent`/`init` 等管理命令与提示词冲突的问题：命令行出现 `--` 时不再按子命令解析，`minimalcode -- init` 发送提示词 `init`。
- 修复供应商在拿到响应前断开（`provider network error: Server disconnected without sending a response.`）时只重试一次的问题：现在重试 10 次，退避从 0.5 秒翻倍到最多 8 秒；已经开始输出的流式中断仍不重试，避免重复输出或重复计费。重试提示写入工具活动流，`--quiet` / `--json` 下保持静默。

## 0.6.0

### 新增

- 新增可配置、fail-closed 的沙箱执行层（`minimalcode/sandbox.py`）。所有 shell/subagent 命令、文件读写和搜索现在都经过 `SandboxRunner`：工作区限定的允许根（含符号链接逃逸检查）、敏感文件拦截、默认禁网（可选主机白名单）、超时/输出/资源上限、每条命令的临时目录用后必定清理，以及拒绝原因审计。后端可插拔：跨平台的 `local` 基线由策略加 POSIX rlimit 组成，`bwrap`、`firejail`、`sandbox-exec`、`docker` 均为可选且不是强依赖；显式选择但不可用的后端会拒绝执行而不是降级。新增配置：`sandbox.enabled`、`sandbox.backend`、`sandbox.allowed_roots`、`sandbox.network_policy`、`sandbox.allowed_hosts`、`sandbox.timeout_seconds`、`sandbox.max_output_chars`、`sandbox.allow_sensitive_files`、`sandbox.max_memory_mb`、`sandbox.max_processes`、`sandbox.docker_image`；新增 CLI 参数 `--sandbox/--no-sandbox`、`--sandbox-backend`、`--sandbox-root`、`--sandbox-network`、`--sandbox-allow-host`、`--sandbox-timeout`、`--sandbox-max-output`、`--sandbox-allow-sensitive`；新增 `minimalcode sandbox` 命令。
- 版本号升级到 0.6.0。

### 修复

- 修复 Linux 上启用沙箱后 PowerShell 7 报 `Out of memory` 的问题：`sandbox.max_memory_mb` 的默认值从 `2048` 改为 `0`（关闭）。`RLIMIT_AS` 限制的是进程预留的地址空间而不是实际占用，.NET（pwsh）、JVM 和 Go 子进程在 2 GB 上限下会在真正运行前中止；需要该限制时仍可显式配置。
- 修复 shell 探测只检查可执行文件是否存在、不检查能否启动的问题：`detect_shell` 现在先用一条 `exit 0` 验证候选 shell，`pwsh` 因缺少 libicu 或无法预留内存而崩溃时会回退到 `bash`/`sh`（Windows 回退到 `powershell`），`ShellError` 会带上每个候选的失败原因；显式配置的 `shell.path` 启动失败会直接报错而不是静默换 shell。探测结果按可执行文件缓存，每个进程只付一次启动开销。
- 修复 `subagent` 端到端测试在 shell 不可用时只观察到 2 次供应商请求的问题：断言现在会附带父进程收到的工具结果，便于定位子进程失败原因；环境完全没有可用 shell 时改为跳过。

## 0.5.0

### 新增

- 新增 `max` 推理强度档位：最高一级，按原样转发为 `reasoning_effort`（Chat）/ `reasoning.effort`（Responses），`--thinking`、`thinking` 和 `/effort` 均接受该值。
- 新增 `subagent` 工具：agent 可以通过配置的 shell 启动一个全新的 `minimalcode -p` 子进程并使用它的最终答复，适合代码审查、广度分析和独立验证。任务正文通过 stdin 传入，子进程继承 `--home`、供应商、模型、推理强度和工作区，嵌套层数由 `subagent.max_depth`（默认 `2`，通过 `MINIMALCODE_SUBAGENT_DEPTH` 记录）限制。新增配置：`subagent.enabled`、`subagent.max_depth`、`subagent.max_turns`、`subagent.timeout_seconds`。
- 新增 `/effort` 交互命令，无需重启即可查看或切换推理强度（`none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`ultra`、`max`）；`none` 关闭思考，`/status` 会显示当前强度。
- 版本号升级到 0.5.0。
- 新增 `read_image` 工具：本地 JPEG/PNG/GIF/WebP 图片发送给供应商的视觉模型，返回文字描述或对问题的回答。该工具由供应商的 `vision` 配置块启用，对 DeepSeek 默认开启，并保持在工作区内。
- 单次任务的默认模型/工具循环上限从 50 提升到 1000 轮。
- 新增交互式终端的流式 Markdown 与 README 渲染。
- 未闭合的粗体、斜体、删除线、行内代码和链接文本会立即渲染，遵循 Streamdown/remend 风格的不完整块处理。
- 链接渲染为 `label (url)`，方便在终端中点击。
- 新增深色/浅色调色板、`--no-highlight` 和 `NO_COLOR` 支持，同时保持 JSON 与重定向输出为纯文本。
- shell 工具活动现在会显示命令退出码、stdout 和 stderr，除非处于静默或 JSON 输出模式。
- `[shell]`、`[read]` 等工具活动标签在支持 ANSI 的终端上显示为绿色。
- 分隔线（`---`、`***`、`___`）会被高亮，而不再被当作强调标记吞掉。

### 变更

- HTTP 层从 `httpx` 迁移到 `httpx2`——由 Pydantic 维护的 `httpx` 延续版本，公共 API 保持一致。`httpx2` 现在是唯一的运行时依赖，provider、web 和 vision 客户端全部改用 `httpx2.Client` / `httpx2.Response`。
- 放弃 Python 3.10 支持：`httpx2` 要求 Python 3.11+，因此 `requires-python` 改为 `>=3.11`，CI 矩阵从 3.11 开始，Ruff 目标为 `py311`。
- TLS 校验改用操作系统信任库，而不是 `certifi` 的证书包（这也是 `httpx2` 的默认行为）。`SSL_CERT_FILE` / `SSL_CERT_DIR` 和显式的 `verify=` 仍然优先。
- 不再打印标题标记：`## Title` 只输出蓝色样式的标题文字，与粗体、斜体去掉分隔符的行为一致。
- shell 工具活动会把 stdout/stderr 裁剪为首尾各若干行，长命令不再刷屏；模型仍会收到完整输出。
- 无论是否安装 `ripgrep`，`grep` 和 `find` 现在都会跳过相同的忽略目录（`.git`、`node_modules`、`build`、`dist`、各类缓存和虚拟环境）。
- 当前启用的供应商中未解析的 `${VAR}` 占位符现在会抛出指明变量名的配置错误，而不再作为字面文本发给供应商。
- `web_fetch` 接受供应商返回的 `null` 标题，只拒绝非字符串标题。
- shell 命令通过有界的内存流捕获输出，因此超时命令即使还有后台子进程存活，也能返回已有部分输出，而不会阻塞或耗尽内存/磁盘。
- 系统提示词每个 agent 只构建一次，而不是每轮模型调用一次，`SYSTEM.md`/`AGENTS.md` 不再每次往返都重新读取。
- Web 搜索结果现在会被规范化：非对象条目被丢弃，数量受 `max_count` 限制，过长的 `content`/`text`/`snippet` 字段按 `max_content_chars` 截断。
- `write` 改为执行 `tools.max_file_bytes`，不再使用无关的 `max_file_chars * 8` 启发式。
- `config show` 不再把 `max_tokens`、`prompt_tokens`、`tokenizer` 等 token 预算和计数器当作敏感字段打码，而 `access_token` 这类键仍然隐藏。
- 配置的 `max_results` 超过工具硬上限时会被收敛到上限，而不是在默认限制下被静默忽略。
- 当 Pi 只有 `models.json` 而没有 `settings.json` 时，`import-pi` 也能正常工作。
- 项目改用 Apache License 2.0：`LICENSE` 保存完整文本，`pyproject.toml` 声明 `license = "Apache-2.0"` 与 `license-files`，构建 wheel 或 sdist 需要 setuptools 77 或更新版本。

### 修复

- 流式响应如果在缺少 `finish_reason` 或 `[DONE]` 的情况下结束，现在会被报告为未完成，而不是当作成功的截断答复；未完成的工具调用不会被执行。
- 来自模型、shell 和错误信息的不受信输出在写入终端前会移除终端转义序列，包括被拆分到多个流式 delta 中的序列。
- 解析后位于其声明目录之外的项目指令文件（例如被符号链接的 `AGENTS.md`）会被忽略，而不再注入系统提示词。
- 非法的 `thinking` 取值以及格式错误的供应商 `protocol`/`base_url`/数值/布尔配置会在加载配置时被拒绝，而不是稍后或在子 agent 中失败。
- `/effort` 现在会同步更新工具运行时而不只是 provider，因此 subagent 使用选定的推理强度。
- `web_search` 会遵循请求的/默认的结果数量，而不只是 `max_count`；供应商返回的所有字符串字段（包括标题和 URL）都受长度限制。
- `edit` 会对结果文件执行 `tools.max_file_bytes`，而不只是检查原文件。
- Python 版 `grep` 回退使用 smart-case、跳过相同的忽略目录、截断超长行，并在超时限制下运行，因此灾难性正则不会挂死 agent。
- `grep`/`find` 只读取所需数量的结果行；`read` 限制单行内存占用，并在 `start_line` 超出文件末尾时报告，而不是返回虚假的行范围。
- shell 输出改用有界捕获而不是无上限的临时文件；缺少 `taskkill` 或按下 Ctrl-C 时，不再遗留仍在运行的进程树，也不会在超时路径上抛异常。
- 压缩（compaction）转录按模型窗口大小裁剪；总结答复的 finish reason 不完整时不再允许替换历史记录。
- 缺少元数据的会话文件现在默认使用请求的工作区，而不是在 sessions 目录里运行工具。
- `config` 会拒绝多余的位置参数，而不是静默忽略。
- 供应商 POST 只在连接失败和限流/服务不可用时重试，避免读取中断后产生重复工作。
- 修复超时 shell 命令遗留子进程的问题：超时现在会杀掉整个进程树（Windows 使用 `taskkill /T`，其他平台使用子进程所在的进程组），而不只是直接 shell 进程，因此被遗弃的测试运行器不会再持续占满 CPU。超时的 `subagent` 提示词同样改为从文件而非管道读取。
- 修复单行超过 `max_file_chars` 时 `read` 返回空内容的问题；现在会返回该超长行的前缀并标记 `truncated`。
- 修复 `edit` 会重写整个文件的行尾、以及 `old_text` 使用 LF 时在 CRLF 文件上匹配失败的问题；现在只重写匹配到的片段。
- 修复 Markdown 渲染在深度缩进行上卡住的问题：每个 delta 从上一个结束的位置继续扫描，而不是重新扫描行前缀。
- `write` 和 `edit` 保留已有文件的权限位，而不再替换为 `0600`；新建文件仍使用 `0600`。
- 非法的工具参数会在分发前被检测出来，处理函数抛出的 `TypeError` 不再被误报为参数错误。
- `/new` 会保留 `--no-highlight` 和非流式输出设置。
- 模型请求的 shell 超时会被 `shell.timeout_seconds` 收敛到上限。
- Web fetch 响应会拒绝非字符串内容，未解析的供应商环境变量占位符会以变量名报错。
- 损坏或非对象的会话头不再抛出 `AttributeError`；`list()` 会跳过它们，并按文件名解析。
- 完整会话 ID 现在会匹配已记录的会话元数据，而不只是缩短后的文件名。
- 存在于磁盘但无法读取的配置文件现在抛出配置错误，而不是静默回退到内置默认值。
- 模型返回空答复时会报告任务未完成，而不是当作成功的空回答；提前停止的诊断信息也不再向 stderr 倾倒完整答复。
- 单轮失败不再终止交互式会话；provider 和 I/O 错误会被告知，提示符继续可用。
- Markdown 渲染不再丢弃答复末尾的反斜杠。
- 当 `#` 标记被拆分到多个流式 delta 时，`##` 和 `###` 标题不会再被渲染成更低级别的标题。
- 缩进的闭合围栏现在会结束代码块，因此列表项中嵌套围栏之后的二级标题和其他 Markdown 会正常渲染，而不再被当作代码内容。
- 会话文件从第一次写入就使用私有权限，而不是事后再收紧；usage 写入失败也不再丢弃模型的答复。
- `sessions` 不再因 `session_id` 不是字符串的元数据而崩溃。
- 供应商和 web 工具的数值配置会拒绝非有限值（`Infinity`），而不是抛出 `OverflowError`。
- Responses 请求不再发送严格服务端会拒绝的空 `role`/`content` 条目。
- Python 回退路径下 `grep` 与 `find` 对 `**/*.py` 这类递归 glob 的判断保持一致，且回退会跳过大于 `tools.max_file_bytes` 的文件。

## 0.3.0 - 2026-10-24

- 从运行时代码中移除供应商主机名检查和硬编码的 web 端点。
- Web 工具的能力、端点、超时、结果上限、内容上限、密钥来源和请求头现在都是供应商配置。
- 把完整的默认配置从 Python 代码移到打包的 `defaults.json`。
- DeepSeek 默认保持 web 工具开启；其他供应商除非显式配置，否则保持关闭。

## 0.2.0 - 2026-10-24

- 新增 DeepSeek 托管的网页搜索和网页抓取工具。
- Web 工具只对官方 DeepSeek API 开放，其他供应商一律屏蔽。
- 新增按供应商区分的工具定义和 web API 回归测试。

## 0.1.0

- 引入 `MinimalCode` 包和 `minimalcode` 命令。
- 新增 PowerShell 7、Windows PowerShell 5.1、bash 和 sh 的 shell 回退。
- 新增 read、write、edit、shell、grep 和 find 工具。
- 新增兼容 OpenAI Chat Completions 与 Responses 的流式供应商支持。
- 新增一条命令迁移兼容的 Pi 供应商和默认模型。
- 新增自动 JSONL 会话、继续会话、用量统计和上下文压缩。
- 新增 `AGENTS.md` 发现、工作区边界、JSON 模式和聚焦测试。
