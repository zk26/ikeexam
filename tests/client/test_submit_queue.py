# -*- coding: utf-8 -*-
"""学生端交卷重试策略回归。"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src", "ikeexam", "client"))

from gui import SubmitQueue


def main():
    queue = SubmitQueue()
    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OSError("temporary network error")
        return "已提交"

    queue.add("理论答案", flaky)
    events = queue.start()
    done = None
    while done is None:
        event = events.get(timeout=10)
        if event[0] == "done":
            done = event
    assert attempts["count"] == 3
    assert len(done[1]) == 1 and not done[2]

    queue = SubmitQueue()
    attempts = {"count": 0}

    def rejected():
        attempts["count"] += 1
        return {"ok": False, "msg": "服务器拒绝"}

    queue.add("接口失败", rejected)
    events = queue.start()
    done = None
    while done is None:
        event = events.get(timeout=10)
        if event[0] == "done":
            done = event
    assert attempts["count"] == 3
    assert not done[1] and len(done[2]) == 1
    print("[OK] 交卷异常与接口失败都会重试三次后才上报结果")


if __name__ == "__main__":
    main()
