#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「实操题库」—— 老师自己挑本堂课考什么
------------------------------------------------
和之前不一样的地方：

  ❌ 不再自动生成考试（老师自己决定考什么）
  ✅ 生成一批 WPS 操作题，进「实操题库」，老师按分类挑
  ✅ 一个主题拆成【基础 / 进阶】两套，各自配不同进度的预处理素材
     （进阶的素材 = 基础已经做好的半成品，学生接着往后做）

产出：
  materials/              学生用的素材（每套一份，进度不同）
  题库/标准答案/          标准答案
  题库/实操题库.json      题库（服务器开机自动装载进「实操题库」）

每套都实测：
  · 标准答案 → 必须满分
  · 原始/半成品素材 → 必须 ≤20 分（不做不得分）

用法：
  python 生成实操题库.py
  python 生成实操题库.py --check
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, ROOT)

MAT_DIR = os.path.join(ROOT, "materials")
ANS_DIR = os.path.join(ROOT, "标准答案") if os.path.isdir(
    os.path.join(ROOT, "标准答案")) else os.path.join(ROOT, "题库", "标准答案")
OUT_JSON = os.path.join(ROOT, "题库", "实操题库.json")
for d in (MAT_DIR, ANS_DIR, os.path.dirname(OUT_JSON)):
    os.makedirs(d, exist_ok=True)

from openpyxl import Workbook, load_workbook                 # noqa: E402
from openpyxl.styles import Font, Alignment, Border, Side    # noqa: E402
from openpyxl.chart import BarChart, Reference               # noqa: E402
from docx import Document                                    # noqa: E402
from docx.shared import Pt, Cm                               # noqa: E402
from docx.oxml.ns import qn                                  # noqa: E402
from pptx import Presentation                                # noqa: E402
from pptx.util import Pt as PPt                              # noqa: E402
from pptx.dml.color import RGBColor as PRGB                  # noqa: E402
from pptx.enum.text import PP_ALIGN                          # noqa: E402

import exam as exm                                           # noqa: E402

THIN = Side(style="thin")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


# ---------------- 小工具：造一张纯色 PNG（不依赖 PIL） ----------------
def make_png(dst, w=320, h=200, rgb=(31, 78, 121)):
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body)))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    with open(dst, "wb") as f:
        f.write(png)


def _set_font(run, name, size=None, bold=None):
    run.font.name = name
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    rPr = run._element.get_or_add_rPr()
    rf = rPr.find(qn("w:rFonts"))
    if rf is None:
        rf = rPr.makeelement(qn("w:rFonts"), {})
        rPr.insert(0, rf)
    rf.set(qn("w:eastAsia"), name)


# ==================================================================
#  Excel ①：期末成绩统计（基础）
# ==================================================================
SCORES = [
    ("2024001", "张明", 88, 92, 79), ("2024002", "李静", 95, 87, 90),
    ("2024003", "王强", 76, 81, 85), ("2024004", "赵敏", 90, 94, 88),
    ("2024005", "陈杰", 82, 78, 91),
]


def x_score_raw(dst):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "期末成绩表"
    for i, h in enumerate(["学号", "姓名", "语文", "数学", "英语"], 1):
        ws.cell(row=2, column=i, value=h)
    for r, row in enumerate(SCORES, 3):
        for c, v in enumerate(row, 1):
            ws.cell(row=r, column=c, value=v)
    for col in "ABCDE":
        ws.column_dimensions[col].width = 12
    wb.save(dst)


def x_score_basic(src, dst):
    """基础：改名 + 总分 + 平均分 + 表头格式 + 边框 + 合并标题"""
    wb = load_workbook(src)
    ws = wb.active
    ws.title = "成绩表"
    ws.merge_cells("A1:G1")
    ws["A1"].font = Font(name="黑体", size=16, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws["F2"] = "总分"
    ws["G2"] = "平均分"
    for col in "ABCDEFG":
        c = ws[f"{col}2"]
        c.font = Font(name="黑体", size=14, bold=True)
        c.alignment = Alignment(horizontal="center")
    for r in range(3, 8):
        ws[f"F{r}"] = f"=SUM(C{r}:E{r})"
        ws[f"G{r}"] = f"=AVERAGE(C{r}:E{r})"
        ws[f"G{r}"].number_format = "0.0"
    for row in ws["A2:G7"]:
        for c in row:
            c.border = BORDER
    wb.save(dst)


def x_score_adv(src, dst):
    """进阶（素材已是基础做好的）：评级 IF + 排名 RANK + 图表"""
    wb = load_workbook(src)
    ws = wb.active
    ws["H2"] = "评级"
    ws["I2"] = "排名"
    for col in "HI":
        c = ws[f"{col}2"]
        c.font = Font(name="黑体", size=14, bold=True)
        c.alignment = Alignment(horizontal="center")
    for r in range(3, 8):
        ws[f"H{r}"] = f'=IF(F{r}>=400,"优秀","合格")'
        ws[f"I{r}"] = f"=RANK(F{r},$F$3:$F$7)"
    for row in ws["A2:I7"]:
        for c in row:
            c.border = BORDER
    ch = BarChart()
    ch.title = "总分对比"
    ch.add_data(Reference(ws, min_col=6, min_row=2, max_row=7),
                titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=2, min_row=3, max_row=7))
    ws.add_chart(ch, "K2")
    wb.save(dst)


# ==================================================================
#  Excel ②：图书销售统计（基础 / 进阶）
# ==================================================================
SALES = [
    ("1月", 120, 85, 210), ("2月", 95, 78, 165), ("3月", 143, 92, 188),
    ("4月", 168, 110, 240), ("5月", 205, 135, 260), ("6月", 188, 120, 232),
]


def x_sale_raw(dst):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for i, h in enumerate(["月份", "文学类", "科技类", "教辅类"], 1):
        ws.cell(row=1, column=i, value=h)
    for r, row in enumerate(SALES, 2):
        for c, v in enumerate(row, 1):
            ws.cell(row=r, column=c, value=v)
    for col in "ABCD":
        ws.column_dimensions[col].width = 12
    wb.save(dst)


def x_sale_basic(src, dst):
    wb = load_workbook(src)
    ws = wb.active
    ws.title = "销售统计"
    ws["E1"] = "合计"
    for col in "ABCDE":
        c = ws[f"{col}1"]
        c.font = Font(name="黑体", size=12, bold=True)
        c.alignment = Alignment(horizontal="center")
    for r in range(2, 8):
        ws[f"E{r}"] = f"=SUM(B{r}:D{r})"
    ws["A8"] = "合计"
    for col in "BCDE":
        ws[f"{col}8"] = f"=SUM({col}2:{col}7)"
    ws["A8"].font = Font(name="黑体", size=12, bold=True)
    for row in ws["A1:E8"]:
        for c in row:
            c.border = BORDER
    ch = BarChart()
    ch.title = "上半年图书销售统计"
    ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=1, max_row=7),
                titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=7))
    ws.add_chart(ch, "G2")
    wb.save(dst)


def x_sale_adv(src, dst):
    """进阶（素材已含合计与图表）：达标判断 IF + 最高月 MAX"""
    wb = load_workbook(src)
    ws = wb.active
    ws["F1"] = "是否达标"
    c = ws["F1"]
    c.font = Font(name="黑体", size=12, bold=True)
    c.alignment = Alignment(horizontal="center")
    for r in range(2, 8):
        ws[f"F{r}"] = f'=IF(E{r}>=500,"达标","未达标")'
    ws["A9"] = "最高月销量"
    ws["B9"] = "=MAX(B2:B7)"
    ws["A9"].font = Font(name="黑体", size=12, bold=True)
    for row in ws["A1:F8"]:
        for c2 in row:
            c2.border = BORDER
    wb.save(dst)


# ==================================================================
#  Word：通知（基础 / 进阶）
# ==================================================================
BODY = [
    "为丰富校园文化生活，培养同学们的创新精神和实践能力，"
    "学校决定举办第十八届校园科技节。现将有关事项通知如下：",
    "一、活动时间：2025年5月20日至5月30日。",
    "二、参加对象：全校初高中学生，以班级为单位组队参加。",
    "三、活动内容：科技创新发明、纸桥承重、编程挑战、科普演讲等项目。",
    "请各班班长于5月15日前将报名表交到教务处。",
]


def w_notice_raw(dst):
    doc = Document()
    doc.add_paragraph("关于举办第十八届校园科技节的通知")
    for b in BODY:
        doc.add_paragraph(b)
    doc.add_paragraph("雅安中学教务处")
    doc.add_paragraph("2025年5月8日")
    doc.save(dst)


def w_notice_basic(src, dst):
    doc = Document(src)
    ps = doc.paragraphs
    ps[0].alignment = 1
    for r in ps[0].runs:
        _set_font(r, "黑体", 22, True)
    for p in ps[1:3]:
        p.paragraph_format.first_line_indent = Pt(24)
        p.paragraph_format.line_spacing = 1.5
        for r in p.runs:
            _set_font(r, "宋体", 12)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(3.17)
    sec.top_margin = sec.bottom_margin = Cm(2.54)
    sec.footer.paragraphs[0].text = "雅安中学教务处 制"
    doc.save(dst)


def w_notice_adv(src, dst):
    """进阶（素材已排版好）：加页眉 + 表格 + 图片"""
    doc = Document(src)
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = "雅安中学教务处"
    t = doc.add_table(rows=3, cols=4)
    t.style = "Table Grid"
    doc.add_paragraph("")
    doc.add_picture(os.path.join(MAT_DIR, "素材图片_校徽.png"),
                    width=Cm(4))
    doc.save(dst)


# ==================================================================
#  Word：课程表制作（基础）
# ==================================================================
def w_table_raw(dst):
    doc = Document()
    doc.add_paragraph("高一（3）班课程表")
    doc.save(dst)


def w_table_basic(src, dst):
    doc = Document(src)
    p = doc.paragraphs[0]
    p.alignment = 1
    for r in p.runs:
        _set_font(r, "黑体", 22, True)
    t = doc.add_table(rows=7, cols=6)
    t.style = "Table Grid"
    head = ["节次", "星期一", "星期二", "星期三", "星期四", "星期五"]
    for i, h in enumerate(head):
        t.cell(0, i).text = h
    doc.save(dst)


# ==================================================================
#  PPT：科技节宣传（基础 / 进阶）
# ==================================================================
def p_intro_raw(dst):
    prs = Presentation()
    s0 = prs.slides.add_slide(prs.slide_layouts[0])
    s0.shapes.title.text = "校园科技节"
    s0.placeholders[1].text = "请补充内容"
    for t, c in (("活动内容", "科技创新发明、纸桥承重"),
                 ("时间安排", "5月20日—5月30日")):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = t
        s.placeholders[1].text = c
    prs.save(dst)


def p_intro_basic(src, dst):
    prs = Presentation(src)
    s0 = prs.slides[0]
    tf = s0.shapes.title.text_frame
    tf.text = "雅安中学科技节"
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.CENTER
        for r in p.runs:
            r.font.name = "微软雅黑"
            r.font.size = PPt(40)
            r.font.bold = True
    bg = s0.background.fill
    bg.solid()
    bg.fore_color.rgb = PRGB(0x1F, 0x4E, 0x79)
    for t, c in (("参赛对象", "全校初高中学生，社团活动小组均可报名"),
                 ("奖项设置", "一等奖2名、二等奖5名、三等奖10名")):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = t
        s.placeholders[1].text = c
    prs.save(dst)


def p_intro_adv(src, dst):
    """进阶（素材已有 5 页/标题/背景）：加表格页 + 图片页 + 动画"""
    prs = Presentation(src)
    # 表格页
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "项目安排"
    tb = s.shapes.add_table(rows=3, cols=3, left=PPt(60), top=PPt(140),
                            width=PPt(600), height=PPt(160)).table
    tb.cell(0, 0).text = "项目"
    tb.cell(0, 1).text = "时间"
    tb.cell(0, 2).text = "地点"
    # 图片页
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "科技节海报"
    s2.shapes.add_picture(os.path.join(MAT_DIR, "素材图片_校徽.png"),
                          left=PPt(120), top=PPt(140), width=PPt(400))
    prs.save(dst)


# ==================================================================
#  Excel ③：校园歌手大赛评分表（基础）
# ==================================================================
SINGERS = [
    ("2024001", "李佳", 88, 92, 79), ("2024002", "王浩", 95, 87, 90),
    ("2024003", "陈晨", 76, 81, 85), ("2024004", "刘洋", 90, 94, 88),
    ("2024005", "赵敏", 82, 78, 91), ("2024006", "周涛", 91, 89, 86),
]


def x_singer_raw(dst):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "校园歌手大赛评分表"
    for i, h in enumerate(["序号", "姓名", "评委1", "评委2", "评委3"], 1):
        ws.cell(row=2, column=i, value=h)
    for r, row in enumerate(SINGERS, 3):
        for c, v in enumerate(row, 1):
            ws.cell(row=r, column=c, value=v)
    for col in "ABCDE":
        ws.column_dimensions[col].width = 12
    wb.save(dst)


def x_singer_basic(src, dst):
    """基础：改名 + 总分/平均分/排名 + 表头格式 + 边框 + 合并标题"""
    wb = load_workbook(src)
    ws = wb.active
    ws.title = "评分表"
    ws.merge_cells("A1:H1")
    ws["A1"].font = Font(name="黑体", size=16, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws["F2"] = "总分"
    ws["G2"] = "平均分"
    ws["H2"] = "排名"
    for col in "ABCDEFGH":
        c = ws[f"{col}2"]
        c.font = Font(name="黑体", size=14, bold=True)
        c.alignment = Alignment(horizontal="center")
    for r in range(3, 9):
        ws[f"F{r}"] = f"=SUM(C{r}:E{r})"
        ws[f"G{r}"] = f"=AVERAGE(C{r}:E{r})"
        ws[f"G{r}"].number_format = "0.0"
        ws[f"H{r}"] = f"=RANK(F{r},$F$3:$F$8)"
    for row in ws["A2:H8"]:
        for c in row:
            c.border = BORDER
    wb.save(dst)


# ==================================================================
#  Word：家长会邀请函（基础）
# ==================================================================
INVITE = [
    "尊敬的家长：",
    "您好！为了加强家校沟通，共同关注孩子的成长，"
    "我班定于2025年5月16日（周五）下午3:00在本班教室召开家长会。",
    "会议内容：通报本学期期中考试情况、介绍下一阶段教学安排、"
    "交流家庭教育经验。",
    "请您提前安排好时间，准时参加。如确有困难，请提前与班主任联系。",
]


def w_invite_raw(dst):
    doc = Document()
    doc.add_paragraph("家长会邀请函")
    for b in INVITE:
        doc.add_paragraph(b)
    doc.add_paragraph("高二（1）班班主任")
    doc.add_paragraph("2025年5月9日")
    doc.save(dst)


def w_invite_basic(src, dst):
    """基础：标题格式 + 正文缩进行距 + 页面设置 + 页脚 + 文末日程表"""
    doc = Document(src)
    ps = doc.paragraphs
    ps[0].alignment = 1
    for r in ps[0].runs:
        _set_font(r, "黑体", 22, True)
    for p in ps[1:5]:
        p.paragraph_format.first_line_indent = Pt(24)
        p.paragraph_format.line_spacing = 1.5
        for r in p.runs:
            _set_font(r, "宋体", 12)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(3.17)
    sec.top_margin = sec.bottom_margin = Cm(2.54)
    sec.footer.paragraphs[0].text = "雅安中学 高二年级组"
    t = doc.add_table(rows=4, cols=3)
    t.style = "Table Grid"
    for i, h in enumerate(["时间", "内容", "地点"]):
        t.cell(0, i).text = h
    doc.save(dst)


# ==================================================================
#  Word：春季研学安排（基础）
# ==================================================================
TRIP = [
    ("7:30", "校门口集合乘车", "张老师"),
    ("9:00", "参观科技馆", "李老师"),
    ("12:00", "午餐、休息", "王老师"),
    ("14:00", "集合返程", "张老师"),
]


def w_trip_raw(dst):
    """素材：标题 + 说明 + 一张只填了「时间」列的空表"""
    doc = Document()
    doc.add_paragraph("春季研学活动安排")
    doc.add_paragraph("请各班按时间集合，全程听从带队老师安排，注意安全。")
    t = doc.add_table(rows=5, cols=3)
    t.style = "Table Grid"
    t.cell(0, 0).text = "时间"
    for i, (tm, _what, _who) in enumerate(TRIP, 1):
        t.cell(i, 0).text = tm
    doc.save(dst)


def w_trip_basic(src, dst):
    """基础：标题格式 + 补全表格 + 页眉 + 页面设置"""
    doc = Document(src)
    ps = doc.paragraphs
    ps[0].alignment = 1
    for r in ps[0].runs:
        _set_font(r, "黑体", 22, True)
    t = doc.tables[0]
    for i, h in enumerate(["时间", "内容", "负责人"]):
        t.cell(0, i).text = h
    for i, (_tm, what, who) in enumerate(TRIP, 1):
        t.cell(i, 1).text = what
        t.cell(i, 2).text = who
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.header.paragraphs[0].text = "雅安中学 教务处"
    doc.save(dst)


# ==================================================================
#  PPT：校园运动会宣传（基础）
# ==================================================================
def p_sport_raw(dst):
    prs = Presentation()
    s0 = prs.slides.add_slide(prs.slide_layouts[0])
    s0.shapes.title.text = "校园运动会"
    s0.placeholders[1].text = "请补充内容"
    for t, c in (("比赛项目", "100米、跳远、拔河"),
                 ("时间安排", "10月18日—10月19日")):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = t
        s.placeholders[1].text = c
    prs.save(dst)


def p_sport_basic(src, dst):
    """基础：改标题 + 字体居中 + 背景 + 新增 2 页 + 项目表格页"""
    prs = Presentation(src)
    s0 = prs.slides[0]
    tf = s0.shapes.title.text_frame
    tf.text = "第18届校园运动会"
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.CENTER
        for r in p.runs:
            r.font.name = "微软雅黑"
            r.font.size = PPt(40)
            r.font.bold = True
    bg = s0.background.fill
    bg.solid()
    bg.fore_color.rgb = PRGB(0x1F, 0x4E, 0x79)
    for t, c in (("奖项设置", "各项目设一、二、三等奖"),
                 ("温馨提示", "运动员请提前热身，注意安全")):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = t
        s.placeholders[1].text = c
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "项目时间表"
    tb = s.shapes.add_table(rows=3, cols=3, left=PPt(60), top=PPt(140),
                            width=PPt(600), height=PPt(160)).table
    for i, h in enumerate(["项目", "时间", "地点"]):
        tb.cell(0, i).text = h
    prs.save(dst)


# ==================================================================
#  一级（入门）：只考「会打开、会填、会保存」——比二级简单一截
# ==================================================================
CONTACTS = [
    ("张小明", "13800000001", "打篮球"),
    ("李小红", "13800000002", "画画"),
    ("王大力", "13800000003", "踢足球"),
    ("赵小美", "13800000004", "唱歌"),
    ("陈小龙", "13800000005", "下棋"),
]

WEEK = ["节次", "星期一", "星期二", "星期三", "星期四", "星期五"]

SELF_INTRO = ("大家好，我叫小明，今年十岁。",
              "我最喜欢的科目是语文，因为可以读到很多有趣的故事。",
              "以后我想当一名老师，把知识教给更多同学。")

POEM = ("春晓", "春眠不觉晓，处处闻啼鸟。", "夜来风雨声，花落知多少。")


def x_contact_raw(dst):
    """素材：数据都填好了，就是一点格式都没做"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "班级通讯录"
    for i, h in enumerate(["姓名", "电话", "爱好"], 1):
        ws.cell(row=2, column=i, value=h)
    for r, row in enumerate(CONTACTS, 3):
        for c, v in enumerate(row, 1):
            ws.cell(row=r, column=c, value=v)
    for col in "ABC":
        ws.column_dimensions[col].width = 14
    wb.save(dst)


def x_contact_basic(src, dst):
    """入门：工作表改名 + 标题合并居中 + 表头加粗居中 + 加边框"""
    wb = load_workbook(src)
    ws = wb.active
    ws.title = "通讯录"
    ws.merge_cells("A1:C1")
    ws["A1"].font = Font(name="黑体", size=14, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")
    for col in "ABC":
        c = ws[f"{col}2"]
        c.font = Font(name="黑体", size=12, bold=True)
        c.alignment = Alignment(horizontal="center")
    for row in ws["A1:C7"]:
        for c in row:
            c.border = BORDER
    wb.save(dst)


def x_timetable_raw(dst):
    """素材：只有标题和「第几节」，表头是空的"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "课程表"
    for i in range(1, 6):
        ws.cell(row=i + 2, column=1, value=f"第{i}节")
    for col in "ABCDEF":
        ws.column_dimensions[col].width = 12
    wb.save(dst)


def x_timetable_basic(src, dst):
    """入门：填表头 + 第一节课写语文 + 标题合并居中 + 加边框"""
    wb = load_workbook(src)
    ws = wb.active
    ws.title = "课程表"
    ws.merge_cells("A1:F1")
    ws["A1"].font = Font(name="黑体", size=14, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")
    for i, h in enumerate(WEEK, 1):
        c = ws.cell(row=2, column=i, value=h)
        c.font = Font(name="黑体", size=12, bold=True)
        c.alignment = Alignment(horizontal="center")
    ws["B3"] = "语文"
    for row in ws["A1:F7"]:
        for c in row:
            c.border = BORDER
    wb.save(dst)


def w_intro_raw(dst):
    doc = Document()
    doc.add_paragraph("我的自我介绍")
    for line in SELF_INTRO:
        doc.add_paragraph(line)
    doc.save(dst)


def w_intro_basic(src, dst):
    """入门：标题黑体二号加粗居中 + 正文宋体小四 + A4 纸"""
    doc = Document(src)
    ps = doc.paragraphs
    ps[0].alignment = 1
    for r in ps[0].runs:
        _set_font(r, "黑体", 22, True)
    for p in ps[1:]:
        for r in p.runs:
            _set_font(r, "宋体", 12)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    doc.save(dst)


def w_poem_raw(dst):
    doc = Document()
    for line in POEM:
        doc.add_paragraph(line)
    doc.save(dst)


def w_poem_basic(src, dst):
    """入门：标题黑体三号加粗居中 + 诗句居中宋体 + 页脚写班级"""
    doc = Document(src)
    ps = doc.paragraphs
    ps[0].alignment = 1
    for r in ps[0].runs:
        _set_font(r, "黑体", 16, True)
    for p in ps[1:]:
        p.alignment = 1
        for r in p.runs:
            _set_font(r, "宋体", 12)
    doc.sections[0].footer.paragraphs[0].text = "五年级（1）班"
    doc.save(dst)


def p_class_raw(dst):
    prs = Presentation()
    s0 = prs.slides.add_slide(prs.slide_layouts[0])
    s0.shapes.title.text = "我的班级"
    s0.placeholders[1].text = "请补充班级信息"
    prs.save(dst)


def p_class_basic(src, dst):
    """入门：改标题 + 字体加粗居中 + 再加 3 页（共 4 页）"""
    prs = Presentation(src)
    s0 = prs.slides[0]
    tf = s0.shapes.title.text_frame
    tf.text = "五年级（1）班"
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.CENTER
        for r in p.runs:
            r.font.name = "微软雅黑"
            r.font.size = PPt(36)
            r.font.bold = True
    for t in ("班级口号", "班级荣誉", "我的目标"):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = t
    prs.save(dst)


# ==================================================================
#  题库条目
# ==================================================================
ITEMS = [
    {
        "name": "期末成绩统计（基础）", "etype": "excel",
        "category": "二级·表格", "level": "基础",
        "material": "成绩表_基础_素材.xlsx", "answer": "成绩表_基础_答案.xlsx",
        "save_as": "座位号_姓名.xlsx",
        "make_mat": x_score_raw, "make_ans": x_score_basic,
        "requirement": """【WPS 表格】期末成绩统计（基础）

1. 下载素材并用 WPS 表格打开
2. 工作表名改为「成绩表」
3. F2、G2 分别输入「总分」「平均分」
4. 用 SUM 求每人总分（F3:F7）
5. 用 AVERAGE 求每人平均分（G3:G7），保留 1 位小数
6. 表头 A2:G2：黑体、14 号、加粗、水平居中
7. A1:G1 合并后居中
8. A2:G7 加外边框
9. 保存为 座位号_姓名.xlsx 并提交""",
        "checks": [
            {"id": "工作表改名", "type": "sheet_name",
             "expect": {"name": "成绩表"}, "score": 10},
            {"id": "总分公式", "type": "formula", "cell": "F3",
             "expect": {"function": "SUM"}, "score": 15},
            {"id": "平均分公式", "type": "formula", "cell": "G3",
             "expect": {"function": "AVERAGE"}, "score": 15},
            {"id": "表头字体", "type": "font", "range": "A2:G2",
             "expect": {"name": "黑体", "size": 14, "bold": True}, "score": 15},
            {"id": "表格边框", "type": "border", "range": "A2:G7", "score": 15},
            {"id": "标题合并", "type": "merged", "range": "A1:G1", "score": 10},
            {"id": "表头居中", "type": "alignment", "range": "A2:G2",
             "expect": {"horizontal": "居中"}, "score": 10},
            {"id": "平均分1位小数", "type": "number_format", "range": "G3:G7",
             "expect": {"format": "0.0"}, "score": 10},
        ],
    },
    {
        "name": "期末成绩统计（进阶）", "etype": "excel",
        "category": "二级·表格", "level": "进阶",
        "material": "成绩表_进阶_素材.xlsx", "answer": "成绩表_进阶_答案.xlsx",
        "save_as": "座位号_姓名.xlsx",
        # 素材 = 基础做完的半成品，学生接着做
        "make_mat": lambda dst: x_score_basic(
            os.path.join(MAT_DIR, "成绩表_基础_素材.xlsx"), dst),
        "make_ans": x_score_adv,
        "requirement": """【WPS 表格】期末成绩统计（进阶）

素材里基础部分已做好（改名、总分、平均分、格式），请继续：

1. H2、I2 分别输入「评级」「排名」
2. 用 IF 判断评级（H3:H7）：总分≥400 为「优秀」，否则「合格」
3. 用 RANK 求排名（I3:I7）：按 F3:F7 降序
4. H2:I2 表头：黑体、14 号、加粗、水平居中
5. 给 A2:I7 全部加上外边框
6. 插入一张柱形图（数据用 F 列总分）
7. 保存为 座位号_姓名.xlsx 并提交""",
        "checks": [
            {"id": "评级IF", "type": "formula", "cell": "H3",
             "expect": {"function": "IF"}, "score": 25},
            {"id": "排名RANK", "type": "formula", "cell": "I3",
             "expect": {"function": "RANK"}, "score": 25},
            {"id": "新表头字体", "type": "font", "range": "H2:I2",
             "expect": {"name": "黑体", "size": 14, "bold": True}, "score": 15},
            {"id": "全表边框", "type": "border", "range": "A2:I7", "score": 20},
            {"id": "图表", "type": "chart", "expect": {"count": 1}, "score": 15},
        ],
    },
    {
        "name": "图书销售统计（基础·含图表）", "etype": "excel",
        "category": "二级·表格", "level": "基础",
        "material": "销售数据_基础_素材.xlsx", "answer": "销售数据_基础_答案.xlsx",
        "save_as": "座位号_姓名.xlsx",
        "make_mat": x_sale_raw, "make_ans": x_sale_basic,
        "requirement": """【WPS 表格】图书销售统计（基础）

1. 下载素材并打开
2. 工作表名改为「销售统计」
3. E1 输入「合计」，用 SUM 求每月合计（E2:E7）
4. 第 8 行总计：A8 输入「合计」，B8:E8 用 SUM 求各列总和
5. 表头 A1:E1：黑体、12 号、加粗、水平居中
6. A1:E8 加外边框
7. 插入柱形图（数据 B1:D7，月份为横轴）
8. 保存为 座位号_姓名.xlsx 并提交""",
        "checks": [
            {"id": "工作表改名", "type": "sheet_name",
             "expect": {"name": "销售统计"}, "score": 10},
            {"id": "每月合计", "type": "formula", "cell": "E2",
             "expect": {"function": "SUM"}, "score": 15},
            {"id": "总计行", "type": "formula", "cell": "B8",
             "expect": {"function": "SUM"}, "score": 15},
            {"id": "图表", "type": "chart", "expect": {"count": 1}, "score": 20},
            {"id": "表头字体", "type": "font", "range": "A1:E1",
             "expect": {"name": "黑体", "size": 12, "bold": True}, "score": 15},
            {"id": "表格边框", "type": "border", "range": "A1:E8", "score": 15},
            {"id": "表头居中", "type": "alignment", "range": "A1:E1",
             "expect": {"horizontal": "居中"}, "score": 10},
        ],
    },
    {
        "name": "图书销售统计（进阶）", "etype": "excel",
        "category": "二级·表格", "level": "进阶",
        "material": "销售数据_进阶_素材.xlsx", "answer": "销售数据_进阶_答案.xlsx",
        "save_as": "座位号_姓名.xlsx",
        "make_mat": lambda dst: x_sale_basic(
            os.path.join(MAT_DIR, "销售数据_基础_素材.xlsx"), dst),
        "make_ans": x_sale_adv,
        "requirement": """【WPS 表格】图书销售统计（进阶）

素材里合计和图表已做好，请继续：

1. F1 输入「是否达标」
2. 用 IF 判断（F2:F7）：合计≥500 为「达标」，否则「未达标」
3. A9 输入「最高月销量」，B9 用 MAX 求 B2:B7 的最大值
4. F1 表头：黑体、12 号、加粗、水平居中
5. 给 A1:F8 全部加外边框
6. 保存为 座位号_姓名.xlsx 并提交""",
        "checks": [
            {"id": "达标IF", "type": "formula", "cell": "F2",
             "expect": {"function": "IF"}, "score": 30},
            {"id": "最大值MAX", "type": "formula", "cell": "B9",
             "expect": {"function": "MAX"}, "score": 30},
            {"id": "表头字体", "type": "font", "range": "F1",
             "expect": {"name": "黑体", "size": 12, "bold": True}, "score": 15},
            {"id": "全表边框", "type": "border", "range": "A1:F8", "score": 15},
            {"id": "表头居中", "type": "alignment", "range": "F1",
             "expect": {"horizontal": "居中"}, "score": 10},
        ],
    },
    {
        "name": "通知排版（基础）", "etype": "word",
        "category": "二级·文字", "level": "基础",
        "material": "通知_基础_素材.docx", "answer": "通知_基础_答案.docx",
        "save_as": "座位号_姓名.docx",
        "make_mat": w_notice_raw, "make_ans": w_notice_basic,
        "requirement": """【WPS 文字】通知排版（基础）

1. 下载素材并打开
2. 标题：黑体、二号、加粗、居中
3. 正文前两段：宋体、小四（12 磅）
4. 正文首行缩进 2 字符（24 磅），行距 1.5 倍
5. 页面：A4（21×29.7 厘米），左右页边距 3.17 厘米
6. 页脚插入「雅安中学教务处 制」
7. 保存为 座位号_姓名.docx 并提交

⚠️ 只改格式，不要删改文字""",
        "checks": [
            {"id": "标题字体", "type": "font", "contains": "校园科技节的通知",
             "expect": {"name": "黑体", "size": "二号", "bold": True}, "score": 20},
            {"id": "标题居中", "type": "paragraph", "contains": "校园科技节的通知",
             "expect": {"align": "居中"}, "score": 10},
            {"id": "正文缩进行距", "type": "paragraph", "contains": "丰富校园文化生活",
             "expect": {"first_line_indent": 24, "line_spacing": 1.5}, "score": 20},
            {"id": "正文字体", "type": "font", "contains": "丰富校园文化生活",
             "expect": {"name": "宋体", "size": 12}, "score": 15},
            {"id": "页面设置", "type": "page_setup",
             "expect": {"width": 21.0, "height": 29.7,
                        "margin_left": 3.17, "margin_right": 3.17}, "score": 20},
            {"id": "页脚", "type": "header_footer", "where": "footer",
             "contains": "教务处", "score": 15},
        ],
    },
    {
        "name": "通知排版（进阶）", "etype": "word",
        "category": "二级·文字", "level": "进阶",
        "material": "通知_进阶_素材.docx", "answer": "通知_进阶_答案.docx",
        "save_as": "座位号_姓名.docx",
        "extra_materials": ["素材图片_校徽.png"],
        "make_mat": lambda dst: w_notice_basic(
            os.path.join(MAT_DIR, "通知_基础_素材.docx"), dst),
        "make_ans": w_notice_adv,
        "requirement": """【WPS 文字】通知排版（进阶）

素材已排好版，请继续：

1. 页眉插入「雅安中学教务处」
2. 在文档末尾插入一个 3 行 4 列的表格
3. 在表格下方插入一张图片（用素材里的 素材图片_校徽.png）
4. 保存为 座位号_姓名.docx 并提交""",
        "checks": [
            {"id": "页眉", "type": "header_footer", "where": "header",
             "contains": "雅安中学教务处", "score": 35},
            {"id": "表格3行4列", "type": "table",
             "expect": {"count": 1, "rows": 3, "cols": 4}, "score": 35},
            {"id": "插入图片", "type": "image", "expect": {"count": 1},
             "score": 30},
        ],
    },
    {
        "name": "课程表制作（基础）", "etype": "word",
        "category": "二级·文字", "level": "基础",
        "material": "课程表_基础_素材.docx", "answer": "课程表_基础_答案.docx",
        "save_as": "座位号_姓名.docx",
        "make_mat": w_table_raw, "make_ans": w_table_basic,
        "requirement": """【WPS 文字】课程表制作（基础）

1. 下载素材并打开
2. 标题「高一（3）班课程表」：黑体、二号、居中
3. 标题下方插入一个 7 行 6 列的表格
4. 表格第一行依次填入：节次、星期一、星期二、星期三、星期四、星期五
5. 保存为 座位号_姓名.docx 并提交""",
        "checks": [
            {"id": "标题字体", "type": "font", "contains": "课程表",
             "expect": {"name": "黑体", "size": "二号"}, "score": 25},
            {"id": "标题居中", "type": "paragraph", "contains": "课程表",
             "expect": {"align": "居中"}, "score": 15},
            {"id": "表格7行6列", "type": "table",
             "expect": {"count": 1, "rows": 7, "cols": 6}, "score": 40},
            {"id": "表头第一节次", "type": "table",
             "expect": {"cell": [0, 0, "节次"]}, "score": 20},
        ],
    },
    {
        "name": "科技节宣传（基础）", "etype": "ppt",
        "category": "二级·演示", "level": "基础",
        "material": "科技节_基础_素材.pptx", "answer": "科技节_基础_答案.pptx",
        "save_as": "座位号_姓名.pptx",
        "make_mat": p_intro_raw, "make_ans": p_intro_basic,
        "requirement": """【WPS 演示】科技节宣传（基础）

1. 下载素材并打开
2. 第 1 页标题改为「雅安中学科技节」
3. 第 1 页标题：微软雅黑、40 磅、加粗、居中
4. 第 1 页背景设为纯色：深蓝（RGB 1F4E79）
5. 新增两页，标题分别为「参赛对象」「奖项设置」
6. 共 5 页
7. 保存为 座位号_姓名.pptx 并提交""",
        "checks": [
            {"id": "共5页", "type": "slide_count", "expect": {"count": 5},
             "score": 20},
            {"id": "首页标题", "type": "title", "index": 0,
             "expect": {"text": "雅安中学科技节"}, "score": 15},
            {"id": "标题字体", "type": "font", "contains": "雅安中学科技节",
             "expect": {"name": "微软雅黑", "size": 40, "bold": True}, "score": 20},
            {"id": "标题居中", "type": "align", "contains": "雅安中学科技节",
             "expect": {"align": "居中"}, "score": 10},
            {"id": "背景色", "type": "background", "index": 0,
             "expect": {"color": "1F4E79"}, "score": 20},
            {"id": "新增页内容", "type": "text",
             "contains": ["参赛对象", "奖项设置"], "score": 15},
        ],
    },
    {
        "name": "科技节宣传（进阶）", "etype": "ppt",
        "category": "二级·演示", "level": "进阶",
        "material": "科技节_进阶_素材.pptx", "answer": "科技节_进阶_答案.pptx",
        "save_as": "座位号_姓名.pptx",
        "extra_materials": ["素材图片_校徽.png"],
        "make_mat": lambda dst: p_intro_basic(
            os.path.join(MAT_DIR, "科技节_基础_素材.pptx"), dst),
        "make_ans": p_intro_adv,
        "requirement": """【WPS 演示】科技节宣传（进阶）

素材已有 5 页（含标题、背景），请继续：

1. 新增一页，标题「项目安排」，插入 3 行 3 列的表格
2. 再新增一页，标题「科技节海报」，插入素材图片 素材图片_校徽.png
3. 共 7 页
4. 保存为 座位号_姓名.pptx 并提交""",
        "checks": [
            {"id": "共7页", "type": "slide_count", "expect": {"count": 7},
             "score": 25},
            {"id": "表格页标题", "type": "title", "index": 5,
             "expect": {"text": "项目安排"}, "score": 15},
            {"id": "表格3行3列", "type": "table",
             "expect": {"count": 1, "rows": 3, "cols": 3}, "score": 30},
            {"id": "插入图片", "type": "image", "expect": {"count": 1},
             "score": 30},
        ],
    },
    # ---------- 以下为后补的 4 套（都是基础题，老师可单独挑用）----------
    {
        "name": "校园歌手大赛评分表（基础）", "etype": "excel",
        "category": "二级·表格", "level": "基础",
        "material": "歌手评分_基础_素材.xlsx", "answer": "歌手评分_基础_答案.xlsx",
        "save_as": "座位号_姓名.xlsx",
        "make_mat": x_singer_raw, "make_ans": x_singer_basic,
        "requirement": """【WPS 表格】校园歌手大赛评分表（基础）

1. 下载素材并用 WPS 表格打开
2. 工作表名改为「评分表」
3. F2、G2、H2 分别输入「总分」「平均分」「排名」
4. 用 SUM 求每人总分（F3:F8）
5. 用 AVERAGE 求每人平均分（G3:G8），保留 1 位小数
6. 用 RANK 求每人排名（H3:H8，按总分在 F3:F8 里的名次）
7. 表头 A2:H2：黑体、14 号、加粗、水平居中
8. A1:H1 合并后居中
9. A2:H8 加外边框
10. 保存为 座位号_姓名.xlsx 并提交""",
        "checks": [
            {"id": "工作表改名", "type": "sheet_name",
             "expect": {"name": "评分表"}, "score": 10},
            {"id": "总分公式", "type": "formula", "cell": "F3",
             "expect": {"function": "SUM"}, "score": 15},
            {"id": "平均分公式", "type": "formula", "cell": "G3",
             "expect": {"function": "AVERAGE"}, "score": 15},
            {"id": "排名公式", "type": "formula", "cell": "H3",
             "expect": {"function": "RANK"}, "score": 15},
            {"id": "表头字体", "type": "font", "range": "A2:H2",
             "expect": {"name": "黑体", "size": 14, "bold": True},
             "score": 15},
            {"id": "标题合并", "type": "merged", "range": "A1:H1", "score": 10},
            {"id": "表头居中", "type": "alignment", "range": "A2:H2",
             "expect": {"horizontal": "居中"}, "score": 10},
            {"id": "表格边框", "type": "border", "range": "A2:H8", "score": 10},
        ],
    },
    {
        "name": "家长会邀请函（基础）", "etype": "word",
        "category": "二级·文字", "level": "基础",
        "material": "邀请函_基础_素材.docx", "answer": "邀请函_基础_答案.docx",
        "save_as": "座位号_姓名.docx",
        "make_mat": w_invite_raw, "make_ans": w_invite_basic,
        "requirement": """【WPS 文字】家长会邀请函（基础）

1. 下载素材并打开
2. 标题「家长会邀请函」：黑体、二号、加粗、居中
3. 正文（尊敬的家长 到 请提前与班主任联系 这 4 段）：宋体、小四（12 磅）
4. 正文首行缩进 2 字符（24 磅），行距 1.5 倍
5. 页面：A4（21×29.7 厘米），左右页边距 3.17 厘米，上下 2.54 厘米
6. 页脚插入「雅安中学 高二年级组」
7. 在文末插入一个 4 行 3 列的表格，表头依次为：时间、内容、地点
8. 保存为 座位号_姓名.docx 并提交

⚠️ 只改格式，不要删改文字""",
        "checks": [
            {"id": "标题字体", "type": "font", "contains": "家长会邀请函",
             "expect": {"name": "黑体", "size": "二号", "bold": True},
             "score": 20},
            {"id": "标题居中", "type": "paragraph", "contains": "家长会邀请函",
             "expect": {"align": "居中"}, "score": 10},
            {"id": "正文字体", "type": "font", "contains": "为了加强家校沟通",
             "expect": {"name": "宋体", "size": 12}, "score": 15},
            {"id": "正文缩进行距", "type": "paragraph",
             "contains": "为了加强家校沟通",
             "expect": {"first_line_indent": 24, "line_spacing": 1.5},
             "score": 15},
            {"id": "页面设置", "type": "page_setup",
             "expect": {"width": 21.0, "height": 29.7,
                        "margin_left": 3.17, "margin_right": 3.17},
             "score": 15},
            {"id": "页脚", "type": "header_footer", "where": "footer",
             "contains": "高二年级组", "score": 10},
            {"id": "日程表4行3列", "type": "table",
             "expect": {"count": 1, "rows": 4, "cols": 3}, "score": 15},
        ],
    },
    {
        "name": "春季研学安排（基础）", "etype": "word",
        "category": "二级·文字", "level": "基础",
        "material": "研学安排_基础_素材.docx", "answer": "研学安排_基础_答案.docx",
        "save_as": "座位号_姓名.docx",
        "make_mat": w_trip_raw, "make_ans": w_trip_basic,
        "requirement": """【WPS 文字】春季研学安排（基础）

1. 下载素材并打开
2. 标题「春季研学活动安排」：黑体、二号、加粗、居中
3. 把表格第一行补全为：时间、内容、负责人
4. 表格内容按素材里的时间补齐（内容、负责人一列自己填写合理内容）
5. 页眉插入「雅安中学 教务处」
6. 页面设为 A4（21×29.7 厘米）
7. 保存为 座位号_姓名.docx 并提交""",
        "checks": [
            {"id": "标题字体", "type": "font", "contains": "春季研学活动安排",
             "expect": {"name": "黑体", "size": "二号", "bold": True},
             "score": 20},
            {"id": "标题居中", "type": "paragraph", "contains": "春季研学活动安排",
             "expect": {"align": "居中"}, "score": 10},
            {"id": "表格5行3列", "type": "table",
             "expect": {"count": 1, "rows": 5, "cols": 3}, "score": 20},
            {"id": "表头第三节", "type": "table",
             "expect": {"cell": [0, 2, "负责人"]}, "score": 15},
            {"id": "内容列已填", "type": "table",
             "expect": {"cell": [1, 1, "校门口集合乘车"]}, "score": 15},
            {"id": "页眉", "type": "header_footer", "where": "header",
             "contains": "教务处", "score": 20},
        ],
    },
    {
        "name": "校园运动会宣传（基础）", "etype": "ppt",
        "category": "二级·演示", "level": "基础",
        "material": "运动会_基础_素材.pptx", "answer": "运动会_基础_答案.pptx",
        "save_as": "座位号_姓名.pptx",
        "make_mat": p_sport_raw, "make_ans": p_sport_basic,
        "requirement": """【WPS 演示】校园运动会宣传（基础）

1. 下载素材并打开
2. 第 1 页标题改为「第18届校园运动会」
3. 第 1 页标题：微软雅黑、40 磅、加粗、居中
4. 第 1 页背景设为纯色：深蓝（RGB 1F4E79）
5. 新增两页，标题分别为「奖项设置」「温馨提示」
6. 再新增一页，标题「项目时间表」，插入 3 行 3 列的表格
7. 共 6 页
8. 保存为 座位号_姓名.pptx 并提交""",
        "checks": [
            {"id": "共6页", "type": "slide_count", "expect": {"count": 6},
             "score": 20},
            {"id": "首页标题", "type": "title", "index": 0,
             "expect": {"text": "第18届校园运动会"}, "score": 15},
            {"id": "标题字体", "type": "font", "contains": "第18届校园运动会",
             "expect": {"name": "微软雅黑", "size": 40, "bold": True},
             "score": 20},
            {"id": "标题居中", "type": "align", "contains": "第18届校园运动会",
             "expect": {"align": "居中"}, "score": 10},
            {"id": "背景色", "type": "background", "index": 0,
             "expect": {"color": "1F4E79"}, "score": 20},
            {"id": "表格3行3列", "type": "table",
             "expect": {"count": 1, "rows": 3, "cols": 3}, "score": 15},
        ],
    },

    # ---------- 以下 5 套是「一级·入门」（比二级简单：改个名、填两格、保存）----------
    {
        "name": "班级通讯录（入门）", "etype": "excel",
        "category": "一级·表格", "level": "入门",
        "material": "通讯录_入门_素材.xlsx", "answer": "通讯录_入门_答案.xlsx",
        "save_as": "座位号_姓名.xlsx",
        "make_mat": x_contact_raw, "make_ans": x_contact_basic,
        "requirement": """【WPS 表格】班级通讯录（入门）

1. 下载素材并用 WPS 表格打开
2. 把工作表名 Sheet1 改为「通讯录」
3. 把 A1 的标题「班级通讯录」设为：黑体、14 号、加粗
4. 把 A1:C1 合并，并水平居中
5. 表头 A2:C2（姓名、电话、爱好）：黑体、12 号、加粗、水平居中
6. 给 A1:C7 加上表格边框
7. 保存为 座位号_姓名.xlsx 并提交""",
        "checks": [
            {"id": "工作表改名", "type": "sheet_name",
             "expect": {"name": "通讯录"}, "score": 20},
            {"id": "标题合并", "type": "merged", "range": "A1:C1",
             "score": 20},
            {"id": "标题字体", "type": "font", "range": "A1",
             "expect": {"name": "黑体", "size": 14, "bold": True},
             "score": 20},
            {"id": "表头加粗居中", "type": "alignment", "range": "A2:C2",
             "expect": {"horizontal": "居中"}, "score": 20},
            {"id": "表格边框", "type": "border", "range": "A1:C7",
             "score": 20},
        ],
    },
    {
        "name": "课程表填写（入门）", "etype": "excel",
        "category": "一级·表格", "level": "入门",
        "material": "课程表_入门_素材.xlsx", "answer": "课程表_入门_答案.xlsx",
        "save_as": "座位号_姓名.xlsx",
        "make_mat": x_timetable_raw, "make_ans": x_timetable_basic,
        "requirement": """【WPS 表格】课程表填写（入门）

1. 下载素材并用 WPS 表格打开
2. 把工作表名 Sheet1 改为「课程表」
3. 把 A1:F1 合并，标题「课程表」：黑体、14 号、加粗、水平居中
4. 第 2 行填上表头：节次、星期一、星期二、星期三、星期四、星期五
5. 第 2 行表头：黑体、12 号、加粗、水平居中
6. 在 B3 单元格写上「语文」（第一节课）
7. 给 A1:F7 加上表格边框
8. 保存为 座位号_姓名.xlsx 并提交""",
        "checks": [
            {"id": "工作表改名", "type": "sheet_name",
             "expect": {"name": "课程表"}, "score": 15},
            {"id": "标题合并", "type": "merged", "range": "A1:F1",
             "score": 20},
            {"id": "表头星期三", "type": "cell_value", "cell": "D2",
             "expect": {"value": "星期三"}, "score": 15},
            {"id": "第一节课语文", "type": "cell_value", "cell": "B3",
             "expect": {"value": "语文"}, "score": 20},
            {"id": "表头加粗", "type": "font", "range": "A2:F2",
             "expect": {"name": "黑体", "size": 12, "bold": True},
             "score": 15},
            {"id": "表格边框", "type": "border", "range": "A1:F7",
             "score": 15},
        ],
    },
    {
        "name": "自我介绍排版（入门）", "etype": "word",
        "category": "一级·文字", "level": "入门",
        "material": "自我介绍_入门_素材.docx", "answer": "自我介绍_入门_答案.docx",
        "save_as": "座位号_姓名.docx",
        "make_mat": w_intro_raw, "make_ans": w_intro_basic,
        "requirement": """【WPS 文字】自我介绍排版（入门）

1. 下载素材并用 WPS 文字打开
2. 标题「我的自我介绍」：黑体、二号、加粗、居中
3. 正文三段：宋体、小四（12 磅）
4. 页面设为 A4（宽 21 厘米、高 29.7 厘米）
5. 保存为 座位号_姓名.docx 并提交""",
        "checks": [
            {"id": "标题字体", "type": "font", "contains": "我的自我介绍",
             "expect": {"name": "黑体", "size": "二号", "bold": True},
             "score": 30},
            {"id": "标题居中", "type": "paragraph", "contains": "我的自我介绍",
             "expect": {"align": "居中"}, "score": 20},
            {"id": "正文字体", "type": "font", "contains": "我最喜欢的科目",
             "expect": {"name": "宋体", "size": 12}, "score": 30},
            {"id": "页面A4", "type": "page_setup",
             "expect": {"width": 21.0, "height": 29.7}, "score": 20},
        ],
    },
    {
        "name": "古诗抄写排版（入门）", "etype": "word",
        "category": "一级·文字", "level": "入门",
        "material": "古诗_入门_素材.docx", "answer": "古诗_入门_答案.docx",
        "save_as": "座位号_姓名.docx",
        "make_mat": w_poem_raw, "make_ans": w_poem_basic,
        "requirement": """【WPS 文字】古诗抄写排版（入门）

1. 下载素材并用 WPS 文字打开
2. 标题「春晓」：黑体、三号、加粗、居中
3. 两句诗句：居中、宋体、小四（12 磅）
4. 页脚写上班名（例如「五年级（1）班」）
5. 保存为 座位号_姓名.docx 并提交""",
        "checks": [
            {"id": "标题字体", "type": "font", "contains": "春晓",
             "expect": {"name": "黑体", "size": "三号", "bold": True},
             "score": 30},
            {"id": "诗句居中", "type": "paragraph", "contains": "春眠不觉晓",
             "expect": {"align": "居中"}, "score": 20},
            {"id": "诗句字体", "type": "font", "contains": "春眠不觉晓",
             "expect": {"name": "宋体", "size": 12}, "score": 25},
            {"id": "页脚班名", "type": "header_footer", "where": "footer",
             "contains": "班", "score": 25},
        ],
    },
    {
        "name": "我的班级介绍（入门）", "etype": "ppt",
        "category": "一级·演示", "level": "入门",
        "material": "我的班级_入门_素材.pptx", "answer": "我的班级_入门_答案.pptx",
        "save_as": "座位号_姓名.pptx",
        "make_mat": p_class_raw, "make_ans": p_class_basic,
        "requirement": """【WPS 演示】我的班级介绍（入门）

1. 下载素材并用 WPS 演示打开
2. 第 1 页标题改为「五年级（1）班」
3. 第 1 页标题：微软雅黑、36 磅、加粗、居中
4. 再新增 3 页，标题分别为「班级口号」「班级荣誉」「我的目标」
5. 一共 4 页
6. 保存为 座位号_姓名.pptx 并提交""",
        "checks": [
            {"id": "共4页", "type": "slide_count", "expect": {"count": 4},
             "score": 25},
            {"id": "首页标题", "type": "title", "index": 0,
             "expect": {"text": "五年级（1）班"}, "score": 25},
            {"id": "标题字体", "type": "font", "contains": "五年级（1）班",
             "expect": {"name": "微软雅黑", "size": 36, "bold": True},
             "score": 25},
            {"id": "标题居中", "type": "align", "contains": "五年级（1）班",
             "expect": {"align": "居中"}, "score": 25},
        ],
    },
]


def generate():
    print("正在生成实操题库…\n")
    # 先造公共素材（图片）
    make_png(os.path.join(MAT_DIR, "素材图片_校徽.png"))

    out = []
    for it in ITEMS:
        mat = os.path.join(MAT_DIR, it["material"])
        ans = os.path.join(ANS_DIR, it["answer"])
        it["make_mat"](mat)
        it["make_ans"](mat, ans)

        mats = [it["material"]] + list(it.get("extra_materials") or [])
        out.append({
            "name": it["name"], "etype": it["etype"],
            "category": it["category"], "level": it["level"],
            "materials": mats, "save_as": it["save_as"],
            "requirement": it["requirement"], "checks": it["checks"],
        })
        print(f"  ✅ [{it['category']}/{it['level']}] {it['name']}"
              f"  ({len(it['checks'])} 个评分点)")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n题库文件: {OUT_JSON}")
    print(f"素材目录: {MAT_DIR}")
    print(f"标准答案: {ANS_DIR}")


def verify():
    print("\n" + "=" * 58)
    print("  实测评分规则")
    print("=" * 58)
    all_ok = True
    for it in ITEMS:
        ans = os.path.join(ANS_DIR, it["answer"])
        mat = os.path.join(MAT_DIR, it["material"])
        full = sum(float(c["score"]) for c in it["checks"])
        got_ans, _, detail = exm.grade_file(ans, it["checks"])
        got_mat, _, _ = exm.grade_file(mat, it["checks"])
        ok_a = abs(got_ans - full) < 0.01
        ok_m = got_mat <= full * 0.2
        all_ok = all_ok and ok_a and ok_m
        print(f"\n【{it['name']}】满分 {full}")
        print(f"  标准答案 {got_ans}  {'✅' if ok_a else '❌'}"
              f"   原始素材 {got_mat}  {'✅ 不做不得分' if ok_m else '❌'}")
        if not ok_a:
            for d in detail:
                if d["got"] < d["full"]:
                    print(f"     ❌ {d['id']}: {d['got']}/{d['full']} {d['msg']}")
    print("\n" + "=" * 58)
    print("  全部通过 ✅" if all_ok else "  有问题，看上面 ❌ 的项")
    print("=" * 58)
    return all_ok


def main():
    ap = argparse.ArgumentParser(description="生成实操题库")
    ap.add_argument("--check", action="store_true", help="只实测，不重新生成")
    a = ap.parse_args()
    if not a.check:
        generate()
    sys.exit(0 if verify() else 1)


if __name__ == "__main__":
    main()
