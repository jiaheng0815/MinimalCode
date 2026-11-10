# 更新日志

## 1.5.3 - 2026-11-10

### 修复

- 沙箱：PowerShell 的 `-Param:value` 写法绕过敏感路径检查。`_looks_sensitive_token` 对以 `-` 开头的词整体返回 False，而 PowerShell 用冒号粘值，于是 `Get-Content -Path:.env`、`Get-Content -LiteralPath:C:\x\.env`、`Set-Content -Path:.env` 都读得到 `Get-Content .env` 会被拒绝的明文。现在冒号后面的值同样参与判定，纯选项名（`rg --hidden`）照旧跳过。
- 沙箱：转义引号让引号配对翻转。两个扫描器都不认转义，`printf "a\"b"; cat .env` 和 ``Write-Output "a`"b"; Get-Content .env`` 里的 `\"` / `` `" `` 被当成收尾引号，`;` 之后的整段成了「引号内数据」，凭据被 `shell` 原样打印。现在按 POSIX 与 PowerShell 两种转义各遮蔽一次再合并结果，只增加命中、不减少。
- 沙箱：行续行符把命令名和文件名拆成两段。POSIX 在分词前会把 `\`+换行、PowerShell 会把 `` ` ``+换行、`cmd` 会把 `^`+换行消掉，而扫描器先在裸换行处切分，`cur\`+换行+`l` 只剩 `cur` 和 `l` 两个无害的词，`curl` 不再被识别；`cat .en\`+换行+`v` 同样漏检。现在两种扫描器都先合并续行符。
- 沙箱网络策略：未知选项的独立取值把真正的子命令顶到第二位。`_network_subcommand` 只跳过 `_SUBCOMMAND_OPTION_VALUES` 里有记录的选项值，`--loglevel verbose`、`--log /tmp/pip.log`、`--config /tmp` 的第一个位置参数被当成子命令，随即 `return False`，`npm --loglevel verbose install lodash`、`pip --log /tmp/pip.log install requests`、`docker --config /tmp run …` 在 `deny` 下直接放行、`allowlist` 下跳过主机比对。六次探测的循环本来就是为这种情况写的，非匹配 token 现在继续探测而不是结束。
- 沙箱网络策略：同一条转义引号问题在 `_segments` 上重复出现，`;` 之后真正的 `curl` 完全没有进入 `_all_command_heads`。
- 沙箱审计：`https://<token>@host` 这种只有凭据、没有冒号的 URL 用户名不被脱敏，`AWS_SECRET_ACCESS_KEY=…` 这种关键字带后缀的键也匹配不到（正则要求 `secret` 后紧跟 `=`/`:`）。`git clone` / `aws` 正是被网络策略拒绝的命令，整串凭据会进入 `SandboxDenial.target`，再由 `/status` 的 `recent_denials` 输出给用户和模型。
- 工具：`grep` 在 CRLF 文件上的 `$` 锚点两种引擎结果相反。`--crlf` 让 ripgrep 把行的内容结束在 `\r` 之前，而 Python 引擎本来就会 `rstrip("\r\n")`，`search.py` 里「ripgrep 不会找到」的注释恰好说反了：`needle$` 只有装了 rg 才返回空。现在 rg 调用带 `--crlf`，三种引擎一致。
- 工具：UTF-8 BOM 让 `^` 锚点永远匹配不到第一行。Python 引擎用 `utf-8` 打开，U+FEFF 留在行首，`^first` 不匹配，而不带 `^` 的同一模式又把 BOM 写进结果文本；ripgrep 会先剥掉 BOM。现在两处都改成 `utf-8-sig`。
- 工具：超过 `tools.max_file_bytes` 的文件在两种引擎下行为不一致。Python 引擎直接跳过，ripgrep 的 `--max-filesize` 却只作用于目录遍历，显式给出的文件照样搜索；同一个 `grep` 调用在有/无 rg 时一个返回命中、一个返回空。现在目录遍历两边用同一个上限，显式指定超大文件则报 `file is too large for grep` 并带上实际字节数与上限。
- 工具：`powershell_heredoc_delimiter` 把 PowerShell 块注释当成 bash heredoc。`<#` 的 `#` 只结束所在行，注释体里的 `cat <<EOF` 被当成待执行代码，`shell` 于是以「PowerShell 7 不支持 bash heredoc」为由拒绝了合法命令。现在块注释在扫描前整段移除。

### 变更

- 版本号升级到 1.5.3。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema。

### 文档

- `README.md` 更新当前版本与 1.5.2 → 1.5.3 升级说明；同时删掉「多供应商切换」里 36 个预设的重复旧条目（1.5.2 起是 37 个）。`docs/configuration.md` 补充 `grep` 的 CRLF/BOM/大小上限说明和敏感路径的新规则，`docs/compatibility.md`、`docs/architecture.md` 更新适用版本与 1.5.3 变更清单。

### 测试

- 新增回归测试：`-Path:.env`、转义引号（POSIX 与 PowerShell）、续行符（`\` 与 `` ` ``）、未知选项后的网络子命令、转义引号后面的 `curl`、续行符拆开的 `curl`、URL 内 token 与 `AWS_SECRET_ACCESS_KEY` 脱敏、rg 调用带 `--crlf`、BOM 下的 `^` 锚点与结果文本、超大文件的显式报错与目录跳过、PowerShell 块注释不是 heredoc。

## 1.5.2 - 2026-11-09

### 新增

- 供应商：`protocol: "deepseek_web"` 直连 chat.deepseek.com 的网页接口，并新增 `deepseek-web` 预设。请求体、JSON-Patch 流式回复和 `x-ds-pow-response` 工作量证明都按浏览器的方式处理；证明用 DeepSeek 自己的 wasm 模块在本机通过 wasmtime 求解，模块随 wheel 发布，缺失时才下载到 `~/.minimalcode/cache`。wasmtime 是可选依赖 `minimalcode[deepseek-web]`，默认安装仍然只需要 `httpx2`。
- 供应商：该协议没有函数调用，工具 schema 渲染进 prompt，调用从回复里解析回来；同时接受约定的 `<<<TOOL_CALLS>>>` JSON 块、裸 JSON 数组和 DeepSeek 原生的 DSML `invoke`/`parameter` 标记，`tool_mode: "none"` 可整体关闭。
- 配置：新增 `cookie`、`cookie_env`、`model_type`、`web_search`、`pow_wasm_path`、`tool_parse_retries` 供应商键；`web_search` 与其它布尔键一样参与 `${VAR}` 展开和类型校验。
- 打包：`minimalcode/provider/sha3_wasm_bg.wasm` 进入 wheel 与 sdist，CI 的产物断言同时检查 `defaults.json` 和该 wasm。

### 修复

- 供应商：`deepseek_web` 的会话 id 读的是 `biz_data.chat_session.id`，而真实服务端把它放在 `biz_data.id`，于是每个回合都报 "session create returned"；现在优先读 `biz_data.id`，旧结构保留为回退。
- 供应商：`deepseek_web` 的补全请求缺 `stream: true` 和 `client_stream_id`，服务端因此只回一整段非 SSE JSON，解析器随即以 "returned application/json instead of text/event-stream" 失败；现在两者都按浏览器格式发送。
- 供应商：`deepseek_web` 的解算器把 challenge/prefix 写进复用的大缓冲（256 字节）。wasm 模块按传入长度接管并释放这块内存，长度与分配不一致时直接 `unreachable` trap，所有 pow 求解都会失败；现在每次按精确长度分配（并注明为什么不能复用、也不显式 free）。
- 供应商：`deepseek_web` 的 pow 票据写在共享客户端上，会跟着下一回合的 `chat_session/create` 和 `create_pow_challenge` 一起发出，而票据是按 `target_path` 签发的；现在请求发出后立即移除。
- 供应商：`deepseek_web` 的 cookie 在会话创建和挑战请求之后才附加。Bearer token 才是凭据，但部署在检查 cookie 的 WAF 后面时首个握手会被拒；现在 cookie 在任何握手之前安装，且仍为可选（缺失不再报错）。
- 供应商：`deepseek_web` 的 `model_type` 永远取预设值，`-m` / `/model deepseek-web/<mode>` 选择被静默丢弃；现在显式选择的模型优先，只有模型仍是预设默认值时才回落到 `model_type`。
- 供应商：`DEEPSEEK_WEB_TOKEN` 里带 `Bearer` 前缀时被拼成 `Authorization: Bearer Bearer …`；现在前缀被去掉再构造头。
- 供应商：`deepseek_web` 把普通 JSON 回答当成工具调用。回复以 `{`/`[` 开头且含 `"name"` 就进入工具解析，`{"name": "Alice", "age": 30}` 会抛解析错误或产生幻影调用，答案被丢弃；现在只有同时出现参数键时才按工具调用处理。
- 供应商：`deepseek_web` 把无法解析的字符串参数包成 `{"value": "<原文>"}` 交给工具。工具收到的不是模型写的参数而是垃圾，而且绕过了本来为语法错误准备的重试；现在这种调用触发一次带原因的纠正回合，参数只接受 JSON 对象或数组。
- 供应商：`deepseek_web` 的 JSON 修复会改写字符串里的逗号。`_strip_trailing_commas` 用正则匹配“逗号 + 右括号”，`"a, }"` 这类参数值被改成 `"a }"`；现在扫描时跳过字符串内容。
- 供应商：`deepseek_web` 缺少 `type` 的流片段被整段丢弃，`type` 大小写不一致时也一样；现在按大小写不敏感匹配，无类型片段按答案显示，`response/content` 补丁也计入正文。
- 供应商：`deepseek_web` 的 DSML 标签只匹配到名字，属性留在清洗后的值里（`name="path"` 会进工具参数），未闭合的标签还会吞掉后续内容；现在完整标签整段消费，只有结尾处的截断标签按开标签处理。
- 供应商：`deepseek_web` 的 pow 求解在 wasmtime 缺失、模块损坏或挑战字段缺失时抛 `ImportError`/`KeyError`，绕过 `ProviderError` 通道；现在都转成带安装提示或缺失字段名的 `ProviderError`。
- 供应商：`_read_body` 只捕获 `httpx2.HTTPError`，而读取路径也可能抛出裸 `OSError`/`ssl.SSLError`，TLS 被重置时以 traceback 结束；现在同样映射为 `ProviderError`，代理提示得以生效。
- 配置：内联 `cookie` 在 `config show` 中以明文输出；现在与 `api_key` 一样脱敏，`cookie`/`cookie_env` 也做字符串类型校验。
- 供应商：`deepseek_web` 在随包 wasm 缺失时会从 CDN 或第三方镜像下载并直接执行，没有任何完整性校验；现在下载结果必须匹配随包模块的 SHA-256，否则拒绝落盘并报告摘要。
- 测试：`tests/test_config.py` 的预设端点断言曾被放宽为四种后缀的并集，`openai_chat` 端点写成 `/chat/completion` 也能通过；现在按协议逐个断言。

### 变更

- 版本号升级到 1.5.2。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema。

### 文档

- `README.md` 更新当前版本、协议列表与预设数量（37 个），新增 1.5.1 → 1.5.2 升级说明和可选依赖安装方式；`docs/configuration.md` 重写 DeepSeek 网页版小节（`userToken` 认证、会话 id、流式字段、`tool_parse_retries`、`-m` 选择 `model_type`、wasmtime 安装）；`docs/architecture.md` 补 `deepseek_web.py` 模块行；`docs/compatibility.md` 更新适用版本与 1.5.2 兼容说明。

### 测试

- 新增回归测试：cookie 先于握手且缺失不报错、票据不残留、`Bearer` 前缀不重复、裸 JSON 回答不被当作调用、字符串参数无法解析时抛出、JSON 字符串参数正确解码、修复不再改写字符串内逗号、`model_type` 覆盖、pow 挑战缺字段/非对象、握手返回非对象 JSON、无类型片段与 `response/content` 显示、会话 id 两种形状、`stream`/`client_stream_id` 字段、随包 wasm 存在且能用已知挑战解出答案 32186、预设端点按协议断言。

## 1.5.1 - 2026-11-08

### 修复

- 沙箱：给敏感路径加引号即可绕过。`sensitive_command_paths` 对带引号的词只在“前一个是 `-c`/`-Command` 等内联旗标”时才扫描，其余引号词一律当数据，于是 `type ".env"`、`Get-Content '.ssh/id_rsa'` 原样返回明文，而不加引号的同一命令会被拒绝。现在引号内的单 token 也参与判定；`git commit -m "document .env"` 这类多词载荷照旧放行。
- 沙箱：`--home <目录>` 被当成凭据读取。上一条收紧后，配置目录名为 `.MinimalCode` 的路径会命中敏感目录规则，而子代理正是用 `minimalcode --home <dir>` 启动的，于是所有 `subagent` 调用都被拒绝。现在“`--长选项` + 纯目录名”不算读凭据（`Get-Content -Path "C:\x\.ssh\id_rsa"` 仍被拒绝）。
- 沙箱网络策略：`-xhost:port` 这种粘连写法不参与 `allowlist` 比对。`_HOST_OPTION_RE` 要求旗标后是 `=` 或空白，curl 的短选项粘连形式匹配不上，`allowed_hosts` 里的 URL 可以经任意代理改道；现在粘连形式也会贡献主机。
- 沙箱网络策略：外层命令里的 PowerShell 下载语句不被分析。`_NESTED_NETWORK_API` 只作用于内联子命令，而本工具的 Windows shell 本身就是 PowerShell，`(New-Object Net.WebClient).DownloadString('http://…')` 直接输入时既没有客户端名也没有 URL scheme，`deny` 放行；现在外层命令也做一次窄匹配（需要 `New-Object …WebClient` 或 `DownloadString(`/`DownloadFile(`）。
- 沙箱网络策略：`py -3 -c …` 与 `python3.12 -c …` 的内联脚本从不被检查。内联宿主名是精确集合，带版本号的名字和 Windows 官方启动器 `py` 都不在里面，`deny` 下这两条都能直接联网；现在按去掉版本号的名字判定，并加入 `py`。
- 沙箱网络策略：`\\?\UNC\host\share` 不被识别。`_UNC_PATTERN` 用 `(?![\\?])` 排除本地长路径 `\\?\C:\…`，同时把扩展长度的远程路径一并排除了；现在 `\\?\UNC\` 有独立分支，主机名照常进入 `allowlist` 比对。
- 沙箱审计：带引号的凭据值以明文进入拒绝记录。脱敏规则的值部分是 `[^\s'\"]+`，紧跟在旗标后的是引号时必然失败，`curl -b "session=…"`、`curl -H "Cookie: session=…"`、`curl --header "X-Token: …"` 原样写进 `SandboxDenial.target`，再由 `/status` 的 `recent_denials` 输出；现在带引号的值一并脱敏。
- 供应商：Chat 省略 `index` 且 `id` 只随首片发送时，参数续片被当成新调用丢弃，首个调用只剩半截 JSON，`finish_reason` 仍是 `tool_calls`，残缺参数会被直接执行。现在无 `id` 的续片归入上一个调用。
- 供应商：同一 `index` 被复用给新调用时，不带 `id` 的续片仍写回旧调用，两个调用的 `arguments` 同时损坏。现在每个槽位记录自己对应的 wire index，续片跟随替换后的调用。
- 供应商：连接重试的每次尝试不受 `timeout_seconds` 约束。预算只裁剪了退避 sleep，单次连接超时仍是 20 秒，`timeout_seconds=30` 时最坏约 40 秒才报错；现在每次尝试按剩余预算下发 `Timeout`，预算用尽即停止。
- 工具：`edit` 的越界提示会把整份文件灌进工具错误。`end_line` 远大于总行数时 `nearest_end` 等于总行数，提示里拼接全部行，20 万行文件产生 3.7 MB 错误文本并进入下一轮请求；现在只显示前 40 行并标注省略区间。
- 工具：`write` 覆盖超大文件时整份读入内存。仅为了保留行尾风格与 Python 校验回滚镜像就 `read_bytes()`，2 GB 文件直接 `MemoryError`；现在与 `edit`/`append_file` 一致，超过 `tools.max_file_bytes` 时返回明确的 `file is too large for write`。
- 工具：`grep(path=<单个文件>, glob=…)` 在有/无 `rg` 时结果相反。ripgrep 不对命令行上显式给出的文件应用 `--glob`，Python 回退会过滤，同一个调用在装了 `rg` 的机器上返回不匹配的文件；现在单文件目标先按 glob 判定（以父目录为根），两个引擎一致。
- 工具：`subagent` 报告的超时可能与实际生效值不一致。`run_shell` 硬上限 3600 秒，而工具结果原样回显配置值，`subagent.timeout_seconds=7200` 时声称 7200 秒、实际 3600 秒被杀；现在统一用 `shell.MAX_TIMEOUT_SECONDS` 裁剪后再回报。
- CLI：stdio 没有固定编码，管道与子代理的中文被损坏。`_configure_stdio` 只设了 `errors="replace"`，Windows 管道默认用 ANSI 代码页：`… | minimalcode -p` 的任务文本按 cp936/cp1252 解码，输出方向被替换成 `?`，而 `errors="replace"` 让这一切静默发生。现在 stdio 固定 UTF-8，子进程环境额外设置 `PYTHONUTF8=1`。
- 配置：`settings.json` 里的 `thinking` 大小写敏感。`--thinking High` 会被 argparse 归一化并接受，同样的值写进配置文件却直接 `ConfigError`；现在校验前去空格并转小写。
- CLI：交互模式下以 `/` 开头的多行粘贴被整段丢弃。命令分派全部落空后按“未知命令”处理，`/dev/null 相关脚本` 这类粘贴内容根本到不了模型；现在只有单行才按未知命令处理。

### 变更

- 版本号升级到 1.5.1。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema；`shell.MAX_TIMEOUT_SECONDS` 是新增常量。
- 可观察的行为变化（均为收紧或纠错）：引号内的敏感路径同样被拒绝；`--home <目录>` 形式不再被误拒；`-xhost:port`、`\\?\UNC\…`、外层 `WebClient` 下载、`py`/带版本号解释器的内联脚本计入网络检查；审计记录中带引号的凭据被脱敏；Chat 续片不再截断或串包；每次请求尝试受剩余预算约束；`edit` 越界提示与 `write` 超大文件有明确上限；单文件 `grep` 的 glob 语义在两引擎间一致；管道 stdio 固定 UTF-8。

### 文档

- `docs/configuration.md` 修正被拼接损坏的后端段落（重复两遍的 `firejail`/`sandbox-exec`/`docker` 描述），并补充引号内敏感路径、`--home` 例外、`-x` 粘连形式、`\\?\UNC\`、外层 `WebClient` 与带版本号解释器；`docs/architecture.md`、`docs/compatibility.md` 更新适用版本与 1.5.1 兼容说明；`README.md` 更新当前版本与 1.5.0 → 1.5.1 升级说明，并去掉重复的“升级指南”标题（`CHANGELOG.md` 顶部重复的“更新日志”标题一并删除）。

### 测试

- 新增回归测试：引号内 `.env` / `id_rsa` 的拒绝与多词引号载荷的放行、`--home <目录>` 不被误拒、`py -3 -c` 与 `python3.12 -c` 的内联脚本、外层 `WebClient` 下载（并保证 `rg "DownloadString"` 不误判）、`-xhost:port` 的 allowlist 比对、`\\?\UNC\` 主机提取、带引号的 `-b`/`-H`/`--header` 脱敏、Chat 无 `index` 续片与复用 `index` 的续片、每次连接尝试的超时预算、`edit` 越界提示的行数与体积、超大文件 `write` 的拒绝、单文件 `grep` 的 glob 一致性、`_clamp_timeout` 与 `run_shell` 上限一致、stdio 固定 UTF-8、子进程 `PYTHONUTF8`、`thinking` 大小写、以 `/` 开头的多行粘贴。

## 1.5.0 - 2026-11-07

### 修复

- 沙箱：`shell` 命令读到了 `read` 拒绝的凭据。敏感文件规则一直只作用于文件工具，`run_argv` 从不检查命令里出现的路径，因此 `type .env`、`cat ~/.ssh/id_rsa` 在任何后端下都返回明文，审计里只留下工具侧的一条拒绝；默认配置 `sandbox.enabled: false` 同样如此。现在命令串里未加引号的敏感路径会被拒绝并记入审计，`cmd /c "type .env"`、`pwsh -Command "Get-Content .env"` 的内联命令会递归扫描，而 `git commit -m "document .env"` 这类引号内的数据不受影响。
- 沙箱敏感名单漏判：`.envrc`（direnv 惯例导出真实 token，与已拦截的 `.env` 只差一个字符）、`secrets.yaml` / `secrets.yml` / `secrets.toml`、`credentials.yaml` / `.yml` / `.toml`、`.git/config`（remote URL 常带 token）此前全部放行，`read` 与 `grep` 都能拿到。现在这些名字被拦截，搜索排除 glob 也逐条补上（`credentials.*` 会连带隐藏 `credentials.py`，所以按扩展名逐条列出）；`secrets.py`、`credentials.py` 仍然可读。
- 沙箱：容器/命名空间后端的 `allowlist` 实际等同 `allow`。`bwrap` 加 `--share-net`、docker 用 `--network bridge`、firejail 不加 `--net=none`、`sandbox-exec` profile 不加 `(deny network*)`，于是 `allowlist` 下后端进程完全放行出口，唯一控制只剩可被绕过的字符串门禁。现在只有 `allow` 放开网络；`allowlist` 与 `deny` 一样隔离网络，主机比对仍在命令门禁里完成（新增 `SandboxPolicy.isolates_network` 只读属性表达这一点）。
- 供应商：SSE 事件被拆成多行 `data:` 时整条丢弃。规范允许一个事件的 JSON 跨多个 `data:` 字段，逐行 `json.loads` 全部失败后 `continue`，内容不进 `content`：Chat 返回空回答且 `finish_reason='stop'`，Anthropic 直接抛 `provider returned an empty stream`。现在 Chat / Responses / Anthropic 三处共用 `iter_sse_data` 先按空行组装事件再解析，同时兼容“每条 `data:` 就是一条完整事件、没有空行分隔”的网关（这类流在缓冲已能解析为完整 JSON 时立即产出）。
- 供应商：Chat 服务器省略 `index` 时并行工具调用串包。两个调用交错到达且每个片段都带 `id` 时，后一个调用的参数续片会被当成第 3 个调用、随后因没有名字被丢弃，第一个调用只剩半截 JSON；现在没有 `index` 的片段先按 `id` 反查已有调用，首个片段缺 `id` 时由后续片段补上。
- 供应商：`arguments` 以对象形式到达时被丢弃。第 240 行的 `and not entry["arguments"]` 让完整对象在已有字符串片段时被忽略，最终留下 `{"path":` 这样的无效 JSON；对象形式现在是完整值，直接覆盖片段，并把该调用标记为已完成，避免后续杂散片段再拼接。
- 沙箱：Windows 控制台输出解码成 U+FFFD。`_decode_output` 一律 `utf-8, errors="replace"`，而 `_child_env` 只给 Python 子进程设了 `PYTHONIOENCODING`；`cmd` 内建命令、`type`、本地化工具写出的 cp936 / cp1252 字节全部损坏且不可恢复。现在先严格按 UTF-8 解码，失败后再依次尝试当前控制台输出代码页、OEM 代码页与 locale 编码。

### 变更

- 版本号升级到 1.5.0。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema；`SandboxPolicy.isolates_network` 是新增的只读属性。
- 可观察的行为变化（均为收紧或纠错）：`shell` 命令里的敏感路径被拒绝（`sandbox.allow_sensitive_files=true` 可放行）；`.envrc`、`secrets.yaml`、`credentials.toml`、`.git/config` 等被拦截；容器后端在 `allowlist` 下不再放行网络；跨行 `data:` 事件不再丢失；省略 `index` 的并行调用保留各自参数；非 UTF-8 的控制台输出按本地代码页解码。

### 文档

- `README.md` 更新当前版本与 1.4.3 → 1.5.0 升级说明；`docs/configuration.md` 补充 `shell` 命令的敏感路径检查、扩充后的敏感名单与 `allowlist` 的后端网络隔离语义；`docs/architecture.md`、`docs/compatibility.md` 更新适用版本与 1.5.0 的兼容说明。

### 测试

- 新增回归测试：`type .env` / `cat .ssh/id_rsa` / `cmd /c "type .env"` 在开启与关闭沙箱两种模式下的拒绝、引号内 `.env` 不被误拒、`.envrc` 与 YAML/TOML 凭据文件与 `.git/config` 的判定（并保证 `secrets.py` 不被误判）、`allowlist` 在三种后端下隔离网络、跨行 `data:` 事件、省略 `index` 的交错并行调用、对象形式 `arguments` 覆盖片段、cp936 字节与普通 UTF-8 的解码。

## 1.4.3 - 2026-11-07

### 修复

- 沙箱网络策略：`env -S 'curl …'` / `env --split-string 'wget …'` 曾整体放行。`-S` / `--split-string` 的值本身就是待执行命令，`_WRAPPER_ARGUMENTS["env"]` 却把它当普通取值选项跳过，于是 `deny` 与 `allowlist` 都只看到一个无害的 `env`；现在该值被取出并作为内联命令重新扫描。
- 沙箱网络策略：`py -3 -m pip install …`、`py -m pip install …`、`python -mpip install …`、`python -m pip3 install …` 不被识别为装包。原来的序列正则只认 `python[0-9.]*\s+-m\s+pip`，漏掉 Windows 的 `py` 启动器、版本选择器、紧贴拼写和带版本号的模块名；这几种写法都能在 `deny` 下从索引装上任意包。
- 沙箱网络策略：内联脚本里的间接网络调用不可见。`_NESTED_NETWORK_API` 只覆盖 `requests` / `urllib` / `httpx` / `socket` 等名字，`pwsh -c "IEX (New-Object Net.WebClient).DownloadString('http://…')"` 与 `powershell -Command "(New-Object Net.WebClient).DownloadFile(…)"` 这种 Windows 上最常见的落地写法不含任何客户端名，检查完全没有触发；现在 `webclient` / `downloadstring` / `downloadfile` 计入嵌套网络 API。
- 沙箱网络策略：`copy \\host\share\x out.txt`、`type \\host\share\s.txt` 这类 UNC 路径不产生任何网络信号。它们走 SMB，命令行里既没有客户端名也没有 URL scheme，`deny` 放行外发与拉取，`allowlist` 也因为拿不到主机名而无法判定；现在 UNC 主机同时作为检测信号与 `allowed_hosts` 的比对来源。
- 沙箱审计：紧贴标志的凭据以明文进入拒绝记录。脱敏正则要求 `-u` / `--user` 与值之间有空格，`curl -uadmin:hunter2`、`curl --user=admin:hunter2`、`mysql -phunter2` 原样写进 `SandboxDenial.target`，再由 `/status` 的 `recent_denials` 输出，审计环缓冲变成了凭证明文副本。现在 `-u` / `--user` / `--password` / `-b` / `--cookie` / `-H` / `--header` 的紧贴与 `=` 形式都脱敏；`-p` 只在值紧贴标志时脱敏，`docker run -p 8080:80` 的端口映射保持可读。
- 文件：`write` 静默丢掉文件原有的 UTF-8 BOM。`read` 用 `utf-8-sig` 解码，`edit` 会保留 BOM，只有 `write` 按裸 `utf-8` 编码，`read` → `write` 往返就改变了整个文件的编码标记；现在原文件带 BOM 时写回同样的 BOM。
- 文件：校验器自身失败被当成“新内容有错”。`_ruff_diagnostics` 在 ruff 退出码非 0 且无可解析诊断时（工作区的 `pyproject.toml` 含未知 `select`、ruff 版本不支持某项配置）合成一条 `ruff [exit]` 诊断，新建文件的前置诊断为空，于是被判为新增诊断，刚写好的文件被 `unlink` 删除并报 `validation failed … rolled back`；现在 `exit` / `unavailable` / `timeout` 走 `warnings`，不参与回滚判定。
- 搜索：`glob` 含 `/` 且 `path` 不是 `.` 时，装有 `rg` 的默认路径静默返回空结果。`rg` 以工作区为 `cwd` 启动却按绝对路径搜索，`--glob` 是相对 `cwd` 匹配的，`find("deep/*.py", path="sub")`、`grep(needle, path="sub", glob="deep/*.py")` 命中 0 条，而 Python 回退引擎能找到——同一查询的结果取决于是否装了 `rg`，模型会把“没找到”当成事实；现在 glob 按搜索根锚定到 `cwd` 相对形式。
- 搜索：`{a,b}` 花括号展开是无限笛卡尔积。`_expand_braces` 递归展开每个组且没有上限，`{a,b}` 重复 20 次时一条 pattern 就能让 `find` 的 Python 回退挂住数分钟；现在超过 256 个变体时按字面量匹配。
- 搜索：`grep` 结果被行内换页符切断。sandbox 分支（即使 `sandbox.enabled=false` 也走）用 `str.splitlines()` 解析 `rg` 输出，它在 `\x0c`、U+2028 和孤立 CR 上也会断行，尾部片段因为没有 `path:N:` 前缀被当成噪声丢弃，`truncated` 仍为 `False`；现在只按 `\n` 分行并去掉 CR。
- 供应商：连接重试不受 `timeout_seconds` 约束。`_open` 的退避睡眠不计入预算，`timeout_seconds=1` 时退避合计 55.5 秒、11 次请求，默认 120 秒配置最坏约 275 秒（2.3 倍超时）；`Retry-After` 的等待同样可能超出。现在截止时间覆盖重试与状态重试的等待。
- 供应商：Anthropic 兼容网关用 `data: [DONE]` 收尾（而非 `message_stop`）时，已经收到 `stop_reason` 的完整回答被判为 `incomplete`，其工具调用被整组丢弃、写入错误工具结果。现在 `[DONE]` 在已报告停止原因时视为正常结束，未报告时仍按截断处理。

### 变更

- 版本号升级到 1.4.3。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema，升级不需要改代码。
- 可观察的行为变化（均为收紧或纠错）：`deny` 下 `env -S`/`--split-string`、`py`/`python` 装包、PowerShell `WebClient` 下载、UNC 路径现在被拒绝；`allowlist` 下 UNC 主机参与比对；审计记录中紧贴标志的凭据被脱敏；`write` 保留 BOM；校验器不可用不再回滚新文件；`grep` / `find` 的 `glob` 按搜索根匹配（此前返回空的查询现在能命中）；供应商重试在 `timeout_seconds` 内结束。

### 文档

- `README.md` 更新当前版本与 1.4.2 → 1.4.3 升级说明；`docs/configuration.md` 补充 `env -S`、`py`/`-mpip`、`WebClient` 与 UNC 的识别范围；`docs/architecture.md`、`docs/compatibility.md` 更新适用版本与 1.4.3 的兼容说明。

### 测试

- 新增回归测试：`env -S` / `--split-string` 的内联命令、`py` 启动器与 `-mpip` / `pip3`、`WebClient` 下载桩、UNC 检测与 `allowlist` 比对、紧贴标志的凭据脱敏（并保证端口映射不被脱敏）、`write` 的 BOM 保留、校验器不可用不删除新文件、非根 `path` 下的路径 glob（`find` 与 `grep` 各一条）、行内换页符、连接重试预算、Anthropic 的 `[DONE]` 收尾。

## 1.4.2 - 2026-11-06


### 修复

- 沙箱网络策略：内联命令取到段尾却保留了开头的引号，带尾随参数时 `sh -c "curl …" arg0`、`pwsh -Command "iwr …" x` 只看得到主机名，`deny` / `allowlist` 都放行；现在按词边界切片，主机前面的真实命令名能被识别。反方向的误判也一并修掉：引号内的整段命令不再被当成命令头，`bash -c "rm -f ./curl"`、`sh -c "cat /usr/bin/wget"` 不再被当成联网命令拒绝。
- 沙箱网络策略：PowerShell 的 `-ec` / `-e` / `-en` 等 `-EncodedCommand` 缩写按前缀识别并解码，`pwsh -ec <base64>` 不再绕过检查；嵌套层数超出预算改为 fail-closed；`npx` / `bunx` 与 `curl.cmd` / `wget.ps1` 这类 Windows 脚本名计入联网程序。
- 沙箱：审计日志补上 URL userinfo（`https://user:pass@host`）与 `net use /user:name password` 的脱敏，普通端口/路径不受影响；子进程读线程启动失败时也终止进程树，不再把仍在运行的命令留在临时目录被删之后；docker 后端拒绝 PowerShell 命令而不是交给 `/bin/sh` 静默误执行；`/status` 的拒绝计数改为同一把锁下的一次快照。
- 工具循环：`tools.max_parallel` / `subagent.max_parallel` 配成 1 时，一组相邻调用只执行第一个、其余既没执行也没有工具结果，下一轮请求因此把未配对的 tool_call 发给供应商；现在按顺序逐个执行。
- 文件：`edit` 保留 UTF-8 BOM，不再在改第 1 行时静默丢掉编码标记；拒绝写入 NUL 字节（否则会产出所有文件工具都读不了的文件）；被替换区间是没有尾随换行的最后一行时，补丁的行尾跟随文件风格而不是回退成 LF；未命中提示与校验预览按物理行编号。
- 会话：`--session` 指向用户自己的目录时不再每次追加就把该目录 `chmod 700`（只收紧本次创建的目录）；最后一行缺少换行的文件先补换行再追加，不再把两条事件粘成一行而整条丢失；含孤立代理对（`\udXXX`）的内容无法编码为 UTF-8 时转义写入，不再让这一次追加抛异常、把已经付费的回复丢掉。
- 渲染：行尾反斜杠不再吞掉换行导致后续所有行的块状态错位（Windows 路径后紧跟代码块时尤其明显）；`ESC` 加 intermediate 字节的转义（`tput sgr0` 的 `ESC ( B`）连同终止字节一起消费，不再漏出一个可见字符；流结束时挂起的缩进不再被丢弃；`//` 只在 token 边界处当作注释，带端口/路径的 URL 不再整段变成注释色。
- 供应商协议：Responses 的 `output_item.done` 通过 `output_keys` 归一化 key，省略 `id` 的重放不再为同一个调用开出第二份状态并把它执行两遍；`function_call_arguments.done` 接受对象形式的参数；Anthropic 的 `message_delta` 里 `input_tokens: 0` 之类的占位不再覆盖 `message_start` 报的输入计数。
- Shell：多行字符串里的 `<<WORD` 与 `#` 注释里的 `<<WORD` 不再被当成 bash heredoc 拒绝（引号状态跨行延续，`#` 后按注释跳过）；显式配置 `cmd.exe` 时直接报错，而不是用 `-c` 启动它（cmd 会打印横幅、退出 0、什么都不执行）。
- 搜索：`smart_case_ignore` 按转义对跳过，`\\D` 之类的字面量大写不会再被当成忽略大小写；`find` 的 `{a,b}.{py,txt}` 展开所有花括号组。
- HTTP / 网页 / 剪贴板 / 配置：预算在第一次尝试前就耗尽时报告超时，而不是 `after 0 attempts: None`；`content: null` 与 `title: null` 一样按空处理；Windows 剪贴板错误按控制台代码页解码；`import-pi` 派生的 `api_key_env` 只含 ASCII 且同名冲突时加序号；`config show` 不再把布尔项 `trust_env` 显示成 `<redacted>`；`save_settings` 回退到设置自身的 home，而不是默认目录。
- `/summary` 走 `dispatch` 写 `AGENTS.md`：`--read-only` 与 `tools.allow` 下会被拒绝，不再绕过工具策略。

### 变更

- 版本号升级到 1.4.2。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema，升级不需要改代码。
- 可观察的行为变化（均为收紧或纠错）：`edit` 拒绝 NUL 字节；未终止末行按文件主导换行补丁；显式配置 `cmd.exe` 作为 shell 直接报错；docker 后端拒绝 PowerShell 命令；`local` 基线下 `npx` / `bunx` 与 `curl.cmd` / `wget.ps1` 视为联网命令，超深嵌套命令按 fail-closed 处理；`/summary` 在 `--read-only` 或 `tools.allow` 未放行 `write` 时被拒绝；`save_settings` 未指定 `home` 时写入设置自身的 `home`。

### 文档

- `README.md` 更新当前版本与 1.4.1 → 1.4.2 升级说明，并补充 `shell.path` 指向 `cmd.exe` 会被拒绝的原因；`docs/configuration.md` 补充 `-EncodedCommand` 缩写、`npx` / Windows 脚本名与 docker 后端拒绝非 POSIX shell；`docs/architecture.md`、`docs/compatibility.md` 更新适用版本与 1.4.2 的兼容说明。

### 测试

- 新增回归测试：内联命令的头识别与误判、`-EncodedCommand` 缩写、`npx` / Windows 脚本名、审计脱敏、`max_parallel: 1` 的整组执行、`edit` 的 BOM / NUL / 未终止末行、会话目录权限 / 缺换行 / 孤立代理对、反斜杠换行与 intermediate 转义、Responses 的 `output_item.done` 与对象参数、Anthropic 的 `message_delta` 零值、heredoc 误判与 `cmd.exe` 拒绝、跨组花括号、转义反斜杠的 smart-case、`content: null`、预算已耗尽、`/summary` 的只读拒绝、`is_allowed` 的相对路径解析。
- 测试自身修正：`os.set_blocking` 降级用例改用 `patch.object(..., create=True)`。Windows + Python 3.11 上该属性本就不存在（正是用例要覆盖的场景），原来的写法在 `patch` 进入时就抛 `AttributeError`，导致 CI 在 3.11 / Windows 组合上失败。

## 1.4.1 - 2026-11-04

### 新增

- `read_range` 与 `append_file`：前者按行号读取显式窗口并带行号，后者在 EOF 追加（文件不存在时创建）并沿用既有换行风格，避免用 `edit` 猜文件尾部。两者都进入工具名集合与只读集合，`--read-only` / `tools.allow` 同样覆盖。
- `edit` 改为行号定位：传 `start_line`、`end_line`、`new_text`，`end_line = start_line - 1` 表示在该行前插入；行号与 `old_text` 同时出现按参数错误拒绝。`old_text` 保留为精确文本兜底，未命中时返回最相似的区间与 diff，超长相似片段会被截断。
- 写入后校验：`write` / `edit` / `append_file` 写入 Python 文件后运行 `py_compile`，装有 `ruff` 时再运行一次；出现新诊断就原子回滚并返回文件、行号与源码预览，改动前已存在的 lint 问题不阻塞无关编辑。
- `read` 报告检测到的 `line_ending`（`LF`、`CRLF`、`CR`、`none`），写入保留目标文件既有风格；混合换行文件按主导风格处理。
- PowerShell 下提前拒绝 bash heredoc 并给出改写提示；引号内与 PowerShell here-string 内的 `<<EOF` 不再误判，`<<\EOF` 仍识别。

### 修复

- 文件：EOF 没有尾随换行时插入不再与最后一行粘连；`replace_all` 累计行数；只读目标返回 `ToolError` 且不留临时文件；`write` / `edit` 不再意外保留 setuid/setgid 位；过长 `mkstemp` 临时名不再抛异常；`read` 恰好到达行长上限时不再误判截断；非 UTF-8 文件不再对请求范围外的字节误报。
- 配置：写盘只写用户改动的增量，不再把整套默认值固化进 `settings.json`，升级后新增默认值照常生效；软链接配置文件不再被原子写替换成普通文件；任意字符串里的 `${VAR}` 都会展开，未设置时点名变量；自定义 `headers` 与 `*env` 字段中的凭据在输出中脱敏；`import-pi` 不再覆盖内置供应商，覆盖时整条替换而非深合并，旧密钥不会残留；`vision` 补上尺寸校验，`web` / `vision` 继承父级认证头。
- 渲染：流末尾的无换行表格分隔符（`| --- |`）不再让 Markdown 渲染游标原地打转，行首分支加安全阀防止异常输入耗尽 CPU；`ThinkingRenderer` 写换行后同步重置 Markdown 状态，标题与代码块不再错乱；空列表项（`- `）不再吞掉换行；thinking 与正文交界处的外部换行不再重置代码块状态。
- 沙箱：重组 `sh -c` 等内联命令时保留分隔符，堵住网络策略绕过；解码 PowerShell `-EncodedCommand`（无法解码时 fail-closed）；折叠 `cmd` 的 caret 转义；`find -exec` 与进程启动器里的真实命令同样被检查；`curl --resolve` / `--connect-to` / `--proxy` / `--socks5*` / `-x` 的目标参与 `allowlist`；`WindowsApps\bash.exe` 不再被当作原生 shell；`grep` / `find` 的 glob 匹配器不再指数级回溯；`run_bounded_command` 在孙进程占住管道时也能按时退出；敏感文件名单补充 `.pypirc`、`.docker/config` 等，Windows 尾点/空格与 ADS 路径不再绕过；审计日志脱敏 `access_token=` 与 `-u user:pass`。
- 搜索：ripgrep 搜单个文件时补上 `--with-filename`，不再静默丢弃唯一匹配；结果路径里形如 `:数字` 的部分不再被当成截断；敏感路径过滤吃满读取窗口时扩大窗口重试；Python 回退支持 `{py,txt}` 与 `!*.py` 形式。
- 会话 / CLI / Agent：压缩重写时合并尾部的 `meta`，`/model` 的切换不会丢失；`--continue` 保留通过 `/model` 选定的 provider/model；压缩失败只在历史增长后重试；半行读取不再当作完整消息；Ctrl-C 取消未闭合的 bracketed paste。
- 协议 / 视觉：Chat 流式工具调用使用稀疏 index 时不再互相覆盖；Responses 的最终输出缺少 `arguments` 时回退到流式参数；`read_image` 使用字节检测出的真实 MIME 类型。
- `subagent`：PowerShell 子进程无法启动时返回 1，不再报告成功。
### 变更

- 版本号升级到 1.4.1。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema。
- 工具 schema 变化：`edit` 的必填项从 `old_text` 改为 `new_text`（行号模式），新增 `read_range`、`append_file`，`read` 结果多出 `line_ending`。
- 可观察的行为变化：`edit` 默认按行号替换；写入因新增语法或 lint 诊断回滚；`settings.json` 只保存用户增量；`--read-only` 与只读集合包含 `read_range`。

### 文档

- README 工具表补充 `read_range`、`append_file`、行号 `edit` 与写入校验，升级指南增加 1.4.0 → 1.4.1；`docs/configuration.md` 补充增量写盘、凭据脱敏与沙箱内联命令解析；`docs/architecture.md`、`docs/compatibility.md` 更新适用版本与 1.4.1 的公开面。

### 测试

- 新增回归测试：行号 `edit` 的区间替换、插入、CRLF、越界提示与“行号 + `old_text`”拒绝，`read_range` / `append_file`、写入回滚（语法错误、ruff、既有 lint 不阻塞）、换行风格主导与 EOF 插入、配置增量写盘与 `${VAR}`、`import-pi` 整条覆盖、凭据脱敏、Markdown 死循环与 thinking 状态同步、沙箱内联/`EncodedCommand`/caret/代理目标、glob 线性、搜索单文件与窗口重试、会话压缩重写、Chat/Responses 流式工具调用、vision MIME 与 subagent 退出码。
## 1.4.0 - 2026-11-01

### 新增

- 只读模式与工具白名单：`--read-only` 只保留 `read`、`grep`、`find`、`read_image`、`web_search`、`web_fetch`；`tools.allow` 直接列出允许的工具名，两者取交集。被禁用的工具既不进入请求的 `tools` 数组，`ToolRuntime.dispatch` 也会拒绝调用（`tool 'write' is disabled by --read-only`），因此恢复的旧会话或忽略 schema 的供应商都绕不过去。`tools.allow` 里的未知名字按配置错误处理——与拼写错误取交集只会留下一个什么也做不了的 agent。`/status` 增加 `read_only` 与 `tools`。
- 相邻只读工具并发：同一轮里相邻的 `read` / `grep` / `find` / `read_image` / `web_search` / `web_fetch` 用同一个信号量并发执行（`tools.max_parallel`，默认 4），结果仍按请求顺序写回会话，活动行仍在主线程按请求顺序打印。与 `write` / `edit` / `shell` / `subagent` 相邻的只读调用保持串行，避免读到写了一半的文件。
- Anthropic prompt 缓存：供应商配置 `"prompt_cache": true` 时，在最后一个工具定义上打一个 `cache_control: {"type": "ephemeral"}` 断点，覆盖 system 提示与工具定义这一段静态前缀；没有工具时改标记 system 文本块。只打一个断点是为了避免同一段字节每轮写两份缓存。默认关闭，因为并非所有 Messages 兼容端点都接受该字段。缓存读取已经出现在 `usage.cached_tokens` 中。
- 会话压缩后重写：`compact` 事件本身携带完整的替换历史，追加到一个已经保存过该历史的文件里会把同一段内容存两遍。现在当文件至少是要保留状态的两倍大且超过 64 KiB 时，原子重写为「`meta` + 该事件」；大小增量与预期不符（例如另一个进程也在追加）时跳过。小会话保持纯追加。

### 修复

- 文件：`read` 此前静默用 `errors="replace"` 解码，非 UTF-8 文件（例如 GBK 或 UTF-16 文本）看起来是一次干净读取，随后 `edit` 却对同一个文件报 “not valid UTF-8 text”。现在先按严格 UTF-8 扫描请求范围，只有真的遇到非 UTF-8 字节时才退回替换解码，并在结果的 `warnings` 里说明内容有损、`edit` 会拒绝该文件；合法文件仍是单次扫描、不带该字段。UTF-8 BOM 现在会被去掉，不再作为第一行的 `\ufeff` 显示。

### 变更

- 版本号升级到 1.4.0。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema。
- 可观察的行为变化：`read` 对非 UTF-8 范围返回 `warnings` 并去掉 BOM；被白名单或 `--read-only` 禁用的工具不再出现在工具定义里、调用时报错；压缩后的大会话文件被重写成「meta + 压缩状态」；`/status` 多出 `read_only` 与 `tools` 两个字段。
- `tools.allow` 使用 `null`（默认）表示不限制；`--read-only` 与它的交集为空时按配置错误拒绝启动。

### 文档

- README 增加白名单/只读模式、只读并发、prompt 缓存、编码可见与会话重写说明，补充 `--read-only` 与 1.3.2 → 1.4.0 升级指南；`docs/configuration.md` 增加 `tools.allow`、`tools.max_parallel`、`prompt_cache` 与重写规则；`docs/architecture.md`、`docs/compatibility.md` 更新适用版本与 1.4.0 的公开面。

### 测试

- 新增回归测试：`read` 对非 UTF-8 内容的 `warnings` 与 BOM 处理、`tools.allow` / `--read-only` 的 dispatch 拒绝与未知工具名、只读工具组并发执行（barrier）、读写混合时保持串行、被禁用工具不出现在工具定义里、`--read-only` 的端到端拒绝、Anthropic 缓存的单断点与默认关闭、压缩后重写与小会话保持追加、`prompt_cache` 的 `${ENV}` 布尔转换与非法配置。

## 1.3.2 - 2026-11-01

### 修复

- 搜索：启用 ripgrep 时，敏感文件过滤用的是一份独立 glob 列表，规则比权威的 `is_sensitive_path` 窄：大小写敏感、缺少 `.env-` 前缀规则，导致 `read` 明确拒绝的 `.env-prod`、`KEY.PEM`、`Secrets` 仍会出现在 `grep` / `find` 结果里，同一份策略对读写和批量搜索给出相反结论。现在 `rg` 的每条输出都再经过一次路径策略过滤，截断标记与半行片段也不会再混进结果。
- 搜索：沙箱开启时，超过输出上限的搜索结果会按“首尾保留”截断，随后又被逐行解析，于是一行 `... <truncated N chars> ...` 和没有 `path:line:` 前缀的片段会作为“匹配”返回；Python 回退的 JSON 信封被截断时更会直接报 `search returned an invalid result`。搜索路径现在按上限多读一条、过滤非匹配行，Python 回退的输出预算按结果数放大。
- 搜索：Python 回退的 glob 用 `fnmatch`，`*` 会跨越 `/`，Windows 上还大小写不敏感：`find("src/*.py")` 会返回 `src/deep/nested.py`，`find("*.py")` 会匹配 `A.PY`，与 ripgrep 路径结果不一致。现在按 ripgrep 语义翻译 glob（`*` 不跨目录、`**/` 可匹配零层、大小写敏感），格式错误的字符组按字面量处理而不是抛异常。
- 搜索：`rg` 会读取 `RIPGREP_CONFIG_PATH` 指向的配置文件，其中的选项可以重新纳入被排除的敏感文件或改变 `--no-follow` 等行为；现在两处调用都带 `--no-config`。
- 文件：`write` 接受 NUL 字节并写成成功，随后 `read` 却以“binary files are not supported by read”拒绝同一个文件；现在 `write` 直接拒绝含 NUL 的内容。
- 文件：`edit` 以 `errors="strict"` 读取文件，非 UTF-8 文件抛出原始 `UnicodeDecodeError` 而不是工具错误；现在报 `file is not valid UTF-8 text`，且含 NUL 的文件按二进制拒绝。
- 协议：Chat / Responses 的 `normalize_usage` 只捕获 `TypeError` / `ValueError`，`JSON` 里的非有限数（`Infinity`、`1e400`）会抛 `OverflowError` 穿出 provider 层，交互会话带 traceback 退出；现在按“用量不可用”处理。空的 `usage` 对象也不再被当成“本轮零消耗”而清零已上报的用量。
- 协议：`Retry-After` 为 `nan` 时 `time.sleep` 抛 `ValueError`，以非 `ProviderError` 逃出请求层；现在非有限值回落到 1 秒。`web_tools` / `vision` 的 429/503 重试把剩余预算睡完后报 `failed after N attempts: None` 的问题也一并修正（上一版只修了预算已耗尽的分支）。
- 协议：SSE 正文以 UTF-8 BOM 开头时，三套解析器都会把第一个 `data:` 行当成未知字段丢弃；单事件流因此报“empty stream”，多事件流则丢掉首段正文或首个工具调用的 `id` / `name`。现在每个事件行都先去掉 BOM。
- 协议：非流式 Responses 请求被网关用 SSE 回答时，正文只认最后一个 `response.completed` 的 `output`：`output` 缺失会让已经打印的正文整轮失败，`output: []` 会返回空内容，SSE 里拼好的工具调用则被完全丢弃。现在以已流出的 delta 为准，并从事件里恢复工具调用、`finish_reason` 与 `usage`。
- 协议：Chat 流式解析在服务器复用 `index` 却给出新 `id` 时，会把两个工具调用合并成一个，`id` / `name` 被覆盖、参数被串接；现在视为新的调用。
- 协议：Responses 的会话历史条目不是对象、或 `role` 不是字符串时（手改过的 JSONL），载荷构造抛 `AttributeError`；现在跳过这类条目。Chat 侧的同类条目也只在 `role` 是字符串时才发送。
- 协议：`base_url` 带查询串（Azure OpenAI 的 `api-version`）时，端点拼接把路径加在 `?` 之后；现在路径与查询串分别拼接。非流式响应体的读取也纳入 `timeout_seconds` 的整体截止时间，此前只受单个 socket 读超时约束。
- Agent / CLI：恢复会话时，若会话记录的 provider 不在当前 `settings.json` 里（典型来源是 `--anthropic-env` 会话），会静默回退到默认 provider，把一套协议的对话历史发给另一套协议；现在直接报错并提示用 `--provider` 或 `--anthropic-env` 指定。
- CLI：`/compact` 与 `/new` 只捕获部分异常，会话文件写入失败（磁盘满、权限不足）会结束整个 REPL；现在报告错误并继续当前会话。`/new` 失败时保留原会话。
- Agent：最后一轮恰好是轮次上限、同时 provider 因 `length` / `content_filter` 等原因提前停止时，错误信息只报“Reached max turns”，真正的停止原因被降为后缀，已生成答案的预览也不再打印；现在优先报告 provider 的停止原因。
- Agent：压缩与 `/summary` 请求的 token 用量完全不计入统计，`--json` 的 `usage` / `session_usage` 和 `[usage]` 行长期少算一整段上下文的消耗；现在计入总量，但不影响主对话的压缩阈值。`[usage]` 行也不再在本轮用量为零时回退显示整个会话的累计值。
- Agent：`subagent` 并发执行时，`thread.start()` 失败（线程或内存压力）会以 `RuntimeError` 结束整轮，已启动的 worker 其结果无人接收；现在该槽位按工具错误返回，其余调用照常执行。
- 会话：`--session <id>` 只在当前工作区目录下查找，而 `minimalcode sessions` 会列出所有工作区的会话，于是列出来的 ID 常常“session not found”；现在先查当前工作区，再查整个会话目录。
- 渲染：一个推理块的全部内容都被 sanitizer 当作未完成的转义序列挂起时，`ThinkingRenderer.finish()` 提前返回而不清空状态，pending 会带进下一块推理，把整块内容吞掉或吃掉首字符；现在每个块结束都重置。孤立的 ESC 后紧跟换行或空格时，兜底逻辑会连同后一个字符一起删除，导致两行粘连；八位 C1 控制序列（`0x9B`、`0x9D` 等）此前只删引导字节、把 `31m`、`0;title` 之类的载荷当正文打印，现在整段消费。
- Shell：Windows 上 `shutil.which("bash")` 常命中 `C:\Windows\System32\bash.exe`（WSL 启动器），选中后命令实际在 Linux 命名空间里执行，Windows 路径和解释器全部失效；现在探测与显式 `shell.path` 都会识别并跳过它。`pwsh-preview` / `powershell-preview` 也按对应的 shell 类型处理，不再落到 `sh` 语义。
- 沙箱：`_Capture` 在超时分支可能与读取线程并发写入，快照可能拿到首尾不一致的中间态；现在读写串行化。POSIX 上从非主线程启动子进程时会跳过 `preexec_fn`（子 agent 并发路径），避免 fork 后死锁。
- 其它：`web_search` 对嵌套列表和对象不设上限，一条结果就能把整个响应塞进上下文；现在每层最多保留 32 项。macOS 剪贴板读取失败（Apple Events 被拒、脚本错误）被当成“没有图片”静默忽略；现在区分“无图片”和“读取失败”。`import-pi` 在供应商已存在且未加 `--overwrite` 时报“no compatible Pi providers found”；现在说明是已导入并提示 `--overwrite`。`localhost.` 与 `0.0.0.0` 未被识别为环回地址，会走环境代理；现在与 `127.0.0.1` 一致。

### 变更

- 版本号升级到 1.3.2。没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema，升级不需要改代码。
- 可观察的行为变化：`grep` / `find` 现在隐藏全部敏感路径（此前开启 ripgrep 时 `.env-prod`、`KEY.PEM`、`Secrets` 会漏出），同时不再受 `RIPGREP_CONFIG_PATH` 影响；`find` / `grep` 的 glob 按 ripgrep 语义匹配，`*` 不再跨目录、Windows 上不再忽略大小写；`write` 拒绝 NUL 字节；恢复会话时若记录的 provider 不在配置里会报错而不是换供应商；Windows 不再自动选用 WSL 的 `bash` 启动器；压缩与总结的用量计入统计。

### 测试

- 新增回归测试：非有限 usage 与非有限 `Retry-After`、BOM 开头的 SSE（三套协议）、Responses 非流式 SSE 的正文与工具调用、复用 `index` 的工具调用、空 usage 不清零、`responses` 输入过滤、带查询串的端点、非流式响应体的截止时间。
- 新增回归测试：ripgrep 结果经过路径策略过滤（敏感文件与截断标记）、`--no-config`、glob 不跨目录与大小写敏感、`write` 拒绝 NUL、`edit` 报告非 UTF-8、429 重试耗尽预算后的状态码。
- 新增回归测试：跨工作区的会话 ID 解析、未配置 provider 的会话被拒绝、压缩用量入账、最后一轮提前停止的原因优先级、空工具调用列表的 `null` 内容修复。
- 新增回归测试：推理块全挂起后重置、孤立 ESC 不吞换行、C1 控制序列、嵌套 web 结果上限、macOS 剪贴板失败、重复导入 `import-pi`、`localhost.` / `0.0.0.0` 的环回判定。

## 1.3.1 - 2026-10-31

### 修复

- 协议：Anthropic 非流式响应里的 `redacted_thinking` 块会把响应体变量覆盖成字符串，随后读取 `stop_reason` / `usage` 抛 `AttributeError`，整轮直接失败。思考块现在正常保留，并在工具调用轮继续回传。
- 协议：Responses 输出项的 `type` 不是字符串时（部分网关下发对象或数组），与已知类型集合比较会抛 `TypeError`；现在按未知类型忽略该条，其余输出照常解析。
- 协议：会话历史里的 `role` 或 `provider_content[].type` 不是字符串时（手改过的 JSONL），Anthropic 载荷构造抛 `TypeError`；现在跳过这类条目。
- 会话：`tool_calls[].function` 不是对象时，启动时的历史修复抛 `AttributeError`；现在按无工具名处理，照常补齐被中断的工具结果。
- 上下文压缩：一次很长的工具循环之后，最近的 user 边界可能就是上一次压缩产生的总结头，此时压缩会只总结自己的总结：历史长度不变、每轮重复一次总结请求，并逐步丢掉细节。现在只有“重写总结头”这一种边界被排除，其他能丢弃消息的边界照常使用（开头两条很大的消息仍会被总结掉）；总结内容覆盖被丢弃的全部消息，没有任何可行边界时不发请求。`/compact` 在这种情况会报出明确原因，而不是假装成功。
- 配置：`web_tools.trust_env` / `vision.trust_env` 没有参与 `${ENV}` 展开后的类型转换，`"${X}"` 展开出的 `"false"` 是字符串，`resolve_trust_env` 会静默忽略（代理设置失效）；现在与供应商级 `trust_env` 一样转换为布尔值，非法值直接报错。
- HTTP：`web_tools` / `vision` 的 429/503 在预算耗尽时无法安排重试，此前报 `request failed after 1 attempts: None` 并丢掉状态码；现在报出实际状态码和“没有剩余时间重试”。
- 搜索：`grep` 的两套 Python 回退把行尾换行符一起交给正则，`\s+$`、`[^0-9]$` 会匹配每一行，而 CRLF 文件里的 `needle$` 反而匹配不到；现在按行内容（不含行结束符）匹配，与 ripgrep 和 `--max-columns` 语义一致。
- 搜索：Python 回退在结果数恰好等于 `max_results` 时误报 `truncated: true`；现在多取一条才判定截断，与 ripgrep 和进程内引擎一致。
- 搜索：Python 回退的 glob 把 `**/` 当成至少一层目录，`find("pkg/**/*.py")` 会漏掉 `pkg/config.py` 并报告结果完整；现在同时尝试 `**/` 折叠为空的写法。
- 沙箱：按原始字符串切分命令时，引号内的 `|` / `;` 也会切断命令，`rg 'nc|netcat'`、`grep -E 'curl|wget'` 被当成联网命令拒绝；现在只在引号外切分。
- 沙箱：内联 shell 里的包装命令（`sh -c "sudo curl ..."`）此前只凭第一个 token 判断，真实客户端被 `sudo` 挡住；`busybox` / `toybox` 也补进前缀词。两者现在都能识别。
- CLI：stdout 读取端提前关闭（`minimalcode ... | head`）时按 0 退出。此前 Windows 把关闭的管道报成 `OSError: [Errno 22]`，打印 `error: [Errno 22] Invalid argument` 并以 2 退出；缓冲输出还会在解释器退出时再次 flush 失败，把退出码改成 120 并打印 `Exception ignored`。现在退出前统一 flush，失败时把标准流指向空设备，`--version`、`--help`、用法错误等由 argparse 退出的路径同样覆盖。
- CLI：`--home` 写在管理命令前面时（`minimalcode --home DIR sessions`）不再把命令词当成提示词发给模型——那会产生一次付费请求并可能执行工具。该写法现在与 `minimalcode sessions --home DIR` 等价；其他前置选项仍按提示词处理。
- 文档：修正 `minimalcode shell` 会打印探测失败原因的说法（只有在没有任何 shell 能启动时，原因才随 `ShellError` 一起报出），并记录 `keep_messages` 在一次长工具轮次中可能保留更少消息。

### 变更

- 版本号升级到 1.3.1。本次发布没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段、会话格式或工具 schema，升级不需要改代码。
- 可观察的行为变化只有两处，且都是修正错误行为：stdout 读取端提前关闭时按 0 退出（此前 Windows 上打印 `error: [Errno 22]` 并以 2 退出），以及 `--home` 写在管理命令前面时按管理命令执行（此前会把命令词当成提示词）。压缩边界的变化只影响“总结自己的总结”这种退化情况。

### 测试

- 新增回归测试：Anthropic 非流式 `redacted_thinking` 的保留与 `stop_reason` / `usage` 解析、非字符串 `role` 与块类型被跳过、Responses 非字符串输出项类型被忽略、非对象 `tool_calls[].function` 的历史修复、长工具循环后的压缩进度与总结覆盖范围。
- 新增回归测试：`web_tools` / `vision` 块内 `${ENV}` 布尔转换、429 预算耗尽的状态码报告、`$` 锚点与 CRLF 行匹配、恰好等于上限的 `truncated`、`pkg/**/*.py` 的零层目录匹配。
- 新增回归测试：引号内 `|` 不被当作命令分隔符、内联 shell 中的 `sudo curl` 与 `busybox wget` 仍被识别。
- 新增回归测试：`--home` 前置的管理命令与普通提示词的区分、stdout 管道提前关闭时的退出码与 stderr 静默，以及 `_reader_closed` / `_discard_standard_streams` 的管道判定。

## 1.3.0 - 2026-10-31

### 新增

- 交互模式支持多行输入：行尾写 `\` 继续录入下一行（`... ` 提示符），行尾 `\\` 表示字面反斜杠。多行消息作为一个整体发给模型，行间换行原样保留。
- 长文本可直接粘贴：交互模式启用终端 bracketed paste，粘贴块的换行不会逐行提交；终端不支持该协议时，改用缓冲探测把连续到达的行拼成一条消息（Windows 控制台走 `msvcrt.kbhit`，其他平台走 `select`）。`-p` 与管道输入仍是原样读取，不受影响。
- 交互模式支持从剪贴板粘贴图片：按终端的粘贴快捷键（Windows `Ctrl+V`、macOS `Cmd+V`、多数 Linux 终端 `Ctrl+Shift+V`）后再回车，MinimalCode 直接读取系统剪贴板里的 PNG 图片，不需要事先保存文件。空回车触发读取，随后可以输入配套问题；直接回车则由供应商 `vision` 配置的视觉模型生成描述，并作为同一条消息发给主模型。Windows 通过 PowerShell 读取剪贴板，macOS 走 `osascript`，Linux 优先 `wl-paste`、回退 `xclip`。剪贴板里没有图片时保持原来的空输入行为；读取失败、格式不支持或超过 `max_image_bytes` 时只报错，不结束会话。

### 修复

- HTTP：JSON API 客户端不再自动跟随重定向。307/308 会把完整请求体（对话、工具输出、图片 base64）重发到新地址，而 httpx2 只会删除 `Authorization`，`x-api-key` 与自定义认证头会保留；现在 3xx 直接报错并给出目标地址。`web_tools` / `vision` 补上 429/503 单次重试（含 `Retry-After`）。
- `/summary`：只剩单边标记时不再把标记之间的正文当作块内容删除；多个完整块归一为一个，孤立标记行按注释丢弃，正文原样保留。总结请求不再把系统提示词（含旧 `AGENTS.md`）喂回模型。
- 沙箱：子代理继承父进程的 `--sandbox`、后端、网络策略、允许根、允许主机、超时、输出上限与敏感文件开关；`--anthropic-env` 启动时子代理改用 `--anthropic-env` 重建供应商，而不是引用只存在于父进程内存的 `anthropic-env`。
- 沙箱：`grep` / `find` 在后端不可用时回退到进程内扫描，不再整体失败；进程内扫描对二进制行跳过，并用 30 秒 alarm 兜底灾难性正则。
- 沙箱：网络识别补齐 `sudo -u`、`sudo -E`、`timeout`、`env -i`、`nice -n`、`xargs`、`bash -lc`、`if/then` 等包装与控制结构；引号内的 `grep "git clone"`、`rg "pip install"` 不再被判定为联网。`allowed_hosts` 接受带端口或完整 URL 的条目。
- 沙箱：Docker 后端增加 daemon 探测（daemon 未运行不再报告可用）、`--name`，并在超时或 Ctrl-C 后 `docker kill` + `docker rm`，避免 docker 客户端被杀而容器继续运行；bwrap 增加真实可用性探测。超时清理会额外收集脱离进程组的子进程并直接 kill。
- 协议：Chat 空回复不再写入 `content: null`（严格服务端会拒绝，导致后续会话卡死），历史中的 null assistant 会在启动时修复。
- 协议：Anthropic 流式与非流式都保留并回放 `redacted_thinking`，扩展思考 + 工具调用可以继续；Responses 非流式请求遇到返回 SSE 的网关时正确解析；Chat 缺失 `index` 的 tool call 分片按 id 归并，整对象 arguments 不再覆盖已累积片段；`refusal` 内容按文本处理。
- 协议：`--anthropic-env -m model[1m]` 在构造客户端时剥离后缀并使用对应上下文窗口，不再把后缀发给服务端。
- 工具：`edit` 不再为单字符匹配枚举全部结果（`replace_all=False` 找到第二个匹配即停止，`replace_all=True` 改为流式替换）；修复 `read` 与 Python grep 在 CRLF 恰好被行长度上限切开时的行号漂移；`read` 检测 64KB 之后的 NUL；新建文件按 umask 而非 `mkstemp` 的 `0600`；文件与目录写入后 fsync。
- 工具：显式搜索被忽略的目录时，ripgrep 与 Python 回退使用同一套忽略规则；`find` 与进程内 grep 的 `truncated` 不再在恰好等于上限时误报；`activity` 对搜索路径做截断。
- 配置：`${ENV}` 展开后按字段重新转换为布尔/整数/浮点数，`stream: "${X}"` 且 `X=false` 不再被 `bool("false")` 当成真；`config set-model` 同步更新当前供应商的 `model`，`set-provider` 同步或提示模型；`import-pi` 为每个供应商保留自己的模型；`/model` 的凭据检测能识别未解析的 `${...}`。
- 会话：恢复会话时恢复记录的供应商与模型，`/model` 切换后写入会话 meta；会话追加 fsync 并收紧到 `0600`；配置目录统一 `0700`。
- 交互：粘贴结束标记之后的文本不再丢失，粘贴长度有上限；POSIX 上补读 `TextIOWrapper` 已缓冲的多行；Windows 用 `GetConsoleMode` 判断真实控制台，NUL 设备与重定向设备不再让 `kbhit()` 一直报告有输入。
- 其他：`BrokenPipeError` 安静退出（`minimalcode ... | head`）；Windows 控制台拒绝 VT 模式时禁用着色而不是打印原始 ANSI；POSIX 增加 `zsh` 候选、Windows 增加 Git Bash 候选；`requires-python` 限定为 `>=3.11,<3.14`。

### 变更

- 版本号升级到 1.3.0。本次发布没有删除任何公开 `__all__` 名字、CLI 参数、配置键、JSON 输出字段或会话格式；`minimalcode/clipboard.py` 与 `ClipboardError` 是新增模块和异常类型。
- 可观察的行为变化：JSON API 请求遇到 3xx 现在直接报错而不是跟随跳转；新建文件按进程 umask 创建（此前固定 `0600`）；`config set-model` 会同时更新当前供应商的 `model`；恢复会话时沿用会话记录的供应商与模型（显式 `--provider` / `--model` 仍然优先）；Chat 空回复不再写入 `content: null`。
- `requires-python` 收窄为 `>=3.11,<3.14`，与实际测试矩阵一致；Python 3.14 及更高版本不再被声明为兼容。

### 文档

- README 增加剪贴板图片粘贴说明、`clipboard.py` 目录项和 1.2 → 1.3 升级说明；`docs/compatibility.md` 增加 1.3.0 的兼容性与行为变化说明；`docs/architecture.md`、`docs/configuration.md` 更新适用版本与剪贴板模块。

### 测试

- 新增多行输入回归测试：`\` 续行拼接与提示符、`\\` 转义、bracketed paste 解包（含结束标记前的换行）、无标记粘贴的逐行缓冲拼接，以及交互循环把多行消息整体交给 Agent。
- 新增剪贴板图片回归测试：Windows base64 解码与无图片退出码、macOS AppleScript 十六进制解析、Linux 工具探测、平台分派、格式校验，以及交互循环中图像与问题合并、纯图片描述、空剪贴板、未启用 `vision` 和视觉调用失败时保留原消息。
- 新增缺陷回归测试：跨源 307 拒绝、`/summary` 不完整标记、单字符 `edit`、CRLF 长行行号、`sudo -u` / `timeout` 等网络识别与引号内文本不误报、后端不可用时搜索回退、子代理沙箱参数与 `--anthropic-env` 重建、Chat 空回复、Anthropic `redacted_thinking`、Responses SSE 网关、`${ENV}` 布尔转换、会话供应商恢复、粘贴结束标记之后的文本、未解析凭据占位符、单轮工具调用数量上限。

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
