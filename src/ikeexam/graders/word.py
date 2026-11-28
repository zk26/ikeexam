"""
WPS 文字（.docx）评分器
依赖 python-docx，直接解析 OOXML
"""
from docx.shared import Pt  # noqa: F401 (评分点换算用)
from docx.oxml.ns import qn
from .base import BaseGrader, validate_ooxml

# 中文字号 -> 磅值
CN_SIZE = {
    "初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22, "小二": 18,
    "三号": 16, "小三": 15, "四号": 14, "小四": 12, "五号": 10.5, "小五": 9,
    "六号": 7.5, "小六": 6.5, "七号": 5.5, "八号": 5,
}

# 对齐方式
ALIGN = {
    "左": "LEFT", "左对齐": "LEFT", "居中": "CENTER", "水平居中": "CENTER",
    "右": "RIGHT", "右对齐": "RIGHT", "两端对齐": "JUSTIFY", "分散对齐": "DISTRIBUTE",
}


def _size_to_pt(v):
    """把 10.5 / '五号' / '22磅' 统一转成磅值"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in CN_SIZE:
        return float(CN_SIZE[s])
    for unit in ("磅", "pt", "PT"):
        if s.endswith(unit):
            try:
                return float(s[: -len(unit)])
            except ValueError:
                return None
    try:
        return float(s)
    except ValueError:
        return None


class WordGrader(BaseGrader):
    kind = "word"
    exts = (".docx",)

    def open(self):
        from docx import Document
        validate_ooxml(self.path)
        self.doc = Document(self.path)

    # ---------------- 取段落 ----------------
    def _paras(self, rule):
        """
        定位段落。支持：
          index: 0 / 1 / -1        按序号
          contains: "关键词"        按内容
          scope: all / body        范围
        """
        ps = self.doc.paragraphs
        if "contains" in rule:
            idx = self._first_para_contains(ps, rule["contains"])
            return [ps[idx]] if idx is not None else []
        if "index" in rule:
            i = rule["index"]
            try:
                return [ps[i]]
            except IndexError:
                return []
        return list(ps)

    def _runs(self, rule):
        """取段落中的 run 集合"""
        runs = []
        for p in self._paras(rule):
            runs.extend(p.runs)
        return runs

    # ---------------- 检查项 ----------------
    def check_font(self, r):
        """字体/字号/加粗/斜体/下划线/颜色"""
        exp = r.get("expect", {})
        runs = self._runs(r)
        if not runs:
            return 0, "未找到指定段落"

        ok_num = 0
        total = len(runs)
        msgs = []

        for run in runs:
            good = True
            f = run.font

            # 中文字体名藏在 rPr/rFonts 的 eastAsia
            ea = None
            rPr = run._element.find(qn("w:rPr"))
            if rPr is not None:
                rf = rPr.find(qn("w:rFonts"))
                if rf is not None:
                    ea = rf.get(qn("w:eastAsia"))

            if "name" in exp:
                want = str(exp["name"])
                got = ea or f.name
                if got != want:
                    good = False
                    msgs.append(f"字体 {got}≠{want}")

            if "size" in exp:
                want = _size_to_pt(exp["size"])
                got = f.size.pt if f.size else None
                if not self._near(got, want, 0.6):
                    good = False
                    msgs.append(f"字号 {got}≠{want}")

            for key, attr in (("bold", "bold"), ("italic", "italic"),
                              ("underline", "underline")):
                if key in exp:
                    got = getattr(f, attr, None)
                    if bool(got) != bool(exp[key]):
                        good = False
                        msgs.append(f"{key} {got}≠{exp[key]}")

            if "color" in exp:
                want = self._norm_color(exp["color"])
                got = None
                if f.color and f.color.rgb:
                    got = str(f.color.rgb).upper()
                if got != want:
                    good = False
                    msgs.append(f"颜色 {got}≠{want}")

            if good:
                ok_num += 1

        if ok_num == 0:
            return 0, "；".join(msgs[:3]) or "字体不符"
        if ok_num < total:
            return r.get("score", 0) * ok_num / total, f"仅 {ok_num}/{total} 段符合；{msgs[0] if msgs else ''}"
        return r.get("score", 0), "符合"

    def check_paragraph(self, r):
        """对齐/缩进/行距/段前段后"""
        exp = r.get("expect", {})
        ps = self._paras(r)
        if not ps:
            return 0, "未找到指定段落"

        ok, total, msgs = 0, len(ps), []
        for p in ps:
            fmt = p.paragraph_format
            good = True

            if "align" in exp:
                want = ALIGN.get(str(exp["align"]).strip(), str(exp["align"]).upper())
                got = str(fmt.alignment).split(" ")[0] if fmt.alignment is not None else None
                if got != want:
                    good = False
                    msgs.append(f"对齐 {got}≠{want}")

            if "first_line_indent" in exp:
                want = _size_to_pt(exp["first_line_indent"])
                got = fmt.first_line_indent.pt if fmt.first_line_indent else 0
                if not self._near(got, want, 1):
                    good = False
                    msgs.append(f"首行缩进 {round(got,1)}≠{want}")

            if "line_spacing" in exp:
                got = fmt.line_spacing
                want = exp["line_spacing"]
                if not self._near(got, want, 0.1):
                    good = False
                    msgs.append(f"行距 {got}≠{want}")

            if good:
                ok += 1

        if ok == 0:
            return 0, "；".join(msgs[:2]) or "段落格式不符"
        return r.get("score", 0) * ok / total, f"{ok}/{total} 段符合"

    def check_page_setup(self, r):
        """纸张大小/方向/页边距"""
        exp = r.get("expect", {})
        sec = self.doc.sections[0]
        msgs, good = [], True

        if "width" in exp or "height" in exp:
            w = sec.page_width.cm if sec.page_width else None
            h = sec.page_height.cm if sec.page_height else None
            if "width" in exp and not self._near(w, float(exp["width"]), 0.3):
                good = False
                msgs.append(f"宽 {round(w,1)}≠{exp['width']}cm")
            if "height" in exp and not self._near(h, float(exp["height"]), 0.3):
                good = False
                msgs.append(f"高 {round(h,1)}≠{exp['height']}cm")

        if "orientation" in exp:
            want = str(exp["orientation"]).upper()
            got = str(sec.orientation).split(" ")[0] if sec.orientation is not None else None
            if got != want:
                good = False
                msgs.append(f"方向 {got}≠{want}")

        for key, attr in (("margin_top", "top_margin"), ("margin_bottom", "bottom_margin"),
                          ("margin_left", "left_margin"), ("margin_right", "right_margin")):
            if key in exp:
                got = getattr(sec, attr)
                got = got.cm if got else None
                if not self._near(got, float(exp[key]), 0.3):
                    good = False
                    msgs.append(f"{key} {round(got,1) if got else None}≠{exp[key]}cm")

        return (r.get("score", 0), "符合") if good else (0, "；".join(msgs))

    def check_text(self, r):
        """文本包含 / 不包含"""
        full = self.doc.element.body.xml
        text = "\n".join(p.text for p in self.doc.paragraphs)
        if "contains" in r:
            hit = all(k in text for k in r["contains"]) if isinstance(r["contains"], list) \
                else r["contains"] in text
            return (r.get("score", 0), "符合") if hit else (0, f"缺少文字: {r['contains']}")
        if "not_contains" in r:
            bad = r["not_contains"]
            return (0, f"仍包含: {bad}") if bad in text else (r.get("score", 0), "符合")
        return 0, "未配置 contains / not_contains"

    def check_heading(self, r):
        """标题样式级别"""
        exp = r.get("expect", {})
        want = exp.get("level") or r.get("level")
        ps = self._paras(r)
        if not ps:
            return 0, "未找到段落"
        for p in ps:
            st = p.style.name if p.style else ""
            if want and (str(want) in st or st.lower().startswith("heading")):
                if f"标题 {want}" in st or f"Heading {want}" in st or st == str(want):
                    return r.get("score", 0), "符合"
        return 0, f"标题样式不符（当前: {ps[0].style.name if ps[0].style else None}）"

    def check_table(self, r):
        """表格数量 / 行列数 / 单元格内容"""
        exp = r.get("expect", {})
        ts = self.doc.tables
        if "count" in exp and len(ts) != int(exp["count"]):
            return 0, f"表格数 {len(ts)}≠{exp['count']}"
        if not ts:
            return 0, "无表格"
        t = ts[r.get("index", 0)]
        if "rows" in exp and len(t.rows) != int(exp["rows"]):
            return 0, f"行数 {len(t.rows)}≠{exp['rows']}"
        if "cols" in exp and len(t.columns) != int(exp["cols"]):
            return 0, f"列数 {len(t.columns)}≠{exp['cols']}"
        if "cell" in exp:
            row, col, want = exp["cell"][0], exp["cell"][1], str(exp["cell"][2])
            got = t.cell(row, col).text.strip()
            if got != want:
                return 0, f"单元格({row},{col}) {got}≠{want}"
        return r.get("score", 0), "符合"

    def check_header_footer(self, r):
        """页眉/页脚文本"""
        sec = self.doc.sections[0]
        where = r.get("where", "header")
        part = sec.header if where == "header" else sec.footer
        text = "\n".join(p.text for p in part.paragraphs) if part else ""
        want = r.get("contains")
        return (r.get("score", 0), "符合") if want in text else (0, f"页{where}内容不符")

    def check_image(self, r):
        """图片数量"""
        n = len(self.doc.inline_shapes)
        want = r.get("expect", {}).get("count", r.get("count"))
        return (r.get("score", 0), "符合") if n == int(want) else (0, f"图片数 {n}≠{want}")

    def checkers(self):
        return {
            "font": self.check_font,
            "paragraph": self.check_paragraph,
            "page_setup": self.check_page_setup,
            "text": self.check_text,
            "heading": self.check_heading,
            "table": self.check_table,
            "header_footer": self.check_header_footer,
            "image": self.check_image,
        }
