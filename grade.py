#!/usr/bin/env python3
"""
答卷评分 / 属性查看工具
--------------------------------
inspect：读出 Office 文件的属性（字体/公式/页面设置……），
        写评分规则时照着输出抄。
grade：  用评分规则 JSON 给一个答卷文件打分，验证规则写得对不对。

用法：
  python grade.py inspect --file 标准答案.xlsx
  python grade.py inspect --file 标准答案.docx --suggest
  python grade.py grade --file 学生答卷.xlsx --rules 评分规则.json

规则文件两种格式都认：
  1. checks 数组本身：[{"id":..., "type":..., ...}, ...]
  2. 完整考试包：{"etype": "excel", "checks": [...], ...}
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

EXT_TYPE = {".docx": "word", ".xlsx": "excel", ".xlsm": "excel", ".pptx": "ppt"}


# ==================== inspect ====================
def _fmt_font(f):
    """把字体对象格式化成一行可读文本"""
    parts = []
    name = getattr(f, "name", None)
    if name:
        parts.append(str(name))
    size = getattr(f, "size", None)
    if size:
        try:
            parts.append(f"{size.pt:g}磅")
        except Exception:
            pass
    if getattr(f, "bold", None):
        parts.append("加粗")
    if getattr(f, "italic", None):
        parts.append("斜体")
    if getattr(f, "underline", None):
        parts.append("下划线")
    try:
        color = f.color
        if color and color.rgb:
            parts.append(f"颜色#{color.rgb}")
    except Exception:
        pass
    return " ".join(parts) or "（默认）"


def inspect_word(path):
    from docx import Document
    from docx.oxml.ns import qn
    doc = Document(path)
    print("== 页面设置（第一节） ==")
    if doc.sections:
        s = doc.sections[0]
        print(f"  纸张: {s.page_width.cm:.1f}cm x {s.page_height.cm:.1f}cm "
              f"({'横向' if s.page_width > s.page_height else '纵向'})")
        print(f"  页边距: 上{s.top_margin.cm:.1f} 下{s.bottom_margin.cm:.1f} "
              f"左{s.left_margin.cm:.1f} 右{s.right_margin.cm:.1f} cm")
    print(f"\n== 段落（共 {len(doc.paragraphs)} 段，非空前 20 段） ==")
    shown = 0
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        shown += 1
        if shown > 20:
            print("  ...")
            break
        fonts = []
        for r in p.runs:
            if r.text.strip():
                f = _fmt_font(r.font)
                # 中文字体单独读 eastasia
                ea = r._element.rPr.rFonts.get(qn("w:eastAsia")) \
                    if r._element.rPr is not None and r._element.rPr.rFonts is not None else None
                if ea:
                    f = f"{ea}(中文) " + f
                fonts.append(f)
        align = {0: "左", 1: "居中", 2: "右", 3: "两端"}.get(
            p.alignment if p.alignment is None else int(p.alignment), str(p.alignment))
        pf = p.paragraph_format
        extra = []
        if pf.first_line_indent:
            extra.append(f"首行缩进{pf.first_line_indent.pt:g}磅")
        if pf.line_spacing:
            extra.append(f"行距{pf.line_spacing}")
        print(f"  [{shown}] 「{text[:30]}」 对齐={align} "
              f"字体={' / '.join(fonts) or '（默认）'} {' '.join(extra)}")
    if doc.tables:
        print(f"\n== 表格：{len(doc.tables)} 个 ==")
        t = doc.tables[0]
        print(f"  表1: {len(t.rows)} 行 x {len(t.columns)} 列")


def inspect_excel(path):
    from openpyxl import load_workbook
    wb = load_workbook(path)
    wbf = load_workbook(path, data_only=True)   # 取计算结果用
    print(f"== 工作表：{wb.sheetnames} ==")
    for name in wb.sheetnames:
        ws = wb[name]
        print(f"\n-- 「{name}」 {ws.max_row} 行 x {ws.max_column} 列 --")
        if ws.merged_cells.ranges:
            print(f"  合并单元格: {', '.join(str(r) for r in list(ws.merged_cells.ranges)[:10])}")
        # 公式
        formulas = []
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    val = wbf[name][c.coordinate].value
                    formulas.append(f"    {c.coordinate}: {c.value}  → 结果 {val}")
        if formulas:
            print("  公式:")
            for line in formulas[:15]:
                print(line)
        # 非默认字体的单元格
        fonts = []
        for row in ws.iter_rows(max_row=min(ws.max_row, 20)):
            for c in row:
                if c.value is None:
                    continue
                f = _fmt_font(c.font)
                if f != "（默认）" and "Calibri" not in f:
                    fonts.append(f"    {c.coordinate}: {f}")
        if fonts:
            print("  字体（非默认，前 10 个）:")
            for line in fonts[:10]:
                print(line)
        # 边框
        bordered = 0
        for row in ws.iter_rows():
            for c in row:
                b = c.border
                if any(getattr(b, side).style for side in ("left", "right", "top", "bottom")):
                    bordered += 1
        if bordered:
            print(f"  有边框的单元格: {bordered} 个")
        if getattr(ws, "_charts", None):
            print(f"  图表: {len(ws._charts)} 个 "
                  f"({', '.join(type(ch).__name__ for ch in ws._charts)})")
        # 列宽
        widths = {k: round(v.width, 1) for k, v in ws.column_dimensions.items()
                  if v.width}
        if widths:
            print(f"  自定义列宽: {dict(list(widths.items())[:8])}")


def inspect_ppt(path):
    from pptx import Presentation
    prs = Presentation(path)
    print(f"== 幻灯片：{len(prs.slides)} 页 ==")
    print(f"  尺寸: {prs.slide_width.cm:.1f}cm x {prs.slide_height.cm:.1f}cm")
    for i, slide in enumerate(prs.slides):
        print(f"\n-- 第 {i + 1} 页（{len(slide.shapes)} 个形状）--")
        for shape in slide.shapes:
            kind = shape.shape_type
            desc = f"    [{kind}]"
            if shape.has_text_frame:
                text = shape.text_frame.text.strip().replace("\n", " / ")[:40]
                if text:
                    desc += f" 「{text}」"
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if run.text.strip():
                            desc += f" 字体({_fmt_font(run.font)})"
                            break
                    else:
                        continue
                    break
            print(desc)


def cmd_inspect(args):
    ext = os.path.splitext(args.file)[1].lower()
    etype = EXT_TYPE.get(ext)
    if not etype:
        print(f"❌ 不支持 {ext}，只认 .docx / .xlsx / .pptx")
        return 1
    print(f"文件: {args.file}  类型: {etype}\n")
    {"word": inspect_word, "excel": inspect_excel, "ppt": inspect_ppt}[etype](args.file)

    if args.suggest:
        print("\n== 建议的评分点（auto_rule 自动生成） ==")
        try:
            import auto_rule
            checks = auto_rule.auto_rule(args.file, default_score=10,
                                         max_items=args.max)["checks"]
            print(json.dumps(auto_rule.clean(checks),
                             ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"（生成建议失败: {e}）")
    print("\n提示：加 --suggest 可直接给出可用的评分规则 JSON 草稿")
    return 0


# ==================== grade ====================
def cmd_grade(args):
    if not os.path.isfile(args.file):
        print(f"❌ 文件不存在: {args.file}")
        return 1
    if not os.path.isfile(args.rules):
        print(f"❌ 规则文件不存在: {args.rules}")
        return 1
    with open(args.rules, "r", encoding="utf-8") as f:
        data = json.load(f)
    checks = data if isinstance(data, list) else data.get("checks", [])
    if not checks:
        print("❌ 规则文件里没有 checks")
        return 1

    from graders import grader_for
    g = grader_for(args.file)
    total, detail = g.run(checks)
    full = sum(float(c.get("score", 0)) for c in checks)
    print(f"文件: {args.file}")
    print(f"得分: {round(total, 2)} / {round(full, 2)}\n")
    for d in detail:
        flag = "✅" if d["got"] >= d["full"] else ("⚠️" if d["got"] > 0 else "❌")
        print(f"  {flag} {d['id']:<18}{d['got']:>6}/{d['full']:<6}{d['msg']}")
    if abs(total - full) < 0.01:
        print("\n满分！")
    return 0


def main():
    p = argparse.ArgumentParser(description="答卷评分 / 属性查看工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("inspect", help="读出文件属性，辅助写评分规则")
    pi.add_argument("--file", required=True, help="docx/xlsx/pptx 文件")
    pi.add_argument("--suggest", action="store_true",
                    help="同时给出自动生成的评分规则草稿")
    pi.add_argument("--max", type=int, default=12, help="建议评分点数量上限")
    pi.set_defaults(fn=cmd_inspect)

    pg = sub.add_parser("grade", help="用评分规则给答卷打分")
    pg.add_argument("--file", required=True, help="答卷文件")
    pg.add_argument("--rules", required=True, help="评分规则 JSON")
    pg.set_defaults(fn=cmd_grade)

    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
