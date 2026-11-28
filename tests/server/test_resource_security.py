# -*- coding: utf-8 -*-
"""发现应答、查重预算与 OOXML 预检回归。"""
import os
import sys
import tempfile
import time
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ikeexam.graders.base import validate_ooxml
from ikeexam.server import beacon, plagiarism


def main():
    now = time.monotonic()
    buckets = {}
    assert all(beacon._rate_allowed("192.0.2.1", now + i * 0.01,
                                    1.0, 2, buckets, 8) for i in range(2))
    assert not beacon._rate_allowed("192.0.2.1", now + 0.03,
                                    1.0, 2, buckets, 8)
    payload = beacon.Beacon(http_port=9000, name="test").payload("192.0.2.10")
    assert len(payload) <= beacon.MAX_RESPONSE_BYTES

    pairs, suspects, info = plagiarism.run(
        [("A", "missing-a"), ("B", "missing-b")], "word",
        verbose=False, max_submissions=0)
    assert not pairs and not suspects and info["status"] == "rejected", info
    assert plagiarism.csv_safe_value("=1+1") == "'=1+1"
    assert plagiarism.csv_safe_value("  -2") == "'  -2"
    assert plagiarism.csv_safe_value("normal") == "normal"

    with tempfile.TemporaryDirectory(prefix="exam_ooxml_test_") as tmp:
        bad = os.path.join(tmp, "bad.docx")
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("../escape.xml", "<x/>")
        try:
            validate_ooxml(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe OOXML path accepted")

        good = os.path.join(tmp, "good.docx")
        with zipfile.ZipFile(good, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
        validate_ooxml(good)
        with zipfile.ZipFile(good) as archive:
            validate_ooxml(archive)
    print("[OK] UDP 限流、查重预算、CSV 公式中和与 OOXML 预检生效")


if __name__ == "__main__":
    main()
