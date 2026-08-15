# daily-report 升级：听歌自我分析 + 运维记录 + 邮件

- 日期：2026-08-16
- 状态：已确认设计
- 相关模块：`src/gacore/tools/ncm_tools.py`、`src/gacore/scheduler.py`（不改）、`config/schedule.json`、`tests/`

## 1. 目标

让每天 23:50 触发的 daily-report 定时任务：

1. 收集"昨天以来"听过的网易云歌曲（快照对比增量）+ 最近一周听歌排行（趋势）；
2. 将听歌信号与当天日报内容（工作/学习/观看记录）交叉融合，产出"自我观察"分析；
3. 邮件正文 = 完整日报（含运维节 + 听歌节 + 自我观察），发送给用户；
4. 运维节引用 `docs/sre/` 最新报告；无当天报告时降级为自动轻量摘要。

## 2. 背景与约束

- `ncm` CLI（Davied-H/ncm-cli，Go 二进制）已封装在 `ncm_tools.py`，登录态正常（用户"西瓜是真好吃"）。
- **硬约束**：`ncm record` 输出只有 `playCount`（累计播放次数）+ `score`（排行分）+ song 元数据，**无任何时间戳字段**。`--week` 只是"最近一周"窗口，无法直接回答"昨天听了哪首"。
  - 因此"昨日增量"只能靠**快照对比**：每次运行保存全量 `allData` 的 playCount 快照，下次运行对比差值（>0 即上次以来新增播放）。
  - `--json` 模式下 `--limit` 不生效，输出完整 weekData + allData，快照可基于全量。
- 现有 `deliver_to: "email"` 通道已上线（commit 1331c73），scheduler 的 `_deliver` 把 agent 最终回复作为邮件正文。**本次不改 scheduler.py**。

## 3. 设计决策（头脑风暴结论）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 听歌数据粒度 | **快照对比得"昨日增量" + 一周排行看趋势**（C 方案） |
| 2 | 运维记录来源 | **引用 docs/sre/ 最新报告**（B 方案） |
| 3 | 无当天报告时 | **降级为 agent 现场探测的轻量摘要**（C 方案） |
| 4 | 自我分析形态 | **融合分析**：听歌作为当日状态信号，与日报交叉解读，非独立音乐周报 |
| 5 | 分析沉淀位置 | **只进 daily note（L1）+ 邮件**，不进长期记忆（L2） |
| 6 | 邮件组装方式 | **Agent 组装**：scheduler 代码不动，邮件正文 = agent 最终回复 |

## 4. 组件设计

### 4.1 新增工具 `ncm_record`（`ncm_tools.py`）

封装 `ncm record` CLI：

- `ncm_record(period: str = "week")`：
  - `period="week"` → `ncm record --week --json`（最近一周排行，按 playCount 降序）
  - `period="all"` → `ncm record --all --json`（全部播放记录）
- 返回结构（新增 `NcmRecordResult` TypedDict）：
  ```python
  {
    "period": "week" | "all",
    "total": int,
    "records": [
      {"song_id": int, "name": str, "artists": str, "album": str,
       "duration_ms": int, "fee": int, "play_count": int, "score": int}
    ]
  }
  ```
- 复用现有 `_find_ncm` / `_run_cli` / `_load_json` / `_parse_song`；新增 `_parse_record_item` 处理 `{playCount, score, song}` 包裹结构。
- 错误路径沿用现有模式：`ncm_not_found` / `command_failed` / `bad_response`；未登录（profile 缺失）返回 `not_authenticated`。

### 4.2 新增工具 `ncm_record_diff`（`ncm_tools.py`）

快照对比，产出"昨日增量"：

- 快照文件：`memory/ncm_play_snapshot.json`（由 `Config.memory_dir` 解析，与 `schedule_state.json` 同目录）。
- 快照结构：
  ```json
  {
    "captured_at": "2026-08-16T23:50:00+08:00",
    "songs": {"<song_id>": {"name": "...", "artists": "...", "play_count": 100, "score": 90}}
  }
  ```
- 流程：
  1. 调 `ncm record --all --json` 拿当前全量 allData；
  2. 读旧快照（不存在 → 首跑）；
  3. 对每首歌：`delta = current_play_count - snapshot_play_count`；`delta > 0` → 计入增量（附带歌曲元数据）；
  4. 新出现的歌曲（snapshot 中无 id）→ 计入增量，delta = 当前 playCount；
  5. 写入新快照（全量覆盖）；
  6. 返回：
  ```python
  {
    "first_run": bool,            # True 表示无旧基线，增量不可得
    "captured_at": str,
    "increments": [...],          # 上次以来新增播放的歌曲（含 delta）
    "note": str                   # 首跑提示 / 空增量提示等
  }
  ```
  - 一周排行**不**在此返回——由 agent 单独调 `ncm_record(period="week")`，保持工具单一职责。
- 失败路径：CLI 失败 / 未登录 → 返回错误字典（不抛异常，agent 可感知并继续）。
- 不删除快照、不做跨周清理（YAGNI：allData 是累计值，快照只存全量 playCount，体积小）。

### 4.3 快照文件进 `.gitignore`

`memory/ncm_play_snapshot.json` 追加到 `.gitignore`（与 `memory/schedule_state.json` 同类，运行时状态）。

### 4.4 daily-report prompt 升级（`config/schedule.json`）

在现有双层产出 prompt 基础上追加三节指令：

1. **听歌采集**：
   - 调 `ncm_record_diff` 拿"昨日以来增量"（首跑时记录基线，输出注明"从明天起有数据"）；
   - 调 `ncm_record(period="week")` 拿一周 Top 排行；
2. **运维节**：
   - 用文件工具读 `docs/sre/` 目录，**按文件修改时间取最新**的一份报告（文件名日期格式不统一：`sre-check-2026-08-15.md` vs `运维检查报告_20260814.md`，故按 mtime 而非解析文件名）；
   - 若最新报告生成日期 **非当天** → 降级：用 `code_run`(PowerShell) 探测：进程存活（`Get-Process python`）、今日日志 ERROR/WARNING 计数（grep `logs/*/app.jsonl`）、D 盘剩余空间、git status 是否有未提交改动；汇总成"轻量运维摘要"；
   - 若最新报告**是当天** → 直接摘录关键结论（进程/日志/磁盘/git 状态表）；
3. **自我观察小节**（daily note 结构新增）：
   - 把听歌信号与当日归档交叉解读，如："深夜 23:00 后仍在处理 X 任务 + 连播伤感老歌 → 可能处于高压/怀旧状态"；
   - 无信号不强写（沿用现有"没干货就省略"原则）。

邮件正文 = agent 最终回复（完整日报），scheduler 的 `_deliver` 不变。

### 4.5 时间语义

- 任务 23:50 触发，快照对比窗口 = 上次运行至今 ≈ 近 24 小时。
- 日报措辞统一用"**昨日以来 / 近 24 小时**"描述增量，避免与自然日"昨天"混淆。
- 首跑只有基线无增量，属预期行为。

### 4.6 测试（`tests/test_tools_ncm.py` 新增 + `tests/test_scheduler.py` 不动）

- `ncm_record`：week/all 两种 period 的参数拼接（mock `_run_cli`）；`_parse_record_item` 解析 `{playCount, score, song}`；未登录 / CLI 失败错误路径。
- `ncm_record_diff`：
  - 首跑：无快照文件 → `first_run=True`，生成基线快照；
  - 二次跑：playCount 增加 → 增量正确；无变化 → 空增量；
  - 新歌出现 → 计入增量；
  - 快照读写用 `Config.for_tests(tmp_path)` 的临时 memory 目录，不污染真实 `memory/`。
- 现有 293 用例保持全绿（scheduler 逻辑未改）。

## 5. 数据流

```
23:50 触发 daily-report
  └─ agent 运行（build_graph + run_once，单轮）
       ├─ read_daily(今天)                    # 当天已有笔记
       ├─ ncm_record_diff()                   # 昨日增量 + 更新快照
       ├─ ncm_record(period="week")           # 一周趋势
       ├─ glob/file_read docs/sre/            # 最新运维报告（或降级 code_run 探测）
       ├─ edit_daily(今天, 自我观察+归档)       # L1 沉淀
       └─ 最终回复 = 完整日报
  └─ scheduler._deliver(job, cfg, reply, error)  # 邮件发送（不改）
       └─ SMTP → 用户邮箱（[gacore] daily-report · 2026-08-16）
```

## 6. 错误处理

| 场景 | 行为 |
|------|------|
| ncm 未登录 / CLI 失败 | 工具返回错误字典，agent 在日报中注明"听歌数据暂不可用"，其余部分照常 |
| 快照文件损坏 / 不可读 | 视为首跑，重建快照 |
| docs/sre/ 无任何报告 | 直接降级探测 |
| 探测命令失败 | 运维节注明"探测失败"，不阻塞日报 |
| SMTP 未配置 | 现有 `_deliver_email` 已处理（warning + 跳过），不变 |

## 7. 明确不做（YAGNI）

- 听歌数据不进长期记忆（L2）；
- 不做听歌情绪/心理画像（用户否决）；
- 不改 scheduler.py 的 `_deliver` / `_deliver_email`；
- 不做歌词/歌曲详情逐首分析；
- 不做快照过期清理（累计值 + 全量覆盖，无增长问题）。