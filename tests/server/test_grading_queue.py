# -*- coding: utf-8 -*-
"""持久评分队列的租约、重试、容量、取消与 worker 回归。"""
import json
import os
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="exam_grading_queue_test_")
os.environ["EXAM_DB"] = os.path.join(_tmp, "test.db")
os.environ["EXAM_AUTH_FILE"] = os.path.join(_tmp, "auth.json")
os.environ["EXAM_DATA"] = _tmp
os.environ["EXAM_ADMIN_PASSWORD"] = "Admin@123"

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ikeexam.server import app as app_mod
from ikeexam.server import db
from ikeexam.server import runtime

db.init()
app_mod.app.config["TESTING"] = True


def make_submission(name, path):
    eid = db.create_exam({"title": name, "etype": "word", "duration": 30,
                          "theory_duration": 30})
    db.set_exam_status(eid, "running")
    db.set_phase(eid, "practice")
    return eid, db.save_submission(f"PC-{name}", eid, "答案.docx", path,
                                   os.path.getsize(path), task_id=0, participant_id=0)


def write_file(name, data=b"doc"):
    path = os.path.join(_tmp, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def write_managed_file(name, data=b"doc"):
    os.makedirs(runtime.UPLOAD_DIR, exist_ok=True)
    path = os.path.join(runtime.UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def job_status(job_id):
    return db.conn().execute("SELECT status FROM grading_jobs WHERE id=?", (job_id,)).fetchone()[0]


# 基本领取、续租、完成与结果写入。
path = write_file("basic.docx")
eid, sub_id = make_submission("basic", path)
job_id, position, created = db.enqueue_grading_job(
    "submit:basic", eid, sub_id, "PC-basic", {"path": path, "checks": []})
assert created and position == 1
same_id, _, created_again = db.enqueue_grading_job(
    "submit:basic", eid, sub_id, "PC-basic", {"path": path, "checks": []})
assert same_id == job_id and not created_again
job = db.claim_grading_job("worker-a", lease_seconds=30)
assert job and job["id"] == job_id and job["attempts"] == 1
assert db.heartbeat_grading_job(job_id, job["lease_token"], "worker-a", 30)
assert db.complete_grading_job(job_id, job["lease_token"], "worker-a", 5, 5, [{"score": 5}])
assert job_status(job_id) == "succeeded"
assert db.conn().execute("SELECT score FROM submissions WHERE id=?", (sub_id,)).fetchone()[0] == 5

# 失败按指数退避重试，超过最大次数后进入 failed。
path2 = write_file("retry.docx")
eid2, sub2 = make_submission("retry", path2)
retry_id, _, _ = db.enqueue_grading_job(
    "submit:retry", eid2, sub2, "PC-retry", {"path": path2, "checks": []}, max_attempts=3)
for attempt in range(1, 4):
    job = db.claim_grading_job(f"retry-{attempt}", 30)
    assert job and job["id"] == retry_id and job["attempts"] == attempt
    assert db.fail_grading_job(retry_id, job["lease_token"], job["lease_owner"], "boom")
    expected = "failed" if attempt == 3 else "pending"
    assert job_status(retry_id) == expected, (attempt, job_status(retry_id))
    if attempt < 3:
        c = db.conn()
        c.execute("UPDATE grading_jobs SET next_attempt_at=? WHERE id=?", (time.time(), retry_id))
        c.commit()

# 过期租约由另一个 worker 回收。
path3 = write_file("lease.docx")
eid3, sub3 = make_submission("lease", path3)
lease_id, _, _ = db.enqueue_grading_job(
    "submit:lease", eid3, sub3, "PC-lease", {"path": path3, "checks": []})
job = db.claim_grading_job("dead-worker", lease_seconds=1)
c = db.conn()
c.execute("UPDATE grading_jobs SET lease_until=? WHERE id=?", (time.time() - 1, lease_id))
c.commit()
reclaimed = db.claim_grading_job("live-worker", 30)
assert reclaimed and reclaimed["id"] == lease_id and reclaimed["attempts"] == 2

# 旧评分结果不能覆盖同份答卷的新评分任务。
path4 = write_file("stale.docx")
eid4, sub4 = make_submission("stale", path4)
old_id, _, _ = db.enqueue_grading_job(
    "regrade:old", eid4, sub4, "PC-stale", {"path": path4, "checks": []})
old_job = db.claim_grading_job("old-worker", 30)
new_id, _, _ = db.enqueue_grading_job(
    "regrade:new", eid4, sub4, "PC-stale", {"path": path4, "checks": []})
assert db.complete_grading_job(old_id, old_job["lease_token"], old_job["lease_owner"], 1, 5, [])
assert db.conn().execute("SELECT score FROM submissions WHERE id=?", (sub4,)).fetchone()[0] is None
new_job = db.claim_grading_job("new-worker", 30)
assert new_job and new_job["id"] == new_id
assert db.complete_grading_job(new_id, new_job["lease_token"], new_job["lease_owner"], 4, 5, [])
assert db.conn().execute("SELECT score FROM submissions WHERE id=?", (sub4,)).fetchone()[0] == 4

# 队列容量上限拒绝新任务。
old_capacity = db.GRADING_QUEUE_CAPACITY
db.GRADING_QUEUE_CAPACITY = 1
try:
    try:
        db.enqueue_grading_job("overflow", eid4, sub4, "PC-stale", {"path": path4})
        raise AssertionError("队列满时应当拒绝")
    except RuntimeError as exc:
        assert "队列已满" in str(exc)
finally:
    db.GRADING_QUEUE_CAPACITY = old_capacity

# 结束考试会持久取消 pending 任务。
path5 = write_file("cancel.docx")
eid5, sub5 = make_submission("cancel", path5)
cancel_id, _, _ = db.enqueue_grading_job(
    "submit:cancel", eid5, sub5, "PC-cancel", {"path": path5, "checks": []})
assert db.stop_exam(eid5)[0]
assert job_status(cancel_id) == "cancelled"

# 重提交不会删除仍被评分任务引用的旧文件。
old_path = write_managed_file("old.docx", b"old")
new_path = write_managed_file("new.docx", b"new")
eid6, sub6 = make_submission("replace", old_path)
replace_job, _, _ = db.enqueue_grading_job(
    "submit:replace", eid6, sub6, "PC-replace", {"path": old_path, "checks": []})
running_job = db.claim_grading_job("replace-worker", 30)
assert running_job and running_job["id"] == replace_job
new_sub = db.save_submission("PC-replace", eid6, "新答案.docx", new_path,
                             os.path.getsize(new_path), task_id=0, participant_id=0)
assert new_sub != sub6
assert os.path.isfile(old_path)
assert job_status(replace_job) == "running"

# 真实 worker 从 SQLite 领取并完成评分。
path7 = write_file("worker.docx")
eid7, sub7 = make_submission("worker", path7)
original_grade = app_mod.exm.grade_file
app_mod.exm.grade_file = lambda _path, _checks: (8, 10, [{"score": 8}])
try:
    app_mod._start_grading_workers()
    app_mod.enqueue_grade(sub7, path7, [], "PC-worker", eid7)
    deadline = time.time() + 5
    while time.time() < deadline:
        counts = db.grading_job_counts()
        row = db.conn().execute(
            "SELECT status FROM grading_jobs WHERE dedupe_key LIKE 'submit:%' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if row and row["status"] == "succeeded":
            break
        time.sleep(0.05)
    else:
        raise AssertionError(("worker 未在时限内完成评分", counts))
    assert db.conn().execute("SELECT score FROM submissions WHERE id=?", (sub7,)).fetchone()[0] == 8
finally:
    app_mod.exm.grade_file = original_grade
    app_mod._stop_grading_workers()

print("[OK] 持久评分队列租约、重试、容量、取消、文件保留与 worker 回归通过")
