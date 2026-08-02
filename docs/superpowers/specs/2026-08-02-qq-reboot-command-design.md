# 设计文档：QQ 前端 `/reboot` 指令（进程重启）

- 日期：2026-08-02
- 状态：已批准
- 项目：gacore（WithLangGraph）

## 背景与目标

开发者在修改 gacore 代码后，需要手动重启 `python.exe src/gacore/frontends/qq.py`
才能让改动生效。目标：在 QQ 前端新增 `/reboot` 指令，收到后重启整个进程——

- 新进程重新导入所有模块（改的代码立即生效）
- 所有运行时状态自然清空：`_user_threads`、`_pending_interrupt`、`_graph`、
  MemorySaver checkpoint（等价于 /new + 代码热载）

## 方案选择

| 方案 | 做法 | 结论 |
| :--- | :--- | :--- |
| **A. `os.execv` 进程重启** | 发确认消息 → 关闭单实例 socket → `os.execv(sys.executable, [sys.executable, __file__])` | **采用**：真·重启，无热重载残留 |
| B. `importlib.reload` 热重载 | 重载 gacore 模块 + 重建 graph | 弃：botpy client / 事件循环 / 中间件链热替换不可靠 |
| C. 退出 + 外部守护 | `sys.exit` 由 supervisor 拉起 | 弃：手动运行，无守护进程 |

## 权限设计

新增环境变量 `QQ_ADMIN_USERS`（逗号分隔 openid）：

- 留空 → **无人**能触发 `/reboot`（默认安全）
- `*` → 白名单（`QQ_ALLOWED_USERS`）内所有用户可触发
- 其他 → 逗号分隔的 openid 列表

不复用 `QQ_ALLOWED_USERS` 作为权限来源：允许名单可能是公开的（`*`），
任意用户都能重启 bot 是安全漏洞。

## 改动明细

### 1. `src/gacore/frontends/qq.py`

**a. config 段（~L70）**：新增 `_ADMIN_IDS` 解析，与 `_ALLOWED` 同模式。

**b. `_ensure_single_instance`（L554）**：socket 提升为模块级全局
`_instance_sock`，供 `/reboot` 释放端口。绑定成功即赋值。

**c. `_handle_command`（L402）**：新增 `user_id: str` 参数 + `/reboot` 分支：

```python
if op == "/reboot":
    if user_id not in _ADMIN_IDS:
        return await self.send_text(chat_id, "⛔ 无权限", msg_id=msg_id, is_group=is_group)
    await self.send_text(chat_id, "✅ 正在重启，请稍候...", msg_id=msg_id, is_group=is_group)
    await asyncio.sleep(0.5)
    if _instance_sock:
        _instance_sock.close()
    os.execv(sys.executable, [sys.executable, __file__])
```

**d. `on_message`（L339）**：`_handle_command` 调用处传入 `user_id=user_id`。

**e. `/help` 文案**：追加 `/reboot — 重启服务(管理员)`。

**f. 模块 docstring**：环境变量段补 `QQ_ADMIN_USERS` 说明。

### 2. `.env.example`

补 QQ 配置段（当前缺失，docstring 引用了它）：

```
# --- QQ Bot (gacore.frontends.qq) ---
QQ_APP_ID=
QQ_APP_SECRET=
QQ_ALLOWED_USERS=*
QQ_ADMIN_USERS=
QQ_LOG_FILE=logs/qq.log
```

### 3. `tests/test_qq.py`

沿用现有 fake-botpy + MagicMock + `patch.object` 模式，新增 3 用例：

- `test_reboot_denied_for_non_admin`：非管理员 → 回复"⛔ 无权限"，`os.execv` 未调用
- `test_reboot_denied_when_no_admin_configured`：`_ADMIN_IDS` 为空 → 拒绝
- `test_reboot_execs_new_process`：管理员 → 先发确认，`os.execv` 以
  `[sys.executable, __file__]` 调用（patch `qq.os.execv`、`qq._instance_sock`）

## 错误处理与风险

- **单实例竞态**：execv 前显式 `close()` 端口，防止新进程 `bind` 失败误判"已运行"。
- **确认消息丢失**：`await send_text` 完成后再 `sleep(0.5)` 再 execv，留出发送时间。
- **execv 失败**：`os.execv` 失败会抛 `OSError`，由 `on_message` 顶层 crash guard
  捕获记录（进程仍存活，端口已释放——可接受，仅短暂暴露多实例窗口）。
- **Windows 行为**：`os.execv` 替换当前进程镜像，Python 官方支持。
- **中断时长**：约数秒，bot 自动重连（`start()` 循环已有指数退避）。

## 不做的事（Out of Scope）

- CLI REPL（`cli.py`）不加 `/reboot`——用户当前工作流是 QQ 前端
- 不清理长期记忆文件（`memory/`）——重启只清运行时状态，与 /new 语义一致
- 不做优雅停服/等待 in-flight 任务——重启语义即"立即中断"
