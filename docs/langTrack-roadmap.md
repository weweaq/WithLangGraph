---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 74fca65fe87f9b5d6900c56ec5512fbd_d52903569a2a11f19467525400287e28
    ReservedCode1: j7ZMrUC2uZGotHxKQLPNvBnn9x8qh3MMpzbViRsoS2ZbsaRFFV9SqZpSC6YmWnyIuUkEOKGb0oy82enWcBFDkIjnK2mNa3Ca7k8lATvggQfko0xXJuL8rDZPQiqeu9PNgpmKM7phATki6ihhrzOqHXGFisvc9heaeJB5Q630NHsucbKxeS7siXYybKs=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 74fca65fe87f9b5d6900c56ec5512fbd_d52903569a2a11f19467525400287e28
    ReservedCode2: j7ZMrUC2uZGotHxKQLPNvBnn9x8qh3MMpzbViRsoS2ZbsaRFFV9SqZpSC6YmWnyIuUkEOKGb0oy82enWcBFDkIjnK2mNa3Ca7k8lATvggQfko0xXJuL8rDZPQiqeu9PNgpmKM7phATki6ihhrzOqHXGFisvc9heaeJB5Q630NHsucbKxeS7siXYybKs=
---

# langTrack 数据分析方案

> 版本：v1.0 ｜ 日期：2026-08-17 ｜ 数据源：langTrack.db events 表（1422 条，含 2026-08-17 上午实测数据）

---

## 1. 项目背景与目标

weiCheckApp（用机时长）采集 9 类手机事件，经 POST /ingest 上报到自建 FastAPI 后端（gacore.langTrack），落库 events 表。当前状态：**仅存不读**——数据是原始事件流，尚未加工成可分析的结构化事实。

本方案目标：把原始事件流加工成"可分析、可展示、可自动化"的数据资产，产出屏幕时间洞察、通知疲劳分析、睡眠推断、场景识别、AI 数字生活日报等能力。

---

## 2. 数据资产总览

| 事件类型 | 含义 | 采集频率 | 今日上午(8:00-8:29)数量 | 敏感度 |
|---|---|---|---|---|
| usage | 前台应用使用时长 | 事件驱动（切前台/退后台） | 12 | 低 |
| session | 会话切换事件 | 事件驱动 | 10 | 低 |
| snapshot | 每分钟环境快照 | 每 1 分钟 | 22 | 中 |
| audio_env | 声学环境指标 | 每 1 分钟 | 21 | 中 |
| audio_clip | 音频片段（原始录音） | 每 5 分钟 | 5 | **高** |
| notification | 通知流 | 事件驱动 | 29 | **高** |
| location | 定位 | 周期+事件 | 6 | **高** |
| battery | 电量/充电状态 | 周期 | 8 | 低 |
| network | 网络状态 | 事件驱动 | 7 | 低 |
| clipboard | 剪贴板内容 | 事件驱动 | 4 | **极高** |
| sms | 短信 | 事件驱动 | 5 | **极高** |
| input | 输入框内容 | 事件驱动 | 3 | **极高** |
| screen_content | 屏幕内容（无障碍） | 事件驱动 | 114(历史) | **极高** |

> 注：clipboard / sms / input / screen_content 为高敏感数据，当前采集量小，方案中单独标注处理策略。

---

## 3. 各类事件详细说明

### 3.1 usage —— 前台应用使用时长

**字段**：`pkg`（包名）、`app`（应用名）、`foreground_ms`（前台毫秒）、`endMs`（结束时间戳）、`activity`（前台 Activity）

**实测样例**：
```json
{"pkg": "com.ss.android.ugc.aweme", "app": "抖音", "foreground_ms": 496930, "endMs": 1786924988130, "activity": "com.oplus.alarmclock.alert.AlarmAlertFullScreen"}
```

**用途**：
- 屏幕时间统计：各 app 使用时长排行、总屏幕时间
- 会话拼接：与 session 配合还原"连续使用段"
- 沉浸式使用检测：单 app 连续 > 10 分钟标记"沉浸时段"
- 防沉迷提醒：连续刷短视频超阈值触发提示

**注意**：历史存在 foreground_ms=0 坏数据（querySessions 未重算 durationMs 的 bug 导致），需清洗过滤。

---

### 3.2 session —— 会话切换事件

**字段**：`kind`（事件类型：app_switch / screen_on / screen_off / 等）

**实测样例**：
```json
{"kind": "app_switch"}
```

**用途**：
- 会话边界：与 usage 配合，把碎片 usage 拼成完整前台会话（app、起止、时长）
- 切换频率：单位时间 app 切换次数 → 注意力碎片化程度
- 屏幕开关：screen_on/off 推断亮屏时段、睡眠时段

---

### 3.3 snapshot —— 每分钟环境快照

**字段**：`fg_pkg`（前台包名）、`fg_activity`、`screen`（屏幕开关）、`battery`（电量）、`charging`、`brightness`（亮度）、`volume`（音量）、`wifi_ssid`、`network`（wifi/移动）、`bt_devices`（蓝牙设备列表）

**实测样例**：
```json
{"fg_pkg": "com.android.launcher", "fg_activity": "com.android.launcher.Launcher", "screen": true, "battery": 55, "charging": false, "brightness": 42, "volume": 42, "wifi_ssid": "hhhyyyqqq-5G", "network": "wifi", "bt_devices": "[OnePlus Bullets Wireless 2, JBL Horizon 2, ...]"}
```

**用途**：
- 场景识别：wifi_ssid + bt_devices → 家/公司/通勤（连 JBL 音箱=在家，连耳机=外出）
- 电量曲线：分钟级电量变化 → 耗电大户、充电习惯
- 屏幕使用：screen 状态 → 亮屏时长、睡眠推断
- 环境感知：亮度/音量变化 → 场景切换信号

---

### 3.4 audio_env —— 声学环境指标

**字段**：`rms_db`（均方根分贝）、`peak_db`（峰值分贝）、`speech_prob`（语音概率 0-1）、`music_prob`（音乐概率 0-1）、`is_silent`（是否静音）

**实测样例**：
```json
{"rms_db": -45.1, "peak_db": -22.2, "speech_prob": 0.31, "music_prob": 0.43, "is_silent": false}
```

**用途**：
- 场景识别：speech_prob 高 → 开会/聊天；music_prob 高 → 听歌/看视频
- 睡眠推断：深夜 is_silent + 熄屏 + 无 usage → 入睡时间
- 环境噪音：分贝曲线 → 通勤/工作/居家环境区分
- 自动化触发：speech_prob 高 → 自动开勿扰

---

### 3.5 audio_clip —— 音频片段（原始录音）

**字段**：`pcm_b64`（15 秒 8kHz 16bit PCM 的 base64，约 320KB/条）

**实测样例**：
```json
{"pcm_b64": "IP8Z/wz/AP/3/u7+5f7a/tX+0v7R/tT+1/7d/t7+2f7X/tj+1/7V/tD+y/7R/tP+0P7Q/sn+x/7H/sL+vf64/rT+r/6t/q7+r/6w/rf+xv7..."}
```

**用途**：
- 本地 ASR 语音识别：识别"在说什么"（需本地模型，如 sherpa-onnx / whisper.cpp）
- 语音活动检测：与 audio_env 交叉验证
- 环境声分类：音乐/人声/交通噪音

**风险**：这是**原始录音**，隐私风险最高。建议：默认不落库或加密存储，仅按需采集。

---

### 3.6 notification —— 通知流

**字段**：`pkg`、`app`、`title`、`text`、`channel`、`clicked`（是否点击）、`removed`（是否移除）

**实测样例**：
```json
{"pkg": "com.ss.android.lark", "app": "飞书", "title": "ETC APP开发对接", "text": "张智超: 我现在发", "channel": "push_oplus_category_service", "clicked": false}
{"pkg": "com.coloros.alarmclock", "app": "时钟", "removed": true, "clicked": false, "channel": "com.oplus.alarmclock.next.alarm"}
```

**用途**：
- 通知疲劳分析：各 app 通知量排行、轰炸时段（实测 8:05 一分钟 8 条）
- 点击率分析：clicked 统计 → 哪些通知有价值、哪些被忽略
- 通知降噪建议：高量低点击的 app → 建议关通知
- 免打扰时段推荐：通知高峰 vs 睡眠时段

---

### 3.7 location —— 定位

**字段**：`lat`、`lon`、`acc`（精度米）、`provider`（gps/network）

**实测样例**：
```json
{"lat": 31.992793, "lon": 118.782878, "acc": 30, "provider": "network"}
{"lat": 31.99283563, "lon": 118.78280399, "acc": 19, "provider": "gps"}
```

**用途**：
- 常驻点识别：DBSCAN 坐标聚类 + 停留时长 → 家/公司
- 逆地理编码：高德 API 把坐标转地址（个人开发者 5000 次/日免费）
- 通勤分析：位置变化时段 → 通勤时间、路线
- 场景关联：位置 + usage → "在通勤路上刷手机"

**实测观察**：全天 46 条定位集中在 31.9927-31.9930 / 118.7827-118.7832（南京雨花台区雨花街道玉兰路），一整天未移动。

---

### 3.8 battery —— 电量/充电状态

**字段**：`level`（电量 0-100）、`charging`（是否充电）、`plugged`（充电方式）

**实测样例**：
```json
{"level": 48, "charging": false, "plugged": 0}
```

**用途**：
- 电量曲线：分钟级电量变化 → 耗电速率
- 耗电分析：电量骤降时段 + usage → 耗电大户 app
- 充电习惯：充电时段、充电频率
- 低电量预警：结合场景触发提醒

---

### 3.9 network —— 网络状态

**字段**：`type`（wifi/移动）、`ssid`（WiFi 名）

**实测样例**：
```json
{"type": "wifi", "ssid": "hhhyyyqqq-5G"}
```

**用途**：
- 场景识别：WiFi SSID 变化 → 家/公司/外出
- 网络切换分析：移动网络时段 → 通勤/外出
- 与 location 交叉验证场景

---

### 3.10 高敏感事件（clipboard / sms / input / screen_content）

| 类型 | 字段 | 实测样例 | 风险 |
|---|---|---|---|
| clipboard | pkg, text | 剪贴板文本（含聊天内容） | 极高 |
| sms | number, text | 10086 流量提醒等 | 极高 |
| input | pkg, activity, text | 输入框内容（如"穿越"） | 极高 |
| screen_content | pkg, activity, texts[] | 屏幕可见文本（抖音页面） | 极高 |

**处理策略**：
- 默认**不采集或脱敏**：仅保留元数据（pkg/activity），丢弃 text 正文
- 若需保留：本地加密存储，不上报明文
- 分析用途：仅做"在哪个 app 输入/看什么类型内容"的粗粒度统计

---

## 4. 处理链路设计

```
采集层(已有) → 清洗层 → 加工层(新增) → 分析层 → 展示层
```

### 4.1 清洗层

| 规则 | 说明 |
|---|---|
| 坏数据过滤 | usage foreground_ms=0 丢弃；payload 解析失败丢弃 |
| 时间戳规范化 | ts 毫秒 → 本地时区 date/hour 字段 |
| 去重兜底 | events 表加 (device_id, ts, type) 唯一约束 |
| 敏感字段脱敏 | clipboard/sms/input/screen_content 的 text 默认丢弃 |

### 4.2 加工层（核心，产出事实表）

**sessions 表**（前台会话）：
```
id, device_id, pkg, app, activity, start_ms, end_ms, duration_ms, day
```
由 usage + session 拼接：app_switch 为边界，usage 为时长，screen_off 截断。

**places 表**（常驻点）：
```
id, device_id, lat, lon, radius, label(家/公司/未知), first_seen, last_seen, visit_count
```
DBSCAN 聚类 + 停留时长 + wifi/bt 佐证。

**daily_stats 表**（日汇总）：
```
day, total_screen_ms, app_usage_json, notification_count, notification_clicked,
top_notification_apps, sleep_start_ms, sleep_end_ms, silent_hours, place_distribution_json
```

### 4.3 分析层（指标）

| 指标 | 数据来源 | 产出 |
|---|---|---|
| 屏幕时间 | sessions | 总时长、app 排行、连续使用段 |
| 注意力碎片化 | session 切换频率 | 切换次数/小时 |
| 通知疲劳 | notification | 轰炸时段、点击率、降噪建议 |
| 睡眠质量 | audio_env + snapshot + usage | 入睡/起床、睡眠时长、夜间亮屏 |
| 场景分布 | places + snapshot | 家/公司/通勤时间占比 |
| 耗电分析 | battery + usage | 耗电大户、充电习惯 |

### 4.4 展示层

- **Web 仪表盘**：FastAPI 加页面——时间线、24h 热力图、app 排行、通知统计、电量曲线
- **AI 数字生活日报**：LLM 读 daily_stats 生成"今日数字生活日报"

---

## 5. 落地步骤

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| P1 | 清洗 + ETL 脚本 | sessions 表 + daily_stats 表 | 半天 |
| P2 | SQL 分析模板 | 屏幕时间/通知疲劳/睡眠 三组查询 | 半天 |
| P3 | Web 仪表盘 | FastAPI 2-3 个页面 | 1-2 天 |
| P4 | AI 日报 | LLM 总结 daily_stats | 1 天 |
| P5 | 高德逆地理编码 | places 表补地址 | key 就绪后半天 |
| P6 | 隐私加固 | HTTPS + 敏感字段脱敏 + allowBackup=false | 1 天 |

---

## 6. 隐私与合规（必须处理）

1. **audio_clip 是原始录音**：默认不落库或加密，仅按需采集
2. **clipboard/sms/input/screen_content 含明文内容**：默认脱敏，只留元数据
3. **明文 HTTP 传输**：建议上 HTTPS 或至少局域网内使用
4. **allowBackup=true**：App 数据可被备份导出，建议关闭
5. **高德合规**：展示结果标注"高德地图"版权；不缓存结果超 30 天对外服务

---

## 7. 建议优先级

1. **P1 清洗+ETL**：地基，马上能看到"今天上午干了啥"完整画像
2. **P2 分析模板**：纯 SQL，快速出数字
3. **P5 高德接入**：key 就绪后补地址，场景识别闭环
4. **P6 隐私加固**：决定长期采集前必须做
5. **P3/P4 展示**：锦上添花，最后做
*（内容由AI生成，仅供参考）*

---

# 评审意见与修订（2026-08-17，用户已确认）

> 基于 2026-08-17 真实数据（4894 条事件，今日 3228 条）评审。原则：**本文档作为后续开发依据，本节修订覆盖原方案中与其冲突的内容。**

## R1. 定位声明（覆盖原 3.10 / 第 6 节）

本项目为**用户自用工具，明确不考虑隐私合规**。原方案第 3.10 节"clipboard/sms/input/screen_content 默认脱敏丢弃"、第 6 节"隐私加固"等条款**全部作废**——这些数据是画像核心价值，必须完整采集与保留。

## R2. 数据可用性结论（实测分级）

| 事件 | 结论 | 依据 |
|---|---|---|
| usage / session / notification / location / audio_env / snapshot / battery / network | ✅ 可用 | 字段完整，实测无坏值（除 ts 脏数据外） |
| accel | 🟡 部分可用 | motion 粗分类可用；x/y/z 均值受手机姿态影响，不宜直接进分析 |
| audio_clip | 🟡 能用但成本爆炸 | 131 段/天 ≈ 42MB，**必须降频**（见 R5） |
| screen_content | 🔴 质量待修 | 736 条 ts=0 脏数据占大头（无障碍 timeStamp=0） |
| input / clipboard / sms | 🔴 量太少 | 今日各 3-9 条，暂不支撑分析 |

## R3. 已发现并修复的实测问题（原方案盲区）

1. **ts=0 脏数据**：935 条（input 199 + screen_content 736）→ ETL 已过滤（`ts < 1e12`）
2. **系统 App 噪音**：`com.android.launcher`（OPPO 实际桌面）等 40+ 系统组件污染排行 → ETL 黑名单已扩充
3. **自家 App 噪音**：`com.wei.checkapp` 自身前台计入统计 → ETL 已过滤

## R4. 地点逆编码（高德）——优先级提升

用户已申请高德 API Key。**原方案 P5 提前到 P2 之后立即执行**（场景识别闭环是"数据用起来"的关键一步）：
- places 表已有网格聚类（14 个网格，2 个主常驻点）
- 对 top 常驻点做逆地理编码 → 家/公司标签
- 展示需标注"高德地图"版权（保留原合规要求）

## R5. 音频成本治理（新增，原方案未量化）

- 现状：8kHz/15s/5min 一次 → 131 段/天 ≈ **42MB/天**
- 决策：**客户端降频**（5min → 30min）或降采样；估算降到 1/6 后约 7MB/天

## R6. 修订后优先级（用户确认的最终版）

1. **P1 清洗 + ETL**（✅ 已完成，`gacore/langTrack/etl.py`）
2. **P2 分析模板**：SQL 产出屏幕时间 / 通知疲劳 / 睡眠推断 / 场景分布
3. **P5 高德逆地理编码**（优先级提升，Key 已就绪）
4. **P3 Web 仪表盘**：基于三张事实表的简单 HTML 页面
5. **P4 AI 数字生活日报**：LLM 读 daily_stats 生成
6. **音频降频**（客户端，与 2-5 并行）
7. **screen_content ts 修复**（客户端无障碍时间戳兜底）
8. **原 P6 隐私加固**：**取消**（自用定位，不适用）

---

# 执行记录（工作日志，随每次改动更新）

> 本文件为 langTrack 数据链路的**路书**：既是指南也是工作记录。
> **每次改动（客户端或服务端）后必须在本节追加记录**（见 AGENTS.md 强制要求）。

## 2026-08-18：音频压缩落地 + 数据质量首轮验证

### 已完成
- ✅ **音频压缩**（客户端 `AudioCollector`）：PCM → AMR-NB（`codec:"amr_nb"`，单条 320KB→41KB base64，降 87%）；特征窗口 10s；静音跳过片段
- ✅ **ETL 三表**（`etl.py`）：sessions/daily_stats/places；过滤 ts<1e12 脏数据、系统 app 黑名单 40+、碎片阈值 5s
- ✅ **报告**（`report.py`）：屏幕/通知/睡眠/场景四块
- ✅ **高德逆编码**（`geocode.py` + `label_places.py`）：家/公司标签 + `place_labels.json` 持久化（ETL 重跑不丢标签）+ `is_primary` 主点标记
- ✅ **Web 仪表盘**（`dashboard.py`）：`/dashboard` 深色单页
- ✅ **异常清理**：`etl.py --purge` 删除 ts 脏数据（累计 948+77 条）

### 实测数据（8-17 同步）
- 总 4894 条 → 清洗后 3959；音频压缩后 audio_clip 单条 41KB（原 320KB）
- 8-18 首日全链路：usage 542 / snapshot 641 / session 417 / accel 344 / audio_env 102 / location 3
- 家/公司标签：新华汇=公司（176 次累积）、雨花街道=家（104 次累积）

### 问题诊断（8-18 数据异常）
**🔴 audio_env/audio_clip 在 00:34 集体停止**（全天仅 34 分钟数据）：
- 根因：`AudioRecord.read()` 在 OPPO 回收麦克风后**永久阻塞**，采集线程卡死空转
- 原代码 `catch` 吞异常 → 线程死无日志
- 修复（客户端 `afc5c59`）：read 20s 超时保护 + 看门狗（5min 无产出自动重启线程）+ Log 日志
- **待验证**：重新打包安装后，次日确认 audio_env 全天在线（预期 ~1400 条/天）

### 待办（按优先级）
- [x] ~~P4 AI 数字生活日报~~ → **已融合进 daily-report**（见下"8-19 记录"）
- [ ] screen_content ts 脏数据修复（客户端 AccessibilityEvent timeStamp=0 兜底，R7）
- [ ] location 采集量偏低排查（8-18 仅 3 条，疑似 OPPO 后台限频）
- [ ] 音频看门狗修复后次日数据验证

---

## 2026-08-19：langTrack → daily-report 融合（外挂式）

### 已完成
- ✅ **新建 `langTrack_stats` 工具**（`src/gacore/tools/langTrack_tools.py`）：
  - 外挂式：只读 `data/langTrack.db` 事实表，自动触发 ETL（幂等），返回结构化画像（屏幕时长/App 排行/通知/睡眠信号/场景）
  - 注册仅 3 行（`tools/__init__.py` import + TOOL_NAMES + _TOOLS），零侵入其他模块
- ✅ **daily-report prompt 追加 langTrack 信号节**（`config/schedule.json`，scheduler.py 零改动）：
  - 步骤 4：`langTrack_stats(day=today)` 获取手机使用画像
  - 新增「langTrack 信号解读」节：屏幕/睡眠/场景与归档交叉成"自我观察"，没干货不强写
- ✅ **测试**：`tests/test_tools_langTrack.py` 6 用例（正常/无数据/DB缺失/ETL失败/DB损坏/注册检查），全 mock
- ✅ **全量回归**：315 passed（原 293 + langTrack 相关 22），ruff clean

### 设计原则（高内聚/低耦合）
- 工具模块独立：不 import scheduler/graph/其他 tools，只依赖 DB + ETL CLI
- prompt 只加"一个信号源"，agent 决定如何解读——日报结构不变
- `LANGTRACK_ROOT` 环境变量可覆盖仓库根（测试隔离用）

### 待办更新
- [x] P4 AI 日报 → 已通过 langTrack_stats 融合进现有 daily-report（不另起炉灶）
- [ ] 验证：23:50 日报实际跑起来后，确认邮件正文含 langTrack 信号节
