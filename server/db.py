"""
数据层：SQLite
50 台规模用 SQLite 足够，开 WAL 模式提升并发写入能力
"""
import json
import logging
import os
import re
import sqlite3
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
# 测试可用环境变量 EXAM_DB 指到临时库，不动正式库
DB_PATH = os.environ.get("EXAM_DB") or os.path.join(ROOT, "exam.db")

# 学生机状态
ONLINE_TIMEOUT = 15      # 超过 15 秒没心跳视为离线（心跳每 3 秒一次，约 5 次未到）

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
                # synchronous=OFF：不每次 commit 都 fsync。
                # 慢磁盘上这是写性能的瓶颈（实测差一个数量级）。
                # 代价：断电可能丢最后几秒事务，但 SQLite 仍保证库不损坏；
                # 学生端有断线重连，丢一次心跳无影响。
                real.execute("PRAGMA synchronous=OFF")
                # 明确用默认 rollback journal（不用 WAL，原因见上方注释）
                try:
                    real.execute("PRAGMA journal_mode=DELETE")
                except Exception:
                    pass
                _global_conn = _SafeConn(real)
    return _global_conn


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
        note       TEXT DEFAULT ''
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
        detail      TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS commands (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        target     TEXT NOT NULL,      -- 'all' 或具体 hostname
        ctype      TEXT NOT NULL,      -- start_exam / collect / stop / message / open_app
        payload    TEXT DEFAULT '{}',  -- JSON
        created_at REAL,
        status     TEXT DEFAULT 'pending'   -- pending / sent
    );

    CREATE TABLE IF NOT EXISTS logs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        hostname   TEXT,
        level      TEXT DEFAULT 'info',
        msg        TEXT,
        created_at REAL
    );

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
        created_at REAL
    );

    -- ===== 考试装了哪些实操题（一场可装多道）=====
    CREATE TABLE IF NOT EXISTS exam_practice (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id     INTEGER NOT NULL,
        practice_id INTEGER NOT NULL,
        qorder      INTEGER DEFAULT 0,
        UNIQUE(exam_id, practice_id)
    );

    -- ===== 考试与题目的关联（组卷）=====
    CREATE TABLE IF NOT EXISTS exam_questions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id     INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        qorder      INTEGER DEFAULT 0,
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
        UNIQUE(exam_id, hostname, question_id)
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
        updated_at    REAL,
        UNIQUE(exam_id, hostname, task)
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
        UNIQUE(exam_id, hostname, kind)
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
        UNIQUE(exam_id, hostname, name)
    );

    CREATE INDEX IF NOT EXISTS idx_cmd_status ON commands(status, target);
    CREATE INDEX IF NOT EXISTS idx_sub_exam   ON submissions(exam_id);

    -- ===== 本场学生名单（花名册）：学生端登录时必须在这个名单里 =====
    CREATE TABLE IF NOT EXISTS exam_roster (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id    INTEGER NOT NULL,
        seat       TEXT DEFAULT '',
        name       TEXT NOT NULL,
        created_at REAL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_roster
        ON exam_roster(exam_id, name, seat);
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
    c = conn()
    c.execute("INSERT INTO logs(hostname,level,msg,created_at) VALUES(?,?,?,?)",
              (hostname, level, msg, now()))
    c.commit()
    c.close()


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
                       materials,save_as,checks,status,phase,require_complete,
                       code,klass,created_at)
                       VALUES(?,?,?,?,?,?,?,?, 'draft', 'idle', ?, ?, ?, ?)""",
                    (data["title"], data["etype"], int(data.get("duration", 60)),
                     int(data.get("theory_duration", 20)),
                     data.get("requirement", ""),
                     json.dumps(data.get("materials", []), ensure_ascii=False),
                     data.get("save_as", ""),
                     json.dumps(data.get("checks", []), ensure_ascii=False),
                     int(data.get("require_complete", 0)),
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
    materials = data.get("materials") if "materials" in data \
        else (json.loads(ex.get("materials") or "[]") if ex else [])
    checks = data.get("checks") if "checks" in data \
        else (json.loads(ex.get("checks") or "[]") if ex else [])
    # 交卷结束语（学生交卷后看到的话）
    finish_note = pick("finish_note", str, "")

    c = conn()
    c.execute("""UPDATE exams SET title=?, etype=?, duration=?, theory_duration=?,
                 requirement=?, materials=?, save_as=?, checks=?, require_complete=?,
                 finish_note=? WHERE id=?""",
              (title, etype, duration, theory_duration, requirement,
               json.dumps(materials or [], ensure_ascii=False), save_as,
               json.dumps(checks or [], ensure_ascii=False), require_complete,
               finish_note, eid))
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


def set_exam_status(eid, status):
    """
    改考试状态。

    ⚠️ 暂停必须真正"停表"：记下暂停时刻，恢复时把暂停期间流逝的
    时长补回 phase_at / start_at。以前只改 status，学生暂停 10 分钟
    就白白损失 10 分钟答题时间。
    """
    c = conn()
    if status == "running":
        # 恢复（或首次开始）：先补回暂停掉的时长
        r = c.execute("SELECT paused_at FROM exams WHERE id=?", (eid,)).fetchone()
        pa = r["paused_at"] if r else None
        if pa:
            delta = now() - float(pa)
            c.execute("""UPDATE exams SET
                         phase_at = COALESCE(phase_at, ?) + ?,
                         start_at  = COALESCE(start_at,  ?) + ?,
                         paused_at = NULL
                         WHERE id=?""",
                      (now(), delta, now(), delta, eid))
        # start_at 只在第一次开始时记（恢复不重置起点）
        c.execute("UPDATE exams SET start_at=? WHERE id=? AND start_at IS NULL",
                  (now(), eid))
        c.execute("UPDATE exams SET status='running' WHERE id=?", (eid,))
    elif status == "paused":
        c.execute("UPDATE exams SET status='paused', paused_at=? WHERE id=?",
                  (now(), eid))
    elif status == "stopped":
        c.execute("UPDATE exams SET status='stopped', stop_at=? WHERE id=?",
                  (now(), eid))
    else:
        c.execute("UPDATE exams SET status=? WHERE id=?", (status, eid))
    c.commit()
    c.close()


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
                     ("seat", "ALTER TABLE students ADD COLUMN seat TEXT DEFAULT ''")):
        if col not in cols:
            try:
                c.execute(ddl)
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
                     ("payload", "ALTER TABLE questions ADD COLUMN payload TEXT DEFAULT ''")):
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
    c.commit()
    c.close()


def _migrate_student_id(new_id, mac):
    """
    老数据迁移：以前学生身份 = Windows 机器名，同名机器互相覆盖；
    现在身份 = client_id（基于 MAC，唯一）。
    按 MAC 找到老行，把 students / theory_answers / submissions /
    typing_results 里的旧 hostname 换成新的 client_id，历史成绩不丢。
    """
    if not mac:
        return
    c = conn()
    old = c.execute("""SELECT hostname, exam_id FROM students
                       WHERE mac=? AND hostname<>?""",
                    (mac, new_id)).fetchone()
    if not old:
        c.close()
        return
    old_host = old["hostname"]
    eid = old["exam_id"]
    c.execute("UPDATE OR IGNORE students SET hostname=? WHERE hostname=?",
              (new_id, old_host))
    for tbl in ("theory_answers", "submissions", "typing_results"):
        c.execute(f"UPDATE {tbl} SET hostname=? WHERE hostname=? "
                  f"AND (exam_id=? OR exam_id IS NULL)",
                  (new_id, old_host, eid))
    c.commit()
    c.close()


def upsert_student(hostname, ip, mac, name=None, seat=None):
    """
    登记/更新学生机。
    name / seat 只在给了值时更新，避免心跳把已填的信息冲掉。

    hostname 这里传的是学生身份标识：新客户端是 client_id（唯一），
    老客户端是 Windows 机器名。第一次见到新身份时，按 MAC 把老数据迁过来。
    """
    if mac:
        _migrate_student_id(hostname, mac)
    c = conn()
    c.execute("""INSERT INTO students(hostname,ip,mac,name,seat,last_seen)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(hostname) DO UPDATE SET
                     ip=COALESCE(excluded.ip, students.ip),
                     mac=COALESCE(excluded.mac, students.mac),
                     name=COALESCE(NULLIF(excluded.name,''), students.name),
                     seat=COALESCE(NULLIF(excluded.seat,''), students.seat),
                     last_seen=excluded.last_seen""",
              (hostname, ip, mac, name or "", seat or "", now()))
    c.commit()
    c.close()


def unbind_student(hostname):
    """
    解除这台机器的学生绑定（换学生坐这台机器时用）。
    只清姓名/座位号，IP/MAC/历史成绩都留着。
    """
    c = conn()
    c.execute("UPDATE students SET name='', seat='' WHERE hostname=?",
              (hostname,))
    c.commit()
    c.close()


# ================= 本场学生名单（花名册）=================
#
# 设计：学生端**不做机器绑定**，正常输入姓名/座位号；
# 老师端给**每场考试**配一份名单，学生登录时姓名必须在名单里才让进
# （防止学生乱填名字/替别人考）。没配名单 = 不限制（老考试照常）。

def roster_of(exam_id):
    """本场名单：[{id, seat, name}]"""
    c = conn()
    rows = c.execute("""SELECT id, seat, name FROM exam_roster
                        WHERE exam_id=? ORDER BY id""", (exam_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def roster_text_of(exam_id):
    """名单 → 编辑框文本（一行一个「姓名,座位号」）"""
    return "\n".join(
        (f"{r['name']},{r['seat']}" if r["seat"] else r["name"])
        for r in roster_of(exam_id))


def set_roster(exam_id, rows):
    """整份替换名单。rows: [(seat, name), ...]"""
    c = conn()
    c.execute("DELETE FROM exam_roster WHERE exam_id=?", (exam_id,))
    for seat, name in rows:
        c.execute("""INSERT INTO exam_roster(exam_id, seat, name, created_at)
                     VALUES(?,?,?,?)""", (exam_id, seat or "", name or "", now()))
    c.commit()
    c.close()


def add_roster(exam_id, seat, name):
    """加一个学生；重名重座位的返回 False"""
    name = (name or "").strip()
    seat = (seat or "").strip()
    if not name:
        return False
    c = conn()
    dup = c.execute("""SELECT 1 FROM exam_roster
                       WHERE exam_id=? AND name=? AND seat=?""",
                    (exam_id, name, seat)).fetchone()
    if dup:
        c.close()
        return False
    c.execute("""INSERT INTO exam_roster(exam_id, seat, name, created_at)
                 VALUES(?,?,?,?)""", (exam_id, seat, name, now()))
    c.commit()
    c.close()
    return True


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


def roster_check(exam_id, name, seat=""):
    """
    学生登录时校验在不在名单里。返回 (ok, msg)。

    · 没配名单 → 不限制（老考试行为不变）
    · 配了名单 → 姓名必须在名单里；填了座位号且名单里该姓名有座位号时要一致
    · 姓名还没填（刚启动还没登录）→ 先放行，等填了再校验
    """
    rows = roster_of(exam_id)
    if not rows:
        return True, ""
    name = (name or "").strip()
    if not name:
        return True, ""
    hit = [r for r in rows if r["name"] == name]
    if not hit:
        return False, (f"「{name}」不在本场考试的学生名单里。\n"
                       f"请联系老师核对姓名，或让老师把这位学生加进名单。")
    seat = (seat or "").strip()
    if seat:
        want = [r["seat"] for r in hit if r["seat"]]
        if want and seat not in want:
            return False, (f"「{name}」的座位号应该是 {want[0]}，"
                           f"你填的是 {seat}。\n请改正后再进入考试。")
    return True, ""


def heartbeat(hostname, status=None):
    c = conn()
    if status:
        c.execute("UPDATE students SET last_seen=?, status=? WHERE hostname=?",
                  (now(), status, hostname))
    else:
        c.execute("UPDATE students SET last_seen=? WHERE hostname=?", (now(), hostname))
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
        d["online"] = (t - (d["last_seen"] or 0)) < ONLINE_TIMEOUT
        out.append(d)
    return out


def clear_offline_students():
    """把掉线的学生机记录删掉（在线的保留）。返回删了几条。"""
    c = conn()
    n = c.execute(
        "DELETE FROM students WHERE last_seen IS NULL OR last_seen<=?",
        (now() - ONLINE_TIMEOUT,)).rowcount
    c.commit()
    c.close()
    return n


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
def push_command(target, ctype, payload=None):
    c = conn()
    cur = c.execute("INSERT INTO commands(target,ctype,payload,created_at) VALUES(?,?,?,?)",
                    (target, ctype, json.dumps(payload or {}, ensure_ascii=False), now()))
    cid = cur.lastrowid
    c.commit()
    c.close()
    return cid


def fetch_commands(hostname):
    """学生拉取待执行命令（含全体命令），并标记为已发送"""
    c = conn()
    rows = c.execute("""SELECT * FROM commands
                        WHERE status='pending' AND (target='all' OR target=?)
                        ORDER BY id""", (hostname,)).fetchall()
    out = [{"id": r["id"], "type": r["ctype"],
            "payload": json.loads(r["payload"] or "{}")} for r in rows]
    if rows:
        ids = [r["id"] for r in rows]
        ph = ",".join("?" for _ in ids)
        c.execute(f"UPDATE commands SET status='sent' WHERE id IN ({ph})", ids)
        c.commit()
    c.close()
    return out


def pending_count():
    c = conn()
    n = c.execute("SELECT COUNT(*) FROM commands WHERE status='pending'").fetchone()[0]
    c.close()
    return n


def clear_commands():
    c = conn()
    c.execute("DELETE FROM commands WHERE status='sent'")
    c.commit()
    c.close()


# ---------------- 提交 ----------------
def save_submission(hostname, exam_id, filename, path, size, task_id=0):
    """
    保存一次提交。
    task_id：一场考试可以装多道操作题，用它区分是哪一道（0 = 老的单题模式）
    """
    c = conn()
    st = c.execute("SELECT id FROM students WHERE hostname=?", (hostname,)).fetchone()
    sid = st["id"] if st else None
    # 同一学生同一场考试的同一道题只保留最新一份；
    # 先记下旧文件路径，替换后把磁盘上的旧答卷一并删掉，避免 uploads 越堆越多
    old = c.execute("""SELECT saved_path FROM submissions
                       WHERE hostname=? AND exam_id=? AND task_id=?""",
                    (hostname, exam_id, task_id)).fetchone()
    c.execute("DELETE FROM submissions WHERE hostname=? AND exam_id=? AND task_id=?",
              (hostname, exam_id, task_id))
    cur = c.execute("""INSERT INTO submissions(student_id,hostname,exam_id,filename,
                       saved_path,size,submitted_at,task_id) VALUES(?,?,?,?,?,?,?,?)""",
                    (sid, hostname, exam_id, filename, path, size, now(), task_id))
    sub_id = cur.lastrowid
    c.commit()
    c.close()
    if old and old["saved_path"] and os.path.abspath(old["saved_path"]) != os.path.abspath(path):
        try:
            os.remove(old["saved_path"])
        except OSError:
            pass
    return sub_id


def submission_of_task(exam_id, hostname, task_id):
    """某个学生在某道操作题上的提交（用于判断交没交）"""
    c = conn()
    r = c.execute("""SELECT * FROM submissions
                     WHERE exam_id=? AND hostname=? AND task_id=?""",
                  (exam_id, hostname, int(task_id or 0))).fetchone()
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
        t = get_practice(tid)
        if t:
            return t["checks"]
    return json.loads((exam or {}).get("checks") or "[]")


def task_in_exam(exam_id, task_id):
    """这道实操题是否属于该考试（防止学生端乱传 task_id）"""
    c = conn()
    r = c.execute("SELECT 1 FROM exam_practice WHERE exam_id=? AND practice_id=?",
                  (exam_id, int(task_id or 0))).fetchone()
    c.close()
    return r is not None


def material_used_by_others(name, exclude_exam=None):
    """
    素材文件还被谁引用（删除素材前先问一句，避免删掉别场考试在用的文件）。
    返回引用它的考试/实操题描述列表；空列表 = 没人用了，可安全删磁盘文件。
    """
    used = []
    c = conn()
    for r in c.execute("SELECT id, title, materials FROM exams").fetchall():
        if exclude_exam and r["id"] == exclude_exam:
            continue
        try:
            mats = json.loads(r["materials"] or "[]")
        except Exception:
            mats = []
        if name in mats:
            used.append(f"考试「{r['title']}」")
    for r in c.execute("SELECT name, materials FROM practice_questions").fetchall():
        try:
            mats = json.loads(r["materials"] or "[]")
        except Exception:
            mats = []
        if name in mats:
            used.append(f"实操题「{r['name']}」")
    c.close()
    return used


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


def has_submission(exam_id, hostname):
    """
    这个学生交过答卷吗。
    心跳每 3 秒一次，不能再用 list_submissions 拉全表
    （全班答卷的 detail 是很大的 JSON，50 台机器一起拉会把服务器拖垮）。
    """
    c = conn()
    r = c.execute("SELECT 1 FROM submissions WHERE exam_id=? AND hostname=? LIMIT 1",
                  (exam_id, hostname)).fetchone()
    c.close()
    return r is not None


def update_score(sub_id, score, full, detail):
    c = conn()
    c.execute("UPDATE submissions SET score=?, full=?, detail=? WHERE id=?",
              (score, full, json.dumps(detail, ensure_ascii=False), sub_id))
    c.commit()
    c.close()


# ---------------- 统计 ----------------
def stats(exam_id):
    """
    控制台顶部数字。
    ⚠️ 学生数只数「这场考试的」（绑定了本场 或 最近还在心跳的），
       不是 students 全表 —— 全表会把以前各场考试的机器都算上，越攒越多。
    """
    c = conn()
    recent = now() - 300
    total = c.execute(
        """SELECT COUNT(DISTINCT hostname) FROM students
           WHERE exam_id=? OR last_seen>?""", (exam_id, recent)).fetchone()[0]
    online = c.execute(
        """SELECT COUNT(DISTINCT hostname) FROM students
           WHERE (exam_id=? OR last_seen>?) AND last_seen>?""",
        (exam_id, recent, now() - ONLINE_TIMEOUT)).fetchone()[0]
    submitted = c.execute("SELECT COUNT(*) FROM submissions WHERE exam_id=?", (exam_id,)).fetchone()[0]
    c.close()
    return {"students": total, "online": online, "submitted": submitted}


# ================= 理论题 =================

# ---------------- 题库 ----------------
def add_question(q):
    """新增题目，返回 id"""
    c = conn()
    cur = c.execute("""INSERT INTO questions(qtype,stem,options,answer,score,
                        category,language,code,stdin_data,expected_output,
                        difficulty,source,payload,created_at)
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
                     q.get("payload", "") or "", now()))
    qid = cur.lastrowid
    c.commit()
    c.close()
    invalidate()          # 题库变了：清热缓存（判分/试卷都读缓存，别读到旧题）
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
        else:
            v = v or ""
        sets.append(f"{col}=?")
        vals.append(v)
    if not sets:
        return
    vals.append(qid)
    c = conn()
    c.execute(f"UPDATE questions SET {', '.join(sets)} WHERE id=?", vals)
    c.commit()
    c.close()
    invalidate()          # 改完题必须清缓存，否则判分还在用改之前的那份


def delete_question(qid):
    c = conn()
    c.execute("DELETE FROM questions WHERE id=?", (qid,))
    c.execute("DELETE FROM exam_questions WHERE question_id=?", (qid,))
    c.commit()
    c.close()


def delete_student(sid):
    """删除单个学生"""
    c = conn()
    c.execute("DELETE FROM students WHERE id=?", (sid,))
    c.commit()
    c.close()


def delete_exam(eid):
    """
    删除考试，连同它派生出来的一切。

    以前只删了 4 张表，留下两个尾巴：
      · exam_practice 里的操作题关联变成孤儿行
      · commands 里该场考试的 start_exam/collect 等命令还在，
        学生端下次心跳会把旧命令再拉一次（"又开始考试了？"）
      · students.exam_id 还指着已删除的考试
    """
    c = conn()
    # 先记下答卷文件路径，删完记录后把磁盘上的文件一并清掉
    file_paths = [r["saved_path"] for r in c.execute(
        "SELECT saved_path FROM submissions WHERE exam_id=?", (eid,)).fetchall()]
    file_paths += [r["saved_path"] for r in c.execute(
        "SELECT saved_path FROM drawing_submissions WHERE exam_id=?",
        (eid,)).fetchall()]
    c.execute("DELETE FROM exam_questions WHERE exam_id=?", (eid,))
    c.execute("DELETE FROM theory_answers WHERE exam_id=?", (eid,))
    c.execute("DELETE FROM submissions WHERE exam_id=?", (eid,))
    c.execute("DELETE FROM exam_practice WHERE exam_id=?", (eid,))
    c.execute("DELETE FROM exam_participants WHERE exam_id=?", (eid,))
    c.execute("DELETE FROM drawing_submissions WHERE exam_id=?", (eid,))
    c.execute("DELETE FROM typing_results WHERE exam_id=?", (eid,))
    # 命令的 exam_id 在 JSON 里，按值匹配
    kill = []
    for r in c.execute("SELECT id, payload FROM commands").fetchall():
        try:
            p = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if str(p.get("exam_id")) == str(eid):
            kill.append(r["id"])
    for cid in kill:
        c.execute("DELETE FROM commands WHERE id=?", (cid,))
    c.execute("UPDATE students SET exam_id=NULL WHERE exam_id=?", (eid,))
    c.execute("DELETE FROM exams WHERE id=?", (eid,))
    c.commit()
    c.close()
    for p in file_paths:
        if p:
            try:
                os.remove(p)
            except OSError:
                pass
    invalidate()


def delete_submission(sid):
    """删除单条提交记录"""
    c = conn()
    c.execute("DELETE FROM submissions WHERE id=?", (sid,))
    c.commit()
    c.close()


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
    sql = "SELECT * FROM questions WHERE 1=1"
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
            f"GROUP BY {field}").fetchall()
        out[field] = {(r["v"] or "未分类"): r["n"] for r in rows}
    out["total"] = c.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
    c.close()
    return out


def list_field_values(field):
    """题库里某个字段出现过哪些值（难度/来源筛选用）。field 只允许白名单。"""
    if field not in ("difficulty", "source", "category"):
        return []
    c = conn()
    rows = c.execute(
        f"SELECT DISTINCT {field} AS v FROM questions WHERE {field}<>'' "
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
           WHERE COALESCE(difficulty,'')=''""").rowcount
    n_s = c.execute(
        """UPDATE questions SET source = CASE
               WHEN category LIKE '%会考%' THEN '雅安会考模拟题'
               ELSE '自创题' END
           WHERE COALESCE(source,'')=''""").rowcount
    c.commit()
    c.close()
    if n_d or n_s:
        invalidate()
    return n_d, n_s


def list_categories():
    c = conn()
    rows = c.execute("SELECT DISTINCT category FROM questions WHERE category<>'' ORDER BY category").fetchall()
    c.close()
    return [r["category"] for r in rows]


def import_questions(items):
    """
    批量导入，返回 (新增数, 跳过数)

    用单个事务：47 题从「每题一次 commit」的 33 秒降到一次 commit。
    慢文件系统上每次 commit 都要建/删 journal 文件，这是主要开销。
    """
    ok = skip = 0
    c = conn()
    try:
        with _global_lock:
            for q in items:
                try:
                    if not q.get("stem") or not q.get("answer"):
                        skip += 1
                        continue
                    if q.get("qtype") not in ("single", "multi", "judge",
                                              "blank", "code", "drag"):
                        skip += 1
                        continue
                    # 去重：题干+题型+答案都一样就是同一道题，跳过
                    # （开机会自动导入内置题库，没有去重会越导越多）
                    dup = c.execute(
                        """SELECT 1 FROM questions WHERE stem=? AND qtype=?
                           AND answer=? LIMIT 1""",
                        (q.get("stem", ""), q["qtype"],
                         str(q.get("answer", "")).strip())).fetchone()
                    if dup:
                        skip += 1
                        continue
                    # 拖拽题的槽位：item 里可以给 slots 数组，也可以给 payload 字符串
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
                except Exception:
                    skip += 1
            c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
    return ok, skip


# ---------------- 组卷 ----------------
def set_exam_questions(exam_id, qids):
    """设置某场考试的理论题（按顺序）"""
    c = conn()
    c.execute("DELETE FROM exam_questions WHERE exam_id=?", (exam_id,))
    for i, qid in enumerate(qids):
        c.execute("INSERT INTO exam_questions(exam_id,question_id,qorder) VALUES(?,?,?)",
                  (exam_id, qid, i))
    c.commit()
    c.close()


def get_exam_questions(exam_id):
    """取试卷题目（按 qorder）"""
    c = conn()
    rows = c.execute("""SELECT q.* FROM exam_questions eq
                        JOIN questions q ON q.id=eq.question_id
                        WHERE eq.exam_id=? ORDER BY eq.qorder""", (exam_id,)).fetchall()
    c.close()
    return [_q(r) for r in rows]


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
    """
    一场考试里【所有人】的已答题数，一次查询拿全。
    （大屏/统计页以前是逐个学生查，50 人就是 50 次查询）
    返回 {hostname: 已答题数}
    """
    c = conn()
    rows = c.execute(
        """SELECT hostname, COUNT(*) AS n FROM theory_answers
           WHERE exam_id=? GROUP BY hostname""", (exam_id,)).fetchall()
    c.close()
    return {r["hostname"]: r["n"] for r in rows}


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
def add_participant(exam_id, hostname, name=None, seat=None, ip=None):
    """
    记一笔「这位学生参与了这场考试」。
    参与 = 考试进行中登录进来，或留下过任何作答证据
    （理论答案 / 实操提交 / 打字上报 / 画板作品）。

    按 (考试, 机器, 姓名) 一行：同一台机器换人登录，两个人都在名单里。
    姓名/座位是**当时的快照** —— 之后学生记录被清掉、机器换人，
    这场考试的名单永远都在（成绩页/导出都从这里出人）。
    """
    if not exam_id or not hostname:
        return
    name = (name or "").strip()
    seat = (seat or "").strip()
    ip = (ip or "").strip()
    t = now()
    c = conn()
    c.execute("""INSERT INTO exam_participants
                   (exam_id, hostname, name, seat, ip, first_seen, last_seen)
                 VALUES(?,?,?,?,?,?,?)
                 ON CONFLICT(exam_id, hostname, name) DO UPDATE SET
                   seat=CASE WHEN excluded.seat!='' THEN excluded.seat
                             ELSE exam_participants.seat END,
                   ip=CASE WHEN excluded.ip!='' THEN excluded.ip
                           ELSE exam_participants.ip END,
                   last_seen=excluded.last_seen""",
              (exam_id, hostname, name, seat, ip, t, t))
    c.commit()
    c.close()


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
                 kind="draw"):
    """
    保存/覆盖这台机器的画图作品（一人每题一份，重复上传以最新为准）。
    kind: draw=画板创作（自由画）, beads=拼豆题
    """
    kind = "beads" if kind == "beads" else "draw"
    c = conn()
    old = c.execute(
        """SELECT saved_path FROM drawing_submissions
           WHERE exam_id=? AND hostname=? AND kind=?""",
        (exam_id, hostname, kind)).fetchone()
    if old and old["saved_path"] and old["saved_path"] != saved_path:
        try:
            os.remove(old["saved_path"])
        except OSError:
            pass
    c.execute(
        """INSERT INTO drawing_submissions
               (exam_id, hostname, kind, filename, saved_path, size, updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(exam_id, hostname, kind) DO UPDATE SET
               filename=excluded.filename, saved_path=excluded.saved_path,
               size=excluded.size, updated_at=excluded.updated_at""",
        (exam_id, hostname, kind, filename, saved_path, int(size or 0),
         time.time()))
    c.commit()
    c.close()


def drawings_for(exam_id):
    """这场考试的画图作品：[{hostname, kind, name, seat, filename, saved_path, size, updated_at}]"""
    c = conn()
    rows = c.execute(
        """SELECT d.hostname, d.kind, d.filename, d.saved_path, d.size,
                  d.updated_at, s.name, s.seat
           FROM drawing_submissions d
           LEFT JOIN students s ON s.hostname = d.hostname
           WHERE d.exam_id=? ORDER BY s.seat, d.hostname, d.kind""",
        (exam_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def drawing_of(exam_id, hostname, kind="draw"):
    """某台机器某道画图题的作品（没有返回 None）"""
    kind = "beads" if kind == "beads" else "draw"
    c = conn()
    r = c.execute(
        "SELECT * FROM drawing_submissions "
        "WHERE exam_id=? AND hostname=? AND kind=?",
        (exam_id, hostname, kind)).fetchone()
    c.close()
    return dict(r) if r else None


# ==================== 打字模块（小学） ====================
def save_typing_report(exam_id, hostname, reports):
    """
    学生端周期性上报打字进度（每个子任务一行，upsert）。
    reports: [{task, progress, total, hits, typed, correct_chars,
               elapsed_ms, wpm, accuracy, reaction_ms, finished}, ...]
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

    c = conn()
    with _global_lock:
        for r in reports:
            if not isinstance(r, dict):
                continue
            task = str(r.get("task") or "").strip()[:32]
            if not task:
                continue
            c.execute("""
                INSERT INTO typing_results(exam_id, hostname, task,
                    progress, total, hits, typed, correct_chars,
                    elapsed_ms, wpm, accuracy, reaction_ms, finished, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(exam_id, hostname, task) DO UPDATE SET
                    progress=excluded.progress, total=excluded.total,
                    hits=excluded.hits, typed=excluded.typed,
                    correct_chars=excluded.correct_chars,
                    elapsed_ms=excluded.elapsed_ms, wpm=excluded.wpm,
                    accuracy=excluded.accuracy,
                    reaction_ms=excluded.reaction_ms,
                    finished=excluded.finished, updated_at=excluded.updated_at
            """, (exam_id, hostname, task,
                  _i(r.get("progress")), _i(r.get("total")),
                  _i(r.get("hits")), _i(r.get("typed")),
                  _i(r.get("correct_chars")), _i(r.get("elapsed_ms")),
                  _f(r.get("wpm")), _f(r.get("accuracy")),
                  _i(r.get("reaction_ms")), _i(r.get("finished")), now))
        c.commit()
    c.close()


def typing_board(exam_id):
    """
    老师端实时看板：本场考试绑定的学生 × 各打字子任务的最新数据。
    返回 [{hostname, name, seat, online, tasks: {task: {...}}, updated_at}]
    """
    c = conn()
    # 本场的学生 = 开考前绑定的 + 开考后才登录但已经打了字上报的
    # （开考之后才登录的机器 students.exam_id 还是空，光看 exam_id 会漏人）
    students = [dict(r) for r in c.execute(
        """SELECT hostname, name, seat, last_seen FROM students
           WHERE exam_id=?
              OR hostname IN (SELECT hostname FROM typing_results
                              WHERE exam_id=?)
           ORDER BY hostname""",
        (exam_id, exam_id)).fetchall()]
    rows = c.execute(
        """SELECT hostname, task, progress, total, hits, typed, correct_chars,
                  elapsed_ms, wpm, accuracy, reaction_ms, finished, updated_at
           FROM typing_results WHERE exam_id=?""", (exam_id,)).fetchall()

    now = time.time()
    by_host = {}
    for r in rows:
        by_host.setdefault(r["hostname"], {})[r["task"]] = dict(r)

    # 学生记录被清掉也要有人名：从参与名单快照补（快照也没有就用机器名）
    have = {s["hostname"] for s in students}
    for h in by_host:
        if h in have:
            continue
        p = c.execute(
            """SELECT name, seat FROM exam_participants
               WHERE exam_id=? AND hostname=? AND name!=''
               ORDER BY last_seen DESC LIMIT 1""", (exam_id, h)).fetchone()
        students.append({"hostname": h,
                         "name": (p["name"] if p else "") or "",
                         "seat": (p["seat"] if p else "") or "",
                         "last_seen": 0})
    c.close()

    out = []
    for s in students:
        tasks = by_host.get(s["hostname"], {})
        updated = max((t.get("updated_at") or 0 for t in tasks.values()), default=0)
        out.append({
            "hostname": s["hostname"], "name": s["name"] or "",
            "seat": s["seat"] or "",
            "online": bool(s["last_seen"] and now - s["last_seen"] < 10),
            "tasks": tasks, "updated_at": updated,
        })

    def _key(r):
        try:
            return (0, int(r["seat"]))
        except (TypeError, ValueError):
            return (1, r["hostname"])
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
    w, args = [], []
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
    have = set(exam_question_ids(exam_id))
    added = 0
    for r in rows:
        if r["id"] in have:
            continue
        c.execute("""INSERT OR IGNORE INTO exam_questions(exam_id,question_id,qorder)
                     VALUES(?,?,?)""", (exam_id, r["id"], len(have)))
        have.add(r["id"])
        added += 1
    c.commit()
    c.close()
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
    have = set(exam_question_ids(exam_id))
    for qid in picked:
        if qid not in have:
            c.execute("""INSERT OR IGNORE INTO exam_questions(exam_id,question_id,qorder)
                         VALUES(?,?,?)""", (exam_id, qid, len(have)))
            have.add(qid)
    c.commit()
    c.close()
    return picked


# ==================== 实操题库 ====================
def add_practice(item):
    """新增一道实操题（重名就更新，方便反复装载内置题库）"""
    c = conn()
    with _global_lock:
        row = c.execute("SELECT id FROM practice_questions WHERE name=?",
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
            cur = c.execute(
                """INSERT INTO practice_questions
                   (name,etype,category,level,materials,requirement,save_as,
                    checks,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (item["name"],) + vals + (now(),))
            pid, is_new = cur.lastrowid, True
        c.commit()
    c.close()
    return pid, is_new


def _practice_row(r):
    d = dict(r)
    d["materials"] = json.loads(d["materials"] or "[]")
    d["checks"] = json.loads(d["checks"] or "[]")
    return d


def list_practice(etype=None, category=None, level=None):
    """按条件列出实操题库"""
    c = conn()
    sql = "SELECT * FROM practice_questions WHERE 1=1"
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
    c = conn()
    c.execute("DELETE FROM practice_questions WHERE id=?", (pid,))
    c.commit()
    c.close()


def practice_filters():
    """题库里有哪些分类 / 难度（筛选下拉用）"""
    c = conn()
    cats = [r[0] for r in c.execute(
        "SELECT DISTINCT category FROM practice_questions "
        "WHERE category<>'' ORDER BY category").fetchall()]
    lvls = [r[0] for r in c.execute(
        "SELECT DISTINCT level FROM practice_questions "
        "WHERE level<>'' ORDER BY level").fetchall()]
    c.close()
    return cats, lvls


def exam_tasks(exam_id):
    """
    这场考试装了哪几道实操题（按顺序）。
    没装过的话返回空列表 —— 老的单题考试走 exams 表里的旧字段。
    """
    c = conn()
    rows = c.execute("""SELECT p.* FROM exam_practice ep
                        JOIN practice_questions p ON p.id=ep.practice_id
                        WHERE ep.exam_id=? ORDER BY ep.qorder""",
                     (exam_id,)).fetchall()
    c.close()
    return [_practice_row(r) for r in rows]


def add_task_to_exam(exam_id, pid):
    """往考试里追加一道实操题"""
    p = get_practice(pid)
    if not p:
        return False
    c = conn()
    have = c.execute("SELECT COUNT(*) FROM exam_practice WHERE exam_id=?",
                     (exam_id,)).fetchone()[0]
    c.execute("""INSERT OR IGNORE INTO exam_practice(exam_id,practice_id,qorder)
                 VALUES(?,?,?)""", (exam_id, pid, have))
    c.commit()
    c.close()
    # 装了操作题就把题型勾上，否则学生端看不到（见 ensure_practice_type）
    ensure_practice_type(exam_id)
    _sync_exam_legacy(exam_id)
    return True


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
    把「第一道实操题」同步到 exams 表的旧字段（materials/checks/...）。

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
    cur = c.execute("SELECT etype FROM exams WHERE id=?", (exam_id,)).fetchone()
    raw = ((cur["etype"] if cur else "") or "").strip()
    if raw:
        c.execute("""UPDATE exams SET requirement=?, materials=?,
                     save_as=?, checks=? WHERE id=?""",
                  (t["requirement"],
                   json.dumps(t["materials"], ensure_ascii=False), t["save_as"],
                   json.dumps(t["checks"], ensure_ascii=False), exam_id))
    else:
        c.execute("""UPDATE exams SET etype=?, requirement=?, materials=?,
                     save_as=?, checks=? WHERE id=?""",
                  (json.dumps([t["etype"]], ensure_ascii=False),
                   t["requirement"],
                   json.dumps(t["materials"], ensure_ascii=False), t["save_as"],
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


def judge(question, student_answer):
    """判一道题，返回 (是否正确, 得分)"""
    qtype = question["qtype"]
    score = float(question["score"])

    if qtype == "code":
        # ① 优先「跑一遍看输出」：答案写法千变万化（for/while、range(1,11)
        #    /range(1, 11)），只在题库里穷举参考答案必然漏判。
        #    老师填了「期望输出」就启用：输出一致直接满分。
        #    没填 / 空没填完 / 服务器没环境 → 返回 None，走下面的按空给分。
        try:
            import coderun
            r = coderun.judge_code(question, student_answer)
        except Exception:
            # 判题机自身的 bug 不能静默吞掉（否则退化成按空给分，老师发现不了）
            logging.exception("coderun 判题异常 qid=%s", question.get("id"))
            r = "error"                    # 与 None 严格区分
        if r is True:
            return (1, score)
        if r == "error":
            # 判题机故障：记录待重判，让老师后台能看到，不要悄无声息
            mark_need_rejudge(question.get("id"))
        # r is None / False / "error" → 落到下面的按空给分回退
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
        # 填空：支持多答案，用 | 分隔。
        # 参考答案和学生答案都按 | 拆开，任意一项命中即算对
        # （用 _norm_text 只做标点/空格归一，不做字符排序 —— 不然 1024 会被 4012 冒充）
        # 古诗/语文默写：学生常在末尾带上句号逗号，首尾标点不影响判分
        def _nb(s):
            return _norm_text(s).strip("。．.,，、；;：:！!？?…—")
        alts = {_nb(a) for a in str(question["answer"]).split("|") if str(a).strip()}
        gots = {_nb(a) for a in str(student_answer or "").split("|") if str(a).strip()}
        return (1, score) if (gots & alts) else (0, 0)
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

    返回 (重判条数, 判对条数)
    """
    qs = {q["id"]: q for q in get_exam_questions(exam_id)
          if q["qtype"] == "code"}
    if not qs:
        return 0, 0
    try:
        import coderun
        coderun.clear_cache()      # 期望输出可能改过，旧结果作废
    except Exception:
        pass
    c = conn()
    rows = c.execute(
        "SELECT id, question_id, answer FROM theory_answers WHERE exam_id=?",
        (exam_id,)).fetchall()
    n = hit = 0
    for r in rows:
        q = qs.get(r["question_id"])
        if not q:
            continue
        correct, got = judge(q, r["answer"])
        c.execute("UPDATE theory_answers SET correct=?, got=? WHERE id=?",
                  (correct, got, r["id"]))
        n += 1
        if correct:
            hit += 1
    c.commit()
    c.close()
    invalidate()
    return n, hit


def save_theory_answer(exam_id, hostname, qid, answer):
    """保存并判分一道题"""
    q = get_question(qid)
    if not q:
        return None
    correct, got = judge(q, answer)
    c = conn()
    c.execute("""INSERT INTO theory_answers(exam_id,hostname,question_id,answer,correct,got,answered_at)
                 VALUES(?,?,?,?,?,?,?)
                 ON CONFLICT(exam_id,hostname,question_id)
                 DO UPDATE SET answer=?, correct=?, got=?, answered_at=?""",
              (exam_id, hostname, qid, str(answer or ""), correct, got, now(),
               str(answer or ""), correct, got, now()))
    c.commit()
    c.close()
    return {"correct": correct, "got": got}


def save_theory_answers(exam_id, hostname, answers):
    """
    批量保存并判分（学生端每 10 秒交一批）。
    一个事务写完，比逐条 INSERT + 逐条 commit 快得多。
    :param answers: {qid: 答案}
    :return: (已保存题数, 本次得分)
    """
    n = 0
    c = conn()
    try:
        # 先判分、再写库。程序填空题的 judge 会真跑一遍代码（subprocess，
        # 最长几十秒），绝不能在持有全局锁时跑 —— 否则判题期间 50 台机器的
        # 心跳、其他提交、老师页面的所有查询全被挡死。
        judged = []
        for qid, ans in (answers or {}).items():
            try:
                qid = int(qid)
            except (TypeError, ValueError):
                continue
            q = get_question(qid)
            if not q:
                continue
            correct, got = judge(q, ans)
            judged.append((qid, ans, correct, got))

        # 判完了，只把写库放进锁内，一个事务写完
        with _global_lock:
            for qid, ans, correct, got in judged:
                c.execute(
                    """INSERT INTO theory_answers
                       (exam_id,hostname,question_id,answer,correct,got,answered_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(exam_id,hostname,question_id)
                       DO UPDATE SET answer=?, correct=?, got=?, answered_at=?""",
                    (exam_id, hostname, qid, str(ans or ""), correct, got, now(),
                     str(ans or ""), correct, got, now()))
                n += 1
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


def theory_score(exam_id, hostname):
    """某学生的理论题得分"""
    c = conn()
    r = c.execute("""SELECT SUM(got) AS s, COUNT(*) AS n FROM theory_answers
                     WHERE exam_id=? AND hostname=?""", (exam_id, hostname)).fetchone()
    c.close()
    return round(r["s"] or 0, 2), r["n"] or 0


def theory_answers_of(exam_id, hostname):
    c = conn()
    rows = c.execute("""SELECT question_id, answer FROM theory_answers
                        WHERE exam_id=? AND hostname=?""", (exam_id, hostname)).fetchall()
    c.close()
    return {r["question_id"]: r["answer"] for r in rows}


def theory_answer_rows(exam_id):
    """
    全班逐条作答记录（评讲页用）：[{question_id, hostname, answer, correct}]
    """
    c = conn()
    rows = c.execute("""SELECT question_id, hostname, answer, correct
                        FROM theory_answers WHERE exam_id=?""",
                     (exam_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def theory_finished(exam_id, hostname):
    """是否已答完所有理论题"""
    qs = get_exam_questions(exam_id)
    if not qs:
        return True
    done = theory_answers_of(exam_id, hostname)
    return len(done) >= len(qs)


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

BUNDLE_VER = 1


def export_bundle(eid):
    """导出一场考试为 dict（不含学生答卷）"""
    ex = get_exam(eid)
    if not ex:
        return None
    qs = get_exam_questions(eid)
    return {
        "version": BUNDLE_VER,
        "exported_at": now(),
        "exam": {
            "title": ex["title"], "etype": ex["etype"],
            "duration": ex["duration"],
            "theory_duration": ex.get("theory_duration", 20),
            "requirement": ex.get("requirement", ""),
            "materials": ex.get("materials", "[]"),
            "save_as": ex.get("save_as", ""),
            "checks": ex.get("checks", "[]"),
            "question_types": ex.get("question_types", "[]"),
            "klass": ex.get("klass", ""),
        },
        # 一场考试可以装多道操作题：把它们一起带走，别只搬第一道
        "practice_tasks": exam_tasks(eid),
        "questions": [{
            "qtype": q["qtype"], "stem": q["stem"],
            "options": q["options"], "answer": q["answer"],
            "score": q["score"], "category": q.get("category", ""),
            "language": q.get("language", ""),
            "code": q.get("code", ""),
            "stdin_data": q.get("stdin_data", ""),
        } for q in qs],
    }


def import_bundle(data, new_title=None):
    """
    导入考试包。返回 (新考试 id, 状态信息 dict)

    .题库里已有的题（按 题干+题型+答案 匹配）直接复用，
     没有的自动加进题库 —— 这样导入后能立刻开考。
    """
    if not isinstance(data, dict) or "exam" not in data:
        return None, {"ok": False, "msg": "文件格式不对，不是有效的考试包"}
    ex = data["exam"]
    qs = data.get("questions") or []

    title = new_title or ex.get("title") or "导入的考试"
    eid = create_exam({
        "title": title,
        "etype": ex.get("etype", "word"),
        "duration": ex.get("duration", 60),
        "theory_duration": ex.get("theory_duration", 20),
    })
    # 补上配置字段（create_exam 可能不接受这些）
    c = conn()
    c.execute("""UPDATE exams SET requirement=?, materials=?, save_as=?, checks=?
                 WHERE id=?""",
              (ex.get("requirement", ""), ex.get("materials", "[]"),
               ex.get("save_as", ""), ex.get("checks", "[]"), eid))
    c.commit()
    c.close()

    added = reused = 0
    qids = []
    for q in qs:
        # 找同题（题干 + 题型 + 答案 一致就认为同一道）
        c = conn()
        row = c.execute(
            """SELECT id FROM questions
               WHERE stem=? AND qtype=? AND answer=? LIMIT 1""",
            (q.get("stem", ""), q.get("qtype", "single"),
             str(q.get("answer", "")))).fetchone()
        c.close()
        if row:
            qids.append(row["id"])
            reused += 1
        else:
            qid = add_question({
                "qtype": q.get("qtype", "single"),
                "stem": q.get("stem", ""),
                "options": q.get("options", []),
                "answer": q.get("answer", ""),
                "score": q.get("score", 2),
                "category": q.get("category", ""),
                "language": q.get("language", ""),
                "code": q.get("code", ""),
                "stdin_data": q.get("stdin_data", ""),
            })
            qids.append(qid)
            added += 1
    set_exam_questions(eid, qids)

    # 恢复题型勾选（旧考试包没有这个字段，保持默认"全部题型"）
    try:
        qtypes = json.loads(ex.get("question_types") or "[]")
        if isinstance(qtypes, list) and qtypes:
            set_question_types(eid, qtypes)
    except (TypeError, ValueError):
        pass

    # 恢复多道操作题（先入实操题库，再装进这场考试）
    tasks = data.get("practice_tasks") or []
    task_n = 0
    for item in tasks:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        pid, _ = add_practice({
            "name": item.get("name"), "etype": item.get("etype", "excel"),
            "category": item.get("category", ""), "level": item.get("level", ""),
            "materials": item.get("materials") or [],
            "requirement": item.get("requirement", ""),
            "save_as": item.get("save_as", ""), "checks": item.get("checks") or [],
        })
        if add_task_to_exam(eid, pid):
            task_n += 1

    return eid, {"ok": True, "msg": f"导入完成：新增题目 {added} 道，"
                                    f"复用已有 {reused} 道，共 {len(qids)} 题"
                                    + (f"，操作题 {task_n} 道" if task_n else "")}


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
    """
    理论 + 实操 合并成绩（成绩页 / 成绩导出的名单）。

    ⚠️ 一场考试可以装多道操作题，每个学生会有**多条** submissions
    （每题一条）。以前用 {hostname: sub} 字典折叠，每人只保留最后
    提交的那道题 —— 多题考试的成绩直接算错。现在按题累加。

    名单三合一 —— 谁参与过谁就在名单里，跟学生机记录在不在无关：
      ① exam_participants 参与快照：登录/作答那一刻记下的姓名座位；
         同一台机器换人登录 = 两个人都在（按姓名分行）
      ② 作答证据：理论答案 / 实操提交 / 打字上报 / 画板作品（补历史数据）
      ③ 当前绑定本场、还没动笔的（刚登录进来也算参加）
    以前只扫 students 表 —— 清了离线记录名单就空了、换人登录把上
    一个人的姓名顶掉，这都是不对的。
    """
    subs_by_host = {}
    for s in list_submissions(exam_id):
        subs_by_host.setdefault(s["hostname"], []).append(s)
    tf = exam_theory_full(exam_id)
    stu_now = {s["hostname"]: s for s in list_students()}

    # 证据机器集合（有这些 = 确实参与过，哪怕快照/学生表都没了）
    ev_hosts = set(subs_by_host)
    c = conn()
    for t in ("theory_answers", "typing_results", "drawing_submissions"):
        try:
            for r in c.execute(f"SELECT DISTINCT hostname FROM {t} "
                               f"WHERE exam_id=?", (exam_id,)):
                if r["hostname"]:
                    ev_hosts.add(r["hostname"])
        except Exception:
            pass
    c.close()

    # 人：快照为主（同一机器多个姓名都要）；空名行在同机器有具名行时跳过
    people, seen = [], set()
    parts = participants_for(exam_id)
    named_hosts = {p["hostname"] for p in parts if (p["name"] or "").strip()}
    for p in parts:
        nm = (p["name"] or "").strip()
        if not nm and p["hostname"] in named_hosts:
            continue
        key = (p["hostname"], nm)
        if key in seen:
            continue
        seen.add(key)
        people.append({"hostname": p["hostname"], "name": nm,
                       "seat": (p["seat"] or "").strip(),
                       "ip": (p["ip"] or "").strip()})
    known_hosts = {p["hostname"] for p in people}
    extra = set(ev_hosts)
    for h, s in stu_now.items():
        if (s.get("exam_id") or 0) == exam_id:
            extra.add(h)
    for h in sorted(extra):
        if h in known_hosts:
            continue
        st = stu_now.get(h) or {}
        nm = (st.get("name") or "").strip()
        if not nm and h in named_hosts:
            continue
        known_hosts.add(h)
        people.append({"hostname": h, "name": nm,
                       "seat": (st.get("seat") or "").strip(),
                       "ip": (st.get("ip") or "").strip()})

    rows = []
    for p in people:
        h = p["hostname"]
        ts, tn = theory_score(exam_id, h)
        group = subs_by_host.get(h) or []
        graded = [s for s in group if s["score"] is not None]
        practice = round(sum(s["score"] or 0 for s in graded), 2) if graded else None
        st = stu_now.get(h) or {}
        rows.append({
            "hostname": h,
            "ip": p["ip"] or st.get("ip") or "",
            "name": p["name"] or "",                 # 登录时填的姓名（快照）
            "seat": p["seat"] or "",
            "online": bool(st.get("online")),
            "sub_id": group[0]["id"] if group else None,
            "theory": ts,
            "theory_full": tf,
            "theory_n": tn,
            "practice": practice,
            "practice_full": round(sum(s["full"] or 0 for s in graded), 2)
                             if graded else None,
            "submitted": bool(group),
            "filename": "、".join(s["filename"] or "" for s in group).strip("、"),
            "total": round((ts or 0) + (practice or 0), 2),
        })

    # 排序：有座位号的按座位号（数字自然序），没有的排后面按机器名
    def key(r):
        st = (r.get("seat") or "").strip()
        if not st:
            return (1, 0, r["hostname"])
        try:
            return (0, int(st), "")
        except ValueError:
            return (0, 0, st)
    rows.sort(key=key)
    return rows


def status_counts():
    """
    各状态的学生人数（控制台的"交卷进度"环形图用）。
    状态含义：idle 待考 / working 答题中 / theory_done 理论已交 / submitted 已交卷
    """
    c = conn()
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
        """SELECT question_id, SUM(got) AS got, COUNT(*) AS n
           FROM theory_answers WHERE exam_id=? GROUP BY question_id""",
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
    rows = full_report(exam_id)
    exam = get_exam(exam_id)
    pf = practice_full_of(exam_id)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["座位号", "姓名", "机器名", "IP", "理论题", "理论满分",
                    "实操题", "实操满分", "总分", "已交卷", "文件名"])
        for r in rows:
            w.writerow([r.get("seat", ""), r.get("name", ""),
                        r["hostname"], r["ip"], r["theory"], r["theory_full"],
                        r["practice"] if r["practice"] is not None else "",
                        r["practice_full"] if r["practice_full"] else pf,
                        r["total"], "是" if r["submitted"] else "否", r["filename"]])
    return out_path


if __name__ == "__main__":
    init()
    print(f"数据库已初始化: {DB_PATH}")
