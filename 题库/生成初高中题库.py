#!/usr/bin/env python3
"""
生成「初高中信息技术」题库
------------------------------------------------
覆盖新课标内容：
  · 信息与信息技术基础
  · 计算机系统 / 进制 / 编码 / 存储单位
  · 网络与信息安全
  · 算法与程序设计（Python 为主）
  · 程序填空题（可本地运行验证）

输出一份可直接导入后台的 Excel。

用法：
    python3 生成初高中题库.py
    python3 生成初高中题库.py --out 我的题库.xlsx
"""
import argparse
import os

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

HEADERS = ["题型", "题目", "选项A", "选项B", "选项C", "选项D", "答案", "分值", "标签",
           "语言", "代码", "运行输入"]
HEAD_DESC = ["单选/多选/判断/填空/程序填空", "题干", "", "", "", "",
             "程序填空多空用 || 分隔，每空多个答案用 | 分隔",
             "默认2分", "分类标签",
             "程序填空填 python 或 c++", "含 ____ 的代码模板", "运行输入（可选）"]


# ==================================================================
#  一、理论题
#  (题型, 题目, A, B, C, D, 答案, 分值, 标签)
# ==================================================================
THEORY = [
    # ---------- 信息与信息的特征 ----------
    ("单选", "下列关于信息的说法，正确的是？",
     "信息就是数据本身", "信息必须依附于载体", "信息不能共享", "信息不会过时",
     "B", 2, "信息基础"),
    ("多选", "信息的基本特征包括以下哪些？",
     "载体依附性", "可传递性", "共享性", "永不失真性", "ABC", 4, "信息基础"),
    ("判断", "信息在传递过程中可以被多人共享，而本身不会减少。", "", "", "", "",
     "正确", 2, "信息基础"),
    ("单选", "“明天的气温是 26℃”这条信息主要体现了信息的？",
     "共享性", "时效性", "真伪性", "依附性", "B", 2, "信息基础"),
    ("填空", "数据经过加工处理后成为____。", "", "", "", "", "信息", 2, "信息基础"),

    # ---------- 计算机系统 ----------
    ("单选", "下列属于计算机输入设备的是？",
     "显示器", "打印机", "键盘", "音箱", "C", 2, "计算机系统"),
    ("多选", "冯·诺依曼体系结构的计算机由哪些部分组成？",
     "运算器", "控制器", "存储器", "散热器", "ABC", 4, "计算机系统"),
    ("单选", "CPU 主要由哪两部分组成？",
     "内存和外存", "运算器和控制器", "硬盘和显卡", "输入和输出设备",
     "B", 2, "计算机系统"),
    ("判断", "RAM 断电后数据会丢失，ROM 断电后数据不会丢失。", "", "", "", "",
     "正确", 2, "计算机系统"),
    ("单选", "下列属于系统软件的是？",
     "WPS 文字", "Windows 操作系统", "QQ", "Photoshop",
     "B", 2, "计算机系统"),
    ("填空", "计算机中最小的数据单位是____（填英文）。", "", "", "", "",
     "bit|比特|位", 2, "计算机系统"),

    # ---------- 进制与存储单位 ----------
    ("单选", "十进制数 10 转换为二进制是？",
     "1010", "1001", "1100", "1110", "A", 2, "进制编码"),
    ("单选", "二进制数 1011 转换为十进制是？",
     "10", "11", "12", "13", "B", 2, "进制编码"),
    ("单选", "1 GB 等于多少 MB？",
     "1000", "1024", "1048", "512", "B", 2, "进制编码"),
    ("判断", "1 Byte（字节）等于 8 bit（位）。", "", "", "", "", "正确", 2, "进制编码"),
    ("单选", "十六进制中使用字母 A~F 表示的是十进制的？",
     "10~15", "11~16", "9~14", "1~6", "A", 2, "进制编码"),
    ("填空", "二进制数 1101 转换为十进制是____。", "", "", "", "", "13", 2, "进制编码"),
    ("单选", "在计算机中，一个汉字通常占用多少字节？",
     "1", "2", "4", "8", "B", 2, "进制编码"),
    ("多选", "常见的字符编码标准有？",
     "ASCII", "Unicode", "GB2312", "HTML", "ABC", 4, "进制编码"),

    # ---------- 网络与信息安全 ----------
    ("单选", "下面哪个是正确的 IPv4 地址？",
     "192.168.1.1", "192.168.1.256", "192.168.1", "www.baidu.com",
     "A", 2, "网络"),
    ("单选", "域名 www.tsinghua.edu.cn 中的 cn 代表？",
     "教育机构", "商业机构", "中国", "政府", "C", 2, "网络"),
    ("单选", "HTTP 协议默认使用的端口号是？",
     "21", "23", "80", "443", "C", 2, "网络"),
    ("多选", "以下属于信息安全防护措施的是？",
     "安装杀毒软件", "定期备份数据", "设置复杂密码", "随意点击陌生链接",
     "ABC", 4, "信息安全"),
    ("判断", "计算机病毒是一种可以自我复制、破坏计算机数据的程序。",
     "", "", "", "", "正确", 2, "信息安全"),
    ("单选", "为防止账号被盗，最不安全的做法是？",
     "使用字母数字符号组合", "定期更换密码", "所有账号用同一个简单密码",
     "开启二次验证", "C", 2, "信息安全"),

    # ---------- 算法基础 ----------
    ("多选", "算法的基本特征包括？",
     "有穷性", "确定性", "有零个或多个输入", "必须有一个输出",
     "ABCD", 4, "算法"),
    ("判断", "算法可以没有输入，但一定要有输出。", "", "", "", "", "正确", 2, "算法"),
    ("单选", "在流程图中，表示判断/分支的图形是？",
     "矩形", "菱形", "平行四边形", "圆角矩形", "B", 2, "算法"),
    ("单选", "以下三种基本控制结构，不包括？",
     "顺序结构", "分支结构", "循环结构", "递归结构", "D", 2, "算法"),
    ("填空", "描述算法的方法有自然语言、流程图和____。", "", "", "", "",
     "伪代码|程序设计语言|代码", 2, "算法"),

    # ---------- Python 基础 ----------
    ("单选", "Python 中，用于接收键盘输入的函数是？",
     "print()", "input()", "len()", "range()", "B", 2, "Python"),
    ("单选", "表达式 7 // 2 的结果是？",
     "3.5", "3", "4", "1", "B", 2, "Python"),
    ("单选", "表达式 7 % 2 的结果是？",
     "3.5", "3", "1", "0", "C", 2, "Python"),
    ("单选", "以下代码的输出是？\nfor i in range(3):\n    print(i)",
     "0 1 2", "1 2 3", "0 1 2 3", "1 2", "A", 2, "Python"),
    ("判断", "Python 中列表的下标从 0 开始。", "", "", "", "", "正确", 2, "Python"),
    ("多选", "下列可以作为 Python 变量名的是？",
     "a1", "_x", "2b", "stu_name", "ABD", 4, "Python"),
    ("单选", "下列数据类型中，属于不可变类型的是？",
     "list", "dict", "str", "set", "C", 2, "Python"),
    ("填空", "Python 中表示“整除”的运算符是____。", "", "", "", "",
     "//|//运算符", 2, "Python"),

    # ---------- 新技术（新课标）----------
    ("单选", "下列应用中，属于人工智能的是？",
     "用计算器算账", "人脸识别解锁手机", "用 Word 打字", "用 U 盘拷文件",
     "B", 2, "新技术"),
    ("多选", "大数据的特征通常概括为？",
     "数据量大", "类型多样", "处理速度快", "价值密度高", "ABC", 4, "新技术"),
    ("判断", "物联网就是让物体通过互联网相连并实现信息交换。", "", "", "", "",
     "正确", 2, "新技术"),
]


# ==================================================================
#  二、程序填空题
#  (题目, 语言, 代码模板, 答案, 分值, 标签, 运行输入)
#  代码中用 ____ 表示空（4 个及以上下划线）
#  答案中：空之间用 || 分隔，每个空的多个可接受答案用 | 分隔
# ==================================================================
CODE = [
    ("补全程序：从键盘输入整数 n，输出 1 到 n 的累加和。",
     "python",
     "n = int(input())\ns = ____\nfor i in range(1, n + 1):\n    s = s + i\nprint(s)",
     "0", 4, "循环", "10"),

    ("补全程序：输出 1 到 n 之间所有偶数的和。",
     "python",
     "n = int(input())\ns = 0\nfor i in range(2, n + 1, ____):\n    s = s + i\nprint(s)",
     "2", 4, "循环", "10"),

    ("补全程序：判断 n 是否为素数（质数），是则输出 True。",
     "python",
     "n = int(input())\nflag = True\nfor i in range(2, n):\n    if n % i ____ 0:\n        flag = False\n        break\nprint(flag)",
     "==", 4, "分支", "7"),

    ("补全程序：用递归求 n 的阶乘（n 为不小于 1 的整数）。",
     "python",
     "def f(n):\n    if n ____ 1:\n        return 1\n    return n * f(n - 1)\n\nprint(f(5))",
     "==|<=|=<", 4, "递归", ""),

    ("补全程序：输出斐波那契数列的前 10 项。",
     "python",
     "a, b = 0, 1\nfor i in range(10):\n    print(a)\n    a, b = b, ____",
     "a+b|a + b", 4, "循环", ""),

    ("补全程序：找出列表中的最大值。",
     "python",
     "a = [3, 7, 2, 9, 5]\nm = a[0]\nfor x in a:\n    if x ____ m:\n        m = x\nprint(m)",
     ">", 4, "循环", ""),

    ("补全程序：计算列表 a 中所有元素的和。",
     "python",
     "a = [1, 2, 3, 4, 5]\nprint(____(a))",
     "sum", 3, "内置函数", ""),

    ("补全程序：将字符串 s 反转后输出。",
     "python",
     "s = 'hello'\nprint(s[____])",
     "::-1", 4, "字符串", ""),

    ("补全程序：用 while 循环输出 1 到 10。",
     "python",
     "i = 1\nwhile i ____ 10:\n    print(i)\n    i = i + 1",
     "<=|<=", 4, "循环", ""),

    ("补全程序：统计输入的字符串中字母 a 出现的次数。",
     "python",
     "s = input()\ncnt = 0\nfor c in s:\n    if c ____ 'a':\n        cnt = cnt + 1\nprint(cnt)",
     "==", 4, "循环", "banana"),

    ("补全程序：交换变量 a 和 b 的值。",
     "python",
     "a, b = 3, 5\na, b = ____, a\nprint(a, b)",
     "b", 3, "基础语法", ""),

    ("补全程序：输出九九乘法表（内层循环条件）。",
     "python",
     "for i in range(1, 10):\n    for j in range(1, ____):\n        print(i * j, end=' ')\n    print()",
     "i+1|i + 1", 4, "循环", ""),

    ("补全程序：计算三位同学的平均分。",
     "python",
     "a = [80, 90, 70]\nprint(sum(a) ____ len(a))",
     "/", 3, "运算", ""),

    ("补全程序：用冒泡排序将列表从小到大排列。",
     "python",
     "a = [5, 3, 8, 1]\nfor i in range(len(a) - 1):\n    for j in range(len(a) - 1 - i):\n        if a[j] ____ a[j + 1]:\n            a[j], a[j + 1] = a[j + 1], a[j]\nprint(a)",
     ">", 5, "排序", ""),

    ("补全程序：把输入的字符串转换成整数并加 1 输出。",
     "python",
     "age = ____(input())\nprint(age + 1)",
     "int", 3, "基础语法", "12"),

    ("补全程序：把 0 到 4 依次添加到列表 a 中。",
     "python",
     "a = []\nfor i in range(5):\n    a.____(i)\nprint(a)",
     "append", 3, "列表", ""),

    ("补全程序：输出字符串 s 的长度。",
     "python",
     "s = 'Python'\nprint(____(s))",
     "len", 3, "内置函数", ""),

    ("补全程序：判断输入的整数是奇数还是偶数。",
     "python",
     "n = int(input())\nif n ____ 2 == 0:\n    print('偶数')\nelse:\n    print('奇数')",
     "%", 4, "分支", "8"),

    ("补全程序：输出 1 到 100 中所有能被 7 整除的数。",
     "python",
     "for i in range(1, 101):\n    if i % 7 != 0:\n        ____\n    print(i)",
     "continue", 5, "循环", ""),

    ("补全程序：求两个正整数的最大公约数（辗转相除法）。",
     "python",
     "a = int(input())\nb = int(input())\nwhile b != 0:\n    a, b = b, a ____ b\nprint(a)",
     "%", 5, "算法", "12\n18"),

    ("补全程序：统计列表中及格（>=60）的人数。",
     "python",
     "a = [55, 72, 88, 43, 91]\ncnt = 0\nfor x in a:\n    if x ____ 60:\n        cnt = cnt + 1\nprint(cnt)",
     ">=", 4, "分支", ""),

    ("补全程序：输出一个由 * 组成的 n 行直角三角形。",
     "python",
     "n = int(input())\nfor i in range(1, n + 1):\n    print('*' ____ i)",
     "*", 4, "循环", "5"),

    ("补全程序：输入一个数，输出它的绝对值。",
     "python",
     "n = int(input())\nif n < 0:\n    n = ____\nprint(n)",
     "-n|-1*n|n*-1", 4, "分支", "-7"),

    ("补全程序：用列表推导式生成 1 到 10 的平方列表。",
     "python",
     "a = [x ____ 2 for x in range(1, 11)]\nprint(a)",
     "**", 4, "列表", ""),

    ("补全程序：计算 1 到 n 中所有奇数的乘积（n 为奇数）。",
     "python",
     "n = int(input())\np = 1\nfor i in range(1, n + 1, 2):\n    p = p ____ i\nprint(p)",
     "*", 4, "循环", "5"),
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
    # 理论题
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

    # 程序填空
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

    widths = (10, 46, 20, 20, 20, 20, 22, 7, 12, 9, 52, 14)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:L{row - 1}"

    wb.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="生成初高中信息技术题库")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "题库_初高中信息技术.xlsx")
    build(out)

    from collections import Counter
    ct = Counter(q[0] for q in THEORY)
    cat = Counter(q[8] for q in THEORY)
    total = sum(q[7] for q in THEORY) + sum(c[4] for c in CODE)
    print(f"✅ 已生成: {out}")
    print(f"   理论题 {len(THEORY)} 题  " + "  ".join(f"{k}{v}" for k, v in ct.items()))
    print(f"   程序填空 {len(CODE)} 题（Python，可本地运行）")
    print(f"   合计 {len(THEORY) + len(CODE)} 题，总分 {total}")
    print("   分类: " + "  ".join(f"{k}{v}" for k, v in cat.items()))
    print("\n导入：后台 → 题库 → 导入 Excel")


if __name__ == "__main__":
    main()
