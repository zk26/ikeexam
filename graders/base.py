"""
评分器基类
每个检查项返回 (得分: float, 说明: str)
"""
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("graders")


class BaseGrader(ABC):
    """所有评分器的基类"""

    # 子类需覆盖：支持的文件扩展名
    exts = ()

    # 子类需覆盖：类型标识
    kind = ""

    def __init__(self, path):
        self.path = str(path)
        self.doc = None

    # ---------- 子类实现 ----------
    @abstractmethod
    def open(self):
        """打开文档，赋给 self.doc"""

    @abstractmethod
    def checkers(self):
        """
        返回 {检查类型名: 执行函数}
        执行函数签名: func(rule: dict) -> (score: float, msg: str)
        """

    # ---------- 通用逻辑 ----------
    def run(self, rules):
        """
        执行全部评分点
        :param rules: 配置中的 checks 列表
        :return: (总分, 明细列表)
        """
        self.open()
        ck = self.checkers()
        detail, total = [], 0.0

        for r in rules:
            name = r.get("type", "")
            func = ck.get(name)
            item = {
                "id": r.get("id", name),
                "type": name,
                "full": float(r.get("score", 0)),
                "got": 0.0,
                "msg": "",
            }

            if func is None:
                item["msg"] = f"未知的检查类型: {name}"
            else:
                try:
                    got, msg = func(r)
                    # 部分得分：rule 里给了 partial 就按比例给
                    partial = r.get("partial")
                    if partial is not None and got > 0:
                        got = float(partial)
                    got = min(got, item["full"])
                    item["got"] = round(got, 2)
                    item["msg"] = msg
                except Exception as e:
                    # 不要静默吞掉：评分点拼错 / openpyxl 版本差异会导致全班该点
                    # 集体 0 分，光靠界面一条 msg 很难发现。这里既标记又记日志。
                    item["msg"] = f"评分出错: {e}"
                    item["error"] = True
                    logger.error("评分点 %r（type=%s）执行失败: %s",
                                 r.get("id"), name, e, exc_info=True)

            total += item["got"]
            detail.append(item)

        return round(total, 2), detail

    # ---------- 工具方法 ----------
    @staticmethod
    def _first_para_contains(paragraphs, text, ran=None):
        """找到首个包含指定文字的段落索引"""
        for i, p in enumerate(paragraphs):
            if text in p.text:
                if ran is None or i in ran:
                    return i
        return None

    @staticmethod
    def _norm_color(v):
        """颜色归一化：支持 FF8C00 / #FF8C00 / 橙色"""
        if v is None:
            return None
        v = str(v).strip().lstrip("#").upper()
        named = {
            "黑色": "000000", "红色": "FF0000", "橙色": "FF8C00",
            "黄色": "FFFF00", "绿色": "008000", "蓝色": "0000FF",
            "白色": "FFFFFF", "灰色": "808080", "紫色": "800080",
        }
        return named.get(str(v).strip(), v)

    @staticmethod
    def _near(a, b, tol=0.5):
        """数值近似比较，容忍浮点误差"""
        try:
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return str(a) == str(b)
