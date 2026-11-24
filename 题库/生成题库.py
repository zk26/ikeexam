#!/usr/bin/env python3
"""
生成 WPS 三件套理论题库
--------------------------------
输出一份可直接导入后台的 Excel 题库，覆盖三大组件。

用法：
    python3 生成题库.py                 # 生成 题库_WPS三件套.xlsx
    python3 生成题库.py --out 我的题库.xlsx
"""
import argparse
import os

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# 与 server/import_excel.py 保持一致
HEADERS = ["题型", "题目", "选项A", "选项B", "选项C", "选项D", "答案", "分值", "标签"]
HEAD_DESC = ["单选/多选/判断/填空", "题干", "", "", "", "",
             "单选填A-D；多选填ABC；判断填正确/错误；填空多答案用|分隔",
             "默认2分", "分类标签"]

# ==================== 题库正文 ====================
# (题型, 题目, A, B, C, D, 答案, 分值, 标签)
QUESTIONS = [
    # ---------- 基础常识 ----------
    ("单选", "WPS 文字（WPS Writer）对应的进程名是？",
     "wps.exe", "et.exe", "wpp.exe", "wpspdf.exe", "A", 2, "基础"),
    ("单选", "WPS 表格（WPS Spreadsheets）对应的进程名是？",
     "wps.exe", "et.exe", "wpp.exe", "ksolaunch.exe", "B", 2, "基础"),
    ("单选", "WPS 演示（WPS Presentation）对应的进程名是？",
     "wps.exe", "et.exe", "wpp.exe", "wpspdf.exe", "C", 2, "基础"),
    ("多选", "WPS Office 三件套包含以下哪些组件？",
     "WPS文字", "WPS表格", "WPS演示", "WPS流程图", "ABC", 4, "基础"),
    ("判断", "WPS 文字保存的默认文档格式是 .docx。", "", "", "", "", "正确", 2, "基础"),
    ("判断", "WPS 表格保存的默认工作簿格式是 .xls。", "", "", "", "", "错误", 2, "基础"),
    ("填空", "WPS 演示文件的默认扩展名是____。", "", "", "", "", "pptx|.pptx", 2, "基础"),
    ("填空", "WPS 表格文件的默认扩展名是____。", "", "", "", "", "xlsx|.xlsx", 2, "基础"),

    # ---------- WPS 文字 ----------
    ("单选", "在 WPS 文字中，要设置段落首行缩进 2 个字符，最规范的做法是？",
     "敲两个空格", "设置首行缩进为 2 字符", "设置左缩进 2 字符",
     "插入制表符", "B", 2, "文字"),
    ("单选", "在 WPS 文字中，Ctrl+S 的作用是？",
     "保存文档", "另存为", "全选", "查找", "A", 2, "文字"),
    ("单选", "要给文档添加页眉，应该在哪个选项卡中操作？",
     "开始", "插入", "页面布局", "引用", "B", 2, "文字"),
    ("单选", "在 WPS 文字中，想让文字居中对齐，快捷键是？",
     "Ctrl+L", "Ctrl+R", "Ctrl+E", "Ctrl+J", "C", 2, "文字"),
    ("单选", "设置纸张大小为 A4，应该在哪个选项卡中操作？",
     "开始", "插入", "页面布局", "视图", "C", 2, "文字"),
    ("多选", "在 WPS 文字中，可以设置哪些段落格式？",
     "对齐方式", "行距", "首行缩进", "单元格填充色", "ABC", 4, "文字"),
    ("判断", "在 WPS 文字中，可以给文档设置不同的页眉页脚。",
     "", "", "", "", "正确", 2, "文字"),
    ("判断", "WPS 文字中的表格不能设置边框和底纹。", "", "", "", "", "错误", 2, "文字"),
    ("填空", "在 WPS 文字中，Ctrl+B 的作用是让文字变____。",
     "", "", "", "", "加粗|粗体", 2, "文字"),
    ("填空", "在 WPS 文字中，插入人工分页符的快捷键是 Ctrl+____。",
     "", "", "", "", "Enter|回车", 2, "文字"),

    # ---------- WPS 表格 ----------
    ("单选", "在 WPS 表格中，求平均值的函数是？",
     "SUM", "AVERAGE", "MAX", "COUNT", "B", 2, "表格"),
    ("单选", "在 WPS 表格中，求和函数是？",
     "SUM", "AVERAGE", "MIN", "IF", "A", 2, "表格"),
    ("单选", "在 WPS 表格中，条件判断函数是？",
     "SUM", "IF", "COUNT", "ROUND", "B", 2, "表格"),
    ("单选", "单元格地址 $A$1 表示什么引用？",
     "相对引用", "绝对引用", "混合引用", "三维引用", "B", 2, "表格"),
    ("单选", "在 WPS 表格中，要统计满足多个条件的单元格数量，用哪个函数？",
     "COUNT", "COUNTIF", "COUNTIFS", "SUMIF", "C", 2, "表格"),
    ("单选", "在 WPS 表格中，VLOOKUP 函数的作用是？",
     "纵向查找", "横向查找", "求和", "排序", "A", 2, "表格"),
    ("单选", "单元格显示 #### 通常是因为？",
     "公式错误", "列宽不够", "字体太大", "网络问题", "B", 2, "表格"),
    ("单选", "在 WPS 表格中，要对数据排序，应在哪个选项卡？",
     "开始", "数据", "公式", "视图", "B", 2, "表格"),
    ("多选", "以下哪些是 WPS 表格的常用函数？",
     "SUM", "AVERAGE", "VLOOKUP", "MERGE", "ABC", 4, "表格"),
    ("多选", "在 WPS 表格中可以创建哪些图表？",
     "柱状图", "折线图", "饼图", "组织结构图", "ABC", 4, "表格"),
    ("判断", "在 WPS 表格中，公式必须以等号 = 开头。", "", "", "", "", "正确", 2, "表格"),
    ("判断", "在 WPS 表格中，一个工作簿只能有一张工作表。",
     "", "", "", "", "错误", 2, "表格"),
    ("填空", "在 WPS 表格中，求最大值的函数是____。", "", "", "", "",
     "MAX|max", 2, "表格"),
    ("填空", "在 WPS 表格中，四舍五入函数是____。", "", "", "", "",
     "ROUND|round", 2, "表格"),
    ("填空", "第 3 行第 2 列的单元格地址是____。", "", "", "", "",
     "B3|b3", 2, "表格"),

    # ---------- WPS 演示 ----------
    ("单选", "在 WPS 演示中，F5 键的作用是？",
     "新建幻灯片", "从第 1 页开始放映", "从当前页放映", "保存", "B", 2, "演示"),
    ("单选", "在 WPS 演示中，从当前页开始放映的快捷键是？",
     "F5", "Shift+F5", "Ctrl+F5", "Alt+F5", "B", 2, "演示"),
    ("单选", "要给幻灯片设置统一的背景和字体，最高效的做法是？",
     "一页一页设置", "修改母版", "用格式刷", "复制粘贴", "B", 2, "演示"),
    ("单选", "在 WPS 演示中，想让对象按顺序出现，应设置？",
     "切换效果", "动画", "超链接", "母版", "B", 2, "演示"),
    ("单选", "幻灯片之间的翻页效果叫？",
     "动画", "切换", "母版", "排练", "B", 2, "演示"),
    ("多选", "在 WPS 演示中可以插入哪些对象？",
     "图片", "表格", "图表", "视频", "ABCD", 4, "演示"),
    ("判断", "在 WPS 演示中，可以为幻灯片设置自动播放时间。",
     "", "", "", "", "正确", 2, "演示"),
    ("判断", "WPS 演示的幻灯片只能按顺序播放，不能跳转。",
     "", "", "", "", "错误", 2, "演示"),
    ("填空", "在 WPS 演示中，新建幻灯片的快捷键是 Ctrl+____。",
     "", "", "", "", "M|m", 2, "演示"),

    # ---------- 操作规范 ----------
    ("单选", "机房上课时，学生作业应该保存在哪里最安全？",
     "C盘桌面", "D盘或指定目录", "C盘根目录", "系统文件夹", "B", 2, "规范"),
    ("单选", "发现电脑异常（如弹窗广告、运行缓慢），正确的做法是？",
     "自己重装系统", "举手报告老师", "忽略不管", "关机了事", "B", 2, "规范"),
    ("判断", "考试时可以自行上网搜索答案。", "", "", "", "", "错误", 2, "规范"),
    ("判断", "考试文件应按老师要求的格式命名，如 学号_姓名.docx。",
     "", "", "", "", "正确", 2, "规范"),
    ("填空", "离开机房前，应该先____再离开座位。", "", "", "", "",
     "关机|正常关机|关闭电脑", 2, "规范"),
]


def build(out_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "题库"

    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # 表头
    for i, h in enumerate(HEADERS, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF", size=11)
        c.fill = PatternFill("solid", fgColor="4472C4")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border

    # 说明行（导入时自动跳过）
    for i, d in enumerate(HEAD_DESC, 1):
        c = ws.cell(row=2, column=i, value=d)
        c.font = Font(size=9, italic=True, color="808080")
        c.fill = PatternFill("solid", fgColor="FFF2CC")
        c.border = border

    # 题目
    for r, q in enumerate(QUESTIONS, 3):
        for i, v in enumerate(q, 1):
            c = ws.cell(row=r, column=i, value=v)
            c.border = border
            c.alignment = Alignment(vertical="center", wrap_text=(i == 2))
        # 题型列标色，方便一眼区分
        t = q[0]
        color = {"单选": "E8F1FB", "多选": "FFF3E0",
                 "判断": "E8F5E9", "填空": "F3E5F5"}.get(t, "FFFFFF")
        ws.cell(row=r, column=1).fill = PatternFill("solid", fgColor=color)
        ws.cell(row=r, column=7).font = Font(bold=True, color="C00000")

    for col, w in zip("ABCDEFGHI", (8, 46, 20, 20, 20, 20, 16, 7, 10)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:I{len(QUESTIONS) + 2}"

    wb.save(out_path)
    return out_path


def main():
    p = argparse.ArgumentParser(description="生成 WPS 理论题库")
    p.add_argument("--out", default=None)
    a = p.parse_args()
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "题库_WPS三件套.xlsx")
    build(out)

    from collections import Counter
    c = Counter(q[0] for q in QUESTIONS)
    tag = Counter(q[8] for q in QUESTIONS)
    print(f"✅ 已生成: {out}")
    print(f"   共 {len(QUESTIONS)} 题")
    print(f"   题型: " + "  ".join(f"{k}{v}题" for k, v in c.items()))
    print(f"   分类: " + "  ".join(f"{k}{v}题" for k, v in tag.items()))
    print(f"   总分: {sum(q[7] for q in QUESTIONS)} 分")
    print("\n导入：后台 → 题库管理 → 导入 Excel")


if __name__ == "__main__":
    main()
