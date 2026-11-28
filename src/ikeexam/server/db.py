"""
数据层：SQLite
50 台规模用 SQLite 足够，开 WAL 模式提升并发写入能力
"""
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

BASE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(BASE)
PROJECT_ROOT = os.path.dirname(os.path.dirname(PACKAGE_DIR))
# 测试可用环境变量 EXAM_DB 指到临时库，不动正式库
DB_PATH = os.environ.get("EXAM_DB") or os.path.join(PROJECT_ROOT, "exam.db")

# 学生机状态
ONLINE_TIMEOUT = 15      # 超过 15 秒没心跳视为离线（心跳每 3 秒一次，约 5 次未到）
FINAL_SUBMIT_GRACE_SECONDS = 5 * 60
COMMAND_TTL_SECONDS = 24 * 3600
MAX_STUDENTS = max(100, int(os.environ.get("EXAM_MAX_STUDENTS", "2000")))
MAX_STUDENTS_PER_IP = max(20, int(os.environ.get("EXAM_MAX_STUDENTS_PER_IP", "200")))
LOG_MAX_ROWS = 5000
LOG_RETENTION_SECONDS = 30 * 24 * 3600
GRADING_QUEUE_CAPACITY = max(20, int(os.environ.get("EXAM_GRADING_QUEUE_CAPACITY", "500")))
GRADING_JOB_MAX_ATTEMPTS = max(1, int(os.environ.get("EXAM_GRADING_JOB_MAX_ATTEMPTS", "3")))
_log_writes = 0

# 考试编号的考点前缀：ikeexam-20260924-003
# 换考点/学校批量部署时用环境变量覆盖，不用改代码：
#   EXAM_CODE_PREFIX="bdfz" python app.py
EXAM_CODE_PREFIX = (os.environ.get("EXAM_CODE_PREFIX") or "ikeexam").strip() or "ikeexam"


# ==================== 连接管理 ====================
#
# 设计：全局单连接 + 可重入锁，所有数据库操作串行化。
#
# 为什么不是「每线程一个连接」：
#   实测在 50 并发下出现 database is locked / disk I/O error /
#   database disk image is malformed。SQLite 的多连接写并发在
#   部分文件系统（沙盒、某些 NAS/overlay）上不可靠，WAL 需要的
#   -shm 共享内存在这些环境也可能有问题。
#
# 为什么这样够用：
#   本机场景峰值约每秒十几个请求（50 台每 3 秒一次心跳），
#   单次操作 ~1ms，串行化后总耗时可以忽略。正确性 >> 这点性能。
#
# 为什么不用 WAL：
#   WAL 要靠 -shm 共享内存做并发控制，在不支持 mmap 的文件系统上
#   会报 disk I/O error。改用默认 rollback journal 最稳。
#   已实测：单连接 + DELETE journal，50 并发零错误。
_global_conn = None
_global_lock = threading.RLock()
_exam_cancel_lock = threading.Lock()
_exam_cancel_events = {}


class _Result:
    """
    execute 的结果快照（**在锁内就把行取完**）。

    ⚠️ 为什么要这样：连接是全局共享的。以前 execute() 返回的是活游标，
    fetchone()/fetchall() 在锁外跑 —— 多线程下别的线程会在这中间执行新 SQL，
    把游标顶掉，结果是：
        · 拿到 None（表现为 TypeError: 'NoneType' object is not subscriptable）
        · 更糟的是可能读到别的学生的行（成绩串号）
    50 并发下必现。现在行数据在锁内物化，之后谁再执行 SQL 都不影响这批结果。
    """
    __slots__ = ("_rows", "lastrowid", "rowcount", "description")

    def __init__(self, rows, lastrowid, rowcount, description):
        self._rows = rows
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self.description = description

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, n=1):
        return list(self._rows[:n])

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, i):
        return self._rows[i]


class _SafeConn:
    """
    包装真实连接：
      - execute / commit 等自动加锁，串行化所有操作
      - execute 返回**结果快照**（见 _Result 的说明）
      - close() 变成空操作（连接长期复用，避免反复开关的开销）
    这样上层 `c = conn() ... c.close()` 的写法一行都不用改。
    """
    __slots__ = ("_c",)

    def __init__(self, real):
        self._c = real

    def close(self):
        pass                                  # 故意不关

    def _locked(self, name):
        fn = getattr(self._c, name)
        def wrapper(*a, **kw):
            with _global_lock:
                return fn(*a, **kw)
        return wrapper

    def execute(self, *a, **kw):
        with _global_lock:
            cur = self._c.execute(*a, **kw)
            try:
                rows = cur.fetchall()         # 锁内取完，避免被别的线程顶掉
            except Exception:
                rows = []
            lastrowid = cur.lastrowid
            rowcount = cur.rowcount
            desc = cur.description
            try:
                cur.close()
            except Exception:
                pass
            return _Result(rows, lastrowid, rowcount, desc)

    def executemany(self, *a, **kw):
        with _global_lock:
            return self._c.executemany(*a, **kw)

    def executescript(self, *a, **kw):
        with _global_lock:
            return self._c.executescript(*a, **kw)

    def commit(self):
        with _global_lock:
            return self._c.commit()

    def rollback(self):
        with _global_lock:
            return self._c.rollback()

    def __getattr__(self, name):
        """其余属性（row_factory、cursor、total_changes…）直接转发"""
        return getattr(self._c, name)


def conn():
    """取全局连接（首次调用时创建）"""
    global _global_conn
    if _global_conn is None:
        with _global_lock:
            if _global_conn is None:
                real = sqlite3.connect(DB_PATH, timeout=30,
                                       check_same_thread=False)
                real.row_factory = sqlite3.Row       # 结果可 dict(row)
                real.execute("PRAGMA busy_timeout=30000")
                # 考试答卷必须落盘后再确认提交；关掉 fsync 会显著降低断电时的数据可靠性。
                real.execute("PRAGMA synchronous=FULL")
                # 明确用默认 rollback journal（不用 WAL，原因见上方注释）
                try:
                    real.execute("PRAGMA journal_mode=DELETE")
                except Exception:
                    pass
                _global_conn = _SafeConn(real)
    return _global_conn


@contextmanager
def _transaction():
    c = conn()
    with _global_lock:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def close_all():
    """程序退出时统一关闭（正常运行时不需要调用）"""
    global _global_conn
    with _global_lock:
        if _global_conn is not None:
            try:
                _global_conn._c.close()
            except Exception:
                pass
            _global_conn = None


def create_admin_session(sid, created_at, expires_at):
    with _transaction() as c:
        c.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (created_at,))
        c.execute("INSERT INTO admin_sessions(sid,created_at,expires_at) VALUES(?,?,?)",
                  (sid, created_at, expires_at))


def admin_session_active(sid, now):
    if not sid:
        return False
    row = conn().execute(
        "SELECT 1 FROM admin_sessions WHERE sid=? AND expires_at>? LIMIT 1",
        (sid, now)).fetchone()
    return row is not None


def revoke_admin_session(sid):
    if not sid:
        return
    with _transaction() as c:
        c.execute("DELETE FROM admin_sessions WHERE sid=?", (sid,))


def init():
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS exams (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT NOT NULL,
        etype       TEXT NOT NULL,          -- word / excel / ppt / mixed
        duration    INTEGER DEFAULT 60,     -- 实操时长（分钟）
        requirement TEXT DEFAULT '',        -- 实操考试要求（正文，下发给学生看）
        materials   TEXT DEFAULT '[]',      -- 素材文件名 JSON 数组
        save_as     TEXT DEFAULT '',        -- 要求保存的文件名，如 学号_姓名.docx
        checks      TEXT DEFAULT '[]',      -- 评分规则 JSON
        status      TEXT DEFAULT 'draft',   -- draft / running / paused / stopped
        phase       TEXT DEFAULT 'idle',    -- idle / running / ended（不再区分 theory/practice）
        theory_duration INTEGER DEFAULT 20, -- 理论题时长（分钟）
        question_types TEXT DEFAULT '[]',   -- JSON: 选中的题型 ["single","judge","blank","code","practice"]
        require_complete INTEGER DEFAULT 0, -- 1=必须全部做完才能提交
        passwordless INTEGER DEFAULT 0, -- 1=学生免填姓名座位，按 IP/主机识别
        code        TEXT DEFAULT '',        -- 考试编号，如 ikeexam-20260924-003（新建考试即生成）
        klass       TEXT DEFAULT '',        -- 班级 / 考场编号（老师开考时填，答卷按它归档）
        start_at    REAL,
        stop_at     REAL,
        phase_at    REAL,
        paused_at   REAL,                   -- 暂停时刻（恢复时用它把停掉的时长补回倒计时）
        created_at  REAL
    );

    CREATE TABLE IF NOT EXISTS students (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        hostname   TEXT UNIQUE NOT NULL,
        ip         TEXT,
        mac        TEXT,
        name       TEXT DEFAULT '',        -- 学生真实姓名（登录时填）
        seat       TEXT DEFAULT '',        -- 座位号/学号（登录时填）
        status     TEXT DEFAULT 'idle',     -- idle / working / submitted
        last_seen  REAL,
        exam_id    INTEGER,
        note       TEXT DEFAULT '',
        token_hash TEXT DEFAULT '',
        participant_id INTEGER DEFAULT 0,
        participant_exam_id INTEGER DEFAULT 0,
        progress_json TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS submissions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id  INTEGER,
        hostname    TEXT,
        exam_id     INTEGER,
        filename    TEXT,
        saved_path  TEXT,
        size        INTEGER,
        submitted_at REAL,
        score       REAL,
        full        REAL,
        detail      TEXT DEFAULT '',
        participant_id INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS commands (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        target     TEXT NOT NULL,      -- 'all' 或具体 hostname
        ctype      TEXT NOT NULL,      -- start_exam / collect / stop / message / open_app
        payload    TEXT DEFAULT '{}',  -- JSON
        created_at REAL,
        status     TEXT DEFAULT 'pending'   -- pending / sent
    );

    CREATE TABLE IF NOT EXISTS command_targets (
        command_id INTEGER NOT NULL,
        hostname   TEXT NOT NULL,
        acked_at   REAL,
        PRIMARY KEY(command_id, hostname)
    );

    CREATE TABLE IF NOT EXISTS logs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        hostname   TEXT,
        level      TEXT DEFAULT 'info',
        msg        TEXT,
        created_at REAL
    );

    CREATE TABLE IF NOT EXISTS admin_sessions (
        sid        TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_admin_sessions_expiry
        ON admin_sessions(expires_at);

    CREATE TABLE IF NOT EXISTS grading_jobs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        dedupe_key      TEXT NOT NULL UNIQUE,
        exam_id         INTEGER NOT NULL,
        submission_id   INTEGER NOT NULL,
        hostname        TEXT DEFAULT '',
        input_json      TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        attempts        INTEGER NOT NULL DEFAULT 0,
        max_attempts    INTEGER NOT NULL DEFAULT 3,
        next_attempt_at REAL NOT NULL,
        lease_owner     TEXT DEFAULT '',
        lease_token     TEXT DEFAULT '',
        lease_until     REAL NOT NULL DEFAULT 0,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at      REAL NOT NULL,
        updated_at      REAL NOT NULL,
        started_at      REAL,
        finished_at     REAL,
        last_error      TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_grading_pending
        ON grading_jobs(status,next_attempt_at);
    CREATE INDEX IF NOT EXISTS idx_grading_exam
        ON grading_jobs(exam_id,status);

    -- ===== 理论题库 =====
    CREATE TABLE IF NOT EXISTS questions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        qtype      TEXT NOT NULL,           -- single / multi / judge / blank
        stem       TEXT NOT NULL,           -- 题干
        options    TEXT DEFAULT '[]',       -- 选项 JSON，如 ["A.对","B.错"]
        answer     TEXT NOT NULL,           -- 正确答案；multi 用 "AB"；blank 用 "答案"
        score      REAL DEFAULT 2,
        category   TEXT DEFAULT '',         -- 分类，便于抽题
        language   TEXT DEFAULT '',         -- 程序填空的语言：python / cpp（其余题型留空）
        code       TEXT DEFAULT '',         -- 程序填空的代码模板，用 ____ 标记空
        stdin_data TEXT DEFAULT '',         -- 程序填空的运行输入（可选）
        owner_exam_id INTEGER DEFAULT 0,
        created_at REAL
    );

    -- ===== 实操题库（老师挑本堂课考什么）=====
    CREATE TABLE IF NOT EXISTS practice_questions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,          -- 题目名，如「期末成绩统计（基础）」
        etype      TEXT NOT NULL,          -- word / excel / ppt
        category   TEXT DEFAULT '',        -- 分类，如 二级·表格
        level      TEXT DEFAULT '',        -- 难度：基础 / 进阶
        materials  TEXT DEFAULT '[]',      -- 素材文件名 JSON 数组
        requirement TEXT DEFAULT '',       -- 考试要求（给学生看）
        save_as    TEXT DEFAULT '',
        checks     TEXT DEFAULT '[]',      -- 评分规则 JSON
        owner_exam_id INTEGER DEFAULT 0,
        created_at REAL
    );

    -- ===== 考试装了哪些实操题（一场可装多道）=====
    CREATE TABLE IF NOT EXISTS exam_practice (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id     INTEGER NOT NULL,
        practice_id INTEGER NOT NULL,
        qorder      INTEGER DEFAULT 0,
        snapshot_json TEXT,
        UNIQUE(exam_id, practice_id)
    );

    -- ===== 考试与题目的关联（组卷）=====
    CREATE TABLE IF NOT EXISTS exam_questions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id     INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        qorder      INTEGER DEFAULT 0,
        snapshot_json TEXT,
        UNIQUE(exam_id, question_id)
    );

    -- ===== 学生理论题作答 =====
    CREATE TABLE IF NOT EXISTS theory_answers (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id     INTEGER NOT NULL,
        hostname    TEXT NOT NULL,
        question_id INTEGER NOT NULL,
        answer      TEXT DEFAULT '',
        correct     INTEGER DEFAULT 0,
        got         REAL DEFAULT 0,
        answered_at REAL,
        participant_id INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 0,
        UNIQUE(exam_id, hostname, question_id, participant_id)
    );

    -- ===== 打字模块（小学）实时成绩：每个学生 × 每个子任务一行 =====
    CREATE TABLE IF NOT EXISTS typing_results (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id       INTEGER NOT NULL,
        hostname      TEXT NOT NULL,
        task          TEXT NOT NULL,       -- key_press / case_press / typing_en / typing_zh
        progress      INTEGER DEFAULT 0,   -- 已完成的轮数（按键题）
        total         INTEGER DEFAULT 0,   -- 总轮数 / 范文总字数
        hits          INTEGER DEFAULT 0,   -- 按对的轮数
        typed         INTEGER DEFAULT 0,   -- 已输入字符数（打字题）
        correct_chars INTEGER DEFAULT 0,   -- 其中正确的字符数
        elapsed_ms    INTEGER DEFAULT 0,   -- 已用时间（毫秒）
        wpm           REAL DEFAULT 0,      -- 速度：字/分钟
        accuracy      REAL DEFAULT 0,      -- 正确率 0~100
        reaction_ms   INTEGER DEFAULT 0,   -- 平均反应时间（按键题，毫秒）
        finished      INTEGER DEFAULT 0,   -- 该子任务是否完成
        attempt       INTEGER DEFAULT 0,   -- 「再练一次」递增，拒绝旧轮次延迟上报
        updated_at    REAL,
        participant_id INTEGER NOT NULL DEFAULT 0,
        UNIQUE(exam_id, hostname, task, participant_id)
    );

    -- ===== 画图作品（一张压缩图）：画板 / 拼豆各一份，重复上传以最新为准 =====
    -- kind: draw=画板创作（自由画）, beads=拼豆题（两道小题可同时考，各交各的）
    CREATE TABLE IF NOT EXISTS drawing_submissions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id    INTEGER NOT NULL,
        hostname   TEXT NOT NULL,
        kind       TEXT NOT NULL DEFAULT 'draw',
        filename   TEXT,
        saved_path TEXT,
        size       INTEGER DEFAULT 0,
        updated_at REAL,
        participant_id INTEGER NOT NULL DEFAULT 0,
        UNIQUE(exam_id, hostname, kind, participant_id)
    );

    -- ===== 考试参与名单（快照）=====
    -- 谁参与了这场考试，登录/作答那一刻就记下来（姓名座位是当时的快照）。
    -- 之后学生表被清掉、同一台机器换人登录，历史名单都不受影响。
    -- 按 (考试, 机器, 姓名) 一行：同一台机器换人考，两个人都在名单里。
    CREATE TABLE IF NOT EXISTS exam_participants (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id    INTEGER NOT NULL,
        hostname   TEXT NOT NULL,
        name       TEXT NOT NULL DEFAULT '',
        seat       TEXT NOT NULL DEFAULT '',
        ip         TEXT DEFAULT '',
        first_seen REAL,
        last_seen  REAL,
        status     TEXT DEFAULT 'idle',
        UNIQUE(exam_id, hostname, name, seat)
    );

    CREATE INDEX IF NOT EXISTS idx_cmd_status ON commands(status, target);
    CREATE INDEX IF NOT EXISTS idx_sub_exam   ON submissions(exam_id);

    -- ===== 本场学生名单（花名册）：学生端登录时必须在这个名单里 =====
    CREATE TABLE IF NOT EXISTS exam_roster (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id    INTEGER NOT NULL,
        seat       TEXT DEFAULT '',
        name       TEXT NOT NULL,
        ip         TEXT DEFAULT '',
        created_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_eq_exam    ON exam_questions(exam_id);
    CREATE INDEX IF NOT EXISTS idx_ta_exam    ON theory_answers(exam_id, hostname);
    CREATE INDEX IF NOT EXISTS idx_typing     ON typing_results(exam_id, hostname);
    CREATE INDEX IF NOT EXISTS idx_drawing    ON drawing_submissions(exam_id);
    """)
    c.commit()
    c.close()

    _migrate()          # 老库补列，重复执行无害


# ---------------- 通用 ----------------
def now():
    return time.time()


def log(hostname, level, msg):
    global _log_writes
    level = str(level or "info")[:20]
    if level not in ("debug", "info", "warn", "error"):
        level = "info"
    with _transaction() as c:
        c.execute("INSERT INTO logs(hostname,level,msg,created_at) VALUES(?,?,?,?)",
                  (str(hostname or "")[:128], level, str(msg or "")[:2000], now()))
        _log_writes += 1
        if _log_writes % 50 == 0:
            c.execute("DELETE FROM logs WHERE created_at<?", (now() - LOG_RETENTION_SECONDS,))
            c.execute("""DELETE FROM logs WHERE id NOT IN
                         (SELECT id FROM logs ORDER BY id DESC LIMIT ?)""", (LOG_MAX_ROWS,))


def get_logs(limit=200):
    c = conn()
    rows = c.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ---------------- 考试 ----------------
def _date_str(ts=None):
    """编号里的日期段 yyyymmdd（本机时区，考场的"当天"）"""
    return time.strftime("%Y%m%d", time.localtime(ts if ts else time.time()))


def _exam_code_seq(c, prefix, dstr):
    """当天已有编号里最大的序号 + 1（删掉几场也不会重号）"""
    rows = c.execute("SELECT code FROM exams WHERE code LIKE ?",
                     (f"{prefix}-{dstr}-%",)).fetchall()
    mx = 0
    for r in rows:
        try:
            n = int((r["code"] or "").rsplit("-", 1)[1])
        except (TypeError, ValueError, IndexError):
            continue
        mx = max(mx, n)
    return mx + 1


def make_exam_code(ts=None, c=None):
    """
    生成考试编号：{考点前缀}-{yyyymmdd}-{NNN}
    例：ikeexam-20260924-003（当天新建的第 3 场）

    ⚠️ 调用方若已持有 _global_lock（create_exam 就是这样），务必把连接传进来，
    否则"取号 → 插入"之间会被别的线程插队，两场考试撞同一个号。
    """
    own = c is None
    if own:
        c = conn()
    try:
        prefix = EXAM_CODE_PREFIX
        dstr = _date_str(ts)
        return f"{prefix}-{dstr}-{_exam_code_seq(c, prefix, dstr):03d}"
    finally:
        if own:
            c.close()


def create_exam(data):
    """
    新建考试。编号在这里生成（老师明确要求：新建就带编号，即使还没开考）。
    """
    c = conn()
    with _global_lock:          # 取号 + 插入必须原子，否则并发建考试会撞号
        eid = None
        for _ in range(3):      # 撞上唯一索引就重取一次号
            code = make_exam_code(c=c)
            try:
                cur = c.execute(
                    """INSERT INTO exams(title,etype,duration,theory_duration,requirement,
                       materials,save_as,checks,status,phase,require_complete,passwordless,
                       code,klass,created_at)
                       VALUES(?,?,?,?,?,?,?,?, 'draft', 'idle', ?, ?, ?, ?, ?)""",
                    (data["title"], data["etype"], int(data.get("duration", 60)),
                     int(data.get("theory_duration", 20)),
                     data.get("requirement", ""),
                     json.dumps(data.get("materials", []), ensure_ascii=False),
                     data.get("save_as", ""),
                     json.dumps(data.get("checks", []), ensure_ascii=False),
                     int(data.get("require_complete", 0)),
                     int(bool(data.get("passwordless", 0))),
                     code, (data.get("klass") or "").strip(), now()))
                eid = cur.lastrowid
                break
            except sqlite3.IntegrityError:
                continue
        c.commit()
    c.close()
    return eid


def set_exam_klass(eid, klass):
    """记录这场考试的班级/考场编号（老师开考时填，答卷按它归档）"""
    c = conn()
    c.execute("UPDATE exams SET klass=? WHERE id=?", ((klass or "").strip(), eid))
    c.commit()
    c.close()
    invalidate()


def update_exam(eid, data):
    """
    更新考试配置。

    ⚠️ 字段"传了才更新，没传就保留原值"：以前用 data.get(key, 默认值)，
    调用方只要漏传一个字段（比如 exam_autorule 没带 require_complete），
    就会把已有配置清成默认值。
    """
    ex = get_exam(eid) or {}

    def pick(key, cast, default):
        if key in data and data[key] is not None:
            return cast(data[key])
        return cast(ex.get(key, default)) if ex.get(key) is not None else cast(default)

    title = pick("title", str, "")
    etype = pick("etype", str, "")
    duration = pick("duration", int, 60)
    theory_duration = pick("theory_duration", int, 20)
    requirement = pick("requirement", str, "")
    save_as = pick("save_as", str, "")
    require_complete = pick("require_complete", int, 0)
    passwordless = pick("passwordless", int, 0)
    materials = data.get("materials") if "materials" in data \
        else (json.loads(ex.get("materials") or "[]") if ex else [])
    checks = data.get("checks") if "checks" in data \
        else (json.loads(ex.get("checks") or "[]") if ex else [])
    # 交卷结束语（学生交卷后看到的话）
    finish_note = pick("finish_note", str, "")

    c = conn()
    c.execute("""UPDATE exams SET title=?, etype=?, duration=?, theory_duration=?,
                 requirement=?, materials=?, save_as=?, checks=?, require_complete=?,
                 passwordless=?, finish_note=? WHERE id=?""",
              (title, etype, duration, theory_duration, requirement,
               json.dumps(materials or [], ensure_ascii=False), save_as,
               json.dumps(checks or [], ensure_ascii=False), require_complete,
               passwordless, finish_note, eid))
    c.commit()
    c.close()


def get_exam(eid):
    c = conn()
    r = c.execute("SELECT * FROM exams WHERE id=?", (eid,)).fetchone()
    c.close()
    return dict(r) if r else None


def list_exams():
    c = conn()
    rows = c.execute("SELECT * FROM exams ORDER BY id DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


def exam_cancel_event(exam_id):
    with _exam_cancel_lock:
        return _exam_cancel_events.setdefault(int(exam_id), threading.Event())


def cancel_exam_judging(exam_id):
    event = exam_cancel_event(exam_id)
    event.set()
    with _exam_cancel_lock:
        _exam_cancel_events.pop(int(exam_id), None)


def set_exam_status(eid, status):
    c = conn()
    with _global_lock:
        if status == "running":
            c.execute("""UPDATE exams SET
                         status='running',
                         phase_at=CASE WHEN status='paused'
                             THEN COALESCE(phase_at, ?)+COALESCE(?-paused_at, 0)
                             ELSE ? END,
                         start_at=CASE WHEN status='paused'
                             THEN COALESCE(start_at, ?)+COALESCE(?-paused_at, 0)
                             ELSE ? END,
                         paused_at=NULL
                         WHERE id=? AND status IN ('draft','paused')""",
                      (now(), now(), now(), now(), now(), now(), eid))
        elif status == "paused":
            c.execute("""UPDATE exams SET status='paused', paused_at=?
                         WHERE id=? AND status='running'""", (now(), eid))
        elif status == "stopped":
            c.execute("""UPDATE exams SET status='stopped', stop_at=?
                         WHERE id=? AND status IN ('running','paused')""", (now(), eid))
        else:
            c.execute("UPDATE exams SET status=? WHERE id=? AND status='draft'",
                      (status, eid))
        c.commit()
    c.close()


def start_exam(eid, klass, passwordless=False):
    with _transaction() as c:
        ex = c.execute("SELECT * FROM exams WHERE id=?", (eid,)).fetchone()
        if not ex or ex["status"] != "draft":
            return False, "考试状态已变化，请刷新后重试"
        other = c.execute("""SELECT id FROM exams
                             WHERE status IN ('running','paused') AND id<>?
                             LIMIT 1""", (eid,)).fetchone()
        if other:
            return False, "请先结束其他考试"
        ts = now()
        c.execute("""UPDATE exams SET klass=?, passwordless=?, status='running',
                     phase='theory', start_at=?, phase_at=?, paused_at=NULL
                     WHERE id=? AND status='draft'""",
                  ((klass or "").strip(), int(bool(passwordless)), ts, ts, eid))
        c.execute("UPDATE students SET exam_id=?, status='idle', participant_id=0, participant_exam_id=0 WHERE 1=1",
                  (eid,))
        _clear_exam_commands(c)
        _push_command(c, "all", "start_exam", {"exam_id": eid, "phase": "theory"})
    invalidate()
    return True, ""


def pause_exam(eid):
    with _transaction() as c:
        cur = c.execute("""UPDATE exams SET status='paused', paused_at=?
                           WHERE id=? AND status='running'""", (now(), eid))
        if not cur.rowcount:
            return False, "只有进行中的考试可以暂停"
        _push_command(c, "all", "pause", {"exam_id": eid})
    invalidate()
    return True, ""


def resume_exam(eid):
    with _transaction() as c:
        ts = now()
        cur = c.execute("""UPDATE exams SET
                             status='running',
                             phase_at=COALESCE(phase_at, ?)+COALESCE(?-paused_at, 0),
                             start_at=COALESCE(start_at, ?)+COALESCE(?-paused_at, 0),
                             paused_at=NULL
                           WHERE id=? AND status='paused' AND paused_at IS NOT NULL""",
                        (ts, ts, ts, ts, eid))
        if not cur.rowcount:
            return False, "考试当前未暂停"
        _push_command(c, "all", "resume", {"exam_id": eid})
    invalidate()
    return True, ""


def transition_phase(eid, phase, payload=None):
    with _transaction() as c:
        cur = c.execute("""UPDATE exams SET phase=?, phase_at=?
                           WHERE id=? AND status IN ('running','paused')""",
                        (phase, now(), eid))
        if not cur.rowcount:
            return False, "考试未在进行"
        command_payload = dict(payload or {})
        command_payload.update(exam_id=eid, phase=phase)
        _push_command(c, "all", "phase", command_payload)
    invalidate()
    return True, ""


def stop_exam(eid, clear_login=False):
    with _transaction() as c:
        cur = c.execute("""UPDATE exams SET status='stopped', phase='ended',
                             stop_at=?, paused_at=NULL
                           WHERE id=? AND status IN ('running','paused')""",
                        (now(), eid))
        if not cur.rowcount:
            return False, "考试未在进行"
        c.execute("""UPDATE grading_jobs SET status='cancelled', updated_at=?,
                     finished_at=? WHERE exam_id=? AND status='pending'""",
                  (now(), now(), eid))
        students = c.execute("""SELECT hostname,name,seat,ip FROM students
                                WHERE exam_id=?""", (eid,)).fetchall()
        for student in students:
            if (student["name"] or "").strip():
                _add_participant(c, eid, student["hostname"], student["name"],
                                 student["seat"], student["ip"])
        _clear_exam_commands(c)
        _push_command(c, "all", "force_exit",
                      {"exam_id": eid, "clear_login": bool(clear_login)})
        _push_command(c, "all", "stop", {"exam_id": eid})
    cancel_exam_judging(eid)
    invalidate()
    return True, ""


def active_exam():
    """当前正在进行的考试（含暂停）"""
    c = conn()
    r = c.execute("SELECT * FROM exams WHERE status IN ('running','paused') ORDER BY start_at DESC LIMIT 1").fetchone()
    c.close()
    return dict(r) if r else None


# ---------------- 学生 ----------------
def _migrate():
    """老数据库补列 / 补索引，重复执行无害"""
    c = conn()

    # students 表
    cols = [r[1] for r in c.execute("PRAGMA table_info(students)").fetchall()]
    for col, ddl in (("name", "ALTER TABLE students ADD COLUMN name TEXT DEFAULT ''"),
                     ("seat", "ALTER TABLE students ADD COLUMN seat TEXT DEFAULT ''"),
                     ("token_hash", "ALTER TABLE students ADD COLUMN token_hash TEXT DEFAULT ''"),
                     ("participant_id", "ALTER TABLE students ADD COLUMN participant_id INTEGER DEFAULT 0"),
                     ("participant_exam_id", "ALTER TABLE students ADD COLUMN participant_exam_id INTEGER DEFAULT 0"),
                     ("progress_json", "ALTER TABLE students ADD COLUMN progress_json TEXT DEFAULT '{}'")):
        if col not in cols:
            try:
                c.execute(ddl)
            except Exception:
                pass

    # 本场名单支持 IP（免密码考试按 IP 识别）
    rcols = [r[1] for r in c.execute("PRAGMA table_info(exam_roster)").fetchall()]
    if "ip" not in rcols:
        try:
            c.execute("ALTER TABLE exam_roster ADD COLUMN ip TEXT DEFAULT ''")
        except Exception:
            pass
    try:
        c.execute("DROP INDEX IF EXISTS idx_roster")
        c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_roster_named
                     ON exam_roster(exam_id, name, seat)
                     WHERE ip='' OR ip IS NULL""")
        c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_roster_ip
                     ON exam_roster(exam_id, ip) WHERE ip<>''""")
    except Exception:
        pass

    # questions 表
    qcols = [r[1] for r in c.execute("PRAGMA table_info(questions)").fetchall()]
    for col, ddl in (("language", "ALTER TABLE questions ADD COLUMN language TEXT DEFAULT ''"),
                     ("code", "ALTER TABLE questions ADD COLUMN code TEXT DEFAULT ''"),
                     ("stdin_data", "ALTER TABLE questions ADD COLUMN stdin_data TEXT DEFAULT ''"),
                     # 程序填空的「期望输出」：填了就按运行结果判分（见 coderun.py）
                     ("expected_output", "ALTER TABLE questions ADD COLUMN expected_output TEXT DEFAULT ''"),
                     # 题库分类：难度（基础/进阶/挑战）+ 来源（自创题 / 2024年雅安会考 …）
                     ("difficulty", "ALTER TABLE questions ADD COLUMN difficulty TEXT DEFAULT ''"),
                     ("source", "ALTER TABLE questions ADD COLUMN source TEXT DEFAULT ''"),
                     # 拖拽题的槽位定义（JSON），程序填空不用它
                     ("payload", "ALTER TABLE questions ADD COLUMN payload TEXT DEFAULT ''"),
                     ("owner_exam_id", "ALTER TABLE questions ADD COLUMN owner_exam_id INTEGER DEFAULT 0")):
        if col not in qcols:
            try:
                c.execute(ddl)
            except Exception:
                pass
    # 题库筛选用（分类/难度/来源/题型）
    for name, ddl in (
        ("idx_q_cat", "CREATE INDEX IF NOT EXISTS idx_q_cat ON questions(category)"),
        ("idx_q_type", "CREATE INDEX IF NOT EXISTS idx_q_type ON questions(qtype)"),
        ("idx_q_diff", "CREATE INDEX IF NOT EXISTS idx_q_diff ON questions(difficulty)"),
    ):
        try:
            c.execute(ddl)
        except Exception:
            pass

    pcols = [r[1] for r in c.execute("PRAGMA table_info(practice_questions)").fetchall()]
    if "owner_exam_id" not in pcols:
        try:
            c.execute("ALTER TABLE practice_questions ADD COLUMN owner_exam_id INTEGER DEFAULT 0")
        except Exception:
            pass

    for assoc, source, fk in (("exam_questions", "questions", "question_id"),
                              ("exam_practice", "practice_questions", "practice_id")):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({assoc})").fetchall()]
        if "snapshot_json" not in cols:
            try:
                c.execute(f"ALTER TABLE {assoc} ADD COLUMN snapshot_json TEXT")
            except Exception:
                pass
        rows = c.execute(
            f"SELECT a.id AS assoc_id, s.* FROM {assoc} a "
            f"JOIN {source} s ON s.id=a.{fk} "
            "WHERE a.snapshot_json IS NULL OR a.snapshot_json=''"
        ).fetchall()
        for row in rows:
            snap = dict(row)
            snap.pop("assoc_id", None)
            c.execute(f"UPDATE {assoc} SET snapshot_json=? WHERE id=?",
                      (json.dumps(snap, ensure_ascii=False, default=str), row["assoc_id"]))
    c.commit()

    # exams 表：加 question_types 列
    ecols = [r[1] for r in c.execute("PRAGMA table_info(exams)").fetchall()]
    if "question_types" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN question_types TEXT DEFAULT '[]'")
        except Exception:
            pass
    if "require_complete" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN require_complete INTEGER DEFAULT 0")
        except Exception:
            pass
    if "passwordless" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN passwordless INTEGER DEFAULT 0")
        except Exception:
            pass
    # 考试编号 / 班级（开考时填，答卷按班级归档）
    if "code" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN code TEXT DEFAULT ''")
        except Exception:
            pass
    if "klass" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN klass TEXT DEFAULT ''")
        except Exception:
            pass
    # 暂停时刻（老库补列）：暂停期间倒计时不该照走
    if "paused_at" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN paused_at REAL")
        except Exception:
            pass
    # 打字模块（小学）的每场配置：轮数/每轮秒数/打字范文等，JSON
    if "typing_config" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN typing_config TEXT DEFAULT ''")
        except Exception:
            pass
    # 交卷结束语：学生点「交卷」后显示的话（做完 / 没做完都显示，老师自己写）
    if "finish_note" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN finish_note TEXT DEFAULT ''")
        except Exception:
            pass
    # 画板创作的每场配置（JSON：{"brief": "老师写的画题要求"}）
    if "drawing_config" not in ecols:
        try:
            c.execute("ALTER TABLE exams ADD COLUMN drawing_config TEXT DEFAULT ''")
        except Exception:
            pass
    # 参与名单不做事后回填 —— 回填只能猜名字（拿学生表当前的姓名往
    # 老考试里填），会把历史名单「覆盖」成别人。名单在参与那一刻实时记，
    # 老数据靠 full_report 的证据兜底显示，不落库、不猜。
    # 老考试补编号：按创建日期分组，同一天按创建顺序给 001、002…
    # （只对 code 为空的动手，幂等）
    try:
        old = c.execute("""SELECT id, created_at FROM exams
                           WHERE code IS NULL OR code=''
                           ORDER BY created_at, id""").fetchall()
        seq_by_date = {}
        for r in old:
            dstr = _date_str(r["created_at"])
            if dstr not in seq_by_date:
                seq_by_date[dstr] = _exam_code_seq(c, EXAM_CODE_PREFIX, dstr)
            c.execute("UPDATE exams SET code=? WHERE id=?",
                      (f"{EXAM_CODE_PREFIX}-{dstr}-{seq_by_date[dstr]:03d}",
                       r["id"]))
            seq_by_date[dstr] += 1
    except Exception:
        pass
    # 编号唯一（空编号按部分索引放过），防止并发建考试撞号
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_exam_code "
                  "ON exams(code) WHERE code != ''")
    except Exception:
        pass

    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_students_seat ON students(seat)")
    except Exception:
        pass
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_students_name ON students(name)")
    except Exception:
        pass
    # 心跳里"这个学生交过卷吗"每 3 秒查一次 × 50 台，加复合索引
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_sub_host "
                  "ON submissions(exam_id, hostname)")
    except Exception:
        pass
    # 一场考试可以装多道操作题：提交记录要区分是哪一道
    try:
        c.execute("ALTER TABLE submissions ADD COLUMN task_id INTEGER DEFAULT 0")
    except Exception:
        pass
    # 画板作品拆 kind（draw=画板 / beads=拼豆，两道小题各交一份）：
    # 旧表 UNIQUE(exam_id, hostname) 改不了约束，建新表搬数据再换名
    dcols = [r[1] for r in c.execute(
        "PRAGMA table_info(drawing_submissions)").fetchall()]
    if "kind" not in dcols:
        try:
            c.execute("""CREATE TABLE drawing_submissions_new (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_id    INTEGER NOT NULL,
                hostname   TEXT NOT NULL,
                kind       TEXT NOT NULL DEFAULT 'draw',
                filename   TEXT,
                saved_path TEXT,
                size       INTEGER DEFAULT 0,
                updated_at REAL,
                UNIQUE(exam_id, hostname, kind))""")
            c.execute("""INSERT INTO drawing_submissions_new
                         (exam_id, hostname, kind, filename, saved_path,
                          size, updated_at)
                         SELECT exam_id, hostname, 'draw', filename, saved_path,
                                size, updated_at FROM drawing_submissions""")
            c.execute("DROP TABLE drawing_submissions")
            c.execute("ALTER TABLE drawing_submissions_new "
                      "RENAME TO drawing_submissions")
        except Exception:
            pass

    # 成绩按“本场参与者”归属，不再只按机器归属。老记录 participant_id=0，
    # 报告里作为历史数据回退到该机器，不会被凭空安到某个姓名头上。
    sub_cols = [r[1] for r in c.execute("PRAGMA table_info(submissions)").fetchall()]
    if "participant_id" not in sub_cols:
        try:
            c.execute("ALTER TABLE submissions ADD COLUMN participant_id INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass

    def _rebuild_participant_table(table, create_sql, copy_columns, unique_columns):
        if isinstance(copy_columns, str):
            copy_columns = [x.strip() for x in copy_columns.split(",") if x.strip()]
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
        unique_sets = []
        for idx in c.execute(f"PRAGMA index_list({table})").fetchall():
            if not idx["unique"]:
                continue
            name = idx["name"].replace("'", "''")
            unique_sets.append({r["name"] for r in c.execute(f"PRAGMA index_info('{name}')")})
        if set(unique_columns) in unique_sets:
            return
        c.execute(f"SAVEPOINT migrate_{table}")
        try:
            c.execute(f"DROP TABLE IF EXISTS {table}_participant_new")
            c.execute(create_sql.format(table=f"{table}_participant_new"))
            available = [col for col in copy_columns if col in cols]
            selected = ", ".join(available)
            pid_expr = "participant_id" if "participant_id" in cols else "0"
            c.execute(f"INSERT INTO {table}_participant_new ({selected}, participant_id) "
                      f"SELECT {selected}, {pid_expr} FROM {table}")
            c.execute(f"DROP TABLE {table}")
            c.execute(f"ALTER TABLE {table}_participant_new RENAME TO {table}")
        except Exception:
            c.execute(f"ROLLBACK TO SAVEPOINT migrate_{table}")
            logging.exception("参与者表结构迁移失败: %s", table)
        finally:
            c.execute(f"RELEASE SAVEPOINT migrate_{table}")

    _rebuild_participant_table(
        "theory_answers",
        """CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL, hostname TEXT NOT NULL,
            question_id INTEGER NOT NULL, answer TEXT DEFAULT '',
            correct INTEGER DEFAULT 0, got REAL DEFAULT 0, answered_at REAL,
            participant_id INTEGER NOT NULL DEFAULT 0,
            UNIQUE(exam_id, hostname, question_id, participant_id))""",
        "id, exam_id, hostname, question_id, answer, correct, got, answered_at",
        ("exam_id", "hostname", "question_id", "participant_id"))
    _rebuild_participant_table(
        "typing_results",
        """CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL, hostname TEXT NOT NULL, task TEXT NOT NULL,
            progress INTEGER DEFAULT 0, total INTEGER DEFAULT 0, hits INTEGER DEFAULT 0,
            typed INTEGER DEFAULT 0, correct_chars INTEGER DEFAULT 0,
            elapsed_ms INTEGER DEFAULT 0, wpm REAL DEFAULT 0, accuracy REAL DEFAULT 0,
            reaction_ms INTEGER DEFAULT 0, finished INTEGER DEFAULT 0, updated_at REAL,
            attempt INTEGER NOT NULL DEFAULT 0,
            participant_id INTEGER NOT NULL DEFAULT 0,
            UNIQUE(exam_id, hostname, task, participant_id))""",
        "id, exam_id, hostname, task, progress, total, hits, typed, correct_chars, "
        "elapsed_ms, wpm, accuracy, reaction_ms, finished, updated_at",
        ("exam_id", "hostname", "task", "participant_id"))
    tcols = [r[1] for r in c.execute("PRAGMA table_info(typing_results)").fetchall()]
    if "attempt" not in tcols:
        try:
            c.execute("ALTER TABLE typing_results ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")
        except Exception:
            logging.exception("打字成绩表版本列迁移失败")
    _rebuild_participant_table(
        "drawing_submissions",
        """CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL, hostname TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'draw', filename TEXT, saved_path TEXT,
            size INTEGER DEFAULT 0, updated_at REAL,
            participant_id INTEGER NOT NULL DEFAULT 0,
            UNIQUE(exam_id, hostname, kind, participant_id))""",
        "id, exam_id, hostname, kind, filename, saved_path, size, updated_at",
        ("exam_id", "hostname", "kind", "participant_id"))

    pcols = [r[1] for r in c.execute("PRAGMA table_info(exam_participants)").fetchall()]
    if "status" not in pcols:
        try:
            c.execute("ALTER TABLE exam_participants ADD COLUMN status TEXT DEFAULT 'idle'")
        except Exception:
            pass
    participant_uniques = []
    for idx in c.execute("PRAGMA index_list(exam_participants)").fetchall():
        if idx["unique"]:
            index_name = str(idx["name"]).replace("'", "''")
            participant_uniques.append({r["name"] for r in c.execute(
                f"PRAGMA index_info('{index_name}')").fetchall()})
    if {"exam_id", "hostname", "name", "seat"} not in participant_uniques:
        c.execute("SAVEPOINT migrate_participant_identity")
        try:
            c.execute("DROP TABLE IF EXISTS exam_participants_identity_new")
            c.execute("""CREATE TABLE exam_participants_identity_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_id INTEGER NOT NULL, hostname TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '', seat TEXT NOT NULL DEFAULT '',
                ip TEXT DEFAULT '', first_seen REAL, last_seen REAL,
                status TEXT DEFAULT 'idle',
                UNIQUE(exam_id, hostname, name, seat))""")
            c.execute("""INSERT OR IGNORE INTO exam_participants_identity_new
                         (id,exam_id,hostname,name,seat,ip,first_seen,last_seen,status)
                         SELECT id,exam_id,hostname,name,seat,ip,first_seen,last_seen,
                                COALESCE(status,'idle') FROM exam_participants""")
            c.execute("DROP TABLE exam_participants")
            c.execute("ALTER TABLE exam_participants_identity_new RENAME TO exam_participants")
        except Exception:
            c.execute("ROLLBACK TO SAVEPOINT migrate_participant_identity")
            logging.exception("参与者身份结构迁移失败")
        finally:
            c.execute("RELEASE SAVEPOINT migrate_participant_identity")
    ta_cols = [r[1] for r in c.execute("PRAGMA table_info(theory_answers)").fetchall()]
    if "version" not in ta_cols:
        try:
            c.execute("ALTER TABLE theory_answers ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
    ccols = [r[1] for r in c.execute("PRAGMA table_info(commands)").fetchall()]
    if "acked_at" not in ccols:
        try:
            c.execute("ALTER TABLE commands ADD COLUMN acked_at REAL")
        except Exception:
            pass
    # 旧命令没有逐机器目标表，迁移时按当时登记机器补齐；sent 视为待确认重新下发。
    try:
        c.execute("UPDATE commands SET status='pending' WHERE status='sent'")
        hosts = [r["hostname"] for r in c.execute("SELECT hostname FROM students").fetchall()]
        for cmd in c.execute("SELECT id,target FROM commands WHERE status='pending'").fetchall():
            targets = hosts if cmd["target"] == "all" else [cmd["target"]]
            for host in targets:
                c.execute("INSERT OR IGNORE INTO command_targets(command_id,hostname) VALUES(?,?)",
                          (cmd["id"], host))
    except Exception:
        pass
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_ta_participant "
                  "ON theory_answers(exam_id, participant_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_typing_participant "
                  "ON typing_results(exam_id, participant_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_drawing_participant "
                  "ON drawing_submissions(exam_id, participant_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sub_participant "
                  "ON submissions(exam_id, participant_id)")
    except Exception:
        pass
    c.commit()
    c.close()


def _migrate_student_id(new_id, mac, source_ip=""):
    """
    老数据迁移只允许来源 IP 与旧登记 IP 一致的机器发起。
    MAC 由客户端上报，不能单独作为迁移凭据。
    """
    if not mac or not source_ip:
        return
    c = conn()
    old = c.execute("""SELECT hostname, exam_id FROM students
                       WHERE mac=? AND hostname<>? AND ip=?""",
                    (mac, new_id, source_ip)).fetchone()
    if not old:
        c.close()
        return
    old_host = old["hostname"]
    eid = old["exam_id"]
    c.execute("UPDATE OR IGNORE students SET hostname=? WHERE hostname=?",
              (new_id, old_host))
    for tbl in ("theory_answers", "submissions", "typing_results", "drawing_submissions"):
        c.execute(f"UPDATE {tbl} SET hostname=? WHERE hostname=? "
                  f"AND (exam_id=? OR exam_id IS NULL)",
                  (new_id, old_host, eid))
    c.commit()
    c.close()


def _upsert_student(c, hostname, ip, mac, name=None, seat=None):
    c.execute("""INSERT INTO students(hostname,ip,mac,name,seat,last_seen)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(hostname) DO UPDATE SET
                     ip=COALESCE(excluded.ip, students.ip),
                     mac=COALESCE(excluded.mac, students.mac),
                     name=excluded.name,
                     seat=excluded.seat,
                     last_seen=excluded.last_seen""",
              (hostname, ip, mac, name or "", seat or "", now()))


def upsert_student(hostname, ip, mac, name=None, seat=None):
    """登记/更新学生机；name/seat 由调用方给出最终生效值。"""
    with _transaction() as c:
        _upsert_student(c, hostname, ip, mac, name, seat)


def update_student_profile(hostname, name, seat):
    c = conn()
    cur = c.execute("UPDATE students SET name=?, seat=? WHERE hostname=?",
                    ((name or "").strip(), (seat or "").strip(), hostname))
    c.commit()
    changed = cur.rowcount > 0
    c.close()
    return changed


def _token_hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def register_student(hostname, ip, mac, name=None, seat=None, token=None,
                     preserve_profile=False, clear_participant=False):
    """验证机器令牌并登记学生，返回 (ok, msg, token)。"""
    hostname = (hostname or "").strip()
    if not hostname:
        return False, "缺少机器标识", None
    if len(hostname) > 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in hostname):
        return False, "机器标识过长或包含非法字符", None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in str(name or "") + str(seat or "")):
        return False, "学生姓名或座位号包含非法字符", None
    supplied = (token or "").strip()
    if not get_student(hostname):
        _migrate_student_id(hostname, mac, ip)
    with _transaction() as c:
        row = c.execute("SELECT * FROM students WHERE hostname=?", (hostname,)).fetchone()
        if row and row["token_hash"]:
            if not supplied or not secrets.compare_digest(row["token_hash"], _token_hash(supplied)):
                return False, "机器凭证无效，请重新连接或联系老师重置", None
        elif row:
            if not row["ip"] or row["ip"] != ip:
                return False, "这台机器已在其他地址登记，请联系老师重置", None
            if row["mac"] and mac and row["mac"].lower() != str(mac).lower():
                return False, "机器信息不一致，请联系老师重置", None
        else:
            total = c.execute("SELECT COUNT(*) FROM students").fetchone()[0]
            if total >= MAX_STUDENTS:
                return False, "学生机登记数量已达上限，请联系老师清理", None
            same_ip = c.execute("SELECT COUNT(*) FROM students WHERE ip=?", (ip,)).fetchone()[0]
            if ip and same_ip >= MAX_STUDENTS_PER_IP:
                return False, "本机登记数量已达上限，请联系老师清理", None
        if preserve_profile and row:
            name, seat = row["name"], row["seat"]
        _upsert_student(c, hostname, ip, mac, name=name, seat=seat)
        if clear_participant:
            c.execute("UPDATE students SET participant_id=0, participant_exam_id=0 "
                      "WHERE hostname=?", (hostname,))
        if not row or not row["token_hash"]:
            issued = secrets.token_urlsafe(32)
            c.execute("UPDATE students SET token_hash=? WHERE hostname=?",
                      (_token_hash(issued), hostname))
            return True, "", issued
        return True, "", supplied


def verify_student(hostname, token):
    c = conn()
    row = c.execute("SELECT token_hash FROM students WHERE hostname=?",
                    ((hostname or "").strip(),)).fetchone()
    c.close()
    if not row or not row["token_hash"] or not token:
        return False
    return secrets.compare_digest(row["token_hash"], _token_hash(token))


def reset_student_token(hostname):
    c = conn()
    c.execute("UPDATE students SET token_hash='' WHERE hostname=?", (hostname,))
    c.commit()
    c.close()


def set_current_participant(hostname, exam_id, participant_id):
    with _transaction() as c:
        c.execute("""UPDATE students SET participant_id=?, participant_exam_id=?
                     WHERE hostname=? AND (?=0 OR EXISTS (
                       SELECT 1 FROM exam_participants p
                       WHERE p.id=? AND p.exam_id=? AND p.hostname=?))""",
                  (int(participant_id or 0), int(exam_id or 0), hostname,
                   int(participant_id or 0), int(participant_id or 0),
                   int(exam_id or 0), hostname))


def current_participant(hostname, exam_id):
    c = conn()
    r = c.execute("""SELECT participant_id FROM students
                     WHERE hostname=? AND participant_exam_id=?""",
                  (hostname, int(exam_id or 0))).fetchone()
    c.close()
    return r["participant_id"] if r and r["participant_id"] else 0


def unbind_student(hostname):
    """
    解除这台机器的学生绑定（换学生坐这台机器时用）。
    只清姓名/座位号，IP/MAC/历史成绩都留着。
    """
    c = conn()
    c.execute("UPDATE students SET name='', seat='', participant_id=0, participant_exam_id=0 WHERE hostname=?",
              (hostname,))
    c.commit()
    c.close()


# ================= 本场学生名单（花名册）=================
#
# 设计：学生端**不做机器绑定**，正常输入姓名/座位号；
# 老师端给**每场考试**配一份名单，学生登录时姓名必须在名单里才让进
# （防止学生乱填名字/替别人考）。没配名单 = 不限制（老考试照常）。

def roster_of(exam_id):
    """本场名单：[{id, seat, name, ip}]"""
    c = conn()
    rows = c.execute("""SELECT id, seat, name, ip FROM exam_roster
                        WHERE exam_id=? ORDER BY id""", (exam_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def roster_text_of(exam_id):
    """名单 → 编辑框文本（姓名,座位号,IP）"""
    lines = []
    for row in roster_of(exam_id):
        parts = [row["name"] or "IP学生", row["seat"] or ""]
        if row.get("ip"):
            parts.append(row["ip"])
        lines.append(",".join(part for part in parts if part))
    return "\n".join(lines)


def set_roster(exam_id, rows):
    """整份替换名单。rows 支持 (seat,name) 或 (seat,name,ip)。"""
    c = conn()
    c.execute("DELETE FROM exam_roster WHERE exam_id=?", (exam_id,))
    for row in rows:
        if len(row) == 3:
            seat, name, ip = row
        else:
            seat, name = row
            ip = ""
        c.execute("""INSERT INTO exam_roster(exam_id, seat, name, ip, created_at)
                     VALUES(?,?,?,?,?)""",
                  (exam_id, seat or "", name or "", ip or "", now()))
    c.commit()
    c.close()


def add_roster(exam_id, seat, name, ip=""):
    """加一个学生；重名重座位或重复 IP 返回 False。"""
    name = (name or "").strip()
    seat = (seat or "").strip()
    ip = (ip or "").strip()
    if not name and not ip:
        return False
    c = conn()
    if ip:
        dup = c.execute("SELECT 1 FROM exam_roster WHERE exam_id=? AND ip=?",
                        (exam_id, ip)).fetchone()
    else:
        dup = c.execute("""SELECT 1 FROM exam_roster
                           WHERE exam_id=? AND name=? AND seat=?""",
                        (exam_id, name, seat)).fetchone()
    if dup:
        c.close()
        return False
    c.execute("""INSERT INTO exam_roster(exam_id, seat, name, ip, created_at)
                 VALUES(?,?,?,?,?)""", (exam_id, seat, name, ip, now()))
    c.commit()
    c.close()
    return True


def roster_by_ip(exam_id, ip):
    ip = (ip or "").strip()
    if not ip:
        return None
    c = conn()
    row = c.execute("SELECT * FROM exam_roster WHERE exam_id=? AND ip=?",
                    (exam_id, ip)).fetchone()
    c.close()
    return dict(row) if row else None


def del_roster(exam_id, rid):
    c = conn()
    c.execute("DELETE FROM exam_roster WHERE exam_id=? AND id=?", (exam_id, rid))
    c.commit()
    c.close()


def clear_roster(exam_id):
    c = conn()
    c.execute("DELETE FROM exam_roster WHERE exam_id=?", (exam_id,))
    c.commit()
    c.close()


def roster_check(exam_id, name, seat="", ip="", passwordless=False):
    """
    学生登录时校验在不在名单里。返回 (ok, msg)。
    免密码考试按 IP 校验；普通考试仍按姓名 + 座位号校验。
    """
    rows = roster_of(exam_id)
    if not rows:
        return True, ""
    if passwordless:
        ip_rows = [row for row in rows if row.get("ip")]
        if not ip_rows:
            return False, "免密码考试名单未配置 IP，请联系老师修正名单"
        if not ip or not roster_by_ip(exam_id, ip):
            return False, (f"本机 IP「{ip or '未知'}」不在本场免密码考试名单里。\n"
                           "请联系老师把该 IP 加入名单。")
        matched = roster_by_ip(exam_id, ip)
        if name != (matched.get("name") or "IP学生"):
            return False, "本机 IP 对应的学生姓名不匹配"
        expected_seat = matched.get("seat") or ""
        if expected_seat and (seat or "").strip() != expected_seat:
            return False, "本机 IP 对应的学生座位号不匹配"
        return True, ""
    name = (name or "").strip()
    if not name:
        return False, "请先填写学生姓名"
    hit = [r for r in rows if r["name"] == name]
    if not hit:
        return False, (f"「{name}」不在本场考试的学生名单里。\n"
                       f"请联系老师核对姓名，或让老师把这位学生加进名单。")
    seat = (seat or "").strip()
    want = [r["seat"] for r in hit if r["seat"]]
    if want and seat not in want:
        return False, (f"「{name}」的座位号应该是 {want[0]}，"
                       f"你填的是 {seat or '空'}。\n请改正后再进入考试。")
    return True, ""


def heartbeat(hostname, status=None, progress=None):
    c = conn()
    fields = ["last_seen=?"]
    values = [now()]
    if status:
        fields.append("status=?")
        values.append(str(status))
    if isinstance(progress, dict):
        clean = {}
        for key in ("current_question", "question_total", "theory_answered",
                    "practice_index", "practice_total"):
            try:
                clean[key] = max(0, int(progress.get(key, 0)))
            except (TypeError, ValueError):
                clean[key] = 0
        clean["section"] = str(progress.get("section") or "")[:40]
        clean["current_task"] = str(progress.get("current_task") or "")[:40]
        fields.append("progress_json=?")
        values.append(json.dumps(clean, ensure_ascii=False))
    values.append(hostname)
    c.execute(f"UPDATE students SET {', '.join(fields)} WHERE hostname=?", values)
    c.commit()
    c.close()


def list_students():
    c = conn()
    rows = c.execute("SELECT * FROM students ORDER BY hostname").fetchall()
    c.close()
    out = []
    t = now()
    for r in rows:
        d = dict(r)
        d["has_token"] = bool(d.pop("token_hash", ""))
        d["online"] = (t - (d["last_seen"] or 0)) < ONLINE_TIMEOUT
        try:
            progress = json.loads(d.get("progress_json") or "{}")
            d["progress"] = progress if isinstance(progress, dict) else {}
        except Exception:
            d["progress"] = {}
        out.append(d)
    return out


def clear_offline_students():
    """把掉线的学生机记录清掉（在线的保留），返回清了几台。"""
    with _transaction() as c:
        cutoff = now() - ONLINE_TIMEOUT
        rows = c.execute("""SELECT hostname FROM students
                            WHERE last_seen IS NULL OR last_seen<=?""",
                         (cutoff,)).fetchall()
        hosts = [r["hostname"] for r in rows]
        for hostname in hosts:
            c.execute("DELETE FROM command_targets WHERE hostname=?", (hostname,))
            c.execute("""DELETE FROM students WHERE hostname=?
                         AND (last_seen IS NULL OR last_seen<=?)""",
                      (hostname, cutoff))
        c.execute("""DELETE FROM commands WHERE status='pending' AND NOT EXISTS
                     (SELECT 1 FROM command_targets ct WHERE ct.command_id=commands.id)""")
    return len(hosts)


def clear_students():
    with _transaction() as c:
        c.execute("DELETE FROM command_targets")
        c.execute("DELETE FROM students")
        c.execute("""DELETE FROM commands WHERE status='pending' AND NOT EXISTS
                     (SELECT 1 FROM command_targets ct WHERE ct.command_id=commands.id)""")


def get_student(hostname):
    c = conn()
    r = c.execute("SELECT * FROM students WHERE hostname=?", (hostname,)).fetchone()
    c.close()
    return dict(r) if r else None


def set_student_status(hostname, status, exam_id=None):
    c = conn()
    if exam_id:
        c.execute("UPDATE students SET status=?, exam_id=? WHERE hostname=?",
                  (status, exam_id, hostname))
    else:
        c.execute("UPDATE students SET status=? WHERE hostname=?", (status, hostname))
    c.commit()
    c.close()


def bind_exam(eid):
    """所有学生绑定到某场考试"""
    c = conn()
    c.execute("UPDATE students SET exam_id=?", (eid,))
    c.commit()
    c.close()


# ---------------- 命令 ----------------
def _push_command(c, target, ctype, payload=None):
    cur = c.execute("INSERT INTO commands(target,ctype,payload,created_at) VALUES(?,?,?,?)",
                    (target, ctype, json.dumps(payload or {}, ensure_ascii=False), now()))
    cid = cur.lastrowid
    if target == "all":
        hosts = [r["hostname"] for r in c.execute("SELECT hostname FROM students").fetchall()]
    else:
        hosts = [target]
    for host in hosts:
        c.execute("INSERT OR IGNORE INTO command_targets(command_id,hostname) VALUES(?,?)",
                  (cid, host))
    return cid


def push_command(target, ctype, payload=None):
    with _transaction() as c:
        return _push_command(c, target, ctype, payload)


def _clear_exam_commands(c):
    rows = c.execute("""SELECT id,payload FROM commands
                        WHERE status='pending'""").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if payload.get("exam_id") is not None:
            c.execute("DELETE FROM command_targets WHERE command_id=?", (row["id"],))
            c.execute("DELETE FROM commands WHERE id=?", (row["id"],))


def _ack_target(c, command_id, hostname):
    c.execute("""UPDATE command_targets SET acked_at=?
                 WHERE command_id=? AND hostname=? AND acked_at IS NULL""",
              (now(), command_id, hostname))


def fetch_commands(hostname, exam_id=None, phase=None):
    """拉取发给本机且尚未确认的命令；过期或跨考试命令自动作废。"""
    out = []
    changed = False
    ts = now()
    with _transaction() as c:
        rows = c.execute("""SELECT cm.* FROM commands cm
                            JOIN command_targets ct ON ct.command_id=cm.id
                            WHERE cm.status='pending' AND ct.hostname=? AND ct.acked_at IS NULL
                            ORDER BY cm.id""", (hostname,)).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
                if not isinstance(payload, dict):
                    raise ValueError
            except (TypeError, ValueError):
                payload = {}
                stale = True
            else:
                try:
                    stale = not row["created_at"] or ts - float(row["created_at"]) > COMMAND_TTL_SECONDS
                except (TypeError, ValueError):
                    stale = True
                payload_eid = payload.get("exam_id")
                if payload_eid is not None and str(payload_eid) != str(exam_id):
                    stale = True
                if (row["ctype"] in ("start_exam", "phase") and payload.get("phase")
                        and phase and payload.get("phase") != phase):
                    stale = True
            if stale:
                _ack_target(c, row["id"], hostname)
                changed = True
                continue
            out.append({"id": row["id"], "type": row["ctype"], "payload": payload})
        if changed:
            c.execute("""UPDATE commands SET status='acked', acked_at=? WHERE status='pending'
                         AND NOT EXISTS (SELECT 1 FROM command_targets ct
                                         WHERE ct.command_id=commands.id AND ct.acked_at IS NULL)""",
                      (ts,))
    return out


def has_pending_command(hostname, ctype):
    c = conn()
    row = c.execute("""SELECT 1 FROM commands cm
                       JOIN command_targets ct ON ct.command_id=cm.id
                       WHERE cm.status='pending' AND cm.ctype=?
                         AND ct.hostname=? AND ct.acked_at IS NULL
                         AND cm.created_at>=?
                       LIMIT 1""", (ctype, hostname, now() - COMMAND_TTL_SECONDS)).fetchone()
    c.close()
    return bool(row)


def ack_commands(hostname, ids):
    """每台机器单独确认；全体命令不会被第一台机器提前标完。"""
    ids = list(dict.fromkeys(int(i) for i in ids if str(i).isdigit()))[:256]
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    ts = now()
    with _transaction() as c:
        cur = c.execute(f"""UPDATE command_targets SET acked_at=?
                           WHERE hostname=? AND acked_at IS NULL AND command_id IN ({ph})""",
                        [ts, hostname, *ids])
        n = cur.rowcount
        c.execute(f"""UPDATE commands SET status='acked', acked_at=? WHERE id IN ({ph})
                      AND NOT EXISTS (SELECT 1 FROM command_targets ct
                                      WHERE ct.command_id=commands.id AND ct.acked_at IS NULL)""",
                  [ts, *ids])
    return n


def pending_count():
    c = conn()
    n = c.execute("SELECT COUNT(*) FROM commands WHERE status='pending'").fetchone()[0]
    c.close()
    return n


def clear_commands():
    c = conn()
    ids = [r["id"] for r in c.execute("SELECT id FROM commands WHERE status='acked'").fetchall()]
    for cid in ids:
        c.execute("DELETE FROM command_targets WHERE command_id=?", (cid,))
    c.execute("DELETE FROM commands WHERE status='acked'")
    c.commit()
    c.close()


# ---------------- 提交 ----------------
def _remove_managed_file(path):
    if not path:
        return
    try:
        import runtime
        real = os.path.realpath(path)
        roots = [os.path.realpath(runtime.UPLOAD_DIR)]
        if os.path.islink(path) or not any(os.path.commonpath((real, root)) == root for root in roots):
            return
        if os.path.isfile(real):
            os.remove(real)
    except (OSError, ValueError, TypeError):
        pass


def _write_window_open(c, exam_id, hostname, participant_id):
    ex = c.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
    if not ex:
        return False
    ts = now()
    if ex["status"] in ("running", "paused"):
        start = ex["start_at"] or ex["phase_at"] or ts
        end = ex["paused_at"] if ex["status"] == "paused" and ex["paused_at"] else ts
        return float(ex["duration"] or 0) * 60 - (float(end) - float(start)) > 0
    if ex["status"] == "stopped":
        if not ex["stop_at"] or ts > float(ex["stop_at"]) + FINAL_SUBMIT_GRACE_SECONDS:
            return False
        return bool(c.execute("""SELECT 1 FROM exam_participants
                                WHERE id=? AND exam_id=? AND hostname=?""",
                              (int(participant_id or 0), exam_id, hostname)).fetchone())
    return False


def save_submission(hostname, exam_id, filename, path, size, task_id=0,
                    participant_id=0):
    """原子替换同一学生、考试、任务和参与者的提交记录。"""
    old_path = None
    with _transaction() as c:
        if not _write_window_open(c, exam_id, hostname, participant_id):
            raise PermissionError("考试未在进行或最终收卷时间已过")
        st = c.execute("SELECT id FROM students WHERE hostname=?", (hostname,)).fetchone()
        sid = st["id"] if st else None
        old = c.execute("""SELECT id, saved_path FROM submissions
                           WHERE hostname=? AND exam_id=? AND task_id=?
                             AND participant_id=?""",
                        (hostname, exam_id, task_id, int(participant_id or 0))).fetchone()
        old_path = old["saved_path"] if old else None
        if old:
            c.execute("""UPDATE grading_jobs SET status='cancelled', updated_at=?,
                         finished_at=? WHERE submission_id=? AND status='pending'""",
                      (now(), now(), old["id"]))
        c.execute("DELETE FROM submissions WHERE hostname=? AND exam_id=? AND task_id=? AND participant_id=?",
                  (hostname, exam_id, task_id, int(participant_id or 0)))
        cur = c.execute("""INSERT INTO submissions(student_id,hostname,exam_id,filename,
                           saved_path,size,submitted_at,task_id,participant_id)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (sid, hostname, exam_id, filename, path, size, now(), task_id,
                         int(participant_id or 0)))
        sub_id = cur.lastrowid
    if old_path and old_path != path and not grading_file_in_use(old_path):
        _remove_managed_file(old_path)
    return sub_id


def submission_of_task(exam_id, hostname, task_id, participant_id=None):
    """某个学生在某道操作题上的提交（用于判断交没交）"""
    c = conn()
    if participant_id is None:
        r = c.execute("""SELECT * FROM submissions
                         WHERE exam_id=? AND hostname=? AND task_id=?
                         ORDER BY participant_id DESC, id DESC LIMIT 1""",
                      (exam_id, hostname, int(task_id or 0))).fetchone()
    else:
        r = c.execute("""SELECT * FROM submissions
                         WHERE exam_id=? AND hostname=? AND task_id=? AND participant_id=?
                         ORDER BY id DESC LIMIT 1""",
                      (exam_id, hostname, int(task_id or 0), int(participant_id))).fetchone()
    c.close()
    return dict(r) if r else None


def list_submissions(exam_id):
    c = conn()
    rows = c.execute("""SELECT * FROM submissions WHERE exam_id=?
                        ORDER BY hostname""", (exam_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def checks_for_submission(exam, sub):
    """
    某份答卷该用哪套评分规则。

    一场考试装多道操作题时，每道题各自的评分规则存在 practice_questions
    里，靠 submissions.task_id 区分；老的单题考试规则挂在 exams.checks。
    重新评分 / 批量评分都要走这里，别统一套 exams.checks（会把多题全判错）。
    """
    tid = (sub.get("task_id") or 0)
    if tid:
        c = conn()
        try:
            row = c.execute("""SELECT ep.snapshot_json, p.id
                               FROM exam_practice ep
                               LEFT JOIN practice_questions p ON p.id=ep.practice_id
                               WHERE ep.exam_id=? AND ep.practice_id=?""",
                            (exam.get("id"), tid)).fetchone()
            if row and row["snapshot_json"]:
                return _practice_row(json.loads(row["snapshot_json"]))["checks"]
            if row and row["id"] is not None:
                task = get_practice(row["id"])
                if task:
                    return task["checks"]
        finally:
            c.close()
        raise ValueError("考试实操题快照不存在")
    return json.loads((exam or {}).get("checks") or "[]")


def task_in_exam(exam_id, task_id):
    """这道实操题是否属于该考试（防止学生端乱传 task_id）"""
    c = conn()
    r = c.execute("SELECT 1 FROM exam_practice WHERE exam_id=? AND practice_id=?",
                  (exam_id, int(task_id or 0))).fetchone()
    c.close()
    return r is not None


def material_references_on(c, name, exclude_exam=None):
    used = []
    for r in c.execute("SELECT id, title, materials FROM exams").fetchall():
        if exclude_exam and r["id"] == exclude_exam:
            continue
        try:
            mats = json.loads(r["materials"] or "[]")
        except (TypeError, ValueError) as exc:
            raise ValueError("考试素材引用数据损坏") from exc
        if not isinstance(mats, list):
            raise ValueError("考试素材引用数据损坏")
        if name in mats:
            used.append(f"考试「{r['title']}」")
    for r in c.execute("SELECT name, materials FROM practice_questions").fetchall():
        try:
            mats = json.loads(r["materials"] or "[]")
        except (TypeError, ValueError) as exc:
            raise ValueError("实操题素材引用数据损坏") from exc
        if not isinstance(mats, list):
            raise ValueError("实操题素材引用数据损坏")
        if name in mats:
            used.append(f"实操题「{r['name']}」")
    return used


def material_used_by_others(name, exclude_exam=None):
    """返回仍引用该素材的考试/实操题描述；空列表表示没有引用。"""
    return material_references_on(conn(), name, exclude_exam=exclude_exam)


def last_stopped_exam():
    """
    最近一场已结束的考试。
    active_exam() 只认 running/paused，考试一结束学生端就查不到它了 ——
    结果学生停在"等待老师开始考试"，而不是看到"考试已结束"。
    """
    c = conn()
    r = c.execute("SELECT * FROM exams WHERE status='stopped' "
                  "ORDER BY start_at DESC LIMIT 1").fetchone()
    c.close()
    return dict(r) if r else None


def has_submission(exam_id, hostname, participant_id=None):
    """
    这个学生交过答卷吗。
    心跳每 3 秒一次，不能再用 list_submissions 拉全表
    （全班答卷的 detail 是很大的 JSON，50 台机器一起拉会把服务器拖垮）。
    """
    c = conn()
    if participant_id is None:
        r = c.execute("SELECT 1 FROM submissions WHERE exam_id=? AND hostname=? LIMIT 1",
                      (exam_id, hostname)).fetchone()
    else:
        r = c.execute("""SELECT 1 FROM submissions
                         WHERE exam_id=? AND hostname=? AND participant_id=? LIMIT 1""",
                      (exam_id, hostname, int(participant_id))).fetchone()
    c.close()
    return r is not None


def update_score(sub_id, score, full, detail):
    c = conn()
    c.execute("UPDATE submissions SET score=?, full=?, detail=? WHERE id=?",
              (score, full, json.dumps(detail, ensure_ascii=False), sub_id))
    c.commit()
    c.close()


def _grading_job(row):
    job = dict(row)
    try:
        job["input"] = json.loads(job.pop("input_json") or "{}")
    except (TypeError, ValueError):
        job["input"] = {}
    return job


def enqueue_grading_job(dedupe_key, exam_id, submission_id, hostname, payload,
                        max_attempts=None):
    """持久化评分任务，返回 (job_id, 前方 pending 数, 是否新建)。"""
    ts = now()
    input_json = json.dumps(payload, ensure_ascii=False)
    with _transaction() as c:
        old = c.execute("SELECT * FROM grading_jobs WHERE dedupe_key=?",
                        (dedupe_key,)).fetchone()
        if old and old["status"] in ("pending", "running"):
            position = c.execute(
                "SELECT COUNT(*) FROM grading_jobs WHERE status='pending' AND id<=?",
                (old["id"],)).fetchone()[0]
            return old["id"], position, False
        active = c.execute(
            "SELECT COUNT(*) FROM grading_jobs WHERE status IN ('pending','running')"
        ).fetchone()[0]
        if active >= GRADING_QUEUE_CAPACITY:
            raise RuntimeError("评分队列已满，请稍后重试")
        if old:
            c.execute("""UPDATE grading_jobs SET exam_id=?, submission_id=?, hostname=?,
                         input_json=?, status='pending', attempts=0, max_attempts=?,
                         next_attempt_at=?, lease_owner='', lease_token='', lease_until=0,
                         cancel_requested=0, updated_at=?, started_at=NULL,
                         finished_at=NULL, last_error='' WHERE id=?""",
                      (exam_id, submission_id, hostname, input_json,
                       int(max_attempts or GRADING_JOB_MAX_ATTEMPTS), ts, ts, old["id"]))
            job_id = old["id"]
        else:
            cur = c.execute("""INSERT INTO grading_jobs
                (dedupe_key,exam_id,submission_id,hostname,input_json,status,attempts,
                 max_attempts,next_attempt_at,created_at,updated_at)
                VALUES(?,?,?,?,?,'pending',0,?,?,?,?)""",
                (dedupe_key, exam_id, submission_id, hostname, input_json,
                 int(max_attempts or GRADING_JOB_MAX_ATTEMPTS), ts, ts, ts))
            job_id = cur.lastrowid
        position = c.execute(
            "SELECT COUNT(*) FROM grading_jobs WHERE status='pending' AND id<=?",
            (job_id,)).fetchone()[0]
    return job_id, position, True


def claim_grading_job(worker_id, lease_seconds=120):
    """原子领取任务，并回收过期租约。"""
    ts = now()
    with _transaction() as c:
        c.execute("""UPDATE grading_jobs
                     SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'pending' END,
                         lease_owner='', lease_token='', lease_until=0,
                         next_attempt_at=?, updated_at=?,
                         last_error=CASE WHEN attempts>=max_attempts
                                     THEN '评分 worker 租约过期' ELSE last_error END
                     WHERE status='running' AND lease_until<?""", (ts, ts, ts))
        c.execute("""UPDATE grading_jobs SET status='cancelled', updated_at=?,
                     finished_at=? WHERE status='pending' AND cancel_requested=1""",
                  (ts, ts))
        row = c.execute("""SELECT * FROM grading_jobs
                           WHERE status='pending' AND next_attempt_at<=?
                           ORDER BY id LIMIT 1""", (ts,)).fetchone()
        if not row:
            return None
        token = secrets.token_urlsafe(24)
        c.execute("""UPDATE grading_jobs SET status='running', attempts=attempts+1,
                     lease_owner=?, lease_token=?, lease_until=?, started_at=?, updated_at=?
                     WHERE id=?""",
                  (worker_id, token, ts + lease_seconds, ts, ts, row["id"]))
        row = c.execute("SELECT * FROM grading_jobs WHERE id=?", (row["id"],)).fetchone()
    return _grading_job(row)


def heartbeat_grading_job(job_id, lease_token, worker_id, lease_seconds=120):
    ts = now()
    with _transaction() as c:
        cur = c.execute("""UPDATE grading_jobs SET lease_until=?, updated_at=?
                     WHERE id=? AND status='running' AND lease_token=?
                       AND lease_owner=?""",
                  (ts + lease_seconds, ts, job_id, lease_token, worker_id))
    return cur.rowcount == 1


def complete_grading_job(job_id, lease_token, worker_id, score, full, detail):
    ts = now()
    with _transaction() as c:
        row = c.execute("""SELECT submission_id,status,lease_token,lease_owner
                           FROM grading_jobs WHERE id=?""", (job_id,)).fetchone()
        if (not row or row["status"] != "running" or row["lease_token"] != lease_token
                or row["lease_owner"] != worker_id):
            return False
        newer = c.execute("""SELECT 1 FROM grading_jobs
                             WHERE submission_id=? AND id>?
                               AND status IN ('pending','running','succeeded')
                             LIMIT 1""", (row["submission_id"], job_id)).fetchone()
        if not newer:
            c.execute("""UPDATE submissions SET score=?, full=?, detail=?
                         WHERE id=?""",
                      (score, full, json.dumps(detail, ensure_ascii=False), row["submission_id"]))
        c.execute("""UPDATE grading_jobs SET status='succeeded', updated_at=?,
                     finished_at=?, lease_owner='', lease_token='', lease_until=0,
                     last_error='' WHERE id=?""", (ts, ts, job_id))
    return True


def fail_grading_job(job_id, lease_token, worker_id, error):
    ts = now()
    with _transaction() as c:
        row = c.execute("""SELECT attempts,max_attempts,status,lease_token,lease_owner
                           FROM grading_jobs WHERE id=?""", (job_id,)).fetchone()
        if (not row or row["status"] != "running" or row["lease_token"] != lease_token
                or row["lease_owner"] != worker_id):
            return False
        if row["attempts"] >= row["max_attempts"]:
            c.execute("""UPDATE grading_jobs SET status='failed', updated_at=?,
                         finished_at=?, lease_owner='', lease_token='', lease_until=0,
                         last_error=? WHERE id=?""",
                      (ts, ts, str(error)[:2000], job_id))
        else:
            delay = min(60, 2 ** max(0, row["attempts"] - 1))
            c.execute("""UPDATE grading_jobs SET status='pending', updated_at=?,
                         next_attempt_at=?, lease_owner='', lease_token='', lease_until=0,
                         last_error=? WHERE id=?""",
                      (ts, ts + delay, str(error)[:2000], job_id))
    return True


def cancel_grading_jobs_for_exam(exam_id):
    ts = now()
    with _transaction() as c:
        c.execute("""UPDATE grading_jobs SET status='cancelled', updated_at=?,
                     finished_at=? WHERE exam_id=? AND status='pending'""",
                  (ts, ts, exam_id))


def cancel_grading_jobs_for_submission(submission_id):
    ts = now()
    with _transaction() as c:
        c.execute("""UPDATE grading_jobs SET status='cancelled', updated_at=?,
                     finished_at=? WHERE submission_id=? AND status='pending'""",
                  (ts, ts, submission_id))


def grading_file_in_use(path):
    if not path:
        return False
    rows = conn().execute(
        "SELECT input_json FROM grading_jobs WHERE status IN ('pending','running')"
    ).fetchall()
    for row in rows:
        try:
            if json.loads(row["input_json"] or "{}").get("path") == path:
                return True
        except (TypeError, ValueError):
            continue
    return False


def grading_job_counts():
    row = conn().execute("""SELECT
        SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running,
        SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
        FROM grading_jobs""").fetchone()
    return {k: int(row[k] or 0) for k in ("pending", "running", "failed")}


# ---------------- 统计 ----------------
def stats(exam_id):
    """控制台统计：有参与快照时按人统计，否则兼容旧机器记录。"""
    c = conn()
    parts = c.execute("SELECT COUNT(*) AS n FROM exam_participants WHERE exam_id=?",
                      (exam_id,)).fetchone()["n"]
    if parts:
        total = parts
        online = c.execute("""SELECT COUNT(*) FROM exam_participants
                              WHERE exam_id=? AND last_seen>?""",
                           (exam_id, now() - ONLINE_TIMEOUT)).fetchone()[0]
    else:
        recent = now() - 300
        total = c.execute(
            """SELECT COUNT(DISTINCT hostname) FROM students
               WHERE exam_id=? OR last_seen>?""", (exam_id, recent)).fetchone()[0]
        online = c.execute(
            """SELECT COUNT(DISTINCT hostname) FROM students
               WHERE (exam_id=? OR last_seen>?) AND last_seen>?""",
            (exam_id, recent, now() - ONLINE_TIMEOUT)).fetchone()[0]
    required = c.execute("SELECT COUNT(*) FROM exam_practice WHERE exam_id=?",
                         (exam_id,)).fetchone()[0]
    if required:
        submitted = c.execute("""
            SELECT COUNT(*) FROM (
                SELECT s.hostname,
                       CASE WHEN s.participant_id>0 THEN s.participant_id ELSE 0 END AS pid,
                       COUNT(DISTINCT s.task_id) AS n
                FROM submissions s
                JOIN exam_practice ep
                  ON ep.exam_id=s.exam_id AND ep.practice_id=s.task_id
                WHERE s.exam_id=?
                GROUP BY s.hostname,
                         CASE WHEN s.participant_id>0 THEN s.participant_id ELSE 0 END
            ) done WHERE done.n=?""", (exam_id, required)).fetchone()[0]
    else:
        submitted = c.execute(
            """SELECT COUNT(DISTINCT CASE WHEN participant_id>0
                      THEN 'p'||participant_id ELSE 'h'||hostname END)
               FROM submissions WHERE exam_id=?""", (exam_id,)).fetchone()[0]
    c.close()
    return {"students": total, "online": online, "submitted": submitted}


# ================= 理论题 =================

# ---------------- 题库 ----------------
def _insert_question(c, q, owner_exam_id=0):
    payload = q.get("payload", "") or ""
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False)
    cur = c.execute("""INSERT INTO questions(qtype,stem,options,answer,score,
                        category,language,code,stdin_data,expected_output,
                        difficulty,source,payload,owner_exam_id,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (q["qtype"], q["stem"],
                     json.dumps(q.get("options", []), ensure_ascii=False),
                     str(q["answer"]).strip(), float(q.get("score", 2)),
                     q.get("category", ""),
                     q.get("language", "") or "",
                     q.get("code", "") or "",
                     q.get("stdin_data", "") or "",
                     q.get("expected_output", "") or "",
                     q.get("difficulty", "") or "",
                     q.get("source", "") or "",
                     payload,
                     int(owner_exam_id or 0), now()))
    return cur.lastrowid


def add_question(q):
    """新增题目，返回 id"""
    with _transaction() as c:
        qid = _insert_question(c, q, q.get("owner_exam_id", 0))
    invalidate()
    return qid


def update_question(qid, q):
    """
    更新题目。

    ⚠️ 两个坑都在这里踩过：
      1. 早期版本只更新前 6 个字段，程序填空题的 language/code
         永远改不动。
      2. 补上之后，题库页的「改」表单没传这三个字段，结果
         编辑任何一道程序填空都会把代码模板清成空 —— 学生端
         只剩题干没有代码，直接没法做。

    所以这里的策略是：**传了才更新，没传就保留原值**。
    """
    opt = {"qtype": False, "stem": False, "options": False, "answer": False,
           "score": False, "category": False,
           "language": True, "code": True, "stdin_data": True,
           "expected_output": True,
           "difficulty": True, "source": True, "payload": True}
    sets, vals = [], []
    for col, nullable in opt.items():
        # nullable 的三个字段：只有显式出现在 dict 里才更新
        if nullable and col not in q:
            continue
        if not nullable and col not in q:
            continue
        v = q[col]
        if col == "options":
            v = json.dumps(v or [], ensure_ascii=False)
        elif col == "answer":
            v = str(v or "").strip()
        elif col == "score":
            v = float(v or 2)
        elif col == "payload" and isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        else:
            v = v or ""
        sets.append(f"{col}=?")
        vals.append(v)
    if not sets:
        return
    vals.append(qid)
    with _transaction() as c:
        row = c.execute("SELECT owner_exam_id FROM questions WHERE id=?", (qid,)).fetchone()
        if not row:
            raise ValueError("题目不存在")
        if row["owner_exam_id"]:
            raise ValueError("考试包私有题目不能直接修改")
        c.execute(f"UPDATE questions SET {', '.join(sets)} WHERE id=?", vals)
    invalidate()          # 改完题必须清缓存，否则判分还在用改之前的那份


def delete_question(qid):
    with _transaction() as c:
        row = c.execute("SELECT owner_exam_id FROM questions WHERE id=?", (qid,)).fetchone()
        if not row:
            return
        if row["owner_exam_id"]:
            raise ValueError("考试包私有题目不能直接删除")
        c.execute("DELETE FROM questions WHERE id=?", (qid,))
    invalidate()


def delete_student(sid):
    """删除单个学生及其未确认命令目标。"""
    with _transaction() as c:
        row = c.execute("SELECT hostname FROM students WHERE id=?", (sid,)).fetchone()
        if row:
            c.execute("DELETE FROM command_targets WHERE hostname=?", (row["hostname"],))
        c.execute("DELETE FROM students WHERE id=?", (sid,))
        c.execute("""DELETE FROM commands WHERE status='pending' AND NOT EXISTS
                     (SELECT 1 FROM command_targets ct WHERE ct.command_id=commands.id)""")


def delete_exam(eid):
    """
    删除考试，连同它派生出来的一切。

    以前只删了 4 张表，留下两个尾巴：
      · exam_practice 里的操作题关联变成孤儿行
      · commands 里该场考试的 start_exam/collect 等命令还在，
        学生端下次心跳会把旧命令再拉一次（"又开始考试了？"）
      · students.exam_id 还指着已删除的考试
    """
    with _transaction() as c:
        file_paths = [r["saved_path"] for r in c.execute(
            "SELECT saved_path FROM submissions WHERE exam_id=?", (eid,)).fetchall()]
        file_paths += [r["saved_path"] for r in c.execute(
            "SELECT saved_path FROM drawing_submissions WHERE exam_id=?",
            (eid,)).fetchall()]
        c.execute("DELETE FROM exam_roster WHERE exam_id=?", (eid,))
        c.execute("DELETE FROM exam_questions WHERE exam_id=?", (eid,))
        c.execute("DELETE FROM theory_answers WHERE exam_id=?", (eid,))
        c.execute("DELETE FROM submissions WHERE exam_id=?", (eid,))
        c.execute("DELETE FROM exam_practice WHERE exam_id=?", (eid,))
        c.execute("DELETE FROM exam_participants WHERE exam_id=?", (eid,))
        c.execute("DELETE FROM drawing_submissions WHERE exam_id=?", (eid,))
        c.execute("DELETE FROM typing_results WHERE exam_id=?", (eid,))
        kill = []
        for r in c.execute("SELECT id, payload FROM commands").fetchall():
            try:
                p = json.loads(r["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            if str(p.get("exam_id")) == str(eid):
                kill.append(r["id"])
        for cid in kill:
            c.execute("DELETE FROM command_targets WHERE command_id=?", (cid,))
            c.execute("DELETE FROM commands WHERE id=?", (cid,))
        c.execute("""UPDATE students SET exam_id=NULL, participant_id=0,
                     participant_exam_id=0 WHERE exam_id=?""", (eid,))
        c.execute("DELETE FROM questions WHERE owner_exam_id=?", (eid,))
        c.execute("DELETE FROM practice_questions WHERE owner_exam_id=?", (eid,))
        c.execute("DELETE FROM grading_jobs WHERE exam_id=?", (eid,))
        c.execute("DELETE FROM exams WHERE id=?", (eid,))
    for path in file_paths:
        _remove_managed_file(path)
    invalidate()


def delete_submission(sid, exam_id=None):
    """删除属于指定考试且未结束的提交记录。"""
    path = None
    with _transaction() as c:
        row = c.execute("""SELECT s.saved_path, s.exam_id, e.status
                           FROM submissions s JOIN exams e ON e.id=s.exam_id
                           WHERE s.id=? AND (? IS NULL OR s.exam_id=?)""",
                        (sid, exam_id, exam_id)).fetchone()
        if not row or row["status"] == "stopped":
            return False
        path = row["saved_path"]
        c.execute("""UPDATE grading_jobs SET status='cancelled', updated_at=?,
                     finished_at=? WHERE submission_id=? AND status='pending'""",
                  (now(), now(), sid))
        c.execute("DELETE FROM submissions WHERE id=? AND exam_id=?",
                  (sid, row["exam_id"]))
    if not grading_file_in_use(path):
        _remove_managed_file(path)
    return True


def _q(row):
    """把 questions 行转成 dict，并把 options 从 JSON 字符串解析成列表"""
    d = dict(row)
    try:
        d["options"] = json.loads(d.get("options") or "[]")
    except Exception:
        d["options"] = []
    return d


def get_question(qid):
    """
    取单道题。判分时每道题都要查一次，50 人 × 12 题就是每分钟几千次，
    加个短缓存（改题时 invalidate 会清掉）。返回副本，避免调用方改坏缓存。
    """
    def _calc():
        c = conn()
        r = c.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        c.close()
        return _q(r) if r else None
    q = _hot_get(f"q:{qid}", _calc)
    return dict(q) if q else None


def list_questions(category=None, keyword=None, qtype=None,
                   difficulty=None, source=None):
    c = conn()
    sql = "SELECT * FROM questions WHERE COALESCE(owner_exam_id,0)=0"
    args = []
    if category:
        sql += " AND category=?"
        args.append(category)
    if qtype:
        sql += " AND qtype=?"
        args.append(qtype)
    if difficulty:
        sql += " AND difficulty=?"
        args.append(difficulty)
    if source:
        sql += " AND source=?"
        args.append(source)
    if keyword:
        sql += " AND stem LIKE ?"
        args.append(f"%{keyword}%")
    sql += " ORDER BY id DESC"
    rows = c.execute(sql, args).fetchall()
    c.close()
    return [_q(r) for r in rows]


def question_meta_counts():
    """
    题库统计：按 难度 / 来源 / 题型 各有多少题（题库页顶部统计条用）。
    一次查完，别让页面自己循环查。
    """
    c = conn()
    out = {"difficulty": {}, "source": {}, "qtype": {}}
    for field in ("difficulty", "source", "qtype"):
        rows = c.execute(
            f"SELECT {field} AS v, COUNT(*) AS n FROM questions "
            f"WHERE COALESCE(owner_exam_id,0)=0 GROUP BY {field}").fetchall()
        out[field] = {(r["v"] or "未分类"): r["n"] for r in rows}
    out["total"] = c.execute(
        "SELECT COUNT(*) AS n FROM questions WHERE COALESCE(owner_exam_id,0)=0"
    ).fetchone()["n"]
    c.close()
    return out


def list_field_values(field):
    """题库里某个字段出现过哪些值（难度/来源筛选用）。field 只允许白名单。"""
    if field not in ("difficulty", "source", "category"):
        return []
    c = conn()
    rows = c.execute(
        f"SELECT DISTINCT {field} AS v FROM questions "
        f"WHERE COALESCE(owner_exam_id,0)=0 AND {field}<>'' "
        f"ORDER BY {field}").fetchall()
    c.close()
    return [r["v"] for r in rows]


# 难度取值（顺序即由易到难，页面下拉与整理都用它）
DIFFICULTIES = ("基础", "进阶", "挑战")


def tidy_question_meta():
    """
    整理题库分类：把「难度/来源」为空的题补上。
      难度启发式：程序填空 → 挑战；多选/填空 → 进阶；单选/判断 → 基础
      来源启发式：分类里带「雅安会考」→ 雅安会考模拟题；其余 → 自创题
    幂等：已经有值的题不动。返回 (补难度的条数, 补来源的条数)
    """
    c = conn()
    n_d = c.execute(
        """UPDATE questions SET difficulty = CASE qtype
               WHEN 'code' THEN '挑战'
               WHEN 'multi' THEN '进阶'
               WHEN 'blank' THEN '进阶'
               WHEN 'drag' THEN '进阶'
               ELSE '基础' END
           WHERE COALESCE(difficulty,'')='' AND COALESCE(owner_exam_id,0)=0""").rowcount
    n_s = c.execute(
        """UPDATE questions SET source = CASE
               WHEN category LIKE '%会考%' THEN '雅安会考模拟题'
               ELSE '自创题' END
           WHERE COALESCE(source,'')='' AND COALESCE(owner_exam_id,0)=0""").rowcount
    c.commit()
    c.close()
    if n_d or n_s:
        invalidate()
    return n_d, n_s


def list_categories():
    c = conn()
    rows = c.execute("SELECT DISTINCT category FROM questions WHERE COALESCE(owner_exam_id,0)=0 AND category<>'' ORDER BY category").fetchall()
    c.close()
    return [r["category"] for r in rows]


def _import_question_batch(c, items):
    ok = skip = 0
    for q in items:
        try:
            if not q.get("stem") or not q.get("answer"):
                skip += 1
                continue
            if q.get("qtype") not in ("single", "multi", "judge",
                                      "blank", "code", "drag"):
                skip += 1
                continue
            dup = c.execute(
                """SELECT 1 FROM questions WHERE stem=? AND qtype=?
                   AND answer=? AND COALESCE(owner_exam_id,0)=0 LIMIT 1""",
                (q.get("stem", ""), q["qtype"],
                 str(q.get("answer", "")).strip())).fetchone()
            if dup:
                skip += 1
                continue
            payload = q.get("payload", "") or ""
            if not payload and q.get("slots"):
                payload = json.dumps({"slots": q.get("slots")},
                                     ensure_ascii=False)
            c.execute(
                """INSERT INTO questions(qtype,stem,options,answer,score,
                                        category,language,code,stdin_data,
                                        expected_output,difficulty,source,
                                        payload,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (q["qtype"], q["stem"],
                 json.dumps(q.get("options", []), ensure_ascii=False),
                 str(q["answer"]).strip(), float(q.get("score", 2)),
                 q.get("category", ""),
                 q.get("language", "") or "",
                 q.get("code", "") or "",
                 q.get("stdin_data", "") or "",
                 q.get("expected_output", "") or "",
                 q.get("difficulty", "") or "",
                 q.get("source", "") or "",
                 payload, now()))
            ok += 1
        except (TypeError, ValueError, json.JSONDecodeError):
            skip += 1
    return ok, skip


def import_questions(items):
    """分批导入，返回 (新增数, 跳过数)。"""
    ok = skip = 0
    batch_size = 100
    for start in range(0, len(items), batch_size):
        with _transaction() as c:
            added, skipped = _import_question_batch(c, items[start:start + batch_size])
        ok += added
        skip += skipped
    if ok:
        invalidate()
    return ok, skip


# ---------------- 组卷 ----------------
def set_exam_questions(exam_id, qids):
    """设置某场考试的理论题（按顺序），并冻结当时题目内容。"""
    with _transaction() as c:
        c.execute("DELETE FROM exam_questions WHERE exam_id=?", (exam_id,))
        for i, qid in enumerate(qids):
            row = c.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
            if not row:
                raise ValueError("试卷中包含不存在的题目")
            snapshot = json.dumps(dict(row), ensure_ascii=False, default=str)
            c.execute("""INSERT INTO exam_questions
                         (exam_id,question_id,qorder,snapshot_json)
                         VALUES(?,?,?,?)""", (exam_id, qid, i, snapshot))
    invalidate()


def get_exam_questions(exam_id):
    """取试卷题目（按 qorder），优先返回组卷时的不可变快照。"""
    c = conn()
    rows = c.execute("""SELECT eq.snapshot_json, q.* FROM exam_questions eq
                        LEFT JOIN questions q ON q.id=eq.question_id
                        WHERE eq.exam_id=? ORDER BY eq.qorder""", (exam_id,)).fetchall()
    c.close()
    out = []
    for row in rows:
        if row["snapshot_json"]:
            out.append(_q(json.loads(row["snapshot_json"])))
        elif row["id"] is not None:
            out.append(_q(row))
    return out


# ---------------- 热点数据缓存 ----------------
#
# 50 台机器每 3 秒一次心跳，试卷、题目数这类"不常变但读得极频繁"的数据
# 反复查库太浪费。这里做一层 TTL 很短的内存缓存，
# 老师一动题目就主动失效（invalidate），不会出现"改了不生效"。
_HOT = {}
_HOT_TTL = 5.0


def _hot_get(key, fn, ttl=_HOT_TTL):
    hit = _HOT.get(key)
    now_ = time.time()
    if hit and now_ - hit[0] < ttl:
        return hit[1]
    val = fn()
    _HOT[key] = (now_, val)
    return val


# 对外名字（app.py 给试卷做缓存用）
hot_get = _hot_get


def invalidate(prefix=""):
    """考试/题目有变动时清缓存。prefix 为空 = 全清"""
    for k in list(_HOT.keys()):
        if not prefix or k.startswith(prefix):
            _HOT.pop(k, None)
    # 题目可能改过（期望输出变了），运行判分的缓存也要跟着失效
    try:
        import coderun
        coderun.clear_cache()
    except Exception:
        pass


def count_exam_questions(exam_id):
    """试卷题目数（心跳用，不能每次都把整份试卷查出来）"""
    def _calc():
        c = conn()
        n = c.execute("SELECT COUNT(*) FROM exam_questions WHERE exam_id=?",
                      (exam_id,)).fetchone()[0]
        c.close()
        return n
    return _hot_get(f"qcount:{exam_id}", _calc)


def theory_counts(exam_id):
    """一场考试里各参与者的已答题数，返回 {participant_id: n}。"""
    c = conn()
    rows = c.execute(
        """SELECT participant_id, COUNT(*) AS n FROM theory_answers
           WHERE exam_id=? GROUP BY participant_id""", (exam_id,)).fetchall()
    c.close()
    return {r["participant_id"]: r["n"] for r in rows}


def exam_question_counts():
    """
    每场考试有几道理论题 {exam_id: n}。
    控制台列表用 —— 以前模板里读的是不存在的字段，永远显示"0 题"。
    """
    c = conn()
    rows = c.execute("""SELECT exam_id, COUNT(*) AS n FROM exam_questions
                        GROUP BY exam_id""").fetchall()
    c.close()
    return {r["exam_id"]: r["n"] for r in rows}


def exam_practice_counts():
    """每场考试装了几道操作题 {exam_id: n}"""
    c = conn()
    rows = c.execute("""SELECT exam_id, COUNT(*) AS n FROM exam_practice
                        GROUP BY exam_id""").fetchall()
    c.close()
    return {r["exam_id"]: r["n"] for r in rows}


def exam_question_ids(exam_id):
    c = conn()
    rows = c.execute("SELECT question_id FROM exam_questions WHERE exam_id=? ORDER BY qorder",
                     (exam_id,)).fetchall()
    c.close()
    return [r["question_id"] for r in rows]


# ==================== 考试参与名单（快照） ====================
def _add_participant(c, exam_id, hostname, name=None, seat=None, ip=None):
    if not exam_id or not hostname:
        return 0
    name = (name or "").strip()
    seat = (seat or "").strip()
    ip = (ip or "").strip()
    if seat:
        old = c.execute("""SELECT id FROM exam_participants
                           WHERE exam_id=? AND hostname=? AND name=? AND seat=''""",
                        (exam_id, hostname, name)).fetchone()
        if old:
            c.execute("UPDATE exam_participants SET seat=?, last_seen=? WHERE id=?",
                      (seat, now(), old["id"]))
            return old["id"]
    ts = now()
    c.execute("""INSERT INTO exam_participants
                   (exam_id, hostname, name, seat, ip, first_seen, last_seen)
                 VALUES(?,?,?,?,?,?,?)
                 ON CONFLICT(exam_id, hostname, name, seat) DO UPDATE SET
                   ip=CASE WHEN exam_participants.ip='' AND excluded.ip!=''
                           THEN excluded.ip ELSE exam_participants.ip END,
                   last_seen=excluded.last_seen""",
              (exam_id, hostname, name, seat, ip, ts, ts))
    row = c.execute("""SELECT id FROM exam_participants
                       WHERE exam_id=? AND hostname=? AND name=? AND seat=?""",
                    (exam_id, hostname, name, seat)).fetchone()
    return row["id"] if row else 0


def add_participant(exam_id, hostname, name=None, seat=None, ip=None):
    """参与身份按考试、机器、姓名和座位隔离。"""
    with _transaction() as c:
        ex = c.execute("SELECT status FROM exams WHERE id=?", (exam_id,)).fetchone()
        if not ex or ex["status"] not in ("running", "paused"):
            return 0
        return _add_participant(c, exam_id, hostname, name, seat, ip)


def update_current_participant_profile(hostname, name, seat):
    student = get_student(hostname) or {}
    exam_id = int(student.get("participant_exam_id") or 0)
    participant_id = int(student.get("participant_id") or 0)
    if not exam_id or not participant_id:
        return False
    with _transaction() as c:
        cur = c.execute("""UPDATE exam_participants SET name=?, seat=?
                           WHERE id=? AND exam_id=? AND hostname=? AND EXISTS (
                             SELECT 1 FROM exams e WHERE e.id=exam_participants.exam_id
                               AND e.status<>'stopped')""",
                        ((name or "").strip(), (seat or "").strip(),
                         participant_id, exam_id, hostname))
        return cur.rowcount > 0


def update_student_profile_atomic(hostname, name, seat):
    with _transaction() as c:
        student = c.execute("SELECT * FROM students WHERE hostname=?",
                            (hostname,)).fetchone()
        if not student:
            return False
        exam_id = int(student["participant_exam_id"] or 0)
        participant_id = int(student["participant_id"] or 0)
        clean_name = (name or "").strip()
        clean_seat = (seat or "").strip()
        if exam_id and participant_id:
            current = c.execute("""SELECT p.id FROM exam_participants p
                                   JOIN exams e ON e.id=p.exam_id
                                   WHERE p.id=? AND p.exam_id=? AND p.hostname=?
                                     AND e.status<>'stopped'""",
                                (participant_id, exam_id, hostname)).fetchone()
            if current:
                duplicate = c.execute("""SELECT 1 FROM exam_participants
                                         WHERE exam_id=? AND hostname=? AND name=? AND seat=?
                                           AND id<>? LIMIT 1""",
                                      (exam_id, hostname, clean_name, clean_seat,
                                       participant_id)).fetchone()
                if duplicate:
                    raise ValueError("该学生信息与本场另一条参与记录重复")
                c.execute("""UPDATE exam_participants SET name=?, seat=?
                             WHERE id=? AND exam_id=? AND hostname=?""",
                          (clean_name, clean_seat, participant_id, exam_id, hostname))
        c.execute("UPDATE students SET name=?, seat=? WHERE hostname=?",
                  (clean_name, clean_seat, hostname))
        _push_command(c, hostname, "update_profile",
                      {"name": clean_name, "seat": clean_seat})
    return True


def snapshot_exam_students(exam_id):
    """结束前把当前考生身份写入本场快照，之后换学生不覆盖历史成绩。"""
    with _transaction() as c:
        for student in c.execute("SELECT * FROM students WHERE exam_id=?", (exam_id,)).fetchall():
            if (student["name"] or "").strip():
                _add_participant(c, exam_id, student["hostname"], student["name"],
                                 student["seat"], student["ip"])


def touch_participant(exam_id, hostname, status=None, participant_id=0):
    if not exam_id or not hostname or not participant_id:
        return 0
    with _transaction() as c:
        if status:
            cur = c.execute("""UPDATE exam_participants
                               SET last_seen=?, status=?
                               WHERE id=? AND exam_id=? AND hostname=?""",
                            (now(), status, participant_id, exam_id, hostname))
        else:
            cur = c.execute("""UPDATE exam_participants SET last_seen=?
                               WHERE id=? AND exam_id=? AND hostname=?""",
                            (now(), participant_id, exam_id, hostname))
        return cur.rowcount


def participant_snapshot(exam_id, participant_id):
    if not participant_id:
        return None
    c = conn()
    row = c.execute("SELECT * FROM exam_participants WHERE exam_id=? AND id=?",
                    (exam_id, int(participant_id))).fetchone()
    c.close()
    return dict(row) if row else None


def participants_for(exam_id):
    """这场考试的参与名单快照：[{hostname, name, seat, ip, first_seen, last_seen}]"""
    c = conn()
    rows = c.execute(
        """SELECT * FROM exam_participants WHERE exam_id=?
           ORDER BY seat, hostname, name""", (exam_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ==================== 画板作品 ====================
def save_drawing(exam_id, hostname, filename, saved_path, size,
                 kind="draw", participant_id=0):
    """原子替换一台机器在一个画图子任务上的作品元数据。"""
    kind = "beads" if kind == "beads" else "draw"
    old_path = None
    with _transaction() as c:
        if not _write_window_open(c, exam_id, hostname, participant_id):
            raise PermissionError("考试未在进行或最终收卷时间已过")
        old = c.execute(
            """SELECT saved_path FROM drawing_submissions
               WHERE exam_id=? AND hostname=? AND kind=? AND participant_id=?""",
            (exam_id, hostname, kind, int(participant_id or 0))).fetchone()
        old_path = old["saved_path"] if old else None
        c.execute(
            """INSERT INTO drawing_submissions
                   (exam_id, hostname, kind, filename, saved_path, size, updated_at,
                    participant_id)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(exam_id, hostname, kind, participant_id) DO UPDATE SET
                   filename=excluded.filename, saved_path=excluded.saved_path,
                   size=excluded.size, updated_at=excluded.updated_at""",
            (exam_id, hostname, kind, filename, saved_path, int(size or 0),
             time.time(), int(participant_id or 0)))
    if old_path and old_path != saved_path:
        _remove_managed_file(old_path)


def drawings_for(exam_id):
    """这场考试的画图作品，姓名座位取参与者快照。"""
    c = conn()
    rows = c.execute(
        """SELECT d.hostname, d.participant_id, d.kind, d.filename, d.saved_path,
                  d.size, d.updated_at, COALESCE(NULLIF(p.name,''),s.name,'') AS name,
                  COALESCE(NULLIF(p.seat,''),s.seat,'') AS seat
           FROM drawing_submissions d
           LEFT JOIN exam_participants p
             ON p.id=d.participant_id AND p.exam_id=d.exam_id
           LEFT JOIN students s ON s.hostname=d.hostname
           WHERE d.exam_id=?
           ORDER BY COALESCE(p.seat,s.seat,''), d.hostname, d.kind""",
        (exam_id,)).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("participant_id"):
            d["name"] = d.get("name") or ""
            d["seat"] = d.get("seat") or ""
        out.append(d)
    return out


def drawing_of(exam_id, hostname, kind="draw", participant_id=None):
    """某台机器某道画图题的作品（没有返回 None）"""
    kind = "beads" if kind == "beads" else "draw"
    c = conn()
    if participant_id is None:
        r = c.execute(
            "SELECT * FROM drawing_submissions "
            "WHERE exam_id=? AND hostname=? AND kind=? "
            "ORDER BY participant_id DESC, id DESC LIMIT 1",
            (exam_id, hostname, kind)).fetchone()
    else:
        r = c.execute(
            "SELECT * FROM drawing_submissions "
            "WHERE exam_id=? AND hostname=? AND kind=? AND participant_id=?",
            (exam_id, hostname, kind, int(participant_id))).fetchone()
    c.close()
    return dict(r) if r else None


# ==================== 打字模块（小学） ====================
def save_typing_report(exam_id, hostname, reports, participant_id=0,
                       allowed_tasks=None):
    """
    学生端周期性上报打字进度（每个子任务一行，upsert）。
    reports: [{task, progress, total, hits, typed, correct_chars,
               elapsed_ms, wpm, accuracy, reaction_ms, finished}, ...]
    同一 attempt 内拒绝倒退指标；新 attempt 允许计数从零重新开始。
    """
    if not reports:
        return
    now = time.time()

    def _i(v, default=0):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    with _transaction() as c:
        if not _write_window_open(c, exam_id, hostname, participant_id):
            raise PermissionError("考试未在进行或最终收卷时间已过")
        for r in reports:
            if not isinstance(r, dict):
                continue
            task = str(r.get("task") or "").strip()[:32]
            if not task or (allowed_tasks is not None and task not in allowed_tasks):
                continue
            attempt = max(0, _i(r.get("attempt")))
            c.execute("""
                INSERT INTO typing_results(exam_id, hostname, task,
                    progress, total, hits, typed, correct_chars,
                    elapsed_ms, wpm, accuracy, reaction_ms, finished, updated_at,
                    participant_id, attempt)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(exam_id, hostname, task, participant_id) DO UPDATE SET
                    attempt=excluded.attempt,
                    progress=excluded.progress, total=excluded.total,
                    hits=excluded.hits, typed=excluded.typed,
                    correct_chars=excluded.correct_chars,
                    elapsed_ms=excluded.elapsed_ms, wpm=excluded.wpm,
                    accuracy=excluded.accuracy,
                    reaction_ms=excluded.reaction_ms,
                    finished=CASE WHEN excluded.attempt>typing_results.attempt
                                 THEN excluded.finished
                                 ELSE MAX(typing_results.finished, excluded.finished) END,
                    updated_at=excluded.updated_at
                WHERE excluded.attempt>typing_results.attempt
                   OR (excluded.attempt=typing_results.attempt
                       AND excluded.elapsed_ms>=typing_results.elapsed_ms
                       AND excluded.progress>=typing_results.progress
                       AND excluded.hits>=typing_results.hits
                       AND excluded.typed>=typing_results.typed
                       AND excluded.correct_chars>=typing_results.correct_chars)
            """, (exam_id, hostname, task,
                  _i(r.get("progress")), _i(r.get("total")),
                  _i(r.get("hits")), _i(r.get("typed")),
                  _i(r.get("correct_chars")), _i(r.get("elapsed_ms")),
                  _f(r.get("wpm")), _f(r.get("accuracy")),
                  _i(r.get("reaction_ms")), _i(r.get("finished")), now,
                  int(participant_id or 0), attempt))


def typing_board(exam_id, allowed_tasks=None):
    """老师端实时看板：按本场参与者展示各打字子任务最新数据。"""
    c = conn()
    people = participants_for(exam_id)
    rows = c.execute(
        """SELECT participant_id, hostname, task, progress, total, hits, typed,
                  correct_chars, elapsed_ms, wpm, accuracy, reaction_ms, finished,
                  updated_at FROM typing_results WHERE exam_id=?""",
        (exam_id,)).fetchall()
    if allowed_tasks is not None:
        rows = [r for r in rows if r["task"] in allowed_tasks]
    students = {r["hostname"]: dict(r) for r in c.execute(
        "SELECT * FROM students WHERE exam_id=?", (exam_id,)).fetchall()}
    c.close()
    by_pid = {}
    legacy_hosts = set()
    for r in rows:
        pid = r["participant_id"] or 0
        if pid:
            by_pid.setdefault(pid, {})[r["task"]] = dict(r)
        else:
            legacy_hosts.add(r["hostname"])
    known = {p["hostname"] for p in people}
    for h in legacy_hosts - known:
        st = students.get(h, {})
        people.append({"id": 0, "hostname": h, "name": st.get("name", ""),
                       "seat": st.get("seat", ""), "last_seen": st.get("last_seen")})
    now = time.time()
    out = []
    for p in people:
        h = p["hostname"]
        tasks = by_pid.get(p["id"], {})
        if not p["id"] and not tasks:
            for r in rows:
                if not r["participant_id"] and r["hostname"] == h:
                    tasks[r["task"]] = dict(r)
        updated = max((t.get("updated_at") or 0 for t in tasks.values()), default=0)
        out.append({
            "participant_id": p["id"], "hostname": h,
            "name": p.get("name") or "", "seat": p.get("seat") or "",
            "online": bool(p.get("last_seen") and now - p["last_seen"] < 10),
            "tasks": tasks, "updated_at": updated,
        })

    def _key(r):
        try:
            return (0, int(r["seat"]), r["participant_id"])
        except (TypeError, ValueError):
            return (1, 0, r["participant_id"])
    out.sort(key=_key)
    return out


def set_typing_config(exam_id, cfg):
    """保存这场考试的打字模块配置（JSON dict）"""
    c = conn()
    c.execute("UPDATE exams SET typing_config=? WHERE id=?",
              (json.dumps(cfg, ensure_ascii=False), exam_id))
    c.commit()
    c.close()


def set_drawing_config(exam_id, cfg):
    """保存这场考试的画板配置（JSON dict，目前只有 brief 画题要求）"""
    c = conn()
    c.execute("UPDATE exams SET drawing_config=? WHERE id=?",
              (json.dumps(cfg, ensure_ascii=False), exam_id))
    c.commit()
    c.close()


def exam_theory_full(exam_id):
    """理论题满分"""
    qs = get_exam_questions(exam_id)
    return round(sum(q["score"] for q in qs), 2)


def _filter_sql(category="", qtype="", difficulty="", source=""):
    """拼筛选条件（分类 + 题型 + 难度 + 来源），返回 (where子句, 参数)"""
    w, args = ["COALESCE(owner_exam_id,0)=0"], []
    if category:
        w.append("category=?")
        args.append(category)
    if qtype:
        w.append("qtype=?")
        args.append(qtype)
    if difficulty:
        w.append("difficulty=?")
        args.append(difficulty)
    if source:
        w.append("source=?")
        args.append(source)
    return (" AND ".join(w) if w else "1=1"), args


def add_questions_by_filter(exam_id, category="", qtype="", limit=None):
    """
    按筛选条件把题加入试卷（不重复）。
    两个筛选都为空 = 全选题库。
    返回实际新增的题数。
    """
    where, args = _filter_sql(category, qtype)
    sql = f"SELECT id FROM questions WHERE {where} ORDER BY id"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    c = conn()
    rows = c.execute(sql, args).fetchall()
    c.close()
    have = list(exam_question_ids(exam_id))
    added = 0
    for r in rows:
        if r["id"] in have:
            continue
        have.append(r["id"])
        added += 1
    if added:
        set_exam_questions(exam_id, have)
    return added


def clear_exam_questions(exam_id):
    """清空某场考试的题目"""
    c = conn()
    c.execute("DELETE FROM exam_questions WHERE exam_id=?", (exam_id,))
    c.commit()
    c.close()
    return True


def random_pick(exam_id, category="", n=5, qtype=""):
    """
    按分类 + 题型随机抽 n 题加入试卷。
    返回本次实际抽中的题目 id 列表（空列表=没抽到）。
    """
    where, args = _filter_sql(category, qtype)
    c = conn()
    rows = c.execute(
        f"SELECT id FROM questions WHERE {where} ORDER BY RANDOM() LIMIT ?",
        args + [int(n)]).fetchall()
    picked = [r["id"] for r in rows]
    c.close()
    have = list(exam_question_ids(exam_id))
    for qid in picked:
        if qid not in have:
            have.append(qid)
    if len(have) != len(exam_question_ids(exam_id)):
        set_exam_questions(exam_id, have)
    return picked


# ==================== 实操题库 ====================
def _insert_practice(c, item, owner_exam_id=0):
    vals = (item.get("etype", "excel"), item.get("category", ""),
            item.get("level", ""),
            json.dumps(item.get("materials") or [], ensure_ascii=False),
            item.get("requirement", ""), item.get("save_as", ""),
            json.dumps(item.get("checks") or [], ensure_ascii=False))
    cur = c.execute(
        """INSERT INTO practice_questions
           (name,etype,category,level,materials,requirement,save_as,
            checks,owner_exam_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (item["name"],) + vals + (int(owner_exam_id or 0), now()))
    return cur.lastrowid


def add_practice(item):
    """新增一道实操题（重名就更新，方便反复装载内置题库）"""
    with _transaction() as c:
        row = c.execute(
            "SELECT id FROM practice_questions WHERE name=? AND COALESCE(owner_exam_id,0)=0",
            (item["name"],)).fetchone()
        vals = (item.get("etype", "excel"), item.get("category", ""),
                item.get("level", ""),
                json.dumps(item.get("materials") or [], ensure_ascii=False),
                item.get("requirement", ""), item.get("save_as", ""),
                json.dumps(item.get("checks") or [], ensure_ascii=False))
        if row:
            c.execute("""UPDATE practice_questions SET etype=?, category=?,
                         level=?, materials=?, requirement=?, save_as=?, checks=?
                         WHERE id=?""", vals + (row["id"],))
            pid, is_new = row["id"], False
        else:
            pid, is_new = _insert_practice(c, item), True
    invalidate()
    return pid, is_new


def update_practice(pid, item):
    with _transaction() as c:
        row = c.execute("SELECT owner_exam_id FROM practice_questions WHERE id=?", (pid,)).fetchone()
        if not row:
            raise ValueError("实操题不存在")
        if row["owner_exam_id"]:
            raise ValueError("考试包私有实操题不能直接修改")
        c.execute("""UPDATE practice_questions SET name=?, etype=?, category=?, level=?,
                     materials=?, requirement=?, save_as=?, checks=? WHERE id=?""",
                  (item["name"], item.get("etype", "excel"), item.get("category", ""),
                   item.get("level", ""),
                   json.dumps(item.get("materials") or [], ensure_ascii=False),
                   item.get("requirement", ""), item.get("save_as", ""),
                   json.dumps(item.get("checks") or [], ensure_ascii=False), pid))
    invalidate()


def _practice_row(r):
    d = dict(r)
    d["materials"] = json.loads(d["materials"] or "[]")
    d["checks"] = json.loads(d["checks"] or "[]")
    return d


def list_practice(etype=None, category=None, level=None):
    """按条件列出实操题库"""
    c = conn()
    sql = "SELECT * FROM practice_questions WHERE COALESCE(owner_exam_id,0)=0"
    args = []
    for col, val in (("etype", etype), ("category", category), ("level", level)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    sql += " ORDER BY etype, level, id"
    rows = c.execute(sql, args).fetchall()
    c.close()
    return [_practice_row(r) for r in rows]


def get_practice(pid):
    c = conn()
    r = c.execute("SELECT * FROM practice_questions WHERE id=?", (pid,)).fetchone()
    c.close()
    return _practice_row(r) if r else None


def delete_practice(pid):
    with _transaction() as c:
        row = c.execute("SELECT owner_exam_id FROM practice_questions WHERE id=?", (pid,)).fetchone()
        if not row:
            return
        if row["owner_exam_id"]:
            raise ValueError("考试包私有实操题不能直接删除")
        c.execute("DELETE FROM practice_questions WHERE id=?", (pid,))
    invalidate()


def practice_filters():
    """题库里有哪些分类 / 难度（筛选下拉用）"""
    c = conn()
    cats = [r[0] for r in c.execute(
        "SELECT DISTINCT category FROM practice_questions "
        "WHERE COALESCE(owner_exam_id,0)=0 AND category<>'' ORDER BY category").fetchall()]
    lvls = [r[0] for r in c.execute(
        "SELECT DISTINCT level FROM practice_questions "
        "WHERE COALESCE(owner_exam_id,0)=0 AND level<>'' ORDER BY level").fetchall()]
    c.close()
    return cats, lvls


def exam_tasks(exam_id):
    """
    这场考试装了哪几道实操题（按顺序）。
    没装过的话返回空列表 —— 老的单题考试走 exams 表里的旧字段。
    """
    c = conn()
    rows = c.execute("""SELECT ep.snapshot_json, p.* FROM exam_practice ep
                        LEFT JOIN practice_questions p ON p.id=ep.practice_id
                        WHERE ep.exam_id=? ORDER BY ep.qorder""",
                     (exam_id,)).fetchall()
    c.close()
    out = []
    for row in rows:
        if row["snapshot_json"]:
            out.append(_practice_row(json.loads(row["snapshot_json"])))
        elif row["id"] is not None:
            out.append(_practice_row(row))
    return out


def add_task_to_exam(exam_id, pid):
    """往考试里追加一道实操题，并冻结当时题目内容。"""
    with _transaction() as c:
        row = c.execute("SELECT * FROM practice_questions WHERE id=?", (pid,)).fetchone()
        if not row:
            return False
        have = c.execute("SELECT COUNT(*) FROM exam_practice WHERE exam_id=?",
                         (exam_id,)).fetchone()[0]
        snapshot = json.dumps(dict(row), ensure_ascii=False, default=str)
        c.execute("""INSERT OR IGNORE INTO exam_practice
                     (exam_id,practice_id,qorder,snapshot_json)
                     VALUES(?,?,?,?)""", (exam_id, pid, have, snapshot))
    ensure_practice_type(exam_id)
    _sync_exam_legacy(exam_id)
    return True


def set_exam_practice(exam_id, pids):
    with _transaction() as c:
        c.execute("DELETE FROM exam_practice WHERE exam_id=?", (exam_id,))
        for order, pid in enumerate(pids):
            row = c.execute("SELECT * FROM practice_questions WHERE id=?", (pid,)).fetchone()
            if not row:
                raise ValueError("考试中包含不存在的实操题")
            snapshot = json.dumps(dict(row), ensure_ascii=False, default=str)
            c.execute("""INSERT INTO exam_practice
                         (exam_id,practice_id,qorder,snapshot_json)
                         VALUES(?,?,?,?)""", (exam_id, pid, order, snapshot))
    ensure_practice_type(exam_id)
    _sync_exam_legacy(exam_id)
    invalidate()


def copy_exam_content(source_exam_id, target_exam_id):
    with _transaction() as c:
        questions = c.execute("""SELECT eq.question_id, eq.qorder, eq.snapshot_json,
                                        q.*
                                 FROM exam_questions eq
                                 LEFT JOIN questions q ON q.id=eq.question_id
                                 WHERE eq.exam_id=? ORDER BY eq.qorder""",
                              (source_exam_id,)).fetchall()
        c.execute("DELETE FROM exam_questions WHERE exam_id=?", (target_exam_id,))
        for order, row in enumerate(questions):
            snapshot = row["snapshot_json"]
            source_id = row["question_id"]
            if snapshot:
                content = _q(json.loads(snapshot))
            elif row["id"] is not None:
                content = _q(row)
                snapshot = json.dumps(dict(row), ensure_ascii=False, default=str)
            else:
                raise ValueError("源考试题目数据损坏")
            target_id = source_id
            if row["id"] is not None and row["owner_exam_id"]:
                target_id = _insert_question(c, content, target_exam_id)
                copied = c.execute("SELECT * FROM questions WHERE id=?", (target_id,)).fetchone()
                snapshot = json.dumps(dict(copied), ensure_ascii=False, default=str)
            c.execute("""INSERT INTO exam_questions
                         (exam_id,question_id,qorder,snapshot_json)
                         VALUES(?,?,?,?)""",
                      (target_exam_id, target_id, order, snapshot))
        practices = c.execute("""SELECT ep.practice_id, ep.qorder, ep.snapshot_json,
                                         p.*
                                  FROM exam_practice ep
                                  LEFT JOIN practice_questions p ON p.id=ep.practice_id
                                  WHERE ep.exam_id=? ORDER BY ep.qorder""",
                              (source_exam_id,)).fetchall()
        c.execute("DELETE FROM exam_practice WHERE exam_id=?", (target_exam_id,))
        for order, row in enumerate(practices):
            snapshot = row["snapshot_json"]
            source_id = row["practice_id"]
            if snapshot:
                content = _practice_row(json.loads(snapshot))
            elif row["id"] is not None:
                content = _practice_row(row)
                snapshot = json.dumps(dict(row), ensure_ascii=False, default=str)
            else:
                raise ValueError("源考试实操题数据损坏")
            target_id = source_id
            if row["id"] is not None and row["owner_exam_id"]:
                target_id = _insert_practice(c, content, target_exam_id)
                copied = c.execute("SELECT * FROM practice_questions WHERE id=?", (target_id,)).fetchone()
                snapshot = json.dumps(dict(copied), ensure_ascii=False, default=str)
            c.execute("""INSERT INTO exam_practice
                         (exam_id,practice_id,qorder,snapshot_json)
                         VALUES(?,?,?,?)""",
                      (target_exam_id, target_id, order, snapshot))
    ensure_practice_type(target_exam_id)
    _sync_exam_legacy(target_exam_id)
    invalidate()


def remove_task_from_exam(exam_id, pid):
    c = conn()
    c.execute("DELETE FROM exam_practice WHERE exam_id=? AND practice_id=?",
              (exam_id, pid))
    c.commit()
    c.close()
    _sync_exam_legacy(exam_id)
    return True


def set_question_types(exam_id, types):
    """
    写这场考试的题型。
    ⚠️ 空列表 = "没设置过"，学生端按"全部题型"处理（兼容旧考试）。
    所以这里只写非空集合，别拿空列表去覆盖。
    """
    c = conn()
    c.execute("UPDATE exams SET question_types=? WHERE id=?",
              (json.dumps(list(types), ensure_ascii=False), exam_id))
    c.commit()
    c.close()
    invalidate()


def ensure_practice_type(exam_id):
    """
    装操作题时顺手把「操作题」勾上。

    不这么做的话：考试建的时候只勾了单选，老师又从实操题库装了一道题，
    学生端 has_practice 仍是 False —— 这道操作题永远不显示，
    而老师那边看配置页一切正常，很难查出问题。
    """
    c = conn()
    r = c.execute("SELECT question_types FROM exams WHERE id=?",
                  (exam_id,)).fetchone()
    c.close()
    if not r:
        return
    try:
        types = json.loads(r["question_types"] or "[]")
    except (TypeError, ValueError):
        types = []
    if not isinstance(types, list):
        types = []
    # 空 = 全部题型（老考试），不用动
    if not types or "practice" in types:
        return
    types.append("practice")
    set_question_types(exam_id, types)


def _sync_exam_legacy(exam_id):
    """
    把「第一道实操题」同步到 exams 表的旧字段（requirement/checks/...）。
    素材以考试级配置为准，不再把实操题素材复制成考试素材，避免重复显示。

    为什么还要同步：老客户端只认单题模式，这样即使装了多道，
    老版本学生端也能正常做第一道，不会白屏。

    ⚠️ etype（操作题类型勾选）绝不能覆盖 —— 它是老师勾的多选
    （["word","excel","ppt"]），以前被写成第一道题的单类型，
    每装一次题「WPS 表格 / 演示」两个勾就被清掉。只在它为空时补一格。
    """
    tasks = exam_tasks(exam_id)
    if not tasks:
        invalidate()
        return
    t = tasks[0]
    c = conn()
    cur = c.execute("SELECT etype, materials FROM exams WHERE id=?", (exam_id,)).fetchone()
    raw = ((cur["etype"] if cur else "") or "").strip()
    materials = (cur["materials"] if cur else None) or "[]"
    if raw:
        c.execute("""UPDATE exams SET requirement=?, materials=?,
                     save_as=?, checks=? WHERE id=?""",
                  (t["requirement"], materials,
                   t["save_as"], json.dumps(t["checks"], ensure_ascii=False), exam_id))
    else:
        c.execute("""UPDATE exams SET etype=?, requirement=?, materials=?,
                     save_as=?, checks=? WHERE id=?""",
                  (json.dumps([t["etype"]], ensure_ascii=False),
                   t["requirement"], materials, t["save_as"],
                   json.dumps(t["checks"], ensure_ascii=False), exam_id))
    c.commit()
    c.close()
    invalidate()


def new_exam_from_practice(pid, duration=40, theory_duration=20):
    """用题库里的一道实操题直接新建一场考试，返回考试 id"""
    p = get_practice(pid)
    if not p:
        return None
    eid = create_exam({
        "title": p["name"], "etype": p["etype"],
        "duration": duration, "theory_duration": theory_duration,
        "requirement": p["requirement"],
        "materials": p["materials"], "save_as": p["save_as"],
        "checks": p["checks"],
    })
    # 只有操作题：题型写死成 practice，否则学生端会多出一块做不了的理论题
    set_question_types(eid, ["practice"])
    invalidate()
    return eid


# ---------------- 判分 ----------------
def _norm_text(s):
    """
    文本类答案归一化（填空 / 程序填空用）：
    去首尾空格、去所有空格、统一全半角标点、转大写。
    ⚠️ 不排序、不去重字符 —— 排序会让 1024 和 4012 判等（变位答案误判）。
    """
    if s is None:
        return ""
    s = str(s).strip().upper()
    # 全角括号、句号统一
    for a, b in (("（", "("), ("）", ")"), ("。", "."), ("，", ","), ("、", ",")):
        s = s.replace(a, b)
    return s.replace(" ", "").replace("\t", "")


def norm_answer(s):
    """
    选择题答案归一化：去空格、统一标点、字符排序去重（"BA" == "AB"）。
    只用于 single / multi —— 文本类答案请用 _norm_text。
    """
    s = _norm_text(s)
    # multi：把 "A,B" 和 "AB" 都整理成字符集合
    s = s.replace(",", "").replace(";", "")
    return "".join(sorted(set(s)))


# 判断题答案的各种写法统一映射
JUDGE_TRUE = {"T", "TRUE", "对", "正确", "是", "√", "Y", "YES", "1", "A", "RIGHT"}
JUDGE_FALSE = {"F", "FALSE", "错", "错误", "否", "×", "X", "N", "NO", "0", "B", "WRONG"}


def norm_judge(s):
    """判断题 -> 'T' / 'F'，无法识别返回原值大写"""
    v = str(s or "").strip().upper()
    if v in JUDGE_TRUE:
        return "T"
    if v in JUDGE_FALSE:
        return "F"
    return v


# 程序填空：多个空之间用 || 分隔，每个空的可接受答案之间用 | 分隔
# 例：range(1,11)|range(1, 11) || i*i|i**2
BLANK_SEP = "||"


def split_blanks(spec):
    """
    拆分程序填空的答案串 -> [[可接受答案...], ...]
    每个空一个列表。
    """
    out = []
    for part in str(spec or "").split(BLANK_SEP):
        alts = [_norm_text(a) for a in part.split("|")]
        alts = [a for a in alts if a]
        if alts:
            out.append(set(alts))
    return out


def count_blanks(code):
    """数代码模板里有几个空（____ 支持 4~10 个下划线）"""
    import re
    return len(re.findall(r"_{4,}", str(code or "")))


def mark_need_rejudge(qid):
    """
    判题机异常时打个标记，让老师后台能看到（不要悄无声息）。

    选型说明：theory_answers 表没有 need_rejudge 字段，加列要做迁移脚本；
    这里选最轻的方案 —— 写进 logs 表（老师后台 /logs 页能直接看到），
    同时打到服务端日志文件，双保险。老师看到后可到成绩页点「重判程序填空」补判。
    """
    msg = f"程序填空判题异常 qid={qid}，待重判（成绩页可点「重判程序填空」补判）"
    logging.warning(msg)
    try:
        c = conn()
        c.execute("INSERT INTO logs(hostname, level, msg, created_at) VALUES(?,?,?,?)",
                  ("judge", "error", msg, now()))
        c.commit()
        c.close()
    except Exception:
        pass


def judge(question, student_answer, cancel_event=None):
    """判一道题，返回 (是否正确, 得分)"""
    qtype = question["qtype"]
    score = float(question["score"])

    if qtype == "code":
        # ① 优先「跑一遍看输出」：答案写法千变万化（for/while、range(1,11)
        #    /range(1, 11)），只在题库里穷举参考答案必然漏判。
        #    老师填了「期望输出」就启用：输出一致直接满分。
        #    没填 / 空没填完 / 服务器没环境 → 返回 None，走下面的按空给分。
        #    实际运行不匹配或失败返回 False，直接 0 分，不再回退。
        try:
            import coderun
            r = coderun.judge_code(question, student_answer,
                                   cancel_event=cancel_event)
        except Exception:
            # 判题机自身的 bug 不能静默吞掉（否则退化成按空给分，老师发现不了）
            logging.exception("coderun 判题异常 qid=%s", question.get("id"))
            r = "error"                    # 与 None 严格区分
        if r is True:
            return (1, score)
        if r is False:
            return (0, 0)
        if r == "error":
            # 判题机故障：记录待重判，让老师后台能看到，不要悄无声息
            mark_need_rejudge(question.get("id"))
        # r is None / "error" → 仅在判题未实际运行时按空给分回退
        # ② 回退：按空给分，对几个空得几分
        want = split_blanks(question["answer"])
        got_raw = str(student_answer or "")
        # 学生答案也用 || 分隔
        got_parts = [_norm_text(x) for x in got_raw.split(BLANK_SEP)]
        if not want:
            return (0, 0)
        hit = 0
        for i, alts in enumerate(want):
            g = got_parts[i] if i < len(got_parts) else ""
            if g and g in alts:
                hit += 1
        # 部分正确给部分分（按比例，保留 2 位）
        if hit == 0:
            return (0, 0)
        got_score = round(score * hit / len(want), 2)
        return (1 if hit == len(want) else 0, got_score)

    if qtype == "judge":
        return (1, score) if norm_judge(student_answer) == norm_judge(question["answer"]) else (0, 0)

    std = norm_answer(question["answer"])
    got = norm_answer(student_answer)

    if qtype == "multi":
        return (1, score) if got == std else (0, 0)
    if qtype == "blank":
        # 每个空必须完整匹配一个可接受答案，不能靠堆叠错误答案碰中正确答案。
        def _nb(s):
            return _norm_text(s).strip("。．.,，、；;：:！!？?…—")
        want = [{_nb(a) for a in alts} for alts in split_blanks(question["answer"])]
        got = [_nb(x) for x in str(student_answer or "").split(BLANK_SEP)]
        if not want or len(got) != len(want):
            return (0, 0)
        hit = sum(1 for alts, g in zip(want, got) if g and g in alts)
        return (1 if hit == len(want) else 0,
                round(score * hit / len(want), 2))
    if qtype == "drag":
        # 拖拽题：答案形如 "1:A|2:C|3:B"（槽位序号:拖拽项字母）。
        # 拖对几个槽位就给几分（部分分），全对才算 correct=1 ——
        # 与程序填空「按空给分」一个口径。
        # ⚠️ 有的题答案不唯一（同一个槽位放 A 或 B 都对），
        #    同一槽位写多遍就是「都算对」：1:A|1:B|2:C
        def _pairs(s, many=False):
            out = {}
            for part in str(s or "").split("|"):
                part = part.strip()
                if not part or ":" not in part:
                    continue
                slot, item = part.split(":", 1)
                slot = re.sub(r"[^0-9A-Za-z]", "", slot).upper()
                item = re.sub(r"[^0-9A-Za-z]", "", item).upper()
                if not slot or not item:
                    continue
                if many:
                    out.setdefault(slot, set()).add(item)
                else:
                    out[slot] = item
            return out

        want = _pairs(question["answer"], many=True)
        got_pairs = _pairs(student_answer)
        if not want:
            return (0, 0)
        hit = sum(1 for k, v in want.items() if got_pairs.get(k) in v)
        return (1 if hit == len(want) else 0,
                round(score * hit / len(want), 2))
    # single
    return (1, score) if got == std else (0, 0)


def rejudge_code_answers(exam_id):
    """
    把本场所有程序填空的答案，按「运行结果」重新判一遍。

    什么时候需要：
      · 老师改了某题的期望输出，但学生早就交过了（旧分数不会自动变）
      · 判分时服务器环境缺失（比如没装 g++），后来补上了

    返回 (重判条数, 判对条数)；取消或考试状态变化时抛出 RuntimeError。
    """
    qs = {q["id"]: q for q in get_exam_questions(exam_id)
          if q["qtype"] == "code"}
    if not qs:
        return 0, 0
    try:
        import coderun
        coderun.clear_cache()
    except ImportError:
        pass
    c = conn()
    try:
        rows = c.execute(
            "SELECT id, question_id, answer FROM theory_answers WHERE exam_id=?",
            (exam_id,)).fetchall()
    finally:
        c.close()
    cancel_event = exam_cancel_event(exam_id)
    judged = []
    for r in rows:
        q = qs.get(r["question_id"])
        if not q:
            continue
        if cancel_event.is_set():
            raise RuntimeError("重判已取消或考试状态已变化")
        correct, got = judge(q, r["answer"], cancel_event=cancel_event)
        if cancel_event.is_set():
            raise RuntimeError("重判已取消或考试状态已变化")
        judged.append((correct, got, r["id"]))
    with _transaction() as c:
        ex = c.execute("SELECT status FROM exams WHERE id=?", (exam_id,)).fetchone()
        if cancel_event.is_set() or not ex or ex["status"] not in ("running", "paused"):
            raise RuntimeError("重判已取消或考试状态已变化")
        c.executemany("UPDATE theory_answers SET correct=?, got=? WHERE id=?",
                      judged)
    invalidate()
    return len(judged), sum(1 for correct, _, _ in judged if correct)


def save_theory_answer(exam_id, hostname, qid, answer, participant_id=0, version=0):
    """保存并判分一道题；带版本号时拒绝旧版本覆盖。"""
    q = get_question(qid)
    if not q or qid not in set(exam_question_ids(exam_id)):
        return None
    correct, got = judge(q, answer)
    with _transaction() as c:
        if not _write_window_open(c, exam_id, hostname, participant_id):
            raise PermissionError("考试未在进行或最终收卷时间已过")
        c.execute("""INSERT INTO theory_answers
                     (exam_id,hostname,question_id,answer,correct,got,answered_at,
                      participant_id,version)
                     VALUES(?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(exam_id,hostname,question_id,participant_id)
                     DO UPDATE SET answer=?, correct=?, got=?, answered_at=?, version=?
                     WHERE excluded.version=0 OR theory_answers.version=0
                        OR excluded.version>theory_answers.version""",
                  (exam_id, hostname, qid, str(answer or ""), correct, got, now(),
                   int(participant_id or 0), int(version or 0), str(answer or ""),
                   correct, got, now(), int(version or 0)))
    return {"correct": correct, "got": got}


def save_theory_answers(exam_id, hostname, answers, participant_id=0, versions=None,
                        cancel_event=None):
    """
    批量保存并判分（学生端每 10 秒交一批）。
    一个事务写完，比逐条 INSERT + 逐条 commit 快得多。
    :param answers: {qid: 答案}
    :return: (已保存题数, 本次得分)
    """
    n = 0
    cancel_event = None if cancel_event is None else cancel_event
    c = conn()
    try:
        # 先判分、再写库。程序填空题的 judge 会真跑一遍代码（subprocess，
        # 最长几十秒），绝不能在持有全局锁时跑 —— 否则判题期间 50 台机器的
        # 心跳、其他提交、老师页面的所有查询全被挡死。
        judged = {}
        allowed = set(exam_question_ids(exam_id))
        for qid, ans in (answers or {}).items():
            try:
                qid = int(qid)
            except (TypeError, ValueError):
                continue
            if qid not in allowed or qid in judged:
                continue
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("判题已取消")
            q = get_question(qid)
            if not q:
                continue
            question = dict(q)
            question["hostname"] = hostname
            question["user_id"] = participant_id or hostname
            correct, got = judge(question, ans, cancel_event=cancel_event)
            try:
                version = int((versions or {}).get(str(qid), 0) or 0)
            except (TypeError, ValueError, AttributeError):
                version = 0
            judged[qid] = (str(ans or ""), correct, got, max(0, version))

        # 判完了，只把写库放进锁内，一个事务写完
        with _global_lock:
            if not _write_window_open(c, exam_id, hostname, participant_id):
                raise PermissionError("考试未在进行或最终收卷时间已过")
            for qid, (answer, correct, got, version) in judged.items():
                cur = c.execute(
                    """INSERT INTO theory_answers
                       (exam_id,hostname,question_id,answer,correct,got,answered_at,
                        participant_id,version)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(exam_id,hostname,question_id,participant_id)
                       DO UPDATE SET answer=?, correct=?, got=?, answered_at=?, version=?
                       WHERE excluded.version=0 OR theory_answers.version=0
                          OR excluded.version>theory_answers.version""",
                    (exam_id, hostname, qid, answer, correct, got, now(),
                     int(participant_id or 0), version, answer, correct,
                     got, now(), version))
                n += max(0, cur.rowcount)
            c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        raise
    finally:
        c.close()
    return n


def theory_score(exam_id, hostname, participant_id=None):
    """某学生的理论题得分，只统计本场试卷内的题目。"""
    c = conn()
    base = """SELECT SUM(a.got) AS s, COUNT(*) AS n FROM theory_answers a
              JOIN exam_questions eq ON eq.exam_id=a.exam_id AND eq.question_id=a.question_id
              WHERE a.exam_id=? AND a.hostname=?"""
    if participant_id is None:
        r = c.execute(base, (exam_id, hostname)).fetchone()
    else:
        r = c.execute(base + " AND a.participant_id=?",
                      (exam_id, hostname, int(participant_id))).fetchone()
    c.close()
    return round(r["s"] or 0, 2), r["n"] or 0


def theory_answers_of(exam_id, hostname, participant_id=None):
    c = conn()
    base = """SELECT a.question_id, a.answer FROM theory_answers a
              JOIN exam_questions eq ON eq.exam_id=a.exam_id AND eq.question_id=a.question_id
              WHERE a.exam_id=? AND a.hostname=?"""
    if participant_id is None:
        rows = c.execute(base, (exam_id, hostname)).fetchall()
    else:
        rows = c.execute(base + " AND a.participant_id=?",
                         (exam_id, hostname, int(participant_id))).fetchall()
    c.close()
    return {r["question_id"]: r["answer"] for r in rows}


def theory_answer_rows(exam_id):
    """
    全班逐条作答记录（评讲页用）：[{question_id, hostname, participant_id, answer, correct}]
    """
    c = conn()
    rows = c.execute("""SELECT question_id, hostname, participant_id, answer, correct
                        FROM theory_answers WHERE exam_id=?""",
                     (exam_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def theory_finished(exam_id, hostname, participant_id=None):
    """是否已答完所有理论题"""
    qs = get_exam_questions(exam_id)
    if not qs:
        return True
    done = theory_answers_of(exam_id, hostname, participant_id)
    allowed = {q["id"] for q in qs}
    return allowed.issubset(done)


# ---------------- 阶段控制 ----------------
def set_phase(exam_id, phase):
    c = conn()
    c.execute("UPDATE exams SET phase=?, phase_at=? WHERE id=?", (phase, now(), exam_id))
    c.commit()
    c.close()


def get_phase(exam_id):
    e = get_exam(exam_id)
    return e["phase"] if e else None


def phase_remaining(exam_id, include_paused=False):
    """
    考试总剩余秒数。

    ⚠️ 一场考试只有一个倒计时（考试时长 duration），从开考那一刻起算，
    理论题 / 实操题共用，切换阶段不再重置。以前理论和实操各算各的，
    学生切个阶段就"满血复活"一段时长。

    include_paused=True：已暂停的考试也算剩余（教师端展示用），
    算到"按下暂停那一刻"，暂停期间不走表；
    默认（学生端）暂停时返回 None，学生端停止计时。
    """
    e = get_exam(exam_id)
    if not e:
        return None
    mins = e["duration"] or 0
    if e["status"] == "running":
        start = e["start_at"] or e["phase_at"] or now()
        return max(0, int(mins * 60 - (now() - start)))
    if e["status"] == "paused" and include_paused:
        start = e["start_at"] or e["phase_at"] or now()
        stop = e.get("paused_at") or now()
        return max(0, int(mins * 60 - (stop - start)))
    return None


# ---------------- 成绩汇总 ----------------
# ==================== 考试包 导出 / 导入 ====================
#
# 把一场考试（配置 + 题目 + 评分规则 + 素材清单）打包成 JSON，
# 可以拿到别的机房导入，不用重新组卷。
#
# 注意：素材文件本身不在包里（二进制太大），只记文件名。
# 迁移时要手动把 materials/ 目录一起拷过去。

BUNDLE_VER = 2


def _bundle_json(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("考试包 JSON 字段格式不正确") from exc
    return value


def _bundle_text(value, default=""):
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError("考试包文本字段格式不正确")
    return value


def _normalize_bundle_question(item):
    if not isinstance(item, dict):
        raise ValueError("考试包题目格式不正确")
    qtype = item.get("qtype", "single")
    if qtype not in ("single", "multi", "judge", "blank", "code", "drag"):
        raise ValueError("考试包包含不支持的题型")
    stem = _bundle_text(item.get("stem", ""))
    if not stem:
        raise ValueError("考试包题目缺少题干")
    options = item.get("options", [])
    if not isinstance(options, list):
        raise ValueError("考试包题目选项必须是数组")
    try:
        score = float(item.get("score", 2))
    except (TypeError, ValueError) as exc:
        raise ValueError("考试包题目分值格式不正确") from exc
    payload = item.get("payload", "") or ""
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False)
    elif not isinstance(payload, str):
        raise ValueError("考试包题目 payload 格式不正确")
    return {
        "qtype": qtype, "stem": stem, "options": options,
        "answer": str(item.get("answer", "")).strip(), "score": score,
        "category": _bundle_text(item.get("category", "")),
        "language": _bundle_text(item.get("language", "")),
        "code": _bundle_text(item.get("code", "")),
        "stdin_data": _bundle_text(item.get("stdin_data", "")),
        "expected_output": _bundle_text(item.get("expected_output", "")),
        "difficulty": _bundle_text(item.get("difficulty", "")),
        "source": _bundle_text(item.get("source", "")),
        "payload": payload,
    }


def _normalize_bundle_practice(item):
    if not isinstance(item, dict):
        raise ValueError("考试包实操题格式不正确")
    name = _bundle_text(item.get("name", ""))
    etype = item.get("etype", "excel")
    if not name or etype not in ("word", "excel", "ppt"):
        raise ValueError("考试包实操题缺少名称或类型无效")
    materials = item.get("materials", [])
    checks = item.get("checks", [])
    if not isinstance(materials, list) or not all(isinstance(x, str) for x in materials):
        raise ValueError("考试包实操题素材必须是文件名数组")
    if not isinstance(checks, list):
        raise ValueError("考试包实操题评分规则必须是数组")
    return {
        "name": name, "etype": etype,
        "category": _bundle_text(item.get("category", "")),
        "level": _bundle_text(item.get("level", "")),
        "materials": materials,
        "requirement": _bundle_text(item.get("requirement", "")),
        "save_as": _bundle_text(item.get("save_as", "")),
        "checks": checks,
    }


def export_bundle(eid):
    """导出一场考试为 dict（不含学生答卷）。"""
    ex = get_exam(eid)
    if not ex:
        return None
    return {
        "version": BUNDLE_VER,
        "exported_at": now(),
        "exam": {
            "title": ex["title"], "etype": ex["etype"],
            "duration": ex["duration"],
            "theory_duration": ex.get("theory_duration", 20),
            "requirement": ex.get("requirement", ""),
            "materials": _bundle_json(ex.get("materials"), []),
            "save_as": ex.get("save_as", ""),
            "checks": _bundle_json(ex.get("checks"), []),
            "question_types": _bundle_json(ex.get("question_types"), []),
            "klass": ex.get("klass", ""),
            "require_complete": int(ex.get("require_complete") or 0),
            "passwordless": int(ex.get("passwordless") or 0),
            "finish_note": ex.get("finish_note", ""),
            "typing_config": _bundle_json(ex.get("typing_config"), {}),
            "drawing_config": _bundle_json(ex.get("drawing_config"), {}),
        },
        "roster": roster_of(eid),
        "practice_tasks": [
            {k: v for k, v in task.items()
             if k not in ("id", "owner_exam_id", "created_at")}
            for task in exam_tasks(eid)
        ],
        "questions": [
            {k: q.get(k, "") for k in (
                "qtype", "stem", "options", "answer", "score", "category",
                "language", "code", "stdin_data", "expected_output",
                "difficulty", "source", "payload")}
            for q in get_exam_questions(eid)
        ],
    }


def import_bundle(data, new_title=None):
    """原子导入考试包；题目与实操题作为考试私有数据，不污染全局题库。"""
    if not isinstance(data, dict) or not isinstance(data.get("exam"), dict):
        return None, {"ok": False, "msg": "文件格式不对，不是有效的考试包"}
    version = data.get("version", 1)
    if version not in (1, 2):
        return None, {"ok": False, "msg": f"不支持的考试包版本：{version}"}
    ex = data["exam"]
    raw_questions = data.get("questions", [])
    raw_tasks = data.get("practice_tasks", [])
    raw_roster = data.get("roster", [])
    if not isinstance(raw_questions, list) or not isinstance(raw_tasks, list):
        return None, {"ok": False, "msg": "考试包题目或实操题格式不正确"}
    if version == 2 and not isinstance(raw_roster, list):
        return None, {"ok": False, "msg": "考试包名单格式不正确"}
    try:
        title = _bundle_text(new_title or ex.get("title") or "导入的考试")
        if not title:
            raise ValueError("考试名称不能为空")
        duration = int(ex.get("duration", 60))
        theory_duration = int(ex.get("theory_duration", duration))
        if not 1 <= duration <= 600 or not 1 <= theory_duration <= 600:
            raise ValueError("考试时长必须在 1～600 分钟之间")
        materials = _bundle_json(ex.get("materials"), [])
        checks = _bundle_json(ex.get("checks"), [])
        qtypes = _bundle_json(ex.get("question_types"), [])
        typing_config = _bundle_json(ex.get("typing_config"), {})
        drawing_config = _bundle_json(ex.get("drawing_config"), {})
        if not isinstance(materials, list) or not all(isinstance(x, str) for x in materials):
            raise ValueError("考试素材必须是文件名数组")
        if not isinstance(checks, list) or not isinstance(qtypes, list):
            raise ValueError("考试评分规则或题型格式不正确")
        if not isinstance(typing_config, dict) or not isinstance(drawing_config, dict):
            raise ValueError("考试打字或画板配置格式不正确")
        questions = [_normalize_bundle_question(q) for q in raw_questions]
        tasks = [_normalize_bundle_practice(p) for p in raw_tasks]
        roster = []
        for row in raw_roster:
            if not isinstance(row, dict):
                raise ValueError("考试包名单格式不正确")
            roster.append((_bundle_text(row.get("seat", "")),
                           _bundle_text(row.get("name", "")),
                           _bundle_text(row.get("ip", ""))))
    except (TypeError, ValueError) as exc:
        return None, {"ok": False, "msg": str(exc)}

    try:
        with _transaction() as c:
            code = make_exam_code(c=c)
            cur = c.execute("""INSERT INTO exams
                (title,etype,duration,theory_duration,requirement,materials,save_as,
                 checks,status,phase,require_complete,passwordless,code,klass,
                 finish_note,typing_config,drawing_config,created_at)
                VALUES(?,?,?,?,?,?,?,?,'draft','idle',?,?,?,?,?,?,?,?)""",
                (title, _bundle_text(ex.get("etype", "word")), duration,
                 theory_duration, _bundle_text(ex.get("requirement", "")),
                 json.dumps(materials, ensure_ascii=False),
                 _bundle_text(ex.get("save_as", "")),
                 json.dumps(checks, ensure_ascii=False),
                 int(bool(ex.get("require_complete", 0))),
                 int(bool(ex.get("passwordless", 0))), code,
                 _bundle_text(ex.get("klass", "")),
                 _bundle_text(ex.get("finish_note", "")),
                 json.dumps(typing_config, ensure_ascii=False),
                 json.dumps(drawing_config, ensure_ascii=False), now()))
            eid = cur.lastrowid
            c.execute("UPDATE exams SET question_types=? WHERE id=?",
                      (json.dumps(qtypes, ensure_ascii=False), eid))
            for order, q in enumerate(questions):
                qid = _insert_question(c, q, eid)
                row = c.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
                c.execute("""INSERT INTO exam_questions
                             (exam_id,question_id,qorder,snapshot_json)
                             VALUES(?,?,?,?)""",
                          (eid, qid, order,
                           json.dumps(dict(row), ensure_ascii=False, default=str)))
            for order, task in enumerate(tasks):
                pid = _insert_practice(c, task, eid)
                row = c.execute("SELECT * FROM practice_questions WHERE id=?", (pid,)).fetchone()
                c.execute("""INSERT INTO exam_practice
                             (exam_id,practice_id,qorder,snapshot_json)
                             VALUES(?,?,?,?)""",
                          (eid, pid, order,
                           json.dumps(dict(row), ensure_ascii=False, default=str)))
            for seat, name, ip in roster:
                c.execute("""INSERT INTO exam_roster
                             (exam_id,seat,name,ip,created_at) VALUES(?,?,?,?,?)""",
                          (eid, seat, name, ip, now()))
    except (TypeError, ValueError, sqlite3.Error):
        return None, {"ok": False, "msg": "考试包内容无效，导入已回滚"}

    invalidate()
    return eid, {"ok": True, "msg": f"导入完成：题目 {len(questions)} 道，"
                                    f"实操题 {len(tasks)} 道，名单 {len(roster)} 条"}


def practice_full_of(exam_id):
    """
    实操满分：一场考试装了多道操作题时 = 各题评分规则分值之和。
    老的单题考试（规则挂在 exams 表）退回 submissions 里记录的满分。
    """
    tasks = exam_tasks(exam_id)
    if tasks:
        tot = 0.0
        for t in tasks:
            try:
                tot += sum(float(c.get("score", 0)) for c in (t.get("checks") or []))
            except Exception:
                pass
        return round(tot, 2)
    subs = [s for s in list_submissions(exam_id) if s["full"]]
    return subs[0]["full"] if subs else 0


def full_report(exam_id):
    """按本场参与者合并理论与实操成绩；老数据 participant_id=0 单独回退。"""
    subs = list_submissions(exam_id)
    subs_by_pid = {}
    legacy_by_host = {}
    for s in subs:
        pid = int(s.get("participant_id") or 0)
        if pid:
            subs_by_pid.setdefault(pid, []).append(s)
        else:
            legacy_by_host.setdefault(s["hostname"], []).append(s)
    tf = exam_theory_full(exam_id)
    stu_now = {s["hostname"]: s for s in list_students()}

    parts = participants_for(exam_id)
    named_counts = {}
    for p in parts:
        if (p["name"] or "").strip():
            named_counts[p["hostname"]] = named_counts.get(p["hostname"], 0) + 1
    participant_status = {p["id"]: p.get("status") for p in parts}
    people, seen = [], set()
    for p in parts:
        nm = (p["name"] or "").strip()
        if not nm and named_counts.get(p["hostname"]):
            continue
        key = (p["hostname"], nm, (p["seat"] or "").strip())
        if key in seen:
            continue
        seen.add(key)
        people.append({"participant_id": p["id"], "hostname": p["hostname"],
                       "name": nm, "seat": (p["seat"] or "").strip(),
                       "ip": (p["ip"] or "").strip()})

    c = conn()
    evidence_pids = set()
    evidence_hosts = set()
    required_task_ids = {r["practice_id"] for r in c.execute(
        "SELECT practice_id FROM exam_practice WHERE exam_id=?", (exam_id,))}
    for t in ("theory_answers", "typing_results", "drawing_submissions"):
        try:
            for r in c.execute(f"SELECT DISTINCT hostname, participant_id FROM {t} "
                               f"WHERE exam_id=?", (exam_id,)):
                evidence_hosts.add(r["hostname"])
                if r["participant_id"]:
                    evidence_pids.add(r["participant_id"])
        except Exception:
            pass
    for pid in evidence_pids:
        if any(p["participant_id"] == pid for p in people):
            continue
        r = c.execute("SELECT * FROM exam_participants WHERE id=? AND exam_id=?",
                      (pid, exam_id)).fetchone()
        if r:
            people.append({"participant_id": pid, "hostname": r["hostname"],
                           "name": (r["name"] or "").strip(),
                           "seat": (r["seat"] or "").strip(),
                           "ip": (r["ip"] or "").strip()})
    c.close()

    known_hosts = {p["hostname"] for p in people}
    legacy_theory_hosts = set()
    legacy_people = []
    c = conn()
    try:
        for r in c.execute("""SELECT hostname, COUNT(*) AS n FROM theory_answers
                              WHERE exam_id=? AND participant_id=0
                              GROUP BY hostname""", (exam_id,)).fetchall():
            legacy_theory_hosts.add(r["hostname"])
            if named_counts.get(r["hostname"], 0) > 1:
                legacy_people.append({"participant_id": 0, "hostname": r["hostname"],
                                      "name": "历史记录", "seat": "",
                                      "ip": (stu_now.get(r["hostname"], {}).get("ip") or "")})
    except Exception:
        pass
    c.close()
    people = legacy_people + people
    known_hosts = {p["hostname"] for p in people}
    for h, st in stu_now.items():
        if (st.get("exam_id") or 0) == exam_id and h not in known_hosts:
            person = {"participant_id": 0, "hostname": h,
                      "name": (st.get("name") or "").strip(),
                      "seat": (st.get("seat") or "").strip(),
                      "ip": st.get("ip") or ""}
            if h in legacy_theory_hosts or h in legacy_by_host:
                people.insert(0, person)
            else:
                people.append(person)
            known_hosts.add(h)
    for h in sorted(evidence_hosts - known_hosts):
        st = stu_now.get(h) or {}
        person = {"participant_id": 0, "hostname": h,
                  "name": (st.get("name") or "").strip(),
                  "seat": (st.get("seat") or "").strip(),
                  "ip": st.get("ip") or ""}
        if h in legacy_theory_hosts or h in legacy_by_host:
            people.insert(0, person)
        else:
            people.append(person)
        known_hosts.add(h)
    for h in sorted(legacy_theory_hosts - known_hosts):
        st = stu_now.get(h, {})
        people.append({"participant_id": 0, "hostname": h,
                       "name": (st.get("name") or "").strip(),
                       "seat": (st.get("seat") or "").strip(),
                       "ip": st.get("ip") or ""})
        known_hosts.add(h)
    for h, group in legacy_by_host.items():
        if h in known_hosts and named_counts.get(h, 0) <= 1:
            continue
        if h in known_hosts and any(p["hostname"] == h and not p["participant_id"]
                                    for p in people):
            continue
        st = stu_now.get(h, {})
        has_legacy_theory = h in legacy_theory_hosts
        person = {"participant_id": 0, "hostname": h,
                  "name": "历史记录" if h in known_hosts
                          else (st.get("name") or "").strip(),
                  "seat": (st.get("seat") or "").strip() if h not in known_hosts else "",
                  "ip": st.get("ip") or ""}
        if has_legacy_theory:
            people.insert(0, person)
        else:
            people.append(person)
        known_hosts.add(h)

    legacy_claimed = set()
    legacy_theory_claimed = set()
    rows = []
    for p in people:
        h = p["hostname"]
        pid = p["participant_id"]
        group = subs_by_pid.get(pid, []) if pid else []
        use_legacy = not pid or named_counts.get(h, 0) <= 1
        if use_legacy and h not in legacy_claimed:
            group = group + legacy_by_host.get(h, [])
            legacy_claimed.add(h)
        ts, tn = theory_score(exam_id, h, pid)
        if not pid:
            if h in legacy_theory_hosts:
                ts, tn = theory_score(exam_id, h)
                legacy_theory_claimed.add(h)
        elif not tn and h in legacy_theory_hosts and h not in legacy_theory_claimed \
                and named_counts.get(h, 0) <= 1:
            ts, tn = theory_score(exam_id, h)
            legacy_theory_claimed.add(h)
        graded = [s for s in group if s["score"] is not None]
        practice = round(sum(s["score"] or 0 for s in graded), 2) if graded else None
        st = stu_now.get(h) or {}
        online = bool(st.get("online") and (not p["name"] or st.get("name") == p["name"]))
        submitted_task_ids = {int(s.get("task_id") or 0) for s in group}
        submitted = (participant_status.get(pid) == "submitted"
                     or (required_task_ids.issubset(submitted_task_ids)
                         if required_task_ids else bool(group)))
        rows.append({
            "participant_id": pid, "hostname": h,
            "ip": p["ip"] or st.get("ip") or "",
            "name": p["name"], "seat": p["seat"], "online": online,
            "sub_id": group[0]["id"] if group else None,
            "theory": ts, "theory_full": tf, "theory_n": tn,
            "practice": practice,
            "practice_full": round(sum(s["full"] or 0 for s in graded), 2)
                             if graded else None,
            "submitted": submitted,
            "filename": "、".join(s["filename"] or "" for s in group).strip("、"),
            "total": round((ts or 0) + (practice or 0), 2),
        })

    def ip_key(value):
        value = (value or "").strip()
        try:
            return (0,) + tuple(int(part) for part in value.split("."))
        except ValueError:
            return (1, value)

    def key(r):
        st = (r.get("seat") or "").strip()
        if st:
            try:
                return (0, int(st), r["hostname"], r["name"])
            except ValueError:
                return (0, 0, st, r["name"])
        return (1, ip_key(r.get("ip") or r["hostname"]),
                r["hostname"], r["name"])
    rows.sort(key=key)
    return rows


def status_counts(exam_id=None):
    """按本场参与者统计；未指定考试时保留机器维度兼容查询。"""
    c = conn()
    if exam_id:
        rows = c.execute("""SELECT status, COUNT(*) AS n FROM exam_participants
                            WHERE exam_id=? GROUP BY status""", (exam_id,)).fetchall()
    else:
        rows = c.execute("""SELECT status, COUNT(*) AS n FROM students
                            GROUP BY status""").fetchall()
    c.close()
    return {r["status"]: r["n"] for r in rows}


def class_summary(exam_id):
    """
    全班成绩概览（成绩页顶部卡片 + 图表用）。
    分数一律换算成百分制（理论满分 + 实操满分不一定等于 100）。
    """
    rows = full_report(exam_id)
    practice_full = practice_full_of(exam_id)
    theory_full = exam_theory_full(exam_id)
    full_total = (theory_full or 0) + (practice_full or 0)

    totals = [r["total"] for r in rows]
    n = len(totals)
    avg = round(sum(totals) / n, 1) if n else 0
    # 百分制换算
    def pct(v):
        return round(v / full_total * 100, 1) if full_total else 0
    pa = [pct(t) for t in totals]
    pass_n = sum(1 for p in pa if p >= 60)          # 及格（60%）
    good_n = sum(1 for p in pa if p >= 85)          # 优秀（85%）
    return {
        "count": n,
        "submitted": sum(1 for r in rows if r["submitted"]),
        "avg": avg,
        "avg_pct": round(sum(pa) / n, 1) if n else 0,
        "max": max(totals) if n else 0,
        "min": min(totals) if n else 0,
        "full_total": full_total,
        "pass_n": pass_n,
        "pass_rate": round(pass_n / n * 100, 1) if n else 0,
        "good_n": good_n,
        "good_rate": round(good_n / n * 100, 1) if n else 0,
    }


def score_distribution(exam_id):
    """
    分数段分布（按百分制折算）：<60 / 60-69 / 70-79 / 80-89 / 90-100
    """
    rows = full_report(exam_id)
    practice_full = practice_full_of(exam_id)
    full_total = (exam_theory_full(exam_id) or 0) + (practice_full or 0)
    labels = ["不及格 (<60)", "及格 (60-69)", "中等 (70-79)",
              "良好 (80-89)", "优秀 (90-100)"]
    buckets = [0] * 5
    for r in rows:
        p = (r["total"] / full_total * 100) if full_total else 0
        if p < 60:
            buckets[0] += 1
        elif p < 70:
            buckets[1] += 1
        elif p < 80:
            buckets[2] += 1
        elif p < 90:
            buckets[3] += 1
        else:
            buckets[4] += 1
    return {"labels": labels, "values": buckets}


def question_rates(exam_id):
    """
    每道理论题的得分率（讲评用：哪道题全班都不会）。
    得分率 = 全班该题得分之和 / (该题满分 × 作答人数)
    """
    qs = get_exam_questions(exam_id)
    if not qs:
        return []
    c = conn()
    rows = c.execute(
        """SELECT a.question_id, SUM(a.got) AS got, COUNT(*) AS n
           FROM theory_answers a
           JOIN exam_questions eq ON eq.exam_id=a.exam_id AND eq.question_id=a.question_id
           WHERE a.exam_id=? GROUP BY a.question_id""",
        (exam_id,)).fetchall()
    c.close()
    stat = {r["question_id"]: (r["got"] or 0, r["n"] or 0) for r in rows}
    out = []
    for i, q in enumerate(qs, 1):
        got, n = stat.get(q["id"], (0, 0))
        full = float(q["score"] or 0)
        rate = (got / (full * n) * 100) if (n and full) else 0
        out.append({
            "no": i, "qtype": q["qtype"], "score": full,
            "answered": n,
            "rate": round(min(rate, 100), 1),
        })
    return out


def check_rates(exam_id):
    """
    实操题各评分点的平均得分率（讲评用：学生普遍漏做哪一步）。
    从 submissions.detail 里聚合，detail 形如 [{id,got,full,msg}]
    """
    agg = {}
    for s in list_submissions(exam_id):
        try:
            detail = json.loads(s["detail"] or "[]")
        except Exception:
            continue
        for d in detail:
            key = (d.get("id") or "未命名")
            full = float(d.get("full") or 0)
            got = float(d.get("got") or 0)
            a = agg.setdefault(key, {"got": 0.0, "full": 0.0, "n": 0})
            a["got"] += got
            a["full"] += full
            a["n"] += 1
    out = []
    for k, a in agg.items():
        rate = (a["got"] / a["full"] * 100) if a["full"] else 0
        out.append({"name": k, "rate": round(rate, 1),
                    "full": round(a["full"] / a["n"], 1) if a["n"] else 0})
    out.sort(key=lambda x: x["rate"])
    return out


def latest_score_json(exam_id):
    """学生端「看看成绩」：汇总学生能看的部分"""
    rows = full_report(exam_id)
    return rows


# ---------------- 成绩导出 ----------------
def export_full_csv(exam_id, out_path):
    import csv
    try:
        from .plagiarism import csv_safe_value
    except ImportError:
        from plagiarism import csv_safe_value
    rows = full_report(exam_id)
    exam = get_exam(exam_id)
    pf = practice_full_of(exam_id)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["座位号", "姓名", "机器名", "IP", "理论题", "理论满分",
                    "实操题", "实操满分", "总分", "已交卷", "文件名"])
        for r in rows:
            w.writerow([csv_safe_value(value) for value in
                        [r.get("seat", ""), r.get("name", ""),
                         r["hostname"], r["ip"], r["theory"], r["theory_full"],
                         r["practice"] if r["practice"] is not None else "",
                         r["practice_full"] if r["practice_full"] else pf,
                         r["total"], "是" if r["submitted"] else "否", r["filename"]]])
    return out_path


if __name__ == "__main__":
    init()
    print(f"数据库已初始化: {DB_PATH}")
