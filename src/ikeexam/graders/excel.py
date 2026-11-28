"""
WPS 表格（.xlsx）评分器
依赖 openpyxl
注意：读公式需 data_only=False，读计算结果需 data_only=True
"""
from openpyxl import load_workbook
from .base import BaseGrader, validate_ooxml

ALIGN = {
    "左": "left", "左对齐": "left", "居中": "center", "水平居中": "center",
    "右": "right", "右对齐": "right", "两端对齐": "justify",
}


class ExcelGrader(BaseGrader):
    kind = "excel"
    exts = (".xlsx", ".xlsm")

    def open(self):
        validate_ooxml(self.path)
        # 公式版（保留公式字符串）
        self.wb_f = load_workbook(self.path, data_only=False)
        # 数值版（公式已计算的结果）
        try:
            self.wb_v = load_workbook(self.path, data_only=True)
        except Exception:
            self.wb_v = self.wb_f
        self.doc = self.wb_f

    def _sheet(self, r):
        name = r.get("sheet")
        if name:
            for key in ("wb_f", "wb_v"):
                wb = getattr(self, key)
                if name in wb.sheetnames:
                    return getattr(self, key)[name] if key == "wb_f" else wb[name]
            return self.wb_f[name] if name in self.wb_f.sheetnames else self.wb_f.active
        return self.wb_f.active

    def _sheet_v(self, r):
        name = r.get("sheet")
        if name and name in self.wb_v.sheetnames:
            return self.wb_v[name]
        return self.wb_v.active

    @staticmethod
    def _topleft(ws, c):
        """
        合并单元格只有左上角存了样式，其余位置读出来是空的。
        遇到被合并的单元格，回退到所属合并区的左上角再读。
        """
        try:
            from openpyxl.cell.cell import MergedCell
            if isinstance(c, MergedCell):
                for rng in ws.merged_cells.ranges:
                    if c.coordinate in rng:
                        return ws.cell(row=rng.min_row, column=rng.min_col)
        except Exception:
            pass
        return c

    def _cells(self, r):
        """
        从规则里取单元格对象，三种写法都支持：
          range: "A1:F1"        整块区域
          cells: [A1, B2]       逐个列出
          cell:  A1             单个
        合并区内的格子会自动映射到左上角去读样式。
        """
        ws = self._sheet(r)
        if r.get("range"):
            rng = ws[r["range"]]
            flat = []
            # 单个单元格（如 "F1"）openpyxl 直接返回 Cell，不是元组；
            # 不处理的话下面迭代会报 'Cell' object is not iterable
            rows = rng if isinstance(rng, tuple) else ((rng,),)
            for row in rows:
                flat.extend(row if isinstance(row, tuple) else [row])
        elif r.get("cells"):
            flat = [ws[c] for c in r["cells"]]
        elif r.get("cell"):
            flat = [ws[r["cell"]]]
        else:
            return []
        return [self._topleft(ws, c) for c in flat]

    # ---------------- 检查项 ----------------
    def check_cell_value(self, r):
        """单元格的值（计算结果）"""
        ws = self._sheet_v(r)
        want = r.get("expect", {}).get("value", r.get("value"))
        cells = r.get("cells") or [r.get("cell")]
        bad = []
        for c in cells:
            got = ws[c].value
            if isinstance(want, (int, float)) and isinstance(got, (int, float)):
                if not self._near(got, want, 0.01):
                    bad.append(f"{c}: {got}≠{want}")
            elif str(got).strip() != str(want).strip():
                bad.append(f"{c}: {got}≠{want}")
        if bad:
            return 0, "；".join(bad[:3])
        return r.get("score", 0), "符合"

    def check_formula(self, r):
        """单元格公式：包含某函数，或完全等于某公式"""
        ws = self._sheet(r)
        exp = r.get("expect", {})
        cell = r.get("cell")
        got = ws[cell].value
        if not isinstance(got, str) or not got.startswith("="):
            return 0, f"{cell} 不是公式（值={got}）"
        up = got.upper()

        if "function" in exp:
            fns = exp["function"]
            fns = fns if isinstance(fns, list) else [fns]
            if not any(f.upper() in up for f in fns):
                return 0, f"{cell} 未使用 {fns}（实际: {got}）"
        if "equals" in exp:
            want = str(exp["equals"]).upper().replace(" ", "")
            if up.replace(" ", "") != want:
                return 0, f"{cell} 公式不符（{got}）"
        return r.get("score", 0), "符合"

    def check_font(self, r):
        """字体/字号/加粗/斜体/颜色"""
        exp = r.get("expect", {})
        flat = self._cells(r)

        ok, total, msgs = 0, 0, []
        for c in flat:
            if c.value is None:
                continue
            total += 1
            f = c.font
            good = True
            if "name" in exp and f.name != exp["name"]:
                good = False
                msgs.append(f"字体 {f.name}≠{exp['name']}")
            if "size" in exp and not self._near(f.size, float(exp["size"]), 0.6):
                good = False
                msgs.append(f"字号 {f.size}≠{exp['size']}")
            if "bold" in exp and bool(f.bold) != bool(exp["bold"]):
                good = False
                msgs.append(f"加粗 {f.bold}≠{exp['bold']}")
            if "italic" in exp and bool(f.italic) != bool(exp["italic"]):
                good = False
                msgs.append(f"斜体不符")
            if "color" in exp:
                want = self._norm_color(exp["color"])
                got = None
                if f.color and f.color.rgb and isinstance(f.color.rgb, str):
                    got = f.color.rgb[-6:].upper()
                if got != want:
                    good = False
                    msgs.append(f"颜色 {got}≠{want}")
            if good:
                ok += 1

        if total == 0:
            return 0, "区域为空"
        if ok == 0:
            return 0, "；".join(msgs[:2])
        return r.get("score", 0) * ok / total, f"{ok}/{total} 单元格符合"

    def check_fill(self, r):
        """单元格填充色"""
        want = self._norm_color(r.get("expect", {}).get("color", r.get("color")))
        bad = []
        for c in self._cells(r):
            got = None
            try:
                fc = c.fill.fgColor
                if fc is not None and isinstance(fc.rgb, str):
                    got = fc.rgb[-6:].upper()
            except Exception:
                got = None
            if got != want:
                bad.append(f"{c.coordinate}: {got}≠{want}")
        if bad:
            return 0, "；".join(bad[:3])
        return (r.get("score", 0), "符合") if self._cells(r) else (0, "未指定单元格")

    def check_border(self, r):
        """是否设置了边框"""
        side = r.get("expect", {}).get("side", "all")
        cells = self._cells(r)
        bad = []
        for c in cells:
            b = c.border
            try:
                if side == "all":
                    ok = all(bool(getattr(b, s).style)
                             for s in ("left", "right", "top", "bottom"))
                else:
                    ok = bool(getattr(b, side, None) and getattr(b, side).style)
            except Exception:
                ok = False
            if not ok:
                bad.append(c.coordinate)
        if bad:
            return 0, f"无边框: {bad[:3]}"
        return (r.get("score", 0), "符合") if cells else (0, "未指定单元格")

    def check_number_format(self, r):
        """数字格式，如 0.00 / 0% / ¥#,##0"""
        want = str(r.get("expect", {}).get("format", r.get("format")))
        cells = self._cells(r)
        bad = [c.coordinate for c in cells if str(c.number_format) != want]
        if bad:
            return 0, f"{bad[:2]} 格式={bad and cells[0].number_format}≠{want}"
        return (r.get("score", 0), "符合") if cells else (0, "未指定单元格")

    def check_alignment(self, r):
        """对齐 / 自动换行"""
        exp = r.get("expect", {})
        bad = []
        for c in self._cells(r):
            a = c.alignment
            if "horizontal" in exp:
                want = ALIGN.get(str(exp["horizontal"]).strip(),
                                 str(exp["horizontal"]).lower())
                if a.horizontal != want:
                    bad.append(f"{c.coordinate}: {a.horizontal}≠{want}")
            if "wrap" in exp and bool(a.wrap_text) != bool(exp["wrap"]):
                bad.append(f"{c.coordinate}: 换行 {a.wrap_text}≠{exp['wrap']}")
        if bad:
            return 0, "；".join(bad[:2])
        return (r.get("score", 0), "符合") if self._cells(r) else (0, "未指定单元格")

    def check_merged(self, r):
        """合并单元格范围"""
        ws = self._sheet(r)
        want = str(r.get("expect", {}).get("range", r.get("range")))
        merged = [str(m) for m in ws.merged_cells.ranges]
        return (r.get("score", 0), "符合") if want in merged else (0, f"未合并 {want}（现有 {merged[:3]}）")

    def check_chart(self, r):
        """图表数量 / 类型"""
        ws = self._sheet(r)
        exp = r.get("expect", {})
        charts = getattr(ws, "_charts", [])
        if "count" in exp and len(charts) != int(exp["count"]):
            return 0, f"图表数 {len(charts)}≠{exp['count']}"
        if "type" in exp and charts:
            got = type(charts[0]).__name__
            if str(exp["type"]).lower() not in got.lower():
                return 0, f"图表类型 {got}≠{exp['type']}"
        return r.get("score", 0), "符合"

    def check_sheet_name(self, r):
        """工作表改名"""
        want = r.get("expect", {}).get("name", r.get("name"))
        return (r.get("score", 0), "符合") if want in self.wb_f.sheetnames else (
            0, f"无工作表 {want}（现有 {self.wb_f.sheetnames}）")

    def checkers(self):
        return {
            "cell_value": self.check_cell_value,
            "formula": self.check_formula,
            "font": self.check_font,
            "fill": self.check_fill,
            "border": self.check_border,
            "number_format": self.check_number_format,
            "alignment": self.check_alignment,
            "merged": self.check_merged,
            "chart": self.check_chart,
            "sheet_name": self.check_sheet_name,
        }
