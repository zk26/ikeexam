import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import urllib.parse


MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_TEACHER_JSON_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_MATERIAL_BYTES = 64 * 1024 * 1024
MAX_EXPORT_BYTES = 128 * 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


class LimitError(ValueError):
    pass


def read_limited(stream, limit=MAX_JSON_BYTES):
    chunks = []
    total = 0
    while True:
        chunk = stream.read(min(CHUNK_BYTES, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise LimitError(f"内容超过 {limit} 字节限制")


def copy_limited(source, destination, limit=MAX_MATERIAL_BYTES):
    total = 0
    while True:
        chunk = source.read(CHUNK_BYTES)
        if not chunk:
            return total
        total += len(chunk)
        if total > limit:
            raise LimitError(f"内容超过 {limit} 字节限制")
        destination.write(chunk)


def material_name(value):
    name = str(value or "")
    if (not name or name in (".", "..") or len(name) > 255
            or name != name.strip()
            or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', name)
            or name.startswith(("\\\\", "//"))
            or name.split(".", 1)[0].upper() in {
                "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError("素材名称无效")
    return name


def normalize_origin(value, allow_scheme=True, allow_plain_http=False):
    raw = str(value or "").strip()
    if not raw or any(ord(ch) < 32 or ch == "\\" for ch in raw):
        raise ValueError("服务器地址无效")
    if allow_scheme and "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("服务器地址无效") from exc
    if (parsed.scheme not in ("http", "https") or not host
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("服务器地址必须是不含账号、路径和参数的 HTTP(S) 源")
    local = host.lower() in ("127.0.0.1", "localhost", "::1")
    if parsed.scheme == "http" and not (allow_plain_http or local):
        raise ValueError("非本机服务器必须使用 HTTPS")
    hostname = f"[{host}]" if ":" in host else host
    default = 80 if parsed.scheme == "http" else 443
    netloc = hostname if port is None or port == default else f"{hostname}:{port}"
    return f"{parsed.scheme}://{netloc}"


def origin_key(value, allow_plain_http=False):
    parsed = urllib.parse.urlsplit(normalize_origin(
        value, allow_plain_http=allow_plain_http))
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def identity_key(cfg, exam_id=None):
    values = [origin_key(getattr(cfg, "server", "") or "localhost:9000",
                         allow_plain_http=True),
              str(getattr(cfg, "client_id", "")),
              str(getattr(cfg, "hostname", "")),
              str(getattr(cfg, "name", "") or "").strip().casefold(),
              str(getattr(cfg, "seat", "") or "").strip(), str(exam_id or "")]
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


def principal_key(cfg):
    values = [origin_key(getattr(cfg, "server", "") or "localhost:9000",
                         allow_plain_http=True),
              str(getattr(cfg, "client_id", "")),
              str(getattr(cfg, "hostname", ""))]
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


def data_dir(app_name):
    root = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(root, app_name)


def _restrict(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    try:
        subprocess.run(["icacls", path, "/inheritance:r", "/grant:r",
                        f"{user}:F"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def _reparse(info):
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def reject_reparse(path, allow_missing_leaf=True):
    absolute = os.path.abspath(path)
    drive, tail = os.path.splitdrive(absolute)
    current = drive + os.sep
    parts = [part for part in tail.split(os.sep) if part]
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if allow_missing_leaf and index == len(parts) - 1:
                return
            raise
        if stat.S_ISLNK(info.st_mode) or _reparse(info):
            raise ValueError("不允许使用符号链接或重解析点路径")


def path_identity(path):
    reject_reparse(path, allow_missing_leaf=False)
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("所选路径不是普通文件")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def open_verified(path, identity, mode="rb"):
    reject_reparse(path, allow_missing_leaf=False)
    flags = os.O_RDONLY if "b" in mode else os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        actual = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if actual != identity or _reparse(info):
            raise ValueError("所选文件在操作前已变化，请重新选择")
        return os.fdopen(fd, mode)
    except Exception:
        os.close(fd)
        raise


def safe_replace(source, destination):
    reject_reparse(destination)
    os.replace(source, destination)


def atomic_write(path, data, limit=MAX_JSON_BYTES):
    if not isinstance(data, (bytes, bytearray)):
        data = str(data).encode("utf-8")
    if len(data) > limit:
        raise LimitError(f"内容超过 {limit} 字节限制")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ikeexam-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _restrict(tmp)
        safe_replace(tmp, path)
        if os.name != "nt":
            try:
                directory = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def atomic_json(path, value, limit=MAX_JSON_BYTES):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    atomic_write(path, data, limit)
