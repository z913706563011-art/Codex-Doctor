# Codex Doctor

Windows 上的 Codex 本地会话诊断与备份工具。报告末尾会说明现在可以继续使用、需要重试检查，还是值得安排维护。

**默认只读。** 无参数运行会执行 `--check`，只读取会话文件元数据和状态库。SQLite 查询在内存快照里执行。

**修复前备份。** 自动写入修复需要通过版本兼容性与恢复测试。v1.0.0 暂未开放自动修复。

## Demo

<!-- TODO: Replace this placeholder with a screenshot made from --check --demo.
     Example: ![Synthetic Codex Doctor report](docs/images/check-demo.png) -->

> Demo 截图占位。运行下方预览命令即可查看同一份虚构报告。

## 快速开始

需要 Python 3.12 或更新版本。检查、归档和测试使用标准库，从仓库根目录即可运行。

~~~powershell
python -B -m codex_doctor --check --demo
python -B -m codex_doctor --check
~~~

`--demo` 使用虚构数据。正常检查使用当前用户目录下的 `.codex`。可覆盖当前目录、旧目录和备份目录。

~~~powershell
python -B -m codex_doctor --check --home 'D:\Example\Codex'
python -B -m codex_doctor --archive --old-home 'D:\Example\OldCodex' --backup-dir 'D:\Example\Backups'
~~~

如果需要使用 `codex-doctor` 命令，可以先在虚拟环境里执行 `python -m pip install -e .`。

## 报告解读

| 项目 | 如何解读 |
| --- | --- |
| 会话大小 | 小于 5 MiB 为安全，5–15 MiB 为轻度，大于 15 MiB 为重度。 |
| SQLite 空闲页 | 表示数据库文件里可以复用的页。 |
| 索引和项目 | 列出更新时间前导索引，分别统计未归属和引用不存在项目的会话。 |
| 运行日志 | 主库加 WAL 达到 32 MiB 时提示评估压缩。 |
| 检查不完整 | 文件被锁定、读取期间持续变化、格式无法识别或快照超出 256 MiB 时，报告会提示处理原因后复查。 |

当前检查识别包含 `threads.project_id`、`projects.id` 和已知更新时间字段的 `state_5.sqlite` 结构。测试使用人工生成的 SQLite WAL 和会话文件。兼容性以实际检查结果为准。

`--check` 在报告完整时返回退出码 `0`，有未覆盖项目时返回 `1`。

## 归档

默认旧目录是当前 Codex 目录旁的同名 `_old` 目录，默认备份目录是用户主目录下的 `Codex_Backups`。在独立终端中执行 `--archive`，输入大写 `YES` 后开始归档。完成文件清单和内容校验后会生成 ZIP，并保留旧目录。

完整 ZIP 包含旧配置和凭据，请作为本地未加密备份保存。链接以目标信息存入 ZIP，恢复时需重新建立对应的链接。确认可恢复所需内容后，再考虑手动移除旧目录。

## 测试

使用 Python 自带的 `unittest`，无需安装 pytest。

~~~powershell
python -B -m unittest discover -s tests -v
~~~

测试使用虚构 JSON 场景和临时目录，覆盖建议生成、预览、SQLite WAL 只读检查和归档。

## 项目信息

独立社区项目。MIT License。公开问题、截图和测试样本使用虚构数据。
