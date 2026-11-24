from .base import BaseGrader
from .word import WordGrader
from .excel import ExcelGrader
from .ppt import PptGrader

__all__ = ["BaseGrader", "WordGrader", "ExcelGrader", "PptGrader"]

# 扩展名 -> 评分器
BY_EXT = {}
for _g in (WordGrader, ExcelGrader, PptGrader):
    for _e in _g.exts:
        BY_EXT[_e] = _g


def grader_for(path):
    """按扩展名选评分器"""
    import os
    ext = os.path.splitext(str(path))[1].lower()
    cls = BY_EXT.get(ext)
    if cls is None:
        raise ValueError(f"不支持的文件类型: {ext}（支持 {', '.join(BY_EXT)}）")
    return cls(path)
