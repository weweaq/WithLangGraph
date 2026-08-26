---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 74fca65fe87f9b5d6900c56ec5512fbd_2e7dcf2ea09211f1a413525400287e28
    ReservedCode1: GOvDpPJbfoNkuP/Jz1z68yKV+joCPNYMNKIeRvteFSBBFuhMQvBS24gdWwu8Wjb6TW7wGmaYIiC4cf7NBcWwD6W6Uo7dBqZElJIMT7X+Zwmxi01a84u8M+bdYBKSPBqSZk5GGK9Fg19Q7uSn5oAVQoA298/SpkQachSZOwaYR3ovxeT8wQUarZowPA0=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 74fca65fe87f9b5d6900c56ec5512fbd_2e7dcf2ea09211f1a413525400287e28
    ReservedCode2: GOvDpPJbfoNkuP/Jz1z68yKV+joCPNYMNKIeRvteFSBBFuhMQvBS24gdWwu8Wjb6TW7wGmaYIiC4cf7NBcWwD6W6Uo7dBqZElJIMT7X+Zwmxi01a84u8M+bdYBKSPBqSZk5GGK9Fg19Q7uSn5oAVQoA298/SpkQachSZOwaYR3ovxeT8wQUarZowPA0=
---

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



## 2026-08-22：A① 服务端契约覆盖校验（contract coverage check）

### 背景
- D 阶段排查发现 17 类设计事件里仅有 14 类实际落地（`screen_content`/`media`/`bt_device`/`call` 从未到达），且服务端无任何机制发现此缺失。本期收敛为**纯观测**的契约覆盖校验（断流告警/客户端保活用户已明确不做）。

### 已完成
- ✅ **契约模块**（`src/gacore/langTrack/contract.py`）：`EXPECTED_EVENT_TYPES`（17 类，含 desc/consumed）、`STALE_DAYS=7`，作为期望事件类型的唯一权威副本。
- ✅ **ETL 新步骤**（`etl.py` `build_contract_coverage(conn)`）：`SELECT type,COUNT(*),MAX(ts) FROM events GROUP BY type` 比对契约，全量重建事实表 `contract_coverage`（列含 type/expected/consumed/desc/arrived/event_count/last_seen_ts/status/created_at/updated_at）；status ∈ {ok, stale, missing, unexpected}；`REPLACE` 幂等；接入 `run()` 末尾（`etl.execute()` 实为 `run()`，函数名偏差，见下）。
- ✅ **工具出口**（`src/gacore/tools/langTrack_tools.py`）：`LangTrackDayStats` 增 `coverage: list[dict]`；`langTrack_stats` 读取非 `ok` 类型（`{type,desc,status,last_seen,consumed}`）。
- ✅ **仪表盘**（`dashboard.py` `render_dashboard_html`）：新增「采集覆盖」卡片，按状态着色 chip（🟢ok/🟡stale/🔴missing/⚪unexpected），复用既有深色样式，无 print。
- ✅ **报告**（`report.py`）：新增「■ 采集覆盖」小节，列出 missing/stale/unexpected 及 desc、last_seen。
- ✅ **单测**（`tests/test_langTrack_contract.py`）：构造假 events（部分 ok / 一个 stale / 一个 unexpected / 四个缺失），断言 status 判定正确，3 用例全过。

### 实测验证（8-22 跑 ETL 后）
- `contract_coverage` 共 18 行（17 契约 + 1 unexpected）。
- missing：`screen_content` / `media` / `bt_device` / `call`（与 D 阶段结论一致）。
- unexpected：`accel`（客户端实际在送、契约未登记——反向信号生效，提示需把 accel 同步进契约）。
- 单测：`3 passed`；`import gacore.langTrack.etl, gacore.tools.langTrack_tools, gacore.langTrack.dashboard, gacore.langTrack.report, gacore.langTrack.contract` 干净。

### 偏差/说明
- 任务书称 `langTrack_tools.py` 在 `gacore.langTrack` 下，实际位于 `gacore.tools`（`tools/__init__.py` 注册）。按真实位置集成，未新建文件。
- 任务书称「接入 `etl.execute()`」，实际函数名为 `run()`，已接入 `run()` 末尾（其他事实表之后）。
- 单测用 `conn.executescript(etl._SCHEMA)` 建表（单一来源），不依赖真实 DB。

### 待办更新
- [x] A① 服务端契约覆盖校验：contract.py + build_contract_coverage + 三出口 + 单测。
- [x] 契约同步：客户端在送的 `accel` 已显式加入 `EXPECTED_EVENT_TYPES`（desc=加速度聚合, consumed=false）。重跑 `build_contract_coverage` 后 `contract_coverage` 共 18 行全为 expected，accel=ok，不再误报 unexpected（原 4 类 missing 仍正确保留）。单测 3 passed 仍通过（`test_contract_coverage_rowcount` 断言动态取 `len(EXPECTED_EVENT_TYPES)+1`）。
- [x] A① 检视闭环：5-Agent 审阅，context-mining 发现 accel 漏登阻塞项，已修复并复跑验证通过。

---

## 2026-08-22：B 阶段 ETL 全量重建 + 配置外置

### 背景

A① 之后数据侧欠账集中暴露：`daily_stats` 缺 device 维度（多设备无法区分）、清洗阈值散落硬编码在 `etl.py` 内部、`routes.build_trips` 的 `day` 归日用本地时区存在漂移风险。B 阶段目标 = 一次性重建 ETL 层，为 C1 人物画像提供干净的多设备事实表。

### 已完成

- ✅ **ETL 全量重建**（`etl.py`，步骤 B1–B8）：sessions / stays / trips / places / daily_stats 五张事实表统一重建，均带 `created_at` / `updated_at`（东八区）与 `etl_version` 列，清洗与合并规则按最新口径实现。
- ✅ **配置外置**（新增 `src/gacore/langTrack/etl_config.py`）：阈值、白名单等可调参数从 `etl.py` 抽出集中管理，ETL 行为改配置不改代码。
- ✅ **B2 主键修正**：`daily_stats` 主键定为 `(device_id, day)`；`_migrate_daily_stats_pk` 对旧库迁移时保留历史数据不丢行。
- ✅ **trips 构建改进**（`routes.py`）：`build_trips` 参数化 `min_duration_ms` / `min_dist_m`；`day` 归日改用 `_TZ_CST` 显式东八区，消除本地时区依赖。

### 实测验证

- 单测 9 passed。
- 真实库拷贝跑全量 ETL：退出码 0；`daily_stats` PK 实测为 `['device_id','day']`；8 张事实表 `etl_version` 全部非空；`etl_state` 恰 1 行。

### 待办更新

- [x] B 阶段 ETL 全量重建 + 配置外置 + B2 主键修正。

## 2026-08-22：C1 服务端人物画像（persona / 甲·外挂式）

### 背景
B 阶段完成数据模型与 ETL 重构后，langTrack 已具备 sessions / stays / trips / places / daily_stats 事实表。C1（北极星最高优先级：行为模式/人物画像）目标=刻画"完全懂主人"的助手——把事实表加工成可解读的行为模式画像。本期采用「外挂式」纯读聚合：不动 ETL、不加表、不污染既有模块。

### 已完成
- ✅ **画像核心**（`src/gacore/langTrack/persona.py`）：`build(conn=None, device_id=None, days=7, db_path=None) -> dict`，纯读 B 阶段事实表，输出 5 维度：
  - 应用分类聚合（按 `data/app_categories.json` 显示名→分类，未登记 app 落入「其他」+ `uncategorized` 清单）
  - 屏幕健康度（日均时长 / 趋势 up·down·flat / 重度屏幕使用者判定）
  - 使用时段分布（CST 6 段划分，深夜占比→夜猫子，峰值时段）
  - 生活规律（家/公司 stay JOIN places 取出门时刻；数据不足降级）
  - 特征 + 画像卡片文本（traits / card）
- ✅ **分类映射**（`data/app_categories.json`）：基于真实 DB 实测 16 个 app 显示名（抖音/微信/Edge/小红书/哔哩哔哩/网易云音乐/夸克/飞书/淘宝/时钟/WeLink/QQ/华为乾崑/天气/便签/Marvis）→ 视频/社交/工具/购物/通讯/其他。
- ✅ **三出口接入**（与既有 TypedDict / 卡片 / 报告章节风格一致，零侵入）：
  - `tools/langTrack_tools.py`：`LangTrackDayStats` 增 `persona: dict`，`langTrack_stats` 返回填充。
  - `dashboard.py`：新增 `_render_persona` 卡片（traits chips / 日均屏幕 / 深夜活跃 / 分类 Top），插入 dashboard body。
  - `report.py`：新增「■ 人物画像」小节（§9.5）+ 画像写入既有 L5 profile 快照（`data/profiles/langTrack_profile_YYYY-MM-DD.json`）。
- ✅ **单测**（`tests/test_langTrack_persona.py`）：6 用例（分类求和与占比 / 屏幕重度 / 夜猫子 / 规律路径 / 设备过滤无数据 / 旧库无 device_id 列兼容），全过。
- ✅ **健壮降级**：`persona.build` 对 `sessions`/`stays`/`trips`/`places` 缺失表做 `try/except sqlite3.OperationalError` 降级（daily_stats 仍为必需），避免极简库/测试夹具触发 `no such table` 拖垮整个 `langTrack_stats` 调用。

### 实测验证（2026-08-21 真实 DB 拷贝）
- 报告「■ 人物画像」渲染正常：分类 Top `视频 5.05h / 社交 4.8h / 其他 2.86h`；屏幕 `日均 2.2h（正常区间）`；节奏 `深夜活跃 20.0%（正常），高峰 晚上`；规律 `作息规律；约 09:09 出门上班`；卡片 `作息规律的通勤上班族；视频消费者（日均 5.05h，居首）；社交轻度。`
- 仪表盘 `_render_persona` 卡片 HTML 正常渲染（traits chips + 指标行）。
- `pytest tests/ -k langTrack`：**31 passed**（含 6 persona 新用例；回归修复后原 2 失败用例恢复）。

### 偏差/说明
- 任务书称 persona 在 `gacore.langTrack` 下，已落地于 `src/gacore/langTrack/persona.py`，与 contract 同目录，符合「外挂式」原则。
- 旧库 `daily_stats` 无 `device_id` 列时：`build(device_id=None)` 自动退化为全量读（已单测覆盖）。

### 待办更新
- [x] C1 人物画像：persona.py + app_categories.json + 三出口 + 单测 + 健壮降级。
- [ ] C2 数据质量可视化、C3 可行动洞察：本期未做（按北极星 C1>C2>C3 优先级，C1 先行）。
- [ ] 分类映射持续补全：真实 DB 新 app 显示名出现时，`uncategorized` 清单会提示需补 `app_categories.json`。

## 2026-08-22：新增技术实现参考文档 docs/langTrack-tech.md

### 背景

需要一份面向开发者的代码级技术参考：接口、实体、数据库表字段用途、数据流图。现有两份文档（本路书=工作日志、`etl-cleaning-guide.md`=清洗规则详解）均非结构化参考视角。

### 已完成

- ✅ `docs/langTrack-tech.md`，8 节：
  1. 端到端数据流总览（mermaid flowchart：采集→接收→原始层→事实层→四出口）
  2. HTTP 接口（/health、/ingest、/dashboard 表格 + ingest_batch 幂等单事务时序图；周期 ETL 线程与 env 覆盖）
  3. Pydantic 实体（Event/IngestRequest + 上报 JSON 示例）
  4. 数据库表字典：原始层 3 表逐字段（events 列名为 `payload`）+ 事实层 12 表逐字段用途（sessions/daily_stats PK(device_id,day)/stays/trips 高德缓存四列/places 全量语义列与保标签 UPSERT/contract_coverage 四态/etl_state 水位线/etl_runs 血缘/dirty_events 隔离区/anomalies/route_grids/grid_pois），B8 etl_version 自然时间列映射表 + erDiagram 关系图
  5. 出口实体：LangTrackDayStats TypedDict 全字段来源表、persona.build() 返回键树+五组判定阈值表、契约 18 类型×consumed
  6. ETL run() 十四步 mermaid flowchart（增量水位线回看3天/trips 已编码路线带回/places 保标签/高德三处外呼失败隔离不阻塞）
  7. 外部依赖与配置（env 变量、data/ 下四个文件含 gitignore 说明）
  8. 测试与常用命令

### 验证

- 所有字段名/行号/阈值对照 codegraph 逐字源码核验（storage/server/schemas/contract/persona/etl/routes/langTrack_tools 共 9 文件），无凭记忆杜撰项。
- 检视说明：独立 agent 检视两次被中断，改为基于同会话逐字源码自查 + mermaid 4 图人工核对语法。

### 待办更新

- [x] 技术实现参考文档落库。


## 2026-08-22：人物卡切换（/角色 命令 + 工具能力保留订正）

### 背景

对话前端 `frontends/qq.py` 需要支持“换人设”能力。设计要点：**切换角色不得让系统智能体失去调用工具的能力**——工具可用性由运行时装配（graph/model 绑定）决定，与人设无关。据此推翻“角色激活时跳过 L0 工具准则”的初稿取舍，改为**工具能力层与人设层解耦**。

### 已完成

- ✅ **人物卡注册表**（新增 `src/gacore/character.py`）：纯数据资产——扫描 `config/assets/characters/*.md`，卡 id = 文件名 stem，显示名 = 首个 `# ` 标题，prompt 正文 = 标题后全部文本。新增卡零代码改动，丢 .md 即切换。
- ✅ **运行时状态**（`src/gacore/state.py`）：`active_card` 字段，会话内生效。
- ✅ **唯一注入点**（`src/gacore/context.py`）：角色激活时 system prompt = L0 工具准则 + 人物卡 + 工具桥接句（“保留系统智能体全部能力，可调用系统工具，用角色口吻表达”）。
- ✅ **前端口令**（`src/gacore/frontends/qq.py`）：`/角色` 查看/切换、`/角色 <id>` 切换、`/角色 off` 退出；`_user_card` 持久化到 `data/active_cards.json`（重启不丢）。
- ✅ **工具能力保留**：各卡可自限工具使用倾向（如纯日常向卡写“非必要不调工具”），但能力统一保留；未来接新工具零成本（注册进 graph 即可，context/卡文件/qq.py 无需改）。
- ✅ **测试**：新增 `tests/test_character.py`(7)、`tests/test_character_system_prompt.py`(4)，`tests/test_qq.py` 增 3 条 `/角色` 断言；相关回归 **69 passed**。
- ✅ **设计文档**：`output/人物卡切换设计方案-高内聚低耦合.md` 已订正 §5.1/§9/§11。

### 待办更新

- [x] C1 人物画像（persona）。
- [x] 人物卡切换：character.py + state/context/qq.py + 物料卡 + 测试 + 文档。
- [ ] 起 bot 手工验收：`/角色` → 换卡聊天 → 重启核对卡不丢 → `/角色 off`。

## 2026-08-22：修复「人物卡已切换但人格未注入」根因

### 背景

手工验收截图反馈：QQ 里输 `/角色 nami` 提示“已切换为「娜美」”，但随后的“在干嘛 / 给我点钱”回复仍是默认助手口吻，娜美人格完全没生效。

### 根因定位

- `/角色` 命令本身正常：`_user_card` 已设置并持久化（`data/active_cards.json` 有 `nami` 映射）。
- `context.build_system_prompt` 逻辑正确、离线直调带 `active_card='nami'` 也能拼出娜美人格。
- **真正的断点在 state 通道**：`GAState` 的 `__annotations__` 中**没有声明 `active_card` 字段**。LangGraph 图通道以注解为准，未声明字段在运行时流转时被静默丢弃 → 每轮 `GAPromptMiddleware` 调 `build_system_prompt` 时拿到的是空 `active_card` → 人格文本永不注入。
- 初版只把 `active_card` 写进了 `new_state()` 的初始字典，以为“进去就能带出来”，实际被通道丢弃；既有单测只测了离线 `build_system_prompt`，没走真实 graph 全链路，因而漏网。

### 修复

- `src/gacore/state.py`：`GAState` 类补 `active_card: str | None` 字段注解。
- `tests/test_character_system_prompt.py`：新增 e2e 回归测试——用捕获 LLM 驱动真实 graph（`build_graph`→`ainvoke`），断言模型收到的 system prompt 同时含 L0 工具准则 + 卡片正文 + 工具桥接句。该用例在修复前必 fail、修复后通过，锁死此坑。

### 验证

- 全量回归 `pytest tests/`：**339 passed**（含新增 e2e）。
- 实测截图场景：经 graph 全链路后 system prompt 正确注入娜美卡片正文。

### 待办更新

- [x] 修复 active_card 通道掉字段（GAState 补注解 + e2e 回归）。
- [ ] **重启 QQ bot 进程**后再做手工验收（旧进程加载的是修复前代码）。
## 2026-08-22：QQ bot 主动推送能力（openid 自动落库 + qq_push 独立脚本）

### 背景
船长希望 QQ 机器人能记住与其私聊过的用户（openid），从而在 weitrack/langTrack 画像产出后**主动推送**消息，而不是被动等待用户发消息。

### 已完成
- `src/gacore/frontends/qq.py`：新增 `_record_known_user()`，收到 C2C 私聊（含群 @）时把 user_id（openid）幂等落库到 `data/qq_known_users.json`（`first_seen`/`last_seen`，文件损坏自动降级重建）。
- 新增 `src/gacore/langTrack/qq_push.py` 独立推送脚本：`python -m gacore.langTrack.qq_push "消息"` 推给全部已知用户，`--to <openid>` 指定用户，`--show` 列出已知用户（运行时需 `PYTHONPATH=src`）。
- botpy 沙箱模式主动推送要求接收方 openid 在 QQ 开放平台沙箱名单；船长已将其本人加入体验名单。

### 验证
- 两文件 `python -m py_compile` 通过；botpy 在 miniconda py12 环境可用。
- `python -m gacore.langTrack.qq_push --show` 正常输出（当前暂无已知用户，等待私聊后自动记录）。

### 待办更新
- [x] 重启 `start.py`（QQ bot + 调度器）以加载新代码。
- [x] 船长给机器人发一条私聊，确认 `data/qq_known_users.json` 落库成功（openid=5270…，first/last_seen=2026-08-22 23:18:39）。
- [x] 用 qq_push 实测主动推送一次（见下一条记录：全链路打通，顺带修了一个挂起 bug）。

## 2026-08-22：QQ 主动推送实测打通 + 修复 qq_push 挂起 bug

### 背景
上一条记录已把 openid 落库与 qq_push 脚本就位，本轮实测主动推送全链路，结果发现脚本有个真 bug。

### 实测过程
1. 船长私聊机器人 → `data/qq_known_users.json` 落库成功（openid=`5270431CCFE7063EA0B30D0D396D91BF`）。
2. `python -m gacore.langTrack.qq_push "…"` 首次运行：botpy 登录、ws 心跳全部正常，但进程 3 分钟无任何输出、消息未发出（job 强杀）。

### 根因（botpy `Client` 不适合一键推送）
- botpy 的 `Client.start()` 是**长驻机器人**入口：`_bot_init → _pool_init` 里 `while not self._closed: await coroutine` 永久阻塞在 websocket 会话循环上，`await client.start()` 根本不会返回，`post_c2c_message` 永远执行不到。
- 且 `Client.close()` 只关 HTTP 不关 ws，`asyncio.run()` 等不到事件循环结束 → 进程假死；`[ok]` 打在管道块缓冲 stdout 上未 flush，kill 后日志全丢（同坑 #13 一类）。

### 修复
`qq_push.py` 弃用 `Client`，改走 **BotHttp + BotAPI 纯 REST**：`http.login(Token(appid, secret))` → `api.post_c2c_message(...)` → `http.close()`，不建 ws 连接，发完即退。新增 `--sandbox` 参数（默认正式域名 `api.sgroup.qq.com`）。

### 实测验证（全链路打通）
- 修复前后各发一条，平台均返回真实 message id（`ROBOT1.0_…`）。
- 船长 QQ **两条都收到**：「连通性测试」（23:43:37）与「【韩立】主动推送链路实测 ✅ …」。
- 修复后脚本输出 `[ok] <openid> id=ROBOT1.0_…` / `完成：成功 1，失败 0`，退出码 0，数秒内干净退出。

### 注意事项（官方策略，2025-04 起）
- QQ 开放平台已公告**不再支持「主动消息推送」**（能力收敛）；本次实测平台仍受理并送达，属灰色地带。
- 最稳路径仍是：用户发消息后 5 分钟内、带 `msg_id` 的**被动回复**；主动推送若后续失效，需退化为「定时提醒用户来一句」模式。

### 待办更新
- [x] 船长私聊 → openid 落库成功。
- [x] qq_push 实测主动推送一次（两条均送达，链路打通）。
- [x] 修复 qq_push 用 `Client` 导致的挂起 bug（改 BotHttp 直连）。
- [ ] （后续）每日画像产出 → 主动推送任务接入调度器（注意主动消息频控/策略）。

## 2026-08-22：QQ 主动推送封装为 agent 工具（qq_push）

### 背景
主动推送实测打通后，把推送能力暴露给 agent（韩立）：让机器人在对话中能自主给主人推消息（画像报告/事项提醒），而不只是靠 CLI 手动发。

### 已完成
- `src/gacore/langTrack/qq_push.py`：抽出公共异步函数 `send_c2c(content, targets, is_sandbox) -> dict`，CLI `_push` 改为薄封装；返回 `{ok, failures, errors, ids}`，缺配置/botpy 缺失返回 `{error, message}`，永不 raise。
- 新增 `src/gacore/tools/qq_tools.py`：`qq_push` 工具（`@tool`）——`message` 必填、`to` 可选（逗号分隔 openid，缺省=全部已知用户）；返回 TypedDict（`sent{ok,to,failures,ids}` / `error`），不抛异常。
- `src/gacore/tools/__init__.py`：外挂式注册三处（import / TOOL_NAMES / _TOOLS），工具总数 26。

### 实现要点
- 工具复用 `send_c2c`（BotHttp+BotAPI 纯 REST），不走 `Client`（ws 长连接会挂死一键调用）。
- **线程桥**：botpy 异步调用经模块级单 worker `ThreadPoolExecutor` 跑 `asyncio.run`（90s 超时），解决"同步工具内直接 `asyncio.run` 在 running loop 报错"的问题（QQ 前端是 asyncio 环境）。
- 参数 schema 只暴露 `message`/`to`（LangChain `@tool` 自动排除未声明参数，测试断言 `props == {message, to}`）。

### 实测验证
- 新测试 `tests/test_tools_qq_push.py` **8 条全过**（monkeypatch 假发送器，不碰网络）：注册、schema、缺省/指定收件人、无收件人、发送器 error 透传、全失败、部分失败。
- registry 一致性：`TOOL_NAMES` 与 `build_tool_list` 26 个一致（直接脚本断言，绕过沙箱拒绝的 tmp_path fixture）。
- 真实冒烟：`qq_push.invoke({message: …})` 经线程桥真实发送成功，返回 message id（`ROBOT1.0_…`），船长 QQ 已收到「工具已封装完成 ✅」。
- import 链验证：`frontends.qq → graph → tools → qq_tools → langTrack.qq_push` 全通。

### 待办更新
- [x] 主动推送封装为 agent 工具 `qq_push`（CLI 复用 `send_c2c`）。
- [ ] （后续）引导模型在画像产出/提醒场景主动调用 `qq_push`（人物卡或 context 注入提示）。
- [ ] （后续）每日画像产出 → 主动推送任务接入调度器（注意主动消息频控/策略）。

## 2026-08-23：数据页「已发送事件」只展示前 5 条 + 查看全部明细页（客户端）
### 背景
- 数据页「已发送事件（最近）」原本一次渲染 `recentSentEvents(50)` 50 条 EventCard，整页被拉得很长，翻看今日事件/采集器状态都要滚很久。
### 已完成（weiCheckApp，未涉及采集格式，服务端 ETL 无影响）
- `ui/data/DataScreen.kt`：改为只取最近 5 条；末尾新增整行可点的「查看全部（共 N 条）」入口卡（回调 `onShowAllSent`）。
- 新增 `ui/data/SentEventsScreen.kt`：全屏已发送明细页（顶栏返回 + 总数 + LazyColumn 懒加载最近 500 条），复用待上传明细页的卡片样式。
- `ui/pending/PendingDetailScreen.kt`：`PendingEventCard` 与类型配色 `eventTypeColor` 提为 `internal` 共享（数据页明细页复用，避免复制粘贴）。
- `MainActivity.kt`：新增 `showAllSent` 覆盖层状态，与既有 `pendingDetailType` 覆盖层并列；从数据页进入，返回回数据页。
### 验证
- `gradlew.bat :app:assembleDebug --offline` 编译通过（EXIT=0）。
- 待装包真机确认：数据页只显示 5 条 + 查看全部入口；入口进明细页可逐条展开完整 JSON，返回不丢数据页滚动位置。
### 待办更新
- [x] 数据页已发送事件折叠为前 5 条 + 查看全部明细页（本记录）。
- [ ] （既有）screen_content ts 脏数据兜底（R7，无障碍 eventTime<=0 用当前时间）。
- [ ] （既有）location 采集量偏低排查（OPPO 后台限频）。
- [ ] （既有）音频看门狗修复后次日数据验证。
- [ ] （既有）AI 日报 23:50 实际跑一次确认邮件含 weiTrack 信号节。


## 2026-08-24：QQ 对话上下文改本地持久化（告别内存态）
### 背景
QQ 前端此前上下文只存内存：graph.py 默认 MemorySaver()（内存 checkpointer）+ qq.py 的 `_user_threads` dict + `_thread_ids` dict，bot 一重启，所有用户对话历史全部清空，跨天/断线后续不聊。
### 已完成
- `src/gacore/frontends/qq.py`：build_config 改用 `AsyncSqliteSaver.from_conn_string(root/data/gacore_chat.db)` 作为持久化 checkpointer（跨重启存活）；`_user_threads` 落盘 `data/qq_user_threads.json`（启动加载、变更即存、损坏降级 {}）；新增 `_user_threads_file()/_load_user_threads()/_save_user_threads()` 三个 helper（对齐既有 `_card_state_*` 风格）；`_thread_for(user, group)` 线程键；`delete_thread`/`get_state` 调用点转 `await`（注意：同步 delete_thread 在主线程抛 InvalidStateError，必须用异步接口）；main 改 `async _boot` 经 `asyncio.run(_boot())` 启动。
- `start.py`：`await build_config()`。
- `pyproject.toml`：新增依赖 `langgraph-checkpoint-sqlite`。
### 验证
- 冒烟（独立临时库）：写 2 条消息 → 关闭连接（模拟重启）→ 重开读回 2 条一致 ✓；`adelete_thread` 删除 ✓（SMOKE PASS）。
- qq.py 模块导入冒烟 PASS：`_user_threads` 启动加载 {}，helpers 就绪，build_config 为协程函数；持久化 DB 落点 `data/gacore_chat.db` 确认。
- `py_compile src/gacore/frontends/qq.py start.py` 退出码 0。
### 待办更新
- [x] QQ 对话上下文持久化（AsyncSqliteSaver + user_threads.json）。
- [ ] 部署环境需按 pyproject 重装依赖：`pip install -e .`（本地 venv 已装 langgraph-checkpoint-sqlite）。
- [ ] 本地未装 qq-botpy：启动 QQ 前端前需 `pip install qq-botpy`。
- [ ]（后续可选）跨天会话上升为分层记忆（工作记忆 + 每日归档 + 画像注入），参考既有 langTrack.db 方案。

## 2026-08-24：QQ 跨天记忆自动翻篇 + 次日开局注入昨日记忆
### 背景
QQ 对话上下文已持久化（上一节），但 thread 会无限累积、跨天记忆无法延续——新的一天仍接着旧 thread 聊，上下文越滚越长。本改造引入「每日翻篇」：23:50 daily-report 跑完后尾随导出记忆包，次日用户首条消息到达时把旧 thread 归档（保留 checkpoint）、切新 thread 并静默注入最近 3 天日报摘要 + 长期画像。
### 设计
- 设计文档：[`qq-crossday-rollover-design.md`](../output/qq-crossday-rollover-design.md)（user workspace output 目录）。
- 数据流：`scheduler.run_job`（daily-report 成功）→ 尾随导出 `data/onboard_pack.json`（date=日报所属日期、created_at、source_job、prev_thread_id 取自 `data/qq_user_threads.json`、payload={daily_summary_md 最近 N 天日报摘要、long_term_md 长期画像全文或摘要、inject_full、recent_days}）→ `qq.on_message` 入口 `_maybe_rollover(user_id)` → 翻篇（保旧 checkpoint、生成新 thread `qq-{openid}-{uuid8}`、更新 user_threads.json、清理 pack）→ 首轮 state `rollover_context` 注入 system prompt。
### 已完成
- `src/gacore/config.py`：新增 `RolloverConfig`（enabled=true / inject_long_term_full=false / keep_old_thread=true / recent_days=3），并入 `Config.rollover`；`from_env` 支持 `GACORE_ROLLOVER_ENABLED` / `GACORE_ROLLOVER_INJECT_LONG_TERM_FULL` / `GACORE_ROLLOVER_KEEP_OLD_THREAD` / `GACORE_ROLLOVER_RECENT_DAYS`。
- `src/gacore/scheduler.py`：`run_job` 中 daily-report 类 job（name 含 `daily`）**成功后**尾随 `_export_onboard_pack(cfg)`（非新增独立 job）；pack 组装失败仅记日志不阻塞日报。新增 `_onboard_pack_path/_load_active_qq_thread/_long_term_insight/_summarize_long_term/_export_onboard_pack`。
- `src/gacore/state.py`：`GAState` 新增 `rollover_context` channel（一次性跨天注入，首轮后清除）。
- `src/gacore/context.py`：`build_system_prompt` 注入 `=== 昨日记忆注入 ===` 节（读 `rollover_context`，空则跳过）。
- `src/gacore/graph.py`：`cleanup_images` 每轮 END 前把 `rollover_context` 置 None，保证注入仅首轮生效。
- `src/gacore/frontends/qq.py`：`on_message` 入口 `await self._maybe_rollover(user_id)`；新增 `_maybe_rollover` 实现翻篇+静默注入编排，全程 asyncio 主循环内、任何异常兜底不阻塞聊天；`_run_agent` 首轮消费 `self._pending_rollover[user_id]` 写入 `state["rollover_context"]`。
### 注入语义与兜底
- 注入**静默**进行：不向用户展示任何内容，仅在日志记录 `cross-day rollover executed`（old_thread/new_thread/pack_date/injected_chars）。
- 「仅首轮」保证：`rollover_context` 经 checkpoint 持久化，但 `cleanup_images` 在首个 agent 轮结束即清除，后续轮不再重复注入。
- 翻篇防重：pack `date >= today` 不动；`prev_thread_id` 与当前 thread 不一致（已翻篇/已 /new）只清 pack 不重复翻篇。
- 失败降级：无包 / 读失败 / 任何异常 → 跳过注入照常聊天（memory 是加分项，不是单点故障）。
### 约束遵守
- 未 kill / 重启 bot；未改动 `.ps1/.bat`；无破坏性删除（旧 checkpoint 完整保留）。
- 铁律：本记录同步更新 docs/langTrack-roadmap.md 与 docs/langTrack-tech.md（见 tech.md §9.6）。
### 待办更新
- [x] 跨天记忆自动翻篇 + 次日注入昨日记忆（本轮）。
- [ ] 部署后观察首个跨天：确认 23:50 后 pack 生成、次日首条消息触发 rollover 日志且回复包含昨日记忆。
- [ ]（后续可选）翻篇后旧 thread 的按周归档/清理策略（keep_old_thread 预留位，暂不接删除）。

## 2026-08-25：QQ 去人机味改造（入口分级闸门 + 真问题多方案输出）
### 背景
QQ 机器人「韩立」已有完整 agent 回路，但面对随口话（“吃饭吃饭”“早安”“嗯”）也会走完整多轮 graph、调工具、长篇输出，人机味重、成本高。真问题（“推荐…”“怎么选…”）又只给单向结论，缺少对比。本改造按设计文档 `qq-chat-multi-answer-research-20260824.md` 之方案 A+B 落地：随口话走极短即兴轻回应（不建 agent、不调工具）；真决策问题注入多方案输出指令并按【方案N】拆条发送。成本几乎不涨。
### 已完成
**Phase 1 · 入口分级闸门（方案 A）**
- `src/gacore/frontends/qq.py`：
  - 新增 `trivial_detect(text)`：极短（≤8 字）或命中白名单词（问候/吃饭/睡觉/骑车/早安/晚安/语气词/表情/口语虚词），且无意图词（如何/怎么/为什么/帮我/推荐/方案/对比/能否等）→ True。设计为 fail-open：边界情形一律放行进正常 agent 回路，绝不误杀正经问题。
  - `on_message` 第 4 步前插入轻回应分支：命中 `trivial_detect` → `_trivial_reply`，不建 agent 任务、不碰 graph。
  - 新增 `_trivial_reply`：独立无工具 LLM 生成（`get_llm([], bind_tools=False).bind(temperature=1.0, max_tokens=60)`），专用轻量 prompt 注入变量（当前时间/饭点/当日画像/用户最近口吻/人物卡），1~2 句即兴口语，无固定模板；异常降级为一句极简回应。
  - 新增 `_meal_period(hour)`、`_recent_user_voice(user_id)`（读 thread 最近 1~3 条用户消息，失败降级空串）。
- `src/gacore/context.py`：`build_system_prompt` 追加 `[回应分层铁律]`（`_RESPONSE_LAYER_RULES`）：随口话/情绪话/简短问候 → 一句带过（20 字内，不调工具不展开）；明确提问/任务 → 全力作答。A0 提示层兜底，防漏网随口话。
- `src/gacore/state.py`：`GAState` 新增 `output_mode` channel（一次性多方案模式标记，首轮后清除）。
**Phase 2 · 真问题多方案出口（方案 B）**
- `src/gacore/frontends/qq.py`：
  - 新增 `proposal_detect(text)`：命中决策类关键词（推荐/哪个好/怎么选/方案/对比/帮我决定/建议/选择等）→ True。
  - `_run_agent` 首轮 `proposal_detect(text)` 命中 → `state["output_mode"]="proposal"`。
  - `context.py` 在 `output_mode=="proposal"` 时注入 `=== 多方案输出模式 ===`（`_PROPOSAL_HEADER/_PROPOSAL_RULE`）：要求回复显式分段为【方案一】【方案二】【方案三】（至多 3 个）+ 一句“我建议选…”收尾。
  - 新增 `_split_by_proposal(text)`：按 `【方案N】` 正则锚点把一条回复拆成多条（首段为开场、末段为收尾建议），`_stream_agent` 发送端复用 `_SPLIT_LIMIT` 基建逐条发送；锚点不足 2 个时原样发送。
  - `graph.py::cleanup_images` 返回值补 `"output_mode": None`，保证多方案模式仅首轮生效不泄漏到后续轮。
### 验证
- `py_compile` 四文件退出码 0（qq.py / context.py / state.py / graph.py）。
- venv import qq.py 模块 PASS；冒烟单测全过：`trivial_detect` 17 例、`proposal_detect` 7 例、`_split_by_proposal` 3 例（含首段/收尾切分、<2 锚点原样返回）。
- 约束遵守：未 kill/重启 bot；未触碰 `.ps1/.bat`；无破坏性操作；git 工作区从干净起点改码。
### 待办更新
- [x] 入口分级闸门（随口话极短即兴回、正经问题全力答）。
- [x] 真问题多方案结构化输出并按【方案N】拆条发送。
- [ ] 部署后观察线上：随口话走轻回应（无“思考中…”），推荐类问题出现多条【方案N】消息。
- [ ]（后续可选）白名单/意图词按真实语料微调；轻回应温度与 max_tokens 可用配置暴露。

## 2026-08-25：QQ 聊天护栏补全（注入勿念 / 禁复述与断言 / 时间权威）
### 背景
去人机味改造上线后，韩立仍有三个毛病：①跨天记忆/日报被整段背出当开场白（话痨复读）；②逐条复述用户原话、断言用户“重复提问”（幻觉重灾区），且把时间推算/纠错思考过程说出来；③拿用户消息、图片 OCR 里的时间数字当“当下”推断，产生时间幻觉。本改动纯 prompt/context 层补三条护栏，不动 graph 拓扑。
### 已完成
- `src/gacore/context.py`（仅此一个文件，改动 3 处）：
  - 新增 `_MEMORY_BG_RULE`（记忆背景铁律）：注入的记忆只是谈话背景，不主动背诵/复述/整段念出，不当开场白照搬，仅在对方问起或话题自然关联时引用一句。挂载点：`DAILY_HEADER` 日报注入后 + `ROLLOVER_HEADER` 昨日记忆注入后（两处共用）。
  - `_RESPONSE_LAYER_RULES`（回应分层铁律）追加硬规则三条：禁止逐条复述对方原话；禁止断言对方“重复提问”（无依据提这个即幻觉，一句不许说）；时间推算/纠错/查漏等脑内步骤不必宣之于口，直接给结论。
  - 新增 `_TIME_AUTHORITY_RULE`（时间铁律）：当前时刻以 system 注入的 `[Current time]` 为唯一依据，用户消息/图片 OCR/描述里的时间日期一律是内容陈述不作数，绝不据此推断“当下”，不解释推算过程。挂载点：`[Current time: …]` 之后恒注入。
### 验证
- `py_compile src/gacore/context.py` 退出码 0。
- venv 行为冒烟 PASS：构建 system prompt 后校验——[时间铁律]/[记忆背景铁律]/禁止逐条复述/禁止断言对方/思考过程不外说 5 组断言全部命中；本机日报存在时记忆铁律正确挂在日报注入后；无注入时该节不出现。
- 约束遵守：未 kill/重启 bot；未改 `.ps1/.bat`；无破坏性删除。
### 待办更新
- [x] 三条聊天护栏（注入勿念 / 禁复述·禁断言重复提问 / 时间权威）补全（本轮，纯 context 层）。

## 2026-08-25：上下文滑动窗口（根治开场复读一大段）
### 背景
唯一 thread 在 `gacore_chat.db` 已累计 236 个 checkpoint，`context.py::build_turn_prompt`（原第 211 行 `return [SystemMessage(content=prompt), *messages]`）把折叠摘要之外的**全量原始历史 messages** 原样发给模型，长会话下模型被前文惯性带跑、每轮开场先复读回顾一大段、token 成本持续膨胀。要求模型输入只保留最近几轮，更早靠折叠摘要兜底。
### 已完成
- `src/gacore/context.py`（仅此一个文件）：
  - 新增常量 `_KEEP_ROUNDS = 6`（保留最近 6 轮对话）。
  - 新增纯函数 `trim_messages(messages, keep_rounds=_KEEP_ROUNDS)`：从尾部按 `HumanMessage` 为轮次边界扫描，保留最近 `keep_rounds` 轮完整消息（其间 AIMessage/ToolMessage 配对一并保留），窗口起点恒为 HumanMessage、窗口内无孤儿 ToolMessage；短历史（≤keep_rounds 轮）原样返回；空 / 无 Human 列表安全返回空；不改入参（纯函数）。
  - `build_turn_prompt` 返回改为 `[SystemMessage(content=prompt), *trim_messages(messages)]`；`fold_history` 折叠摘要仍进 system prompt，不动。
  - 纯函数、不改 state、不动存储层——checkpoint 仍全量持久化（236 个 checkpoint 一个不删），仅「入模型时」截窗口。
### 验证
- `py_compile context.py` 退出码 0；miniconda py12 解释器（`D:\softwares\miniconda\envs\py12\python.exe`）`import gacore.context` 冒烟 OK（`_KEEP_ROUNDS=6`、`trim_messages` 存在）。
- 行为自测 PASS：
  - a) 20 轮 Human/AI/Tool 混合 47 条 → 截后 14 条、恰好 6 轮、首条为 HumanMessage、最近一轮 Human(Q19) 必在、输入未被修改（纯函数）、窗口内无孤儿 ToolMessage；
  - b) 短历史（<6 轮）原样返回；空列表 / 全 AI 列表安全处理；
  - c) `build_turn_prompt` 输出 = SystemMessage + 裁剪后消息（1+14），折叠摘要 `=== Earlier context ===` 仍在 system 中。
### 待办更新
- [x] 上下文滑动窗口：`build_turn_prompt` 只投最近 6 轮，更早靠折叠摘要兜底。
- [ ] 部署后观察线上：开场不再复读一大段历史；必要时把 `_KEEP_ROUNDS` 提为可配置项（config 侧）。
*（内容由AI生成，仅供参考）*

## 2026-08-25：发送端去重基准持久化（根治"回答叠一块"复读）
### 背景
用户发图反馈韩立"还是把回答都叠在一块发给我了"。链路：包装图 process→cleanup_images→END，`cleanup_images` 返回全量 messages（graph.py:165 的 `return {"messages": cleaned,...}`），发送层 `_stream_agent` 靠纯 RAM 的 `_rendered_msg_ids` 做"每条只发一次"去重。今天进程重启 4 次（20:47→21:50→22:11→22:34），内存集合被清空，重启后首个 turn 会把 checkpointer 中所有历史已发过的 AIMessage 重放进 `reply_parts`，与当轮回复 `\n\n` 拼成一条长消息发出（截图 = 上一轮"夜骑南京眼"方案回复 + 本轮"会错意"招呼，叠成一条）。
### 已完成
- `src/gacore/frontends/qq.py`（仅此一处）：
  - `_stream_agent` 开头先用 `graph.aget_state(config)` 读取本 thread checkpoint 中已存在的消息 id，批量播进 `_rendered_msg_ids[user_id]` 去重集作为基线；此后 `cleanup_images` 重放全量历史时，已发消息 id 命中基线直接跳过。
  - 纯增量：发送逻辑、【方案N】拆条逻辑、RAM 去重语义均不变；checkpoint 读取失败时降级为原行为，不阻塞聊天。
### 验证
- `py_compile qq.py` 退出码 0；
- 新 bot pid 2132（conda py12 + PYTHONPATH=src）23:29:52 ready："QQ bot ready: 韩立"，恢复单实例；旧 pid 13280 已停。
### 待办更新
- [x] 发送前播入 checkpoint 基线 id，杜绝进程重启后重放历史消息。
- [ ] 线上观察：下一条消息不再出现"历史回复 + 当轮回复"叠发。
*（内容由AI生成，仅供参考）*

## 2026-08-26：时间铁律升级为硬约束 + 新增 get_time 工具（焊死时间幻觉）
### 背景
上一轮 `_TIME_AUTHORITY_RULE`（时间铁律）为 prompt 级软约束：只认 `[Current time]` 为唯一依据，但未强制"涉及时间必须调工具"。模型仍可能凭记忆/上下文猜当下几点几分，时间幻觉难以根绝。本轮把铁律升级为"禁止"语气硬约束，并给 bot 挂一个权威时钟工具 `get_time`，让时间答案必须走系统时钟，杜绝凭记忆瞎猜当下时刻。
### 已完成
- `src/gacore/context.py`：升级 `_TIME_AUTHORITY_RULE`（时间铁律）文案——
  - 明确时间只允许两个权威来源：①已调用 `get_time` 工具返回的系统时间；②系统注入的 `[Current time]`。
  - 新增"禁止"语句：未经调用时间工具、也未读到系统注入时间时，**禁止**在回复里断言任何具体时钟读数（几点几分）。
  - 需回答"现在几点、今天几号、星期几、过了多久"等时间问题，**必须先调用 `get_time` 拿系统时间再作答**；工具不可用时以 `[Current time]` 为准。
  - 保留原约束：对方消息/图片 OCR/描述里的时间只是内容陈述不作数；不要解释推算过程、不展示推算步骤。
- `src/gacore/tools/get_time.py`（新增）：`@tool` 纯函数工具，返回当前系统时间（`YYYY-MM-DD 星期N HH:MM:SS (Asia/Shanghai, UTC+8)`），时区东八区 `timezone(timedelta(hours=8))`，无 I/O、无副作用、多行 docstring 面向模型说明"需要时间必须先调本工具"。
- `src/gacore/tools/__init__.py`：完成注册——import `get_time`、`TOOL_NAMES` 首位加 `"get_time"`、`_TOOLS` 首位加 `get_time`。注册链：`tools/__init__.py`（单一事实来源）→ `graph.py:177 build_tool_list(cfg)` → `create_agent(tools=...)`，bot 主回路模型真正可见。qq.py 轻回应分支走 `get_llm([], bind_tools=False)` 不挂工具，不受影响。
### 验证
- `py_compile` 三文件退出码 0（context.py / get_time.py / tools/__init__.py）。
- miniconda py12 冒烟 PASS：`_TIME_AUTHORITY_RULE` 已含"禁止、先调用 get_time 工具、两个权威来源"断言；`TOOL_NAMES` 含 `get_time`；`build_tool_list` 返回 27 个工具且含 `get_time`；实调 `get_time.invoke({})` 返回 `2026-08-26 星期三 00:12:33 (Asia/Shanghai, UTC+8)` 格式正确。
- 约束遵守：未 kill/重启 bot；脚本与工具文件纯 ASCII；无破坏性操作。
### 待办更新
- [x] 时间铁律升级为硬约束（禁止凭记忆断言时钟读数，时间问题先调 get_time）。
- [x] 新增并注册 `get_time` 权威时钟工具（tools/__init__.py 单一注册源）。
- [ ] 部署后观察线上：韩立对"几点/几号/星期几"的回答是否全部走 get_time、不再编时间。

## 2026-08-26：时间硬化 v2（入口短路 + 完整锚点 + 记忆历史标记）
### 背景
上一轮挂了 `get_time` 工具并把铁律升级成硬约束，但韩立仍会在部分路径（尤其轻回应分支）凭历史/记忆里的旧时刻乱报当下（如把昨晚 23:54 当当下 09:51）。本轮做三刀结构性硬化：
- P0：时间意图消息在进 LLM 之前，由代码直接短路秒回真实时钟；
- P1：每轮 system 时间锚升级为完整"日期+星期+时分秒+时区"，并挪到 prompt 末尾贴近用户消息，铁律追加"历史/记忆里的时间均为旧记录禁止当当下"；
- P2：每日笔记等记忆注入时给带时间戳内容打 `[历史@时间戳]` 标记，并声明仅供了解过往。
### 已完成
- `src/gacore/frontends/qq.py`：新增模块级 `_is_time_intent` / `_time_intent_answer` / `_stamp_memory_history` 等；`on_message` 在最前置（比 trivial_detect 更早，位于鉴权之后、rollover/LLM 之前）用正则命中"几点/几点钟/几点了/什么时间/今天几号/星期几/礼拜几/周几/过了多久/多久了"等时间意图，命中即 `datetime.now(_TZ_SH)` 拼口语答案原地秒回（含年月日+星期+时分秒+Asia/Shanghai UTC+8），完全不走 LLM/graph/get_time，复用 `send_text` 原回复通道。
- `src/gacore/context.py`：`build_system_prompt` 把中段单行 `[Current time: ...]` 移除，改为在 prompt 末尾（hints 之后、return 之前）拼接完整锚点块 `【当前真实时间】YYYY-MM-DD HH:MM:SS 星期X（Asia/Shanghai, UTC+8）` 并紧跟 `[历史时间禁令]`（对话历史/历史记忆/每日笔记/昨日记忆注入中的时间均为陈旧记录，严禁当当下）；`_TIME_AUTHORITY_RULE` 同步追加"历史/记忆时间均为陈旧记录"声明。
- `src/gacore/context.py`：`DAILY_HEADER` 改为明确"以下均为历史记录，仅供了解过往，绝不代表当前"；新增 `stamp_daily_history` 对含时间戳特征的行（`\d{1,2}[点时]`/日期格式）加 `[历史@时间戳]` 前缀。
- `src/gacore/frontends/qq.py`：`_trivial_reply` 轻回应分支的 ctx 同步升级为完整锚点（日期+星期+时分秒+时区）+ 历史时间禁令，daily 注入同样走 `_stamp_memory_history` 打历史标记。
### 验证
- py_compile context.py / qq.py 通过；miniconda py12 冒烟 PASS：
- P0：11 个时间意图用例判中期望值全对（正例"现在几点/几点啦/今天几号/星期几/礼拜几/今天周几/过了多久了/现在时间"，反例"帮我写个方案/今天天气怎么样/随便聊聊"）；`_time_intent_answer()` 返回 `现在是 2026年08月26日 星期三 10:11:30（Asia/Shanghai, UTC+8）`。
- P1：`build_system_prompt` 输出含 `【当前真实时间】` 完整锚点块且位于 prompt 末尾（hints 之后、return 之前），铁律与锚点禁令均含"陈旧记录/严禁当作当下时刻作答"声明。
- P2：`stamp_daily_history` 对"早上 9点 开会 / 昨天 2026-08-25 下午 / 8月24日 打球"正确加 `[历史@时间戳]` 前缀，无时间戳行保持原样。
- 未重启 bot，未改 `.ps1/.bat`。
- 补充（2026-08-26 后续）：**P0 入口短路已移除**——`qq.py` 中 `_TIME_INTENT_RE` / `_is_time_intent` / `_time_intent_answer` 及 `on_message` 最前置"秒回"块全部删除，时间类提问恢复走 trivial 闸门 + LLM 主流程，依托 P1 完整锚点与 `get_time` 工具由韩立自然作答；保留 P1（时间锚升级）与 P2（记忆历史标记）不动。`_TZ_SH` / `_WEEK_CN` / `_stamp_memory_history` 因仍被 `_trivial_reply` 引用而保留。
### 待办更新
- [x] P0 时间意图入口短路秒回真实时间（不走 LLM/graph）。
- [x] P1 完整时间锚点挪到 prompt 末尾 + 历史旧记录禁令。
- [x] P2 每日笔记/轻回应 daily 注入打历史标记。
- [ ] 部署后观察线上：时间问题是否一律即时回到真实时钟、轻回应分支不再报旧时刻。
