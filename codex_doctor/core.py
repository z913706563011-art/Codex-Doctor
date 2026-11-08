"""Read-only local diagnostics and recommendation rules."""

import hashlib
import sqlite3
import struct
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .utils import Paths, SafetyError, entries

MIB = 1024 ** 2
HEAVY_LIMIT = 15 * MIB
LOG_LIMIT = 32 * MIB
SNAPSHOT_LIMIT = 256 * MIB
LEVELS = ("安全", "轻度", "重度")


@dataclass(frozen=True)
class SessionFile:
    path: Path
    size: int

    @property
    def level(self):
        if self.size > HEAVY_LIMIT:
            return "重度"
        return "轻度" if self.size >= 5 * MIB else "安全"


@dataclass(frozen=True)
class StateMetrics:
    pages: int
    free_pages: int
    indexes: dict[str, tuple[str, ...]]
    missing_indexes: tuple[str, ...]
    unassigned: int
    broken: int


@dataclass(frozen=True)
class CheckReport:
    sessions: tuple[SessionFile, ...]
    state: StateMetrics | None
    log_bytes: int
    log_wal_bytes: int
    old_bytes: int | None
    errors: tuple[str, ...]

    @property
    def counts(self):
        found = Counter(item.level for item in self.sessions)
        return {level: found[level] for level in LEVELS}


@dataclass(frozen=True)
class Advice:
    code: str
    text: str


def recommendations(report):
    if report.errors or report.state is None:
        return [Advice("retry", "先处理检查未覆盖的项目。遇到占用或文件持续变化时，退出客户端后重跑 --check。")]
    reasons = []
    counts = report.counts
    if counts["重度"]:
        reasons.append(f"{counts['重度']} 个会话超过 15 MiB")
    if report.state.missing_indexes:
        reasons.append("缺少更新时间索引")
    if report.state.broken:
        reasons.append(f"{report.state.broken} 条项目关联失效")
    if reasons:
        actions = [Advice("maintenance", "建议安排维护（" + "，".join(reasons) + "）。修复前先备份。")]
    else:
        actions = [Advice("healthy", "无需立即修复，可以继续使用。")]
    if counts["轻度"]:
        actions.append(Advice("observe", f"{counts['轻度']} 个轻度会话只需观察。"))
    if report.state.unassigned:
        actions.append(Advice("unassigned", "未归属项目的会话可以保持原样。"))
    if report.log_bytes + report.log_wal_bytes >= LOG_LIMIT:
        actions.append(Advice("logs", "日志库与 WAL 合计达到 32 MiB。如需回收空间，可先备份再评估压缩。"))
    if report.old_bytes:
        actions.append(Advice("archive", "确认当前旧会话可打开后，可用 --archive 备份旧环境。"))
    return actions


def demo_report():
    """A virtual report with no filesystem or database access."""
    sessions = tuple(SessionFile(Path(f"demo-{i:02}.jsonl"), MIB) for i in range(1, 18))
    indexes = {"updated_at_ms": ("example_updated_ms",), "updated_at": ("example_updated",)}
    return CheckReport(
        sessions + (SessionFile(Path("demo-large.jsonl"), 8 * MIB),),
        StateMetrics(81, 1, indexes, (), 18, 0), 7 * MIB, 4 * MIB, None, (),
    )


def _size(path):
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _side(path, suffix):
    return path.with_name(path.name + suffix)


def _file_digest(path):
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").digest()
    except FileNotFoundError:
        return None


def _read_pair(path):
    wal = _side(path, "-wal")
    journal = _side(path, "-journal")
    for item in (path, wal, journal):
        if item.is_symlink() or item.is_junction():
            raise SafetyError(f"数据库或辅助文件是链接 {item}")
    for _ in range(4):
        if _size(journal):
            raise SafetyError("发现未清理的回滚日志，请退出客户端后重试")
        if _size(path) + _size(wal) > SNAPSHOT_LIMIT:
            raise SafetyError("主库和 WAL 超出 256 MiB 的内存检查上限")
        base = path.read_bytes()
        try:
            log = wal.read_bytes()
        except FileNotFoundError:
            log = None
        base_hash = hashlib.sha256(base).digest()
        log_hash = hashlib.sha256(log).digest() if log is not None else None
        if (base_hash, log_hash) == (_file_digest(path), _file_digest(wal)) and not _size(journal):
            return base, log or b""
    raise SafetyError("无法获得稳定的主库与 WAL，请退出客户端后重试")


def _checksum(data, layout, pair=(0, 0)):
    a, b = pair
    for x, y in struct.iter_unpack(layout, data):
        a = (a + x + b) & 0xFFFFFFFF
        b = (b + y + a) & 0xFFFFFFFF
    return a, b


def _committed_image(base, wal):
    """Assemble a SQLite image from the stable main file and committed WAL pages."""
    if len(base) < 100 or base[:16] != b"SQLite format 3\0":
        raise SafetyError("SQLite 主库文件头无效")
    page_size = int.from_bytes(base[16:18], "big")
    if page_size == 1:
        page_size = 65536
    if not (512 <= page_size <= 65536) or page_size & (page_size - 1) or len(base) % page_size:
        raise SafetyError("SQLite 页大小或文件长度无效")
    pages = len(base) // page_size
    committed = {}
    if wal:
        if len(wal) < 32:
            raise SafetyError("WAL 文件头不完整")
        magic, version, wal_page_size = struct.unpack_from(">III", wal)
        if magic not in (0x377F0682, 0x377F0683) or version != 3007000 or wal_page_size != page_size:
            raise SafetyError("WAL 格式不兼容")
        layout = "<II" if magic == 0x377F0682 else ">II"
        running = _checksum(wal[:24], layout)
        if running != struct.unpack_from(">II", wal, 24):
            raise SafetyError("WAL 文件头校验失败")
        pending = {}
        salt = wal[16:24]
        stride = 24 + page_size
        for offset in range(32, len(wal) - stride + 1, stride):
            number, commit_pages = struct.unpack_from(">II", wal, offset)
            if not number or wal[offset + 8:offset + 16] != salt:
                break
            data = wal[offset + 24:offset + stride]
            running = _checksum(data, layout, _checksum(wal[offset:offset + 8], layout, running))
            if running != struct.unpack_from(">II", wal, offset + 16):
                break
            pending[number] = data
            if commit_pages:
                pages = commit_pages
                if pages * page_size > SNAPSHOT_LIMIT:
                    raise SafetyError("WAL 快照超出内存上限")
                committed.update(pending)
                committed = {number: value for number, value in committed.items() if number <= pages}
                pending.clear()
    extent = pages * page_size
    image = bytearray(base[:extent])
    image.extend(bytes(extent - len(image)))
    for number, data in committed.items():
        image[(number - 1) * page_size:number * page_size] = data
    image[18:20] = b"\x01\x01"
    struct.pack_into(">I", image, 28, pages)
    return bytes(image)


def _state_metrics(path):
    image = _committed_image(*_read_pair(path))
    with closing(sqlite3.connect(":memory:")) as db:
        db.deserialize(image)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA temp_store=MEMORY")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SafetyError("状态库完整性检查未通过")
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        fields = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
        project_ids = {row[0] for row in db.execute("SELECT id FROM projects")}
        links = [row[0] for row in db.execute("SELECT project_id FROM threads")]
        updated_columns = [col for col in ("updated_at_ms", "updated_at") if col in fields]
        if not updated_columns:
            raise SafetyError("状态库缺少已支持的更新时间字段")
        index_map = {col: [] for col in updated_columns}
        for _, name, _, _, partial in db.execute("PRAGMA index_list(threads)"):
            if partial:
                continue
            first = db.execute(
                "SELECT name FROM pragma_index_xinfo(?) WHERE key=1 ORDER BY seqno LIMIT 1", (name,),
            ).fetchone()
            if first and first[0] in index_map:
                index_map[first[0]].append(name)
        return StateMetrics(
            pages, free, {col: tuple(names) for col, names in index_map.items()},
            tuple(col for col, names in index_map.items() if not names),
            sum(not value for value in links),
            sum(bool(value) and value not in project_ids for value in links),
        )


def check(paths: Paths):
    errors = []
    sessions = []
    state = None
    old_bytes = None
    logs = wal = 0
    root = paths.home / "sessions"
    try:
        for path, info, kind in entries(root):
            if kind == "link":
                errors.append(f"跳过会话目录中的链接 {path}")
            elif kind == "file" and path.suffix.lower() == ".jsonl":
                sessions.append(SessionFile(path.relative_to(root), info.st_size))
    except (OSError, SafetyError) as error:
        errors.append(f"会话目录 {error}")
    try:
        state = _state_metrics(paths.home / "state_5.sqlite")
    except (OSError, sqlite3.Error, SafetyError) as error:
        errors.append(f"状态库 {error}")
    try:
        logs = _size(paths.home / "logs_2.sqlite")
        wal = _size(paths.home / "logs_2.sqlite-wal")
    except OSError as error:
        errors.append(f"日志库 {error}")
    try:
        if paths.old_home.exists():
            old_bytes = sum(info.st_size for _, info, kind in entries(paths.old_home) if kind == "file")
    except (OSError, SafetyError) as error:
        errors.append(f"旧环境 {error}")
    return CheckReport(tuple(sessions), state, logs, wal, old_bytes, tuple(errors))
