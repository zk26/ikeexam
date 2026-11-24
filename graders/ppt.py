"""
WPS 演示（.pptx）评分器
依赖 python-pptx
"""
from pptx import Presentation
from .base import BaseGrader

ALIGN = {
    "左": "LEFT", "左对齐": "LEFT", "居中": "CENTER", "水平居中": "CENTER",
    "右": "RIGHT", "右对齐": "RIGHT", "两端对齐": "JUSTIFY",
}


class PptGrader(BaseGrader):
    kind = "ppt"
    exts = (".pptx",)

    def open(self):
        self.doc = Presentation(self.path)

    def _slides(self, r):
        ss = self.doc.slides
        if "index" in r:
            try:
                return [ss[r["index"]]]
            except IndexError:
                return []
        return list(ss)

    def _shapes_with_text(self, slide):
        return [s for s in slide.shapes if s.has_text_frame]

    def _all_runs(self, r):
        """
        取 run 集合。
        支持 contains：只取文字里包含该关键词的形状，实现精确定位。
        """
        key = r.get("contains")
        runs = []
        for sl in self._slides(r):
            for sh in self._shapes_with_text(sl):
                txt = sh.text_frame.text
                if key and key not in txt:
                    continue
                for p in sh.text_frame.paragraphs:
                    runs.extend(p.runs)
        return runs

    def _paras(self, r):
        """类似 _all_runs，但返回段落（供 align 用）"""
        key = r.get("contains")
        out = []
        for sl in self._slides(r):
            for sh in self._shapes_with_text(sl):
                txt = sh.text_frame.text
                if key and key not in txt:
                    continue
                out.extend(sh.text_frame.paragraphs)
        return out

    # ---------------- 检查项 ----------------
    def check_slide_count(self, r):
        want = int(r.get("expect", {}).get("count", r.get("count")))
        got = len(self.doc.slides)
        return (r.get("score", 0), "符合") if got == want else (0, f"幻灯片 {got}≠{want} 页")

    def check_slide_layout(self, r):
        want = str(r.get("expect", {}).get("layout", r.get("layout")))
        for sl in self._slides(r):
            name = sl.slide_layout.name
            if want.lower() in name.lower():
                return r.get("score", 0), "符合"
        return 0, f"版式不符（当前: {[s.slide_layout.name for s in self._slides(r)][:2]}）"

    def check_title(self, r):
        """某页标题文本"""
        want = str(r.get("expect", {}).get("text", r.get("text")))
        for sl in self._slides(r):
            if sl.shapes.title and want in sl.shapes.title.text_frame.text:
                return r.get("score", 0), "符合"
        return 0, f"标题不符，未找到: {want}"

    def check_text(self, r):
        """全文包含 / 不包含"""
        alltext = []
        for sl in self.doc.slides:
            for sh in self._shapes_with_text(sl):
                alltext.append(sh.text_frame.text)
        text = "\n".join(alltext)

        if "contains" in r:
            keys = r["contains"] if isinstance(r["contains"], list) else [r["contains"]]
            miss = [k for k in keys if k not in text]
            return (r.get("score", 0), "符合") if not miss else (0, f"缺少: {miss}")
        if "not_contains" in r:
            return (0, f"仍包含: {r['not_contains']}") if r["not_contains"] in text \
                else (r.get("score", 0), "符合")
        return 0, "未配置"

    def check_font(self, r):
        """字体/字号/加粗/颜色"""
        exp = r.get("expect", {})
        runs = self._all_runs(r)
        if not runs:
            return 0, "未找到文字"

        ok, total, msgs = 0, len(runs), []
        for run in runs:
            f = run.font
            good = True
            if "name" in exp and f.name != exp["name"]:
                good = False
                msgs.append(f"字体 {f.name}≠{exp['name']}")
            if "size" in exp:
                got = f.size.pt if f.size else None
                if not self._near(got, float(exp["size"]), 0.6):
                    good = False
                    msgs.append(f"字号 {got}≠{exp['size']}")
            if "bold" in exp and bool(f.bold) != bool(exp["bold"]):
                good = False
                msgs.append(f"加粗不符")
            if "italic" in exp and bool(f.italic) != bool(exp["italic"]):
                good = False
                msgs.append(f"斜体不符")
            if "color" in exp:
                want = self._norm_color(exp["color"])
                got = None
                try:
                    if f.color is not None and f.color.type is not None \
                            and f.color.rgb is not None:
                        got = str(f.color.rgb).upper()
                except (AttributeError, ValueError):
                    got = None
                if got != want:
                    good = False
                    msgs.append(f"颜色 {got}≠{want}")
            if good:
                ok += 1

        if ok == 0:
            return 0, "；".join(msgs[:2])
        return r.get("score", 0) * ok / total, f"{ok}/{total} 处符合"

    def check_align(self, r):
        """段落对齐（支持 contains 定位）"""
        want = ALIGN.get(str(r.get("expect", {}).get("align", r.get("align"))).strip(),
                         str(r.get("align", "")).upper())
        for p in self._paras(r):
            got = str(p.alignment).split(" ")[0] if p.alignment is not None else None
            if got == want:
                return r.get("score", 0), "符合"
        return 0, f"无 {want} 对齐的段落"

    def check_background(self, r):
        """背景纯色填充"""
        want = self._norm_color(r.get("expect", {}).get("color", r.get("color")))
        for sl in self._slides(r):
            bg = sl.background
            fill = bg.fill
            try:
                if fill.type is not None and str(fill.type) == "MSO_FILL_TYPE.BACKGROUND (5)":
                    continue
                got = None
                if fill.fore_color and fill.fore_color.rgb:
                    got = str(fill.fore_color.rgb).upper()
                if got == want:
                    return r.get("score", 0), "符合"
            except Exception:
                continue
        return 0, f"背景色不符（期望 {want}）"

    def check_image(self, r):
        """图片数量"""
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        want = int(r.get("expect", {}).get("count", r.get("count")))
        n = 0
        for sl in self._slides(r):
            n += sum(1 for s in sl.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE)
        return (r.get("score", 0), "符合") if n == want else (0, f"图片 {n}≠{want}")

    def check_table(self, r):
        """表格：数量 / 行列"""
        exp = r.get("expect", {})
        found = []
        for sl in self._slides(r):
            for sh in sl.shapes:
                if sh.has_table:
                    found.append(sh.table)
        if "count" in exp and len(found) != int(exp["count"]):
            return 0, f"表格数 {len(found)}≠{exp['count']}"
        if not found:
            return 0, "无表格"
        t = found[0]
        if "rows" in exp and len(t.rows) != int(exp["rows"]):
            return 0, f"行 {len(t.rows)}≠{exp['rows']}"
        if "cols" in exp and len(t.columns) != int(exp["cols"]):
            return 0, f"列 {len(t.columns)}≠{exp['cols']}"
        return r.get("score", 0), "符合"

    def check_animation(self, r):
        """是否有动画（读 XML 判断）"""
        ns = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
        for sl in self._slides(r):
            xml = sl.element.xml
            if "anim" in xml or "<p:timing>" in xml.replace(ns, ""):
                return r.get("score", 0), "符合"
        return 0, "未检测到动画"

    def checkers(self):
        return {
            "slide_count": self.check_slide_count,
            "slide_layout": self.check_slide_layout,
            "title": self.check_title,
            "text": self.check_text,
            "font": self.check_font,
            "align": self.check_align,
            "background": self.check_background,
            "image": self.check_image,
            "table": self.check_table,
            "animation": self.check_animation,
        }
