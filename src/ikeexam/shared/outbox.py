import copy
import os
import threading

from .security import MAX_IMAGE_BYTES, MAX_JSON_BYTES, atomic_json, atomic_write


class DurableOutbox:
    MAX_BYTES = 16 * 1024 * 1024
    MAX_RECORD_BYTES = 12 * 1024 * 1024

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._records = {}
        self._meta = {}
        self._load()

    def _load(self):
        import json
        try:
            with open(self.path, "rb") as stream:
                raw = stream.read(self.MAX_BYTES + 1)
            if len(raw) > self.MAX_BYTES:
                return
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                records = payload.get("records")
                meta = payload.get("meta")
                if isinstance(records, dict):
                    self._records = {str(k): v for k, v in records.items()
                                     if isinstance(v, dict)}
                if isinstance(meta, dict):
                    self._meta = meta
        except (OSError, ValueError, UnicodeError):
            pass

    def _save_locked(self):
        if len(self._records) > 64:
            raise ValueError("本地待提交队列已满")
        for record in self._records.values():
            import json
            size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
            if size > self.MAX_RECORD_BYTES:
                raise ValueError("待提交内容超过本地队列限制")
        atomic_json(self.path, {"version": 1, "meta": self._meta,
                                "records": self._records}, self.MAX_BYTES)

    def set_meta(self, **values):
        with self._lock:
            updated = dict(self._meta)
            updated.update(values)
            old = self._meta
            self._meta = updated
            try:
                self._save_locked()
            except Exception:
                self._meta = old
                raise

    def meta(self):
        with self._lock:
            return copy.deepcopy(self._meta)

    def put(self, key, identity, kind, data, principal=""):
        import json
        record = {"identity": str(identity), "principal": str(principal or identity),
                  "kind": str(kind), "data": data}
        encoded = json.dumps(record, ensure_ascii=False).encode("utf-8")
        if len(encoded) > self.MAX_RECORD_BYTES:
            raise ValueError("待提交内容超过本地队列限制")
        with self._lock:
            old = self._records.get(key)
            self._records[key] = record
            try:
                self._save_locked()
            except Exception:
                if old is None:
                    self._records.pop(key, None)
                else:
                    self._records[key] = old
                raise

    def remove(self, key):
        with self._lock:
            old = self._records.pop(key, None)
            if old is None:
                return
            try:
                self._save_locked()
            except Exception:
                self._records[key] = old
                raise

    def records(self, identity=None, kind=None, principal=None):
        with self._lock:
            result = []
            for key, record in self._records.items():
                record_identity = record.get("identity")
                record_principal = record.get("principal") or record_identity
                if identity is not None and record_identity != identity:
                    continue
                if principal is not None and record_principal != principal:
                    continue
                if kind is not None and record.get("kind") != kind:
                    continue
                result.append((key, copy.deepcopy(record)))
            return result

    def rebind_identity(self, old_identity, new_identity, principal):
        with self._lock:
            old = self._records
            rebound = {}
            for key, record in old.items():
                if record.get("identity") != old_identity:
                    rebound[key] = record
                    continue
                marker = f":{old_identity}:"
                new_key = key.replace(marker, f":{new_identity}:") if marker in key else key
                if new_key != key and new_key in old and new_key != key:
                    raise ValueError("待提交队列身份迁移发生键冲突")
                rebound[new_key] = {**record, "identity": new_identity,
                                    "principal": principal}
            self._records = rebound
            try:
                self._save_locked()
            except Exception:
                self._records = old
                raise

    def record(self, key):
        with self._lock:
            return copy.deepcopy(self._records.get(key))

    def clear(self):
        with self._lock:
            old = self._records
            self._records = {}
            try:
                self._save_locked()
            except Exception:
                self._records = old
                raise

    def discard_path(self):
        try:
            os.remove(self.path)
        except OSError:
            pass
