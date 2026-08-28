# Hardening Round 2：全量缺陷修复计划（spec 011 + 实现计划）

## 目标

将上一轮审查确认的全部 34 项缺陷（4 高危 + 15 中危 + 15 低危）修复完毕并发布 v0.3.0。按仓库 Superpowers 工作流，执行开始时先落盘两份 agent 文档（英文），再逐任务 TDD 实施：

- Spec：`.agents/docs/specs/011-hardening-round-2.md`（需求与设计决策）
- Plan：`.agents/docs/plans/2026-08-28-011-hardening-round-2.md`（bite-sized 任务清单，含每步测试代码）

分支：`fix/hardening-round-2`（单目标分支，每任务一个 conventional commit）。

## 全局约束（每个任务隐含遵守）

- 包内导入全部相对；`_conf_schema.json` 保持 FLAT 格式、类型用 `int/bool/string/list`（禁 `integer/boolean`）
- `SQLModel.metadata.create_all` 必须传 `tables=_PLUGIN_TABLES`
- 代码/注释/commit 全英文；`docs/` 人档中文；`__init__` 不抛异常；单条消息不崩溃
- 每任务完成即跑 `uv run pytest -q` + `uv run ruff check .`；Rust 任务加 `cargo test` + `uv run maturin develop --release` 冒烟

## 任务分解（12 个任务）

### Task 1：时区统一（高危 #2/#3 + 低危 #8）
- `unified_chat/storage/repo.py:404,423,444`：`datetime.now()` → `datetime.now(UTC)`；`search_by_keyword`/`MemoryFts.search`/`get_by_ids` 的 `now=None` 默认值同改
- `MemoryFts.search`：`:now` 绑定改为预格式化 UTC 墙钟字符串（消除 sqlite3 弃用 adapter 依赖，115 条 DeprecationWarning 随之消失）
- `repo.py:488-521` `distinct_umos/distinct_group_umos`：naive datetime → `.replace(tzinfo=UTC).timestamp()`；并加显式排序（`distinct_umos` 按 max(created_at) DESC 供俚语挖掘取活跃会话；`distinct_group_umos` ASC 供主动开话取最沉默会话）
- `database.py` 连接事件加 `PRAGMA busy_timeout=5000`
- 测试：`test_repo.py` 用固定 aware datetime 与任意本机时区断言过期可见性、epoch 无偏移（含 UTC±8 等价性验证）

### Task 2：KB 迁移安全重构（高危 #1 + 中危卡死锁/结果丢弃/快照覆盖）
- `migration_service.py`：改为**逐文档**"删一个→传一个"，任一上传失败时用内存快照尽力回传原文档后再抛错（失败只影响单个文档而非清空 KB）
- running 标志存 `{"started": epoch}`，超过 6h 视为陈旧自动清除；`on_load` 时扫描删除全部 `migration:%:running` 残留（`kv.py` 新增 `kv_delete_prefix`）
- 失败也写 LearningLog + KV `migration:<kb>:last_result`；`get_status_async` 展示最近迁移结果；`_log_migration_done` 读取 `task.result()` 记日志
- embedding 快照：仅首次启动写入，之后仅在记忆 KB 迁移成功后更新（漂移信号不再被启动覆盖）
- 测试：上传中途失败→其余文档完好；陈旧标志恢复；结果持久化

### Task 3：过滤解耦与危险命令防护（高危 #4/#5 + 低危 L5/L6）
- `lifecycle.handle_message`：黑名单+屏蔽词预检提升到 `humanize_enable` 判断之外（新模块级 helper，与人化开关无关；命令文本豁免屏蔽词，与 gate 语义一致）；命中即 `stop_event` 不入库不学习
- `memory_service.forget_session`：`session_id` 为空时直接返回 0；`umem reset` 在会话隔离关闭时返回明确提示而非清空全局池；`repo.list_by_session/delete_by_session` 空串防护
- `HumanizeService.process`：仅真正产生回复的 reason 才 drain unreplied 缓存（`command`/`private`/`disabled` 不清）
- 测试：humanize 关闭时黑名单生效、隔离关闭 reset 安全、命令不丢缓存

### Task 4：数据留存与异步备份（中危 M4/M5）
- 新配置 `message_retention_days=90`、`learning_log_retention_days=30`（0=永久），四处同步：DEFAULTS/PluginConfig/from_dict/to_dict/_conf_schema.json
- `MessageRepo/LearningLogRepo` 各加 `delete_older_than(cutoff)`；每日 03:00 cron 追加清理步骤
- 备份改 `asyncio.to_thread`：`backup_service.daily_tick` 与 `/umem backup` 两处
- 测试：清理边界、配置钳制、schema/默认值一致性（同步更新 scaffold 测试）

### Task 5：人化竞态与缓存语义（中危 M1）
- `HumanizeService`：per-session `asyncio.Lock` 串行化 decide→air→commit（air await 期间不再被并发消息旁路，双回复概率归零）
- 测试：同会话并发两条消息仅一次 commit_reply

### Task 6：学习循环修复（中危 M2/M3/M7/M8 + 低危多项）
- 俚语：推断成功的词转 `inferred` 状态（不再每日重复调用 LLM，top-50 之外的词得以推进）；`/uslang list` 显示完整释义（去 `[:40]` 截断）并含 inferred；注入文本加引号包裹；CJK 单字不再成为候选（正则 `{1,}`→`{2,}`）
- `inject_composer`：mood/affinity 行优先、俚语行按预算逐行加入，不再尾部盲截
- 亲和度衰减改单条 SQL UPDATE（`score = 50 + (score-50)*0.9`，全表生效 + WHERE 过滤 <0.01 变化），移除 500 行上限与 read-modify-write
- `learning_jobs`/`proactive` 循环：`contextlib.suppress` 改为 try/except + `_log_error`
- `kv_set` 改 `INSERT ... ON CONFLICT(key) DO UPDATE` 原子 upsert（主动开话"先发送后记账"竞态随之消解）；`set_mood` 存 `str(round(scalar,6))`
- 测试：状态推进、预算保序、UPDATE 衰减、upsert 幂等

### Task 7：每消息全表扫描修复（中危 M2perf）
- `MemoryRepo.sender_stats(source, since)` SQL 聚合（COUNT+MAX）替代 `list_all()`；`compute_importance` 签名改为 `(content, freq, newest)`，同步更新单测
- 测试：频率/新近度统计与原实现等价

### Task 8：FTS 脱同步治理（中危 A7/A10）
- `index_add/index_remove` 失败记 warning（不再静默）；删除死代码 `MemoryRepo.delete_expired`
- `MemoryFts.reconcile()`：清理孤儿 FTS 行、补录缺失行；挂入每日 cron
- 测试：孤儿清除、缺失补录

### Task 9：生命周期与任务收尾（中危 C-M1 + 低危 L1/L6）
- `main.py`：仅当 `lifecycle._status == "loaded"` 才置 `_initialized`（半初始化不再伪装成功）
- `pipeline._log_done`/`lifecycle._log_migration_done`：先查 `task.cancelled()` 再取 exception；`_migration_tasks` 完成后修剪
- 测试：cancelled 任务回调不炸、失败加载后 `_initialized=False`

### Task 10：内存状态淘汰（低危 L2）
- gate/cache/chat_service/proactive/summarizer 各实现 `sweep()`（按各自 TTL 视野淘汰过期会话条目）；cron 每日调用（`sweep_targets` 注入）
- 测试：各组件 sweep 行为

### Task 11：原生扩展与杂项（低危 + Rust 构建两项）
- `Cargo.toml` 删除 `panic = "abort"`（panic 转 Python 异常而非杀进程）；`pyproject.toml` 删除 `target-cpu=x86-64-v3`（老 CPU SIGILL）；同步更新 AGENTS.md 对应描述；`cargo test` + `maturin develop` 冒烟
- fallback `chunk_text` 负参数行为对齐（抛 ValueError）；新增跨实现一致性测试（原生模块在场时逐向量对比 Rust vs fallback）
- `bootstrap.try_load_cached` 读取随二进制落盘的 `.sha256` 校验后再导入
- `path.py`：`StarTools.get_data_dir(plugin_name="astrbot_plugin_unified_chat")` 修复永不解析层；tier 4 目录名对齐
- `pipeline` 消息捕获去重按会话隔离（`exists_hash` 加 umo 参数）+ `(dedup_hash, umo)` 唯一部分索引（schema 版本 3→4，迁移含旧数据去重）；删除 `maybe_learn` 中死代码块
- `requirements.txt` 注释修正
- 测试：path 层单测、捕获隔离测试、schema 迁移测试

### Task 12：文档与 v0.3.0 发版准备
- `.agents/docs/plan.md` 追加 Phase 9（DONE 条目）；`docs/`（中文）更新 CONFIG.md 新配置、OPERATION.md 迁移行为、ARCHITECTURE 时区约定
- `metadata.yaml`/`pyproject.toml` 版本 → 0.3.0；全量验证（pytest + ruff + cargo test）；发版（push/PR）等你明确批准后才执行

## 执行方式

按 `subagent-driven-development`：每任务派发独立子代理（读取 plan 对应任务 + 两阶段审查），我在任务间把关；或 `executing-plans` 本会话内批量执行（执行开始时你可以再选）。每任务：先写失败测试 → 实现 → 通过 → commit。

低危中两项经评估**不修**并记入 spec 遗留清单：`database.py` 模块级 `asyncio.Lock`（单事件循环模型下无害，加注释说明）；`/umem reset` 管理员权限校验（AstrBot admin API 需运行时验证，列为后续独立任务）。
