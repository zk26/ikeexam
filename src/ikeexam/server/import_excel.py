"""
题库导入：Excel 批量 + 手动补录
---------------------------------
用法：
  # 1. 先拿模板
  python3 import_excel.py template 题库模板.xlsx

  # 2. 老师填好 Excel 后导入
  python3 import_excel.py import 题库.xlsx

  # 3. 查看现有题库
  python3 import_excel.py list
"""
import os
import sys

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, PatternFill

try:
    from . import db
    from .graders.base import validate_ooxml
except ImportError:
    import db
    from graders.base import validate_ooxml

# 题型别名 -> 标准类型
TYPE_ALIAS = {
    "单选": "single", "单选题": "single", "single": "single", "s": "single", "1": "single",
    "多选": "multi", "多选题": "multi", "multiple": "multi", "multi": "multi", "m": "multi", "2": "multi",
    "判断": "judge", "判断题": "judge", "judge": "judge", "j": "judge", "3": "judge",
    "填空": "blank", "填空题": "blank", "blank": "blank", "b": "blank", "4": "blank",
    # 程序填空
    "code": "code", "程序填空": "code", "程序题": "code", "编程": "code",
    "编程填空": "code", "代码填空": "code", "coding": "code", "5": "code",
}

# 语言别名
LANG_ALIAS = {
    "python": "python", "py": "python", "python3": "python", "派森": "python",
    "c++": "cpp", "cpp": "cpp", "c": "cpp", "g++": "cpp", "c语言": "cpp",
}

HEADERS = ["题型", "题目", "选项A", "选项B", "选项C", "选项D", "答案", "分值", "标签",
           "语言", "代码", "运行输入"]

HEAD_DESC = [
    "单选/多选/判断/填空",
    "题目正文",
    "选项A内容（填空判断留空）",
    "选项B内容",
    "选项C内容",
    "选项D内容",
    "单选填A/B/C/D；多选填ABC；判断填正确或错误；填空填答案，多个用|分隔",
    "数字，默认2",
    "选填，便于抽题",
]


def norm_type(v):
    if v is None:
        return None
    return TYPE_ALIAS.get(str(v).strip().lower())


def make_template(out_path):
    """生成 Excel 模板"""
    wb = Workbook()
    ws = wb.active
    ws.title = "题库"

    # 表头
    for i, h in enumerate(HEADERS, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4472C4")
        c.alignment = Alignment(horizontal="center")

    # 说明行（第2行，淡黄底，导入时自动跳过）
    for i, d in enumerate(HEAD_DESC, 1):
        c = ws.cell(row=2, column=i, value=d)
        c.font = Font(size=9, italic=True, color="808080")
        c.fill = PatternFill("solid", fgColor="FFF2CC")

    # 示例
    samples = [
        ["单选", "WPS文字的进程名是？", "wps.exe", "et.exe", "wpp.exe", "wpspdf.exe", "A", 2, "基础"],
        ["多选", "WPS三件套包含哪些组件？", "文字", "表格", "演示", "PDF", "ABC", 4, "基础"],
        ["判断", "WPS表格的进程名是et.exe", "", "", "", "", "正确", 2, "基础"],
        ["填空", "1GB等于多少MB？", "", "", "", "", "1024", 2, "基础"],
        ["填空", "中国的首都是____", "", "", "", "", "北京|北平", 2, "常识"],
    ]
    for r, row in enumerate(samples, 3):
        for i, v in enumerate(row, 1):
            ws.cell(row=r, column=i, value=v)

    for col, w in zip("ABCDEFGHI", (10, 40, 22, 22, 22, 22, 16, 8, 12)):
        ws.column_dimensions[col].width = w

    wb.save(out_path)
    return out_path


def parse_excel(path):
    """解析 Excel -> 题目列表"""
    validate_ooxml(path)
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb.active

    items = []
    skipped = []

    for r in range(2, ws.max_row + 1):  # 从第2行开始（第1行表头，第2行说明也算数据行但会被跳过）
        vals = [ws.cell(row=r, column=i).value for i in range(1, len(HEADERS) + 1)]
        if all(v is None or str(v).strip() == "" for v in vals):
            continue

        qtype_raw, stem = vals[0], vals[1]
        qtype = norm_type(qtype_raw)

        # 跳过说明行 / 无法识别的题型
        if qtype is None:
            if stem and ("题型" in str(qtype_raw) or "单选" in str(qtype_raw)):
                continue  # 说明行
            skipped.append((r, f"题型无法识别: {qtype_raw!r}"))
            continue

        stem = str(stem or "").strip()
        if not stem:
            skipped.append((r, "题干为空"))
            continue

        # 程序填空的额外字段（第 10~12 列：语言 / 代码 / 运行输入）
        language = code = stdin_data = ""
        if qtype == "code" and len(vals) >= 11:
            language = LANG_ALIAS.get(str(vals[9] or "").strip().lower(), "python")
            code = str(vals[10] or "")
            stdin_data = str(vals[11] or "") if len(vals) >= 12 else ""

        opts = [str(v).strip() for v in vals[2:6] if v is not None and str(v).strip()]
        answer = str(vals[6]).strip() if vals[6] is not None else ""
        if not answer:
            skipped.append((r, "答案为空"))
            continue

        try:
            score = float(vals[7]) if vals[7] not in (None, "") else 2.0
        except (TypeError, ValueError):
            score = 2.0

        category = str(vals[8]).strip() if vals[8] is not None else ""

        # 判断题没选项，补默认
        if qtype == "judge" and not opts:
            opts = ["正确", "错误"]

        # 单选/多选没选项，跳过
        if qtype in ("single", "multi") and not opts:
            skipped.append((r, "选择题缺少选项"))
            continue

        # 给选项加 A. B. 前缀（老师若已写则保留）
        pretty = []
        for i, o in enumerate(opts):
            tag = "ABCD"[i]
            if o and o[0] in "ABCD" and o[1:2] in (".", "、"):
                pretty.append(o)
            else:
                pretty.append(f"{tag}. {o}")

        # 程序填空自检：空的数量要和答案段数一致
        if qtype == "code":
            import re as _re
            n_blank = len(_re.findall(r"_{4,}", code))
            n_ans = len([x for x in str(answer).split("||") if str(x).strip()])
            if n_blank == 0:
                skipped.append((r, "程序填空没有找到 ____ 空位"))
                continue
            if n_ans != n_blank:
                skipped.append((r, f"空位数({n_blank})与答案段数({n_ans})不符"))
                continue

        items.append({
            "qtype": qtype, "stem": stem, "options": pretty,
            "answer": answer, "score": score, "category": category,
            "language": language, "code": code, "stdin_data": stdin_data,
        })

    wb.close()
    return items, skipped


def cmd_template(args):
    p = make_template(args.file)
    print(f"✅ 模板已生成: {p}")
    print("   按第2行的说明填写，从第3行开始是正式数据")


def cmd_import(args):
    if not os.path.isfile(args.file):
        print(f"文件不存在: {args.file}")
        sys.exit(1)
    items, skipped = parse_excel(args.file)
    if not items:
        print("没有解析到任何题目")
        for r, why in skipped:
            print(f"  第{r}行: {why}")
        sys.exit(1)

    print(f"解析到 {len(items)} 道题：")
    from collections import Counter
    cnt = Counter(i["qtype"] for i in items)
    names = {"single": "单选", "multi": "多选", "judge": "判断", "blank": "填空"}
    for k, v in cnt.items():
        print(f"   {names[k]}: {v}")

    if skipped:
        print(f"\n⚠️  跳过 {len(skipped)} 行：")
        for r, why in skipped[:10]:
            print(f"   第{r}行: {why}")
        if len(skipped) > 10:
            print(f"   ...还有 {len(skipped)-10} 行")

    if not args.yes:
        ans = input("\n确认导入？(y/n) ").strip().lower()
        if ans != "y":
            print("已取消")
            return

    ok, skip = db.import_questions(items)
    print(f"\n✅ 导入完成：新增 {ok} 题，跳过 {skip} 题")
    print(f"   题库现有 {len(db.list_questions())} 题")


def cmd_list(args):
    qs = db.list_questions()
    if not qs:
        print("题库为空。用 `python3 import_excel.py template 模板.xlsx` 生成模板")
        return
    names = {"single": "单选", "multi": "多选", "judge": "判断", "blank": "填空"}
    print(f"共 {len(qs)} 题\n")
    for q in qs[:50]:
        opts = " | ".join(q["options"]) if q["options"] else "-"
        print(f"[{q['id']:>3}] {names[q['qtype']]} {q['score']}分 "
              f"[{q['category'] or '未分类'}] {q['stem'][:34]}")
        if opts != "-":
            print(f"       {opts[:70]}")
        print(f"       答案: {q['answer']}")
    if len(qs) > 50:
        print(f"... 还有 {len(qs)-50} 题")


def main():
    import argparse
    p = argparse.ArgumentParser(description="题库导入工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("template", help="生成 Excel 模板")
    a.add_argument("file", nargs="?", default="题库模板.xlsx")
    a.set_defaults(func=cmd_template)

    b = sub.add_parser("import", help="导入 Excel 题库")
    b.add_argument("file")
    b.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    b.set_defaults(func=cmd_import)

    c = sub.add_parser("list", help="查看题库")
    c.set_defaults(func=cmd_list)

    args = p.parse_args()
    db.init()
    args.func(args)


if __name__ == "__main__":
    main()
