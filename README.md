# gacore

GenericAgent 核心的 LangGraph 重实现（学习项目）。

gacore 用 [LangGraph](https://langchain-ai.github.io/langgraph/) 重新实现了
[GenericAgent](https://github.com/lsdefine/GenericAgent)（下称 GA）的核心循环：
把 GA 里手写的 `while` 循环、增量消息、StepOutcome 机制，改写成 StateGraph 节点、
显式状态通道、条件边和 `interrupt()` 人类介入。代码刻意保持小、可读、带注释，
目的是讲清楚"GA 的每个设计决定，在 LangGraph 里对应什么、为什么这样对应"。

架构细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 功能特性 (Features)

- **2 节点 StateGraph**：唯一的业务节点 `agent`（单轮 LLM 推理 + 最终答案校验）
  加预置 `ToolNode`，由 1 条条件边驱动（agent 分派、tools 静态回环），见
  ARCHITECTURE.md 的拓扑图。
- **9 个原子工具**：`code_run`、`file_read`、`file_patch`、`file_write`、
  `web_scan`、`web_execute_js`、`update_working_checkpoint`、`start_long_term_update`、`ask_user`，
  与 GA 的工具集一一对应。
- **工作记忆 / 长期记忆（L1 / L2）**：`update_working_checkpoint` 写入工作记忆
  （每轮注入系统提示词），`start_long_term_update` 把内容蒸馏到
  `memory/global_mem.txt`（L2 全局事实）与 `memory/global_mem_insight.txt`（L1 洞察索引）。
- **ask_user 人类介入中断**：`ask_user` 工具调用 `interrupt()` 暂停图执行，
  配合 MemorySaver 检查点和 `Command(resume=...)` 恢复。
- **max_turns 守卫**：agent 节点在调用 LLM 前检查轮数，超过上限直接以
  `MAX_TURNS_EXCEEDED` 终止，不会再消耗一次模型调用。
- **done_hooks 续接**：收尾提示队列（GA 的 `_done_hooks`），任务主体完成后按序
  追加一轮 HumanMessage 继续执行。
- **JSONL 结构化日志**：每次运行写入独立目录 `logs/YYYY-MM-DD-HHmmss/app.jsonl`，
  每条日志含 timestamp / level / module / message，错误日志附 error_type / stack_trace / context。

---

## 环境要求 (Requirements)

- **Python 3.12**（pyproject.toml 声明 `>=3.12,<3.14`，本机验证环境为 conda env `py12`）。
- 本机路径示例：

  ```
  D:\softwares\miniconda\envs\py12\python.exe
  ```

- 运行依赖：`langgraph`、`langgraph-checkpoint`、`langchain-core`、
  `langchain-openai`、`langchain-anthropic`、`python-dotenv`、`httpx`、`prompt_toolkit`。
  本机锁定的关键版本：langgraph 1.2.10、langgraph-checkpoint 4.1.1、langchain-core 1.5.3。

---

## 安装 (Install)

在项目根目录（含 `pyproject.toml`）执行，依赖清单与 pyproject.toml 完全一致：

```powershell
# 激活 conda 环境（或直接用完整 python 路径）
conda activate py12
pip install langgraph langgraph-checkpoint langchain-core langchain-openai langchain-anthropic python-dotenv httpx prompt_toolkit
```

开发 / 测试额外依赖：

```powershell
pip install pytest ruff
```

项目使用 src 布局，不安装为包也可运行，见下文"运行"。

---

## 配置 (Configuration)

复制 `.env.example` 为 `.env` 并填写：

```powershell
Copy-Item .env.example .env
```

支持的变量：

| 变量 | 说明 | 示例 |
| :--- | :--- | :--- |
| `LLM_PROVIDER` | 模型提供方，`openai` / `anthropic` / `deepseek` | `deepseek` |
| `OPENAI_API_KEY` | OpenAI 系 API Key | `sk-xxx` |
| `OPENAI_BASE_URL` | OpenAI 兼容网关地址，留空用官方 | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | OpenAI 模型名 | `gpt-4o` |
| `ANTHROPIC_API_KEY` | Anthropic API Key | `sk-ant-xxx` |
| `ANTHROPIC_MODEL` | Anthropic 模型名 | `claude-sonnet-4-5` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（`platform.deepseek.com` 获取） | `sk-xxx` |
| `DEEPSEEK_BASE_URL` | DeepSeek 网关地址，留空用 `https://api.deepseek.com/v1` | `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | DeepSeek 模型名 | `deepseek-v4-pro` |
| `DEFAULT_MAX_TURNS` | 单次任务默认最大轮数 | `40` |

`deepseek` 与 GA 的配置一致（`configure_mykey.py` 中 `type: native_oai`）：走 OpenAI 兼容
协议，由 `ChatOpenAI` 驱动，默认 base URL 为 `https://api.deepseek.com/v1`、默认模型为
`deepseek-v4-pro`（可选 `deepseek-v4-flash`）。

另有环境变量可覆盖目录（不写进 .env 也行）：`GACORE_ASSET_DIR`、`GACORE_MEMORY_DIR`、
`GACORE_LOGS_DIR`、`GACORE_TEMP_DIR`，相对路径以项目根为基准。测试用
`GACORE_MEMORY_DIR` 指向临时目录，避免污染真实 `memory/`。

---

## 运行 (Run)

交互式 CLI（REPL）：

```powershell
$env:PYTHONPATH = "src"
python -m gacore
```

要点：

- 启动后逐行输入任务，`/quit` 退出；一轮以 `EXITED` 结束时也会自动退出 REPL。
- 模型调用 `ask_user` 时，图会暂停并打印 `[ask_user] <问题>`，然后提示
  `Your answer (<问题>): `。输入回答后通过 `Command(resume=...)` 恢复执行；
  输入 `abort` / `exit` / `quit` / `stop` / `cancel`（或直接 Ctrl-D）视为中止，
  本轮以 `EXITED` 结束。
- 未配置 `.env`（缺 `LLM_PROVIDER` 或 API Key）时优雅退出，不崩溃：

  ```
  gacore: ConfigError: LLM_PROVIDER must be one of ('openai', 'anthropic', 'deepseek'), got ''
  ```

  退出码为 1。

---

## 测试 (Testing)

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests -q      # 当前 137 个用例全部通过
python -m ruff check src tests # lint 干净
```

137 个测试覆盖：状态初始化、agent 节点（含内联的最终答案校验）逻辑与路由、
LLM 工厂（openai / anthropic / deepseek 分支与缺失 Key 报错）、工具注册表、
code_run / file / memory / web 四组工具、ask_user 中断与恢复（含 langgraph 两种
中断表象）、端到端多轮循环与 done_hooks 续接。

---

## 与 GenericAgent 的对应关系 (Mapping to GA)

| GA 文件 | gacore 模块 | 说明 |
| :--- | :--- | :--- |
| `agent_loop.py`（`agent_runner_loop` :42-107） | `graph.py` + `nodes/agent.py` | 主循环拆成 StateGraph 拓扑 + agent 节点（含最终答案校验） |
| `agent_loop.py`（工具分发、`StepOutcome`） | 预置 `ToolNode` + `Command(update=...)` | 工具执行；控制信号（`exit_reason` / `working`）由工具直接经 Command 回写 |
| `agent_loop.py`（`no_tool` 分支、`_done_hooks`） | `nodes/agent.py`（内联） | 最终答案校验 + done_hooks 续接 |
| `ga.py`（`turn_end_callback` :570、`_get_anchor_prompt`、`get_global_memory` :602） | `context.py` | 每轮提示词组装（系统提示词 + 折叠历史 + 周期提示） |
| `ga.py`（`do_code_run` / `do_file_*` / `do_ask_user` 等） | `tools/*.py` | 9 个原子工具 |
| `llmcore.py`（`client.chat` / Session） | `llm.py` | LLM 工厂，openai / anthropic / deepseek 三选一 |
| `mykey.py` / `NativeToolClient` | `llm.py`（环境变量驱动） | 配置方式替换 |
| `frontends/tui_v3.py` | `cli.py` | 交互前端（REPL） |
| `memory/global_mem*.txt` | `memory/` + `tools/memory_tools.py` | L1 / L2 长期记忆 |
| （无 GA 对应） | `state.py` / `logging.py` | `GAState` 状态通道 + JSONL 日志 |

忠实移植的语义与刻意简化的差异，逐条记录在 ARCHITECTURE.md 第 4、5 节。

---

## 范围说明 (Scope)

以下 GA 能力**不在**本项目范围内（学习项目刻意裁剪）：

- **TMWebDriver 真实浏览器**：GA 用注入的 Chrome 会话保留登录态、跑真实网页。
  gacore 用 `httpx` 静态抓取作为 `web_scan` 的替代；`web_execute_js` 是显式 stub，
  恒返回"不支持"错误字典（见 `tools/web_tools.py`）。
- **reflect 自治模式**：`reflect/goal_mode.py` 等自驱、时间预算型执行未移植。
- **插件机制**：GA 的 `plugins/hooks.py` hook 链未移植。
- **IM 前端**：Telegram / Discord / 微信 / 飞书等 Bot 前端未移植，只有终端 REPL。
- **L4 会话归档**：长程任务归档记忆未移植（L1/L2 已覆盖）。
- **Mixin 多模型故障切换**：GA `NativeToolClient` 的多模型兜底切换未移植，
  gacore 是单一 `LLM_PROVIDER` 的选择逻辑（`llm.py`）。

---

## 项目结构 (Layout)

```
WithLangGraph/
├── pyproject.toml            # 依赖与工具配置（ruff line-length=120）
├── .env.example              # 配置模板，复制为 .env 使用
├── src/gacore/
│   ├── __main__.py           # python -m gacore 入口
│   ├── cli.py                # 交互 REPL，ask_user 中断处理
│   ├── graph.py              # StateGraph 拓扑、编译、run_once
│   ├── state.py              # GAState TypedDict + 初始化
│   ├── context.py            # 每轮提示词组装（纯函数）
│   ├── llm.py                # LLM 工厂
│   ├── config.py             # 配置解析（目录 / max_turns）
│   ├── logging.py            # JSONL 日志
│   ├── nodes/                # agent 节点（含最终答案校验、路由函数）
│   ├── tools/                # 9 个工具
│   └── memory/               # 记忆模块（预留）
├── config/assets/            # sys_prompt.txt（L0 规则）、code_run_header.py
├── memory/                   # L1 / L2 记忆文件（运行时生成）
├── logs/                     # JSONL 日志（运行时生成）
└── tests/                    # 137 个测试
```
