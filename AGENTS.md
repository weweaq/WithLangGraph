# AGENTS.md — WithLangGraph 项目工作规则

> 本项目是 weiTrack 数据链路（weiCheckApp 客户端 + 本仓库服务端）的**服务端/数据侧**。
> 与客户端仓库 `weiCheckApp` 配合：客户端负责采集上报，本仓库负责接收存储、ETL 加工、分析展示。

## 项目定位（必须遵守）

- **纯自用工具**：weiCheckApp 是手机行为数据采集器，不考虑隐私合规。所有数据（剪贴板/短信/屏幕内容/录音）都完整采集、完整保留、不做脱敏。
- **客户端=采集**：weiCheckApp 只负责采集 + 上报，**不做客户端分析**。
- **服务端=加工分析**：本仓库（WithLangGraph）负责 `/ingest` 接收、ETL 清洗建表、report 分析、geocode 逆编码、dashboard 展示。分析能力逐步建设。

## 铁律：改完必更路书

**`docs/weitrack-roadmap.md` 是本项目的数据链路路书（指南 + 工作日志）。**

**每次改动代码（客户端或服务端，哪怕一行）后，必须：**
1. 在路书「执行记录」节追加一条记录：改了什么、为什么改、验证结果
2. 更新「待办」清单：完成项打 ✅，新增项追加
3. 数据相关的验证结论（实测数字、问题诊断）也要记录

**违反此规则 = 工作不完整，不允许直接收尾。**

## 提交流程（沿用既有军规）

1. 提交前 `codegraph sync` 同步索引
2. `git add` 只 stage 本次相关文件
3. commit 用中文描述，格式 `type(scope): 描述`
4. 涉及数据链路的行为变更（采集格式/上报协议/ETL 逻辑）必须先过一遍路书，确认记录同步更新

## 实测踩坑记录（低级错误警示，避免重犯）

### 1. 音频采集线程静默卡死（2026-08-18）
- **现象**：audio_env/audio_clip 数据在某时刻集体停止，全天只剩 34 分钟数据，且**无任何日志**
- **根因**：`AudioRecord.read()` 在系统回收麦克风（打电话/语音）后永久阻塞不返回，采集线程空转
- **错误**：外层 `try-catch(Exception)` 把异常全吞掉，线程死了看不见
- **修复**：read 加超时保护 + 看门狗线程（N 分钟无产出自动重启）+ 保留 Log 日志
- **教训**：
  - **后台采集线程必须有超时保护**，凡是 `read()/阻塞 IO` 都要设 deadline
  - **吞异常 = 事故**。采集类代码异常必须 `Log.w/e`，不能静默
  - **数据突然变少 = 先查采集器存活**，用时间分布（按小时分组）定位停止时刻，别只看总量

### 2. 服务端 events 表字段名（2026-08-17）
- events 表存的是 `payload` 列（不是 `data`），写 SQL 分析时用错字段会 `no such column`
- 查询前先 `PRAGMA table_info(events)` 或看 `storage.py` 的 `_SCHEMA`

### 3. Windows 环境编码（2026-08-17）
- `.env` 可能是 GBK 编码，Python `read_text(encoding="utf-8")` 会崩
- 读配置用**字节查找**（`find(b"KEY=")`）避免编码问题
- PowerShell 传中文给 Python 会乱码：用 `python -X utf8` + 写脚本文件而非内联

### 4. ETL 全量重建会丢人工数据（2026-08-18）
- places 表被 `DELETE + INSERT` 重建后，手工标好的「家/公司」标签全丢
- **教训**：用户人工确认的数据必须持久化到独立配置（`data/place_labels.json`），ETL 重跑后自动恢复
- 通用原则：**ETL 能全量重建的是"可计算数据"，"人工/外部数据"要单独存**

## 常用命令速查

```powershell
# 服务端测试（py12 环境）
$env:PYTHONPATH = "src"
& "D:\softwares\miniconda\envs\py12\python.exe" -m pytest tests/test_weitrack_*.py -q

# ETL + 清理 + 报告
python -m gacore.weitrack.etl --purge
python -m gacore.weitrack.report --day 2026-08-18

# 高德逆编码 / 标签确认
python -m gacore.weitrack.geocode
python -m gacore.weitrack.label_places

# 客户端构建安装（weiCheckApp 仓库）
gradlew.bat :app:assembleDebug --offline
adb install -r app\build\outputs\apk\debug\app-debug.apk
```
