"""Path discovery and verified archival of an old Codex home."""

import datetime as dt
import hashlib
import os
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path


class SafetyError(Exception):
    """A safety precondition failed."""


@dataclass(frozen=True)
class Paths:
    home: Path
    old_home: Path
    backups: Path


def discover_paths(home=None, old_home=None, backups=None):
    """Resolve explicit overrides and use per-user defaults."""
    user = Path.home()
    home = Path(home or user / ".codex").expanduser().absolute()
    old_home = Path(old_home or home.with_name(home.name + "_old")).expanduser().absolute()
    backups = Path(backups or user / "Codex_Backups").expanduser().absolute()
    return Paths(home, old_home, backups)


def entries(root):
    """Yield (path, lstat, kind) in a stable order without following links."""
    stack = [root]
    while stack:
        path = stack.pop()
        info = path.lstat()
        if path.is_symlink() or path.is_junction():
            yield path, info, "link"
        elif getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise SafetyError(f"无法安全读取重解析点 {path}")
        elif stat.S_ISDIR(info.st_mode):
            yield path, info, "dir"
            stack.extend(sorted(path.iterdir(), reverse=True))
        elif stat.S_ISREG(info.st_mode):
            yield path, info, "file"
        else:
            raise SafetyError(f"不支持的文件类型 {path}")


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def _signature(records):
    return [(path, kind, info.st_size, info.st_mtime_ns, info.st_ino)
            for path, info, kind in records]


def archive_old(paths):
    """Write and verify a ZIP. The original directory is kept."""
    old = paths.old_home
    if old.is_symlink() or old.is_junction() or not old.is_dir():
        raise SafetyError(f"旧环境须为实际目录 {old}")
    if paths.backups.resolve().is_relative_to(old.resolve()):
        raise SafetyError("备份目录不能位于旧环境内")
    if paths.backups.resolve().is_relative_to(paths.home.resolve()):
        raise SafetyError("备份目录不能位于当前环境内")

    records = list(entries(old))
    paths.backups.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = f"Codex_Backup_{timestamp}_{uuid.uuid4().hex[:8]}.zip"
    target = paths.backups / name
    partial = target.with_suffix(".zip.part")
    expected = {}
    with partial.open("xb"):
        pass
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path, _, kind in records:
                member = (Path(old.name) / path.relative_to(old)).as_posix()
                if kind == "link":
                    value = os.readlink(path).encode("utf-8")
                    entry = zipfile.ZipInfo(member)
                    entry.create_system = 3
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                    output.writestr(entry, value)
                    expected[member] = hashlib.sha256(value).digest()
                    continue
                entry = zipfile.ZipInfo.from_file(path, member, strict_timestamps=False)
                if kind == "dir":
                    output.writestr(entry, b"")
                    expected[entry.filename] = hashlib.sha256(b"").digest()
                    continue
                entry.compress_type = zipfile.ZIP_DEFLATED
                expected[member] = _digest(path)
                with path.open("rb") as source, output.open(entry, "w", force_zip64=True) as destination:
                    shutil.copyfileobj(source, destination)
        with zipfile.ZipFile(partial) as result:
            if sorted(result.namelist()) != sorted(expected):
                raise SafetyError("归档文件清单校验失败")
            for member, expected_digest in expected.items():
                with result.open(member) as stream:
                    if hashlib.file_digest(stream, "sha256").digest() != expected_digest:
                        raise SafetyError(f"归档内容校验失败 {member}")
        if _signature(records) != _signature(list(entries(old))):
            raise SafetyError("归档时旧环境发生变化，请关闭相关程序后重试")
        if target.exists():
            raise FileExistsError(target)
        partial.rename(target)
        return target
    finally:
        partial.unlink(missing_ok=True)
