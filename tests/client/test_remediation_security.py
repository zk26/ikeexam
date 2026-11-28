# -*- coding: utf-8 -*-
"""学生端身份、离线队列与发现审批回归。"""
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "ikeexam", "client"))

from ikeexam.client import discover
from ikeexam.client.exam_client import AnswerSync, ExamClient
from ikeexam.shared.outbox import DurableOutbox
from ikeexam.shared.security import (identity_key, normalize_origin, origin_key,
                                    principal_key)
from ikeexam.teacher.api import AdminAPI


def test_origins_and_principal():
    assert normalize_origin("192.0.2.10:9000", allow_plain_http=True) == \
        "http://192.0.2.10:9000"
    api = AdminAPI("192.0.2.10:9000")
    assert api.base == "http://192.0.2.10:9000"
    cfg = SimpleNamespace(server="http://192.0.2.10:9000", client_id="machine",
                          hostname="PC", name="姓名甲", seat="01")
    changed = SimpleNamespace(**{**vars(cfg), "name": "姓名乙", "seat": "02"})
    assert principal_key(cfg) == principal_key(changed)
    assert identity_key(cfg, 7) != identity_key(changed, 7)


def test_outbox_rebind_and_answer_rollback():
    with tempfile.TemporaryDirectory(prefix="exam_outbox_test_") as tmp:
        outbox = DurableOutbox(os.path.join(tmp, "outbox.json"))
        outbox.put("answers:old:7", "old", "answers",
                   {"exam_id": 7, "answers": {"1": "A"}}, principal="machine")
        outbox.put("final:old:7", "old", "final",
                   {"exam_id": 7}, principal="machine")
        outbox.rebind_identity("old", "new", "machine")
        assert outbox.record("answers:old:7") is None
        assert outbox.record("answers:new:7")["identity"] == "new"
        assert outbox.record("final:new:7")["principal"] == "machine"
        reloaded = DurableOutbox(outbox.path)
        assert reloaded.record("answers:new:7")["data"]["answers"] == {"1": "A"}

        client = SimpleNamespace(identity="new", generation=1, principal="machine",
                                 outbox=outbox, _log=lambda *args: None,
                                 api=SimpleNamespace(submit_theory=lambda *args: {"ok": True}))
        sync = AnswerSync(client, 7)
        sync.update({"1": "A"})
        with patch.object(outbox, "remove", side_effect=OSError("disk full")):
            assert sync._send({"1": "A"}) is False
        assert sync.dirty == {"1": "A"}
        assert outbox.record("answers:new:7") is not None
        assert sync._send({"1": "A"}) is True
        assert not sync.dirty and outbox.record("answers:new:7") is None


def test_explicit_identity_change_does_not_rebind_outbox():
    with tempfile.TemporaryDirectory(prefix="exam_identity_test_") as tmp:
        cfg = SimpleNamespace(server="http://192.0.2.10:9000", client_id="machine",
                              hostname="PC", name="姓名甲", seat="01")
        client = ExamClient.__new__(ExamClient)
        client.cfg = cfg
        client.outbox = DurableOutbox(os.path.join(tmp, "outbox.json"))
        client.state = {"exam": {"id": 7}}
        client.identity = identity_key(cfg, 7)
        client.principal = principal_key(cfg)
        client.origin = origin_key(cfg.server, allow_plain_http=True)
        client.generation = 1
        client._identity_pending = False
        client._bind_api = lambda: None
        old_identity = client.identity
        old_principal = client.principal
        key = client.submission_key("answers", 7)
        client.outbox.put(key, old_identity, "answers", {"exam_id": 7},
                          principal=old_principal)

        cfg.name = "姓名乙"
        cfg.seat = "02"
        assert client._refresh_identity(rebind=False)
        assert client.identity != old_identity
        assert client.outbox.record(key)["identity"] == old_identity
        assert client.outbox.records(client.identity, principal=client.principal) == []
        assert len(client.outbox.records(old_identity, principal=old_principal)) == 1


def test_origin_change_preserves_old_server_records():
    with tempfile.TemporaryDirectory(prefix="exam_origin_test_") as tmp:
        cfg = SimpleNamespace(server="http://192.0.2.10:9000", client_id="machine",
                              hostname="PC", name="姓名甲", seat="01")
        client = ExamClient.__new__(ExamClient)
        client.cfg = cfg
        client.api = SimpleNamespace()
        client.outbox = DurableOutbox(os.path.join(tmp, "outbox.json"))
        client.identity = identity_key(cfg, 7)
        client.principal = principal_key(cfg)
        client.origin = origin_key(cfg.server, allow_plain_http=True)
        client.generation = 1
        client._identity_pending = False
        client.answer_sync = None
        client.state = {"exam": {"id": 7}, "submitted": False}
        client._bind_api = lambda: None
        key = client.submission_key("final", 7)
        client.outbox.put(key, client.identity, "final", {"exam_id": 7},
                          principal=client.principal)

        cfg.name = "姓名乙"
        assert client._refresh_identity()
        assert client.outbox.records(client.identity, principal=client.principal)
        old_identity = client.identity
        old_origin = client.origin
        old_key = client.outbox.records(old_identity)[0][0]

        cfg.server = "http://192.0.2.20:9000"
        client._prepare_identity_change()
        assert client._refresh_identity()
        assert client.origin != old_origin
        assert not client.outbox.records(client.identity, principal=client.principal)
        assert client.outbox.records(old_identity)
        assert client.outbox.record(old_key)["data"] == {"exam_id": 7}


def test_typing_reports_include_attempt():
    client = ExamClient.__new__(ExamClient)
    client.identity = "identity"
    client.state = {"exam": {"id": 7}}
    client._typing_state = {"exam_id": 7, "identity": "identity", "stats": {
        "typing_zh": {"task": "typing_zh", "typed": 12, "attempt": 3}}}
    assert client.typing_reports() == [{
        "task": "typing_zh", "progress": None, "total": None, "hits": None,
        "typed": 12, "correct_chars": None, "elapsed_ms": None, "wpm": None,
        "accuracy": None, "reaction_ms": None, "finished": None, "attempt": 3}]


def test_discovery_requires_explicit_opt_in():
    with patch.object(discover, "load_cache", return_value=None), \
            patch.object(discover, "listen_once") as listen, \
            patch.object(discover, "probe") as probe:
        assert discover.find_server(timeout=0.01) is None
        listen.assert_not_called()
        probe.assert_not_called()

    heard = [{"http": "http://192.0.2.10:9000", "name": "QA"}]
    with patch.object(discover, "load_cache", return_value=None), \
            patch.object(discover, "listen_once", return_value=heard), \
            patch.object(discover, "probe") as probe, \
            patch.object(discover, "try_url", return_value=True) as connect:
        assert discover.find_server(timeout=0.01, allow_discovered=True) == \
            "http://192.0.2.10:9000"
        connect.assert_called_once()
        probe.assert_not_called()


if __name__ == "__main__":
    test_origins_and_principal()
    test_outbox_rebind_and_answer_rollback()
    test_explicit_identity_change_does_not_rebind_outbox()
    test_origin_change_preserves_old_server_records()
    test_typing_reports_include_attempt()
    test_discovery_requires_explicit_opt_in()
    print("[OK] LAN 源审批、稳定机器身份、跨源隔离、打字轮次与发现opt-in生效")
