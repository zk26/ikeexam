#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「雅安会考（信息技术学业水平考试）」模拟题库
------------------------------------------------
⚠️ 说明：近两年（2024-2025）雅安市学业水平考试真题没有公开渠道，
本文件是依据【四川省普通高中学业水平考试 信息技术学科考试说明】
和近年常见考点编写的**模拟题**，用于平时练习和模考。

拿到真题后怎么替换：
  1. 按同格式改本文件里的 THEORY / CODE 两个列表（题干、选项、答案）
  2. 或直接编辑生成的 Excel
  3. 后台 → 题库 → 导入 Excel

考点覆盖（每个模块都有题，方便按分类抽题）：
  · 信息与信息技术（含 AI / 大数据 / 物联网 / 云计算 / 5G 等近两年热点）
  · 计算机系统（硬件软件、进制、存储单位）
  · 文件与操作系统
  · 网络基础
  · 信息安全与信息社会责任
  · WPS 文字 / 表格 / 演示数据处理
  · 算法与 Python 程序设计
  · 程序填空（可本地运行验证）

用法：
    python 生成雅安会考题库.py
    python 生成雅安会考题库.py --out 我的会考题.xlsx
"""
import argparse
import os
from collections import Counter

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

HEADERS = ["题型", "题目", "选项A", "选项B", "选项C", "选项D", "答案", "分值", "标签",
           "语言", "代码", "运行输入"]
HEAD_DESC = ["单选/多选/判断/填空/程序填空", "题干", "", "", "", "",
             "程序填空多空用 || 分隔，每空多个答案用 | 分隔",
             "默认2分", "分类标签",
             "程序填空填 python 或 c++", "含 ____ 的代码模板", "运行输入（可选）"]

# 统一分类：后台按分类抽题时，选「雅安会考」即可整卷抽取
CAT = "雅安会考"


# ==================================================================
#  一、理论题
#  (题型, 题目, A, B, C, D, 答案, 分值, 标签)
# ==================================================================
THEORY = [
    # ---------- 信息与信息技术基础 ----------
    ("单选", "下列关于信息的说法，正确的是？",
     "信息不需要依附载体", "信息必须依附于载体", "信息不能传递", "信息共享后会减少",
     "B", 2, CAT),
    ("多选", "信息的基本特征包括以下哪些？",
     "载体依附性", "可传递性", "共享性", "时效性", "ABCD", 4, CAT),
    ("判断", "同一条信息对不同的人价值可能不同，这体现了信息的价值相对性。",
     "", "", "", "", "正确", 2, CAT),
    ("单选", "“明天的气温是 26℃”这条信息主要体现了信息的？",
     "共享性", "时效性", "真伪性", "依附性", "B", 2, CAT),
    ("填空", "信息技术主要包括感测技术、通信技术、____技术和控制技术。",
     "", "", "", "", "计算机|计算机与智能|智能", 2, CAT),
    ("多选", "下列属于信息技术应用的是？",
     "网上银行转账", "网课直播", "手写书信", "人脸识别门禁", "ABD", 4, CAT),
    ("判断", "信息可以脱离载体独立存在。", "", "", "", "", "错误", 2, CAT),
    ("单选", "下列属于“信息”本身的是？",
     "报纸", "报纸上的新闻内容", "U 盘", "电视机", "B", 2, CAT),

    # ---------- 新技术（近两年高频考点）----------
    ("单选", "人工智能的英文缩写是？",
     "AI", "IoT", "VR", "GPS", "A", 2, CAT),
    ("单选", "物联网的英文缩写是？",
     "AI", "IoT", "VR", "5G", "B", 2, CAT),
    ("多选", "大数据的典型特征（4V）包括？",
     "数据量大", "类型多样", "处理速度快", "价值密度低", "ABCD", 4, CAT),
    ("判断", "生成式人工智能生成的内容也可能出现错误，需要人工核实。",
     "", "", "", "", "正确", 2, CAT),
    ("单选", "云计算的本质是？",
     "把计算和存储放到网络上的服务器集群", "只能在本地计算机上运行",
     "必须借助光盘存储", "完全不需要网络", "A", 2, CAT),
    ("填空", "5G 是第____代移动通信技术。", "", "", "", "", "五|5", 2, CAT),
    ("单选", "VR 指的是？",
     "虚拟现实", "增强现实", "混合现实", "3D 打印", "A", 2, CAT),

    # ---------- 计算机系统 ----------
    ("单选", "下列属于计算机输出设备的是？",
     "键盘", "鼠标", "显示器", "扫描仪", "C", 2, CAT),
    ("判断", "硬盘属于外存储器，内存储器断电后数据会丢失。",
     "", "", "", "", "正确", 2, CAT),
    ("单选", "1 TB 等于多少 GB？",
     "1000", "1024", "100", "512", "B", 2, CAT),
    ("填空", "冯·诺依曼体系结构的计算机包括运算器、控制器、存储器、输入设备和____。",
     "", "", "", "", "输出设备", 2, CAT),
    ("单选", "二进制数 1000 转换为十进制是？",
     "4", "6", "8", "10", "C", 2, CAT),
    ("单选", "十进制数 25 转换为二进制是？",
     "11001", "10011", "10101", "11100", "A", 2, CAT),
    ("多选", "计算机病毒可能的传播途径有？",
     "U 盘等移动存储设备", "网络下载的文件", "电子邮件附件", "正常关机",
     "ABC", 4, CAT),
    ("判断", "计算机软件分为系统软件和应用软件，Windows 属于系统软件。",
     "", "", "", "", "正确", 2, CAT),

    # ---------- 文件与操作系统 ----------
    ("单选", "下列属于视频文件的是？",
     "music.mp3", "movie.mp4", "photo.jpg", "report.docx", "B", 2, CAT),
    ("填空", "WPS 表格文件的默认扩展名是____。", "", "", "", "",
     ".xlsx|xlsx|et", 2, CAT),
    ("单选", "在 Windows 中选中多个不连续的文件，需要按住？",
     "Ctrl", "Shift", "Alt", "Tab", "A", 2, CAT),
    ("判断", "文件路径 D:\\素材\\成绩.xlsx 中，D: 表示盘符。",
     "", "", "", "", "正确", 2, CAT),
    ("多选", "以下属于文本类文件扩展名的是？",
     ".txt", ".docx", ".mp4", ".xlsx", "ABD", 4, CAT),

    # ---------- 网络基础 ----------
    ("单选", "下列合法的 IPv4 地址是？",
     "192.168.1.1", "256.1.1.1", "192.168.1", "192.168.1.300", "A", 2, CAT),
    ("单选", "域名后缀 .edu 一般表示？",
     "教育机构", "政府机构", "商业机构", "军事机构", "A", 2, CAT),
    ("单选", "把域名转换成 IP 地址的服务是？",
     "DNS", "DHCP", "FTP", "HTML", "A", 2, CAT),
    ("判断", "局域网的英文缩写是 LAN。", "", "", "", "", "正确", 2, CAT),
    ("多选", "常见的网络设备有？",
     "路由器", "交换机", "网卡", "打印机", "ABC", 4, CAT),
    ("单选", "下列不属于无线接入方式的是？",
     "Wi-Fi", "蓝牙", "光纤", "4G", "C", 2, CAT),

    # ---------- 信息安全与信息社会责任 ----------
    ("单选", "下列密码中最安全的是？",
     "123456", "abcdef", "Ya#2025@exAm", "20080101", "C", 2, CAT),
    ("多选", "防范电信诈骗的正确做法有？",
     "不向他人透露验证码", "不随意点击陌生链接", "通过官方渠道核实", "轻信中奖短信",
     "ABC", 4, CAT),
    ("判断", "定期备份重要数据可以降低数据丢失带来的损失。",
     "", "", "", "", "正确", 2, CAT),
    ("单选", "收到自称“银行”索要验证码的短信，正确做法是？",
     "立即提供", "不予理会，通过官方渠道核实", "转发给好友", "按提示转账",
     "B", 2, CAT),
    ("填空", "计算机病毒具有传染性、潜伏性、____性和破坏性。", "", "", "", "",
     "隐蔽|隐蔽性|寄生|可触发", 2, CAT),
    ("单选", "下列做法有利于保护个人信息的是？",
     "在公共电脑登录账号后不退出", "在社交平台公开身份证号",
     "不同账号使用不同密码", "连接不明免费 Wi-Fi 进行支付", "C", 2, CAT),

    # ---------- WPS 数据处理 ----------
    ("单选", "WPS 表格中求平均值的函数是？",
     "SUM", "AVERAGE", "MAX", "COUNT", "B", 2, CAT),
    ("判断", "在 WPS 表格中，公式必须以“=”开头。", "", "", "", "", "正确", 2, CAT),
    ("单选", "WPS 表格中 $A$1 属于？",
     "相对引用", "绝对引用", "混合引用", "无效地址", "B", 2, CAT),
    ("多选", "WPS 演示中可以插入的对象有？",
     "图片", "音频", "视频", "超链接", "ABCD", 4, CAT),
    ("填空", "WPS 文字中，快捷键 Ctrl+S 的功能是____。", "", "", "", "",
     "保存|保存文件|保存文档", 2, CAT),
    ("单选", "要直观比较各班的平均分，最合适的呈现方式是？",
     "柱形图", "一段文字描述", "一张照片", "一段音频", "A", 2, CAT),

    # ---------- 算法与程序设计 ----------
    ("单选", "在流程图中，表示判断或分支的图形是？",
     "矩形", "菱形", "平行四边形", "圆角矩形", "B", 2, CAT),
    ("多选", "描述算法的常用方法有？",
     "自然语言", "流程图", "伪代码", "只能写成程序", "ABC", 4, CAT),
    ("判断", "算法可以没有输入，但一定要有输出。", "", "", "", "", "正确", 2, CAT),
    ("单选", "Python 中表达式 7 // 2 的结果是？",
     "3.5", "3", "1", "4", "B", 2, CAT),
    ("填空", "Python 中表示“并且”的关键字是____。", "", "", "", "", "and", 2, CAT),
    ("单选", "Python 中 range(1, 5) 生成的序列是？",
     "1,2,3,4", "1,2,3,4,5", "0,1,2,3,4", "0,1,2,3", "A", 2, CAT),
]


# ==================================================================
#  二、程序填空（Python，学生端可点「运行代码」验证）
#  (题干, 语言, 代码模板, 答案, 分值, 标签, 运行输入)
# ==================================================================
CODE = [
    ("补全程序：计算 1 + 2 + … + 100 的和。",
     "python",
     "s = 0\nfor i in range(1, ____):\n    s = s + i\nprint(s)",
     "101|100+1|100 + 1", 4, CAT, ""),

    ("补全程序：输入正整数 n，输出 n 的阶乘。",
     "python",
     "n = int(input())\np = 1\nfor i in range(1, n + 1):\n    p = ____\nprint(p)",
     "p * i|p*i", 5, CAT, "5"),

    ("补全程序：输入成绩，60 分及以上输出“及格”。",
     "python",
     "score = int(input())\nif score ____ 60:\n    print('及格')\nelse:\n    print('不及格')",
     ">=", 4, CAT, "75"),

    ("补全程序：统计某个字符在字符串中出现的次数。",
     "python",
     "s = input()\nch = input()\nprint(s.____(ch))",
     "count", 4, CAT, "hello\nl"),

    ("补全程序：计算四位同学的平均分。",
     "python",
     "a = [85, 92, 78, 90]\nprint(sum(a) ____ len(a))",
     "/", 3, CAT, ""),

    ("补全程序：找出列表中的最大值。",
     "python",
     "a = [12, 45, 7, 33]\nm = a[0]\nfor x in a:\n    if x ____ m:\n        m = x\nprint(m)",
     ">", 4, CAT, ""),

    ("补全程序：生成 1 到 n 中所有偶数组成的列表。",
     "python",
     "n = int(input())\na = [i for i in range(1, n + 1) if i ____ 2 == 0]\nprint(a)",
     "%", 5, CAT, "10"),

    ("补全程序：把输入的字符串倒序输出。",
     "python",
     "s = input()\nprint(s[::-____])",
     "1", 4, CAT, "abc"),

    ("补全程序：输入半径 r，计算圆的面积（π 取 3.14）。",
     "python",
     "r = float(input())\ns = 3.14 * r ____ 2\nprint(s)",
     "**", 4, CAT, "2"),
]


def set_text(cell, value):
    """
    写单元格。⚠️ 关键：以 = 开头的字符串（如程序填空答案 "=="）
    会被 openpyxl 当成【公式】，存进去再读出来是 None —— 表现为
    "答案为空"。这里强制标记为文本类型。
    """
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"
    return cell


def build(out_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "题库"

    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for i, h in enumerate(HEADERS, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF", size=11)
        c.fill = PatternFill("solid", fgColor="4472C4")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border

    for i, d in enumerate(HEAD_DESC, 1):
        c = ws.cell(row=2, column=i, value=d)
        c.font = Font(size=9, italic=True, color="808080")
        c.fill = PatternFill("solid", fgColor="FFF2CC")
        c.border = border

    row = 3
    for q in THEORY:
        vals = list(q) + ["", "", ""]
        for i, v in enumerate(vals, 1):
            c = set_text(ws.cell(row=row, column=i), v)
            c.border = border
            c.alignment = Alignment(vertical="center", wrap_text=(i == 2))
        ws.cell(row=row, column=1).fill = PatternFill(
            "solid", fgColor={"单选": "E8F1FB", "多选": "FFF3E0",
                              "判断": "E8F5E9", "填空": "F3E5F5"}.get(q[0], "FFFFFF"))
        ws.cell(row=row, column=7).font = Font(bold=True, color="C00000")
        row += 1

    for stem, lang, code, ans, score, tag, stdin_data in CODE:
        vals = ["程序填空", stem, "", "", "", "", ans, score, tag,
                lang, code, stdin_data]
        for i, v in enumerate(vals, 1):
            c = set_text(ws.cell(row=row, column=i), v)
            c.border = border
            c.alignment = Alignment(vertical="top", wrap_text=(i in (2, 11)))
        ws.cell(row=row, column=1).fill = PatternFill("solid", fgColor="FDE9E9")
        ws.cell(row=row, column=7).font = Font(bold=True, color="C00000")
        ws.cell(row=row, column=11).font = Font(name="Consolas", size=10)
        ws.row_dimensions[row].height = 90
        row += 1

    widths = (10, 46, 20, 20, 20, 20, 22, 7, 14, 9, 52, 14)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:L{row - 1}"

    wb.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="生成雅安会考（信息技术）模拟题库")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "题库_雅安会考模拟.xlsx")
    build(out)

    ct = Counter(q[0] for q in THEORY)
    total = sum(q[7] for q in THEORY) + sum(c[4] for c in CODE)
    print(f"✅ 已生成: {out}")
    print(f"   理论题 {len(THEORY)} 题  " + "  ".join(f"{k}{v}" for k, v in ct.items()))
    print(f"   程序填空 {len(CODE)} 题（Python，学生端可运行验证）")
    print(f"   合计 {len(THEORY) + len(CODE)} 题，总分 {total}")
    print(f"   分类标签: {CAT}（后台按分类抽题即可整卷抽取）")
    print("\n导入：后台 → 题库 → 导入 Excel")


if __name__ == "__main__":
    main()
