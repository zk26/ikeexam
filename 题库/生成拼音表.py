# -*- coding: utf-8 -*-
"""
生成/扩充「拼音常用字表.json」——打名字测试用。

旧表只收录了 188 个常用字，学生名字里的字稍微少见一点就查不到，
拼音提示只能显示「?」。这个脚本用 pypinyin 把基本汉字区（U+4E00~U+9FFF）
全部补齐，并生成多音字清单；原来人工校对过的读音**优先保留**。

另外补一份「姓氏读音」：多音字做姓氏时要按姓氏读音（曾→zēng、解→xiè），
否则会把学生的名字读错。

用法：python 生成拼音表.py        （需要 pip install pypinyin，只生成时用，
生成完的 JSON 是纯数据，服务器运行时不需要 pypinyin）
"""
import base64
import json
import os
import zlib

from pypinyin import Style, pinyin

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "拼音常用字表.json")

# 「常提醒」多音字精选名单：只提醒这些真正常读错的字。
# pypinyin 的异读清单把文白异读全算了（6000+ 字），全量提醒会让几乎每个
# 名字都标「多音字」，提醒就没人看了。宁缺毋滥。
MULTI = set(
    "长重朝行乐曾解查仇单朴区覃翟繁燕员秘洗折都缪句盖尉种召华过纪"
    "少大了着说为相应便教觉空落难切塞省盛数宿血要与载脏只中传调分"
    "干更好还几假将降角尽看量露论脉闷磨宁泊奇强曲散厦扇率挑帖系巷"
    "削兴旋咽叶殷扎涨正属转仔钻阿蒙於语晕择艾柏膀磅刨臂扁屏伯"
    "便辟扁泊参差禅颤朝称冲臭处揣创答当倒得地丁都度恶蕃菲否夫杆"
    "给枸骨冠哈号和横虹划晃会稽几系茄间将嚼角脚剿觉解禁劲矩卡看"
    "壳可空蓝姥擂累蠡俩量撩淋令溜笼搂露绿落络脉蔓没秘缪模磨摩难"
    "宁弄疟哦胖刨炮喷片漂朴曝期栖蹊奇契洽茜强抢切亲曲圈嚷散丧扫"
    "莎煞扇上舍拾似熟术沓踏台苔提挑帖通同拓哇万亡为圩尾尉纹涡洗"
    "系虾吓纤相巷削芯兴宿熏压轧咽殷荫柚予吁约钥晕载脏炸粘涨着症"
    "殖中种轴属著转幢椎啄仔钻作"
)

# 多音字姓氏的「姓氏读音」（和常见读音不同的才列；两字为复姓）
SURNAME = {
    "曾": "zēng", "解": "xiè", "查": "zhā", "仇": "qiú", "单": "shàn",
    "乐": "yuè", "朴": "piáo", "区": "ōu", "覃": "qín", "翟": "zhái",
    "繁": "pó", "燕": "yān", "员": "yùn", "秘": "bì", "洗": "xiǎn",
    "折": "shé", "都": "dū", "缪": "miào", "句": "gōu", "盖": "gě",
    "尉": "yù", "种": "chóng", "召": "shào", "华": "huà", "过": "guō",
    "纪": "jǐ", "阚": "kàn", "乜": "niè", "撒": "sà", "能": "nài",
    "阿": "ē", "隽": "juàn", "贾": "jiǎ", "仇": "qiú",
    # 复姓（两字）
    "万俟": "mò qí", "尉迟": "yù chí", "长孙": "zhǎng sūn",
    "单于": "chán yú", "澹台": "tán tái", "乐正": "yuè zhèng",
    "宰父": "zǎi fǔ", "中行": "zhōng háng",
}


def main():
    data = {}
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    zh = {str(k): str(v) for k, v in (data.get("字") or {}).items()}
    old_n = len(zh)

    for cp in range(0x4E00, 0xA000):
        ch = chr(cp)
        if ch in zh:
            continue                      # 人工校对过的读音优先
        try:
            prons = pinyin(ch, heteronym=True, style=Style.TONE)[0]
        except Exception:
            continue
        prons = [p for p in prons if p]
        if not prons:
            continue
        zh[ch] = prons[0]

    # 多音提醒 = 精选名单（只提醒真正常读错的，避免满屏误报）
    duoyin = MULTI & set(zh)

    data["字"] = zh
    data["多音"] = sorted(duoyin)
    data["姓氏"] = SURNAME
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    # 再生成一份内置兜底数据（server/pinyin_data.py）：
    # 老师电脑 exe 旁边可能是旧版字表（188 字），升级包不覆盖旧文件时
    # 名字照样能解析。JSON 表仍然优先（老师可以自己改）。
    out_py = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "server", "pinyin_data.py")
    blob = base64.b64encode(
        zlib.compress(json.dumps(data, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
    ).decode("ascii")
    with open(os.path.abspath(out_py), "w", encoding="utf-8") as f:
        f.write(
            '# -*- coding: utf-8 -*-\n'
            '"""\n'
            '内置汉字拼音表（生成拼音表.py 自动产出，别手改）。\n'
            '老师端 exe 旁边的 题库/拼音常用字表.json 缺字时用它兜底，\n'
            '保证「可、龘、镕」这类字都能拼出拼音。\n'
            '"""\n'
            "import base64, json, zlib\n\n"
            f'_BLOB = "{blob}"\n\n'
            "def builtin():\n"
            '    """-> {"字": {...}, "多音": [...], "姓氏": {...}}"""\n'
            "    return json.loads(zlib.decompress(base64.b64decode(_BLOB)))\n")
    print(f"完成：{old_n} 字 → {len(zh)} 字，多音字 {len(duoyin)} 个，"
          f"姓氏读音 {len(SURNAME)} 条；内置兜底已写 server/pinyin_data.py")


if __name__ == "__main__":
    main()
