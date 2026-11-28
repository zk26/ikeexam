#!/usr/bin/env python3
"""
自动对比标准答案 → 生成评分规则
--------------------------------
老师自己做一份满分答案，本工具读出它的全部属性，
自动生成评分规则（checks JSON），不用手写。

用法：
  python3 auto_rule.py 标准答案.docx
  python3 auto_rule.py 标准答案.xlsx --out rule.json
  python3 auto_rule.py 标准答案.pptx --score 10

生成后务必做一次「自评验证」：
  python3 auto_rule.py 标准答案.xlsx --verify
  拿不到满分就说明规则要调整。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

try:
    from ..graders.base import validate_ooxml
except ImportError:
    sys.path.insert(0, os.path.dirname(ROOT))
    from graders.base import validate_ooxml

from docx.oxml.ns import qn           # noqa: E402
from openpyxl import load_workbook     # noqa: E402
from pptx import Presentation          # noqa: E402

# 磅值 -> 中文字号
PT_TO_CN = [
    (42, "初号"), (36, "小初"), (26, "一号"), (24, "小一"), (22, "二号"),
    (18, "小二"), (16, "三号"), (15, "小三"), (14, "四号"), (12, "小四"),
    (10.5, "五号"), (9, "小五"), (7.5, "六号"), (6.5, "小六"), (5.5, "七号"),
]
ALIGN_CN = {"LEFT": "左", "CENTER": "居中", "RIGHT": "右",
            "JUSTIFY": "两端对齐", "DISTRIBUTE": "分散对齐"}

# 默认字体名 —— 只有这些不算「有考查价值的格式」
DEFAULT_FONTS = {"Calibri", "宋体", "等线", "Times New Roman", "Arial", "SimSun"}


def pt_to_cn(pt):
    """磅值尽量转成中文字号，转不了就保留数字"""
    if pt is None:
        return None
    for v, name in PT_TO_CN:
        if abs(pt - v) < 0.3:
            return name
    return round(pt, 1)


def trim(s, n=15):
    """截取段落前 n 个字符作为定位关键词"""
    if s is None:
        return ""
    s = "".join(s.split())          # 去掉所有空白，避免换行导致匹配不上
    return s[:n]


# ==================== WPS 文字 ====================
def analyze_word(path, default_score, max_items=12):
    from docx import Document
    validate_ooxml(path)
    doc = Document(path)
    checks = []

    # 1. 页面设置（永远值得查）
    s = doc.sections[0]
    page = {"width": round(s.page_width.cm, 1), "height": round(s.page_height.cm, 1)}
    orient = str(s.orientation).split(" ")[0].lower() if s.orientation is not None else None
    if orient:
        page["orientation"] = orient
    checks.append({"id": "页面设置", "type": "page_setup", "expect": page,
                   "score": default_score, "_why": "纸张尺寸与方向"})

    # 2. 逐段落：抓有特征的（非默认字体、有格式）
    seen_font = set()
    for i, p in enumerate(doc.paragraphs):
        text = p.text.strip()
        if not text:
            continue
        key = trim(text)

        # 段落格式
        fmt = p.paragraph_format
        exp = {}
        if fmt.alignment is not None:
            a = str(fmt.alignment).split(" ")[0]
            exp["align"] = ALIGN_CN.get(a, a)
        if fmt.first_line_indent:
            exp["first_line_indent"] = round(fmt.first_line_indent.pt, 1)
        if fmt.line_spacing and isinstance(fmt.line_spacing, (int, float)):
            exp["line_spacing"] = round(float(fmt.line_spacing), 2)
        if exp and len(checks) < max_items:
            checks.append({"id": f"段落格式{i+1}", "type": "paragraph",
                           "contains": key, "expect": exp,
                           "score": default_score,
                           "_why": f"第{i+1}段的 " + "、".join(exp.keys())})

        # 字体（去重，同样组合只生成一条）
        if p.runs and len(checks) < max_items:
            f = p.runs[0].font
            ea = None
            rPr = p.runs[0]._element.find(qn("w:rPr"))
            if rPr is not None:
                rf = rPr.find(qn("w:rFonts"))
                if rf is not None:
                    ea = rf.get(qn("w:eastAsia"))
            fexp = {}
            if ea or f.name:
                fexp["name"] = ea or f.name
            if f.size:
                fexp["size"] = pt_to_cn(f.size.pt)
            if f.bold is not None:
                fexp["bold"] = bool(f.bold)
            if f.italic:
                fexp["italic"] = True
            if f.color and f.color.rgb:
                fexp["color"] = str(f.color.rgb).upper()
            sig = json.dumps(fexp, sort_keys=True, ensure_ascii=False)
            if fexp and sig not in seen_font:
                seen_font.add(sig)
                checks.append({"id": f"字体{i+1}", "type": "font",
                               "contains": key, "expect": fexp,
                               "score": default_score,
                               "_why": f"第{i+1}段的字体设置"})

    # 3. 表格
    if doc.tables and len(checks) < max_items:
        t = doc.tables[0]
        checks.append({"id": "表格", "type": "table",
                       "expect": {"count": len(doc.tables),
                                  "rows": len(t.rows), "cols": len(t.columns)},
                       "score": default_score,
                       "_why": f"{len(doc.tables)}个表格，首个 {len(t.rows)}行×{len(t.columns)}列"})

    # 4. 页眉页脚
    for where, part in (("header", doc.sections[0].header), ("footer", doc.sections[0].footer)):
        if part is not None:
            txt = "".join(x.text for x in part.paragraphs).strip()
            if txt and len(checks) < max_items:
                checks.append({"id": "页眉" if where == "header" else "页脚",
                               "type": "header_footer", "where": where,
                               "contains": trim(txt, 10),
                               "score": default_score,
                               "_why": f"页{where}内容「{trim(txt,10)}」"})

    # 5. 图片
    n_img = len(doc.inline_shapes)
    if n_img and len(checks) < max_items:
        checks.append({"id": "插图", "type": "image",
                       "expect": {"count": n_img},
                       "score": default_score, "_why": f"{n_img}张图片"})

    return checks[:max_items]


# ==================== WPS 表格 ====================
def analyze_excel(path, default_score, max_items=12):
    validate_ooxml(path)
    wb_f = load_workbook(path, data_only=False)
    checks = []
    ws = wb_f.active
    sheet = ws.title

    def cellref(c):
        return c.coordinate

    # 1. 工作表名
    if sheet and sheet != "Sheet1":
        checks.append({"id": "工作表名", "type": "sheet_name", "name": sheet,
                       "score": default_score, "_why": f"工作表改名为「{sheet}」"})

    # 2. 公式（最有价值，最多取 5 个不同函数组合）
    fn_seen = {}
    for row in ws.iter_rows():
        for c in row:
            v = c.value
            if isinstance(v, str) and v.startswith("="):
                up = v.upper()
                fn = None
                for f in ("VLOOKUP", "SUMIFS", "COUNTIFS", "AVERAGE", "ROUND",
                          "SUM", "COUNT", "MAX", "MIN", "IF", "RANK"):
                    if f in up:
                        fn = f
                        break
                if fn and fn not in fn_seen:
                    fn_seen[fn] = cellref(c)
    for fn, ref in list(fn_seen.items())[:5]:
        checks.append({"id": f"公式-{fn}", "type": "formula", "sheet": sheet,
                       "cell": ref, "expect": {"function": fn},
                       "score": default_score,
                       "_why": f"{ref} 使用了 {fn} 函数"})

    # 3. 字体（采样：只抓有非默认格式的行）
    for row in ws.iter_rows(max_row=min(ws.max_row, 30)):
        c = row[0]
        if c.value is None:
            continue
        f = c.font
        exp = {}
        if f.name and f.name not in DEFAULT_FONTS:
            exp["name"] = f.name
        if f.size and f.size != 11:
            exp["size"] = f.size
        if f.bold:
            exp["bold"] = True
        if f.italic:
            exp["italic"] = True
        if f.color and isinstance(f.color.rgb, str):
            exp["color"] = f.color.rgb[-6:].upper()
        # exp 为空（默认字体/无格式）没有考查价值，跳过。
        # 注：exp 里一旦有 "name"，必然来自 f.name not in DEFAULT_FONTS（见上），
        # 所以不存在「只含默认字体名」的情况，原来的两个判断是永远为假的死分支。
        if not exp:
            continue
        if len(checks) < max_items:
            checks.append({"id": f"字体-{cellref(c)}", "type": "font",
                           "sheet": sheet, "cell": cellref(c), "expect": exp,
                           "score": default_score,
                           "_why": f"{cellref(c)} 的字体设置"})

    # 4. 填充色（采样前 20 行）
    for row in ws.iter_rows(max_row=min(ws.max_row, 20)):
        for c in row:
            try:
                fc = c.fill.fgColor
                if fc is not None and isinstance(fc.rgb, str) and c.fill.fill_type == "solid":
                    checks.append({"id": f"填充-{cellref(c)}", "type": "fill",
                                   "sheet": sheet, "cell": cellref(c),
                                   "expect": {"color": fc.rgb[-6:].upper()},
                                   "score": default_score,
                                   "_why": f"{cellref(c)} 填充色"})
                    break
            except Exception:
                continue
        if len(checks) >= max_items:
            break
        if any(x["id"].startswith("填充-") for x in checks):
            break

    # 5. 边框（检查 A1 所在区域是否整体有框）
    try:
        b = ws["A1"].border
        if all(bool(getattr(b, s).style) for s in ("left", "right", "top", "bottom")):
            rng = f"A1:{ws.cell(row=ws.max_row, column=ws.max_column).coordinate}"
            checks.append({"id": "表格边框", "type": "border", "sheet": sheet,
                           "range": rng, "expect": {"side": "all"},
                           "score": default_score, "_why": f"{rng} 区域加边框"})
    except Exception:
        pass

    # 6. 数字格式
    for row in ws.iter_rows(max_row=min(ws.max_row, 20)):
        for c in row:
            nf = c.number_format
            if nf and nf != "General" and len(checks) < max_items:
                checks.append({"id": f"数字格式-{cellref(c)}", "type": "number_format",
                               "sheet": sheet, "cell": cellref(c), "format": nf,
                               "score": default_score,
                               "_why": f"{cellref(c)} 格式为 {nf}"})
                break
        if any(x["id"].startswith("数字格式-") for x in checks):
            break

    # 7. 合并单元格
    merged = [str(m) for m in ws.merged_cells.ranges]
    if merged and len(checks) < max_items:
        checks.append({"id": "合并单元格", "type": "merged", "sheet": sheet,
                       "range": merged[0], "score": default_score,
                       "_why": f"合并了 {merged[0]}"})

    # 8. 图表
    charts = getattr(ws, "_charts", [])
    if charts and len(checks) < max_items:
        ctype = type(charts[0]).__name__.replace("Chart", "").lower()
        checks.append({"id": "图表", "type": "chart", "sheet": sheet,
                       "expect": {"count": len(charts), "type": ctype},
                       "score": default_score,
                       "_why": f"{len(charts)}个图表（{ctype}）"})

    return checks[:max_items]


# ==================== WPS 演示 ====================
def analyze_ppt(path, default_score, max_items=12):
    validate_ooxml(path)
    prs = Presentation(path)
    checks = []

    # 1. 页数
    checks.append({"id": "幻灯片页数", "type": "slide_count",
                   "count": len(prs.slides), "score": default_score,
                   "_why": f"共 {len(prs.slides)} 页"})

    # 2. 首页版式 + 标题
    if prs.slides:
        s0 = prs.slides[0]
        layout = s0.slide_layout.name
        checks.append({"id": "首页版式", "type": "slide_layout", "index": 0,
                       "layout": layout, "score": default_score,
                       "_why": f"首页用「{layout}」版式"})
        if s0.shapes.title and s0.shapes.title.text_frame.text.strip():
            t = s0.shapes.title.text_frame.text.strip()
            checks.append({"id": "首页标题", "type": "title", "index": 0,
                           "text": trim(t, 12), "score": default_score,
                           "_why": f"首页标题「{trim(t,12)}」"})

    # 3. 字体（采样首页，带 contains 精确定位）
    if prs.slides:
        for sh in prs.slides[0].shapes:
            if not sh.has_text_frame:
                continue
            txt = sh.text_frame.text.strip()
            if not txt:
                continue
            for p in sh.text_frame.paragraphs:
                if not p.runs:
                    continue
                f = p.runs[0].font
                exp = {}
                if f.name:
                    exp["name"] = f.name
                if f.size:
                    exp["size"] = pt_to_cn(f.size.pt)
                if f.bold:
                    exp["bold"] = True
                # 颜色可能是 _NoneColor（没显式设置），直接读 .rgb 会抛异常
                try:
                    if f.color is not None and f.color.type is not None \
                            and f.color.rgb is not None:
                        exp["color"] = str(f.color.rgb).upper()
                except (AttributeError, ValueError):
                    pass
                if exp:
                    rule = {"id": "首页字体", "type": "font", "index": 0,
                            "expect": exp, "score": default_score,
                            "_why": f"首页「{trim(txt,10)}」的字体设置"}
                    # 带上 contains，评分时只查这个文本框，避免整页误判
                    rule["contains"] = trim(txt, 10)
                    checks.append(rule)
                    break
            if any(x["id"] == "首页字体" for x in checks):
                break

    # 3b. 首页对齐
    if prs.slides:
        for sh in prs.slides[0].shapes:
            if not sh.has_text_frame:
                continue
            txt = sh.text_frame.text.strip()
            for p in sh.text_frame.paragraphs:
                if p.alignment is not None:
                    a = str(p.alignment).split(" ")[0]
                    checks.append({"id": "首页对齐", "type": "align", "index": 0,
                                   "contains": trim(txt, 10),
                                   "expect": {"align": ALIGN_CN.get(a, a)},
                                   "score": default_score,
                                   "_why": f"首页文字 {ALIGN_CN.get(a,a)}对齐"})
                    break
            if any(x["id"] == "首页对齐" for x in checks):
                break

    # 4. 图片 / 表格统计
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    n_img = n_tbl = 0
    tbl_rows = tbl_cols = 0
    for sl in prs.slides:
        for sh in sl.shapes:
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                n_img += 1
            if sh.has_table:
                n_tbl += 1
                if not tbl_rows:
                    tbl_rows, tbl_cols = len(sh.table.rows), len(sh.table.columns)
    if n_img:
        checks.append({"id": "插图", "type": "image", "count": n_img,
                       "score": default_score, "_why": f"{n_img}张图片"})
    if n_tbl:
        exp = {"count": n_tbl}
        if tbl_rows:
            exp["rows"], exp["cols"] = tbl_rows, tbl_cols
        checks.append({"id": "表格", "type": "table", "expect": exp,
                       "score": default_score,
                       "_why": f"{n_tbl}个表格 {tbl_rows}×{tbl_cols}"})

    # 5. 动画
    for sl in prs.slides:
        if "anim" in sl.element.xml or "<p:timing>" in sl.element.xml:
            checks.append({"id": "动画", "type": "animation", "score": default_score,
                           "_why": "设置了动画"})
            break

    return checks[:max_items]


# ==================== 入口 ====================
def auto_rule(path, default_score=10, max_items=12):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        etype, checks = "word", analyze_word(path, default_score, max_items)
    elif ext in (".xlsx", ".xlsm"):
        etype, checks = "excel", analyze_excel(path, default_score, max_items)
    elif ext == ".pptx":
        etype, checks = "ppt", analyze_ppt(path, default_score, max_items)
    else:
        raise ValueError(f"不支持 {ext}，只认 .docx / .xlsx / .pptx")

    name = os.path.splitext(os.path.basename(path))[0]
    return {
        "title": f"{name} 操作考试",
        "etype": etype,
        "duration": 40,
        "theory_duration": 20,
        "requirement": "1. 打开下发的素材文件\n2. 按要求完成操作\n3. 保存并提交",
        "materials": [],
        "save_as": f"学号_姓名{ext}",
        "checks": checks,
    }


def clean(checks):
    """去掉辅助字段 _why，输出干净的 checks"""
    return [{k: v for k, v in c.items() if k != "_why"} for c in checks]


def verify(path, pack):
    """自评验证：用生成的规则给标准答案打分，应该拿满分"""
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from ikeexam.graders import grader_for
    checks = clean(pack["checks"])
    g = grader_for(path)
    total, detail = g.run(checks)
    full = sum(float(c.get("score", 0)) for c in checks)
    return round(total, 2), round(full, 2), detail


def main():
    p = argparse.ArgumentParser(description="自动对比标准答案生成评分规则")
    p.add_argument("file", help="标准答案文件（docx/xlsx/pptx）")
    p.add_argument("--out", help="输出 JSON 文件")
    p.add_argument("--score", type=float, default=10, help="每个评分点的默认分值")
    p.add_argument("--max", type=int, default=12, help="最多生成多少个评分点")
    p.add_argument("--verify", action="store_true", help="生成后自评验证")
    a = p.parse_args()

    if not os.path.isfile(a.file):
        print(f"文件不存在: {a.file}")
        sys.exit(1)

    pack = auto_rule(a.file, a.score, a.max)
    print(f"文件: {a.file}")
    print(f"类型: {pack['etype']}")
    print(f"\n自动生成 {len(pack['checks'])} 个评分点：\n")
    for i, c in enumerate(pack["checks"], 1):
        why = c.get("_why", "")
        loc = c.get("contains") or c.get("cell") or c.get("range") or c.get("sheet") or ""
        print(f"{i:>2}. {c['id']:<16} [{c['type']}] {why}")
        if loc:
            print(f"    定位: {loc}")

    if a.verify:
        total, full, detail = verify(a.file, pack)
        print("\n" + "=" * 52)
        print(f" 自评验证：{total} / {full}")
        print("=" * 52)
        for d in detail:
            flag = "✅" if d["got"] >= d["full"] else ("⚠️" if d["got"] > 0 else "❌")
            print(f"  {flag} {d['id']:<18}{d['got']:>6}/{d['full']:<6}{d['msg']}")
        if abs(total - full) < 0.01:
            print("\n✅ 满分！规则可以直接用。")
        else:
            print(f"\n⚠️  差 {round(full-total, 2)} 分。上面标 ❌/⚠️ 的项需要手动调整或删除。")

    out = a.out or os.path.splitext(a.file)[0] + "_规则.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({k: (clean(v) if k == "checks" else v)
                   for k, v in pack.items()}, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存: {out}")
    print("   把里面的 checks 数组贴到后台的「评分规则」框里")


if __name__ == "__main__":
    main()
