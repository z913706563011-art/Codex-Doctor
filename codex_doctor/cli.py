"""Command-line interface for Codex Doctor."""

import argparse
import sys
import zipfile
from pathlib import Path

from . import __version__
from .core import MIB, check, demo_report, recommendations
from .utils import SafetyError, archive_old, discover_paths


def render(report, *, demo=False):
    print("Codex 只读自检" + (" · 虚构数据" if demo else ""))
    for item in sorted(report.sessions, key=lambda item: (-item.size, str(item.path))):
        if item.level != "安全":
            print(f"{item.level} · {item.size/MIB:.2f} MiB · {item.path}")
    print("会话 · " + " · ".join(f"{level} {count}" for level, count in report.counts.items()))
    if report.state is not None:
        state = report.state
        percent = state.free_pages / max(1, state.pages)
        print(f"状态库 · {state.pages} 页 · 空闲 {state.free_pages} 页 · 空闲页率 {percent:.1%}")
        print(f"会话归属 · 未归属 {state.unassigned} · 关联失效 {state.broken}")
        for column, indexes in state.indexes.items():
            print(f"{column} 前导索引 · {', '.join(indexes) or '缺少'}")
    print(f"运行日志 · 主库 {report.log_bytes/MIB:.2f} MiB · WAL {report.log_wal_bytes/MIB:.2f} MiB")
    if report.old_bytes is not None:
        print(f"旧环境 · {report.old_bytes/MIB:.2f} MiB")
    for error in report.errors:
        print(f"检查未覆盖 · {error}")
    print("\n下一步建议")
    for action in recommendations(report):
        print(f"- {action.text}")


def parser():
    result = argparse.ArgumentParser(description="Windows 上的 Codex 本地诊断与备份")
    result.add_argument("--version", action="version", version=__version__)
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="只读自检（默认）")
    modes.add_argument("--archive", action="store_true", help="校验归档旧环境")
    modes.add_argument("--fix", action="store_true", help="保留入口，当前版本暂未开放自动修复")
    result.add_argument("--demo", action="store_true", help="用虚构数据预览自检报告")
    result.add_argument("--home", type=Path, help="当前 Codex 目录")
    result.add_argument("--old-home", type=Path, help="旧 Codex 目录")
    result.add_argument("--backup-dir", type=Path, help="备份目录")
    return result


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.demo:
        if args.fix or args.archive or any((args.home, args.old_home, args.backup_dir)):
            arguments.error("--demo 只能用于虚构的自检预览")
        render(demo_report(), demo=True)
        return 0
    if args.fix:
        print("自动修复尚未开放。可先运行 --check 并备份数据。", file=sys.stderr)
        return 2
    try:
        paths = discover_paths(args.home, args.old_home, args.backup_dir)
        if not args.archive:
            report = check(paths)
            render(report)
            return 1 if report.errors else 0
        print(f"归档来源 · {paths.old_home}\n备份目录 · {paths.backups}")
        print("请退出使用旧目录的程序。ZIP 包含旧配置和凭据，按本地未加密备份保存。")
        if not sys.stdin.isatty():
            raise SafetyError("归档需要在交互式终端里确认")
        if input("请输入大写 YES 确认归档 > ") != "YES":
            print("已取消归档。")
            return 0
        target = archive_old(paths)
        print(f"归档校验通过 · {target}")
        print("原目录仍在。遇到 ZIP 中的链接条目，恢复时需按链接目标重新建立。")
        return 0
    except (OSError, SafetyError, ValueError, zipfile.BadZipFile) as error:
        if getattr(error, "winerror", 0) in (32, 33):
            print("文件被占用。请退出相关客户端后重试。", file=sys.stderr)
        print(f"操作中止 · {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("已取消。", file=sys.stderr)
        return 130
