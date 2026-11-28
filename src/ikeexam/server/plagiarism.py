#!/usr/bin/env python3
"""
答卷防抄袭检测
--------------------------------
把一场考试的全部答卷放在一起两两比对，找出异常相似的组合。

用法：
  # 检测某场考试
  python3 plagiarism.py --exam 1

  # 或指定目录
  python3 plagiarism.py --dir uploads/exam1 --type excel

  # 调整阈值
  python3 plagiarism.py --exam 1 --high 0.90 --warn 0.75

输出：
  控制台列出可疑组合 + exams/抄袭检测_exam1.csv

⚠️ 重要：学生做同一道题，答案本来就会相似！
   本工具是「线索」不是「证据」，高分组合需要老师人工复核。
"""
import argparse
import csv
import difflib
import os
import sys
from itertools import combinations

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server"))

EXTS = {"word": ".docx", "excel": ".xlsx", "ppt": ".pptx"}
HIGH_DEFAULT = 0.90      # 高度可疑
WARN_DEFAULT = 0.75      # 值得关注
MAX_SUBMISSIONS = 200
MAX_PAIRS = 5000
MAX_TEXT_CHARS = 200000
MAX_TOTAL_TEXT_CHARS = 8000000
MAX_PAIR_TEXT_CHARS = 200000
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def csv_safe_value(value):
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value

# 判定文案：纯文字，不带 emoji。
# 这些值会出现在网页表格和导出的 CSV 里 —— emoji 在 CSV（WPS 打开）里
# 会变成方框，在网页上也和统一图标风格冲突。颜色/圆点由页面自己加。
VERDICT = {
    "hard": "内容完全相同（严重）",
    "high": "显著高于平均（严重）",
    "warn": "略高于平均（注意）",
    "ok":   "正常",
}


# ==================== 指纹提取 ====================
def _validate_document(path):
    import zipfile
    try:
        from ikeexam.graders.base import validate_ooxml
    except ImportError:
        from graders.base import validate_ooxml
    with zipfile.ZipFile(path) as archive:
        validate_ooxml(archive)


def fp_word(path):
    """WPS文字：文本 + 结构"""
    _validate_document(path)
    from docx import Document
    doc = Document(path)
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    tables = []
    for t in doc.tables:
        for row in t.rows:
            tables.append([c.text.strip() for c in row.cells])

    text = "\n".join(paras)
    for row in tables:
        text += "\n" + "|".join(row)

    # core：正文去空白后的紧凑串。完全相同 = 硬证据
    core = "".join("".join(paras).split())

    return {
        "text": text,
        "paras": paras,
        "core": core,
        "struct": {
            "段落数": len(doc.paragraphs),
            "表格数": len(doc.tables),
            "图片数": len(doc.inline_shapes),
        },
        "extra": " ".join(paras),
    }


def fp_excel(path):
    """WPS表格：单元格值 + 公式 + 结构"""
    _validate_document(path)
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=False, read_only=True)
    ws = wb.active

    cells, formulas = [], []
    for row in ws.iter_rows():
        for c in row:
            if c.value is None:
                continue
            cells.append(f"{c.coordinate}={c.value}")
            if isinstance(c.value, str) and c.value.startswith("="):
                formulas.append(f"{c.coordinate}{c.value.upper().replace(' ', '')}")

    # core：排除表头行(第1行)和首列(姓名)，剩下的才是「学生自己的作答」
    core_parts = []
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if c.column == 1:        # 第一列通常是姓名，天然不同
                continue
            if c.value is not None:
                core_parts.append(f"{c.coordinate}={c.value}")
    core = "".join("".join(core_parts).split())

    text = "\n".join(cells)
    return {
        "text": text,
        "paras": cells,
        "core": core,
        "struct": {
            "行数": ws.max_row,
            "列数": ws.max_column,
            "公式数": len(formulas),
            "图表数": len(getattr(ws, "_charts", [])),
        },
        "extra": " ".join(formulas),
    }


def fp_ppt(path):
    """WPS演示：文本 + 结构"""
    _validate_document(path)
    from pptx import Presentation
    prs = Presentation(path)

    texts = []
    for sl in prs.slides:
        for sh in sl.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                texts.append(sh.text_frame.text.strip())

    core = "".join("".join(texts).split())
    return {
        "text": "\n".join(texts),
        "paras": texts,
        "core": core,
        "struct": {
            "页数": len(prs.slides),
        },
        "extra": " ".join(texts),
    }


FP = {"word": fp_word, "excel": fp_excel, "ppt": fp_ppt}


# ==================== 相似度 ====================
def sim_text(a, b):
    """文本相似度 0~1"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0   # 快速路径：全班答案高度相似时大量文本相等，免跑 O(n²)
    return difflib.SequenceMatcher(None, a, b).ratio()


def sim_struct(a, b):
    """结构相似度：各维度是否一致"""
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    same = sum(1 for k in keys if a.get(k) == b.get(k))
    return same / len(keys)


def sim_set(a, b):
    """集合相似度（Jaccard）：用于公式序列这类"""
    sa, sb = set(a.split()), set(b.split())
    if not sa and not sb:
        return 0.0   # 两个空集 = 无法判断相似，不该算成「完全相同」抬高综合分
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def compare(f1, f2):
    """
    综合相似度 + 硬证据标记
    返回 (score, parts, identical_core)
    """
    s_text = sim_text(f1["text"], f2["text"])
    s_struct = sim_struct(f1["struct"], f2["struct"])
    s_extra = sim_set(f1.get("extra", ""), f2.get("extra", ""))

    score = 0.70 * s_text + 0.15 * s_struct + 0.15 * s_extra

    # 硬证据：核心作答内容逐字节相同（且不是空内容）
    c1, c2 = f1.get("core", ""), f2.get("core", "")
    identical = bool(c1) and c1 == c2

    return round(score, 4), {
        "文本": round(s_text, 3),
        "结构": round(s_struct, 3),
        "公式内容": round(s_extra, 3),
    }, identical


def baseline(scores):
    """算出这批相似度的均值和标准差（操作题答案天然相似，必须看相对值）"""
    n = len(scores)
    if n == 0:
        return 0.0, 0.0
    mean = sum(scores) / n
    var = sum((s - mean) ** 2 for s in scores) / n
    return mean, var ** 0.5


# ==================== 主流程 ====================
def collect(dirpath, etype):
    """收集目录下的答卷"""
    ext = EXTS.get(etype, ".docx")
    out = []
    if not os.path.isdir(dirpath):
        return out
    for f in sorted(os.listdir(dirpath)):
        if f.lower().endswith(ext) and not f.startswith("~$"):
            out.append((f, os.path.join(dirpath, f)))
    return out


def run(files, etype, high=HIGH_DEFAULT, warn=WARN_DEFAULT,
        z_high=2.0, z_warn=1.0, verbose=True, *,
        max_submissions=MAX_SUBMISSIONS, max_pairs=MAX_PAIRS,
        max_text_chars=MAX_TEXT_CHARS, max_total_text_chars=MAX_TOTAL_TEXT_CHARS,
        max_pair_text_chars=MAX_PAIR_TEXT_CHARS):
    """
    :param files: [(显示名, 路径)]
    :return: (全部对, 可疑对, 基线信息)

    判定是「双轨」的：
      轨道一（硬证据）：核心作答内容完全相同 → 直接 🔴
      轨道二（相对基线）：比全班平均水平高出 z 个标准差 → 🟡/🔴

    为什么不能只看绝对相似度：同一道操作题，学生的答案天然就有 0.9+ 的相似度，
    用固定阈值会导致「全班都可疑」，等于没有检测。
    """
    max_submissions = max(0, int(max_submissions))
    max_pairs = max(0, int(max_pairs))
    max_text_chars = max(1, int(max_text_chars))
    max_total_text_chars = max(1, int(max_total_text_chars))
    max_pair_text_chars = max(1, int(max_pair_text_chars))
    unique = {}
    for name, path in sorted(files, key=lambda item: (str(item[0]), str(item[1]))):
        unique[name] = path
    selected = sorted(unique.items())[:max_submissions]
    pair_total = len(selected) * (len(selected) - 1) // 2
    info = {"mean": 0.0, "std": 0.0, "n": 0, "z_high": z_high,
            "z_warn": z_warn, "degenerate": True, "status": "complete",
            "submissions": len(selected), "unique_hosts": len(unique),
            "duplicates": len(files) - len(unique), "pair_total": pair_total,
            "compared": 0, "skipped_pairs": 0, "skipped_files": 0,
            "limits": {"submissions": max_submissions, "pairs": max_pairs,
                       "text_chars": max_text_chars}}
    if len(unique) > max_submissions or pair_total > max_pairs:
        info["status"] = "rejected"
        info["reason"] = "submission_or_pair_budget_exceeded"
        return [], [], info

    fp_fn = FP[etype]
    fps = {}
    total_chars = 0
    for name, path in selected:
        try:
            fp = fp_fn(path)
            size = len(fp.get("text") or "") + len(fp.get("core") or "")
            if size > max_text_chars or total_chars + size > max_total_text_chars:
                raise ValueError("text_budget_exceeded")
            fps[name] = fp
            total_chars += size
        except Exception as e:
            info["skipped_files"] += 1
            if verbose:
                print(f"  ⚠️ 跳过 {name}: {e}")

    names = sorted(fps)
    raw = []
    for a, b in combinations(names, 2):
        pair_chars = sum(len(fps[n].get(key) or "")
                         for n in (a, b) for key in ("text", "core"))
        if pair_chars > max_pair_text_chars:
            info["skipped_pairs"] += 1
            continue
        score, parts, identical = compare(fps[a], fps[b])
        raw.append({"a": a, "b": b, "score": score,
                    "相同内容": identical, "parts": parts})
    info["compared"] = len(raw)
    info["n"] = len(raw)
    if len(fps) < 2:
        info["status"] = "insufficient"
    elif info["skipped_files"] or info["skipped_pairs"]:
        info["status"] = "partial"

    mean, std = baseline([r["score"] for r in raw])
    # 标准差≈0 时相对基线失效：全班分数几乎一样，z 全趋近 0，会导致除了
    # 「内容完全相同」外无人被标记。此时退回绝对阈值（高阈值）兜底。
    degenerate = std <= 1e-9

    all_pairs, suspects = [], []
    for r in raw:
        score = r["score"]
        z = (score - mean) / std if not degenerate else 0.0

        row = {"a": r["a"], "b": r["b"], "score": score, "z": round(z, 2),
               "text": r["parts"]["文本"], "struct": r["parts"]["结构"],
               "formula": r["parts"]["公式内容"],
               "identical": bool(r["相同内容"])}

        if r["相同内容"]:
            row["verdict"] = "hard"
        elif degenerate:
            # 相对基线不可用，用绝对阈值兜底（高阈值，只标接近全同的）
            if score >= high:
                row["verdict"] = "high"
            elif score >= warn:
                row["verdict"] = "warn"
            else:
                row["verdict"] = "ok"
        elif z >= z_high:
            row["verdict"] = "high"
        elif z >= z_warn:
            row["verdict"] = "warn"
        else:
            row["verdict"] = "ok"

        all_pairs.append(row)
        if row["verdict"] != "ok":
            suspects.append(row)

    all_pairs.sort(key=lambda x: -x["score"])
    suspects.sort(key=lambda x: -x["score"])
    info.update({"mean": round(mean, 4), "std": round(std, 4),
                 "degenerate": degenerate})
    return all_pairs, suspects, info


def report(all_pairs, suspects, out_csv=None, top=20, info=None):
    print("\n" + "=" * 66)
    print(" 抄袭检测报告")
    print("=" * 66)
    if info:
        print(f"比对组合: {info['n']} 对   全班平均相似度 {info['mean']:.3f}"
              f"   标准差 {info['std']:.3f}")
        if info.get("degenerate"):
            print("  ⚠️ 全班相似度高度一致（标准差≈0），相对基线失效，已改用绝对阈值判定")
        print(f"判定标准: 内容完全相同 → 🔴；"
              f"高出平均 {info['z_high']}σ → 🔴；"
              f"高出 {info['z_warn']}σ → 🟡")

    hard = [x for x in suspects if x["verdict"] == "hard"]
    high = [x for x in suspects if x["verdict"] == "high"]
    warn = [x for x in suspects if x["verdict"] == "warn"]
    print(f"🔴 内容完全相同: {len(hard)} 对    "
          f"🔴 显著高于平均: {len(high)} 对    🟡 略高于平均: {len(warn)} 对")

    if suspects:
        print(f"\n--- 可疑组合（最多 {top} 条）---")
        print(f"{'学生A':<24}{'学生B':<24}{'综合':>7}{'偏离':>7}{'全同':>6}  判定")
        for s in suspects[:top]:
            print(f"{s['a'][:22]:<24}{s['b'][:22]:<24}"
                  f"{s['score']:>7.3f}{s['z']:>7.2f}"
                  f"{'是' if s['identical'] else '-':>6}  {VERDICT[s['verdict']]}")
    else:
        print("\n✅ 未发现异常相似的组合")

    print("\n--- 相似度最高的 5 对（供参考）---")
    for s in all_pairs[:5]:
        print(f"  {s['a'][:22]:<24}{s['b'][:22]:<24}"
              f"{s['score']:>7.3f}  偏离{s['z']:>6.2f}σ")

    if out_csv:
        os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["学生A", "学生B", "综合相似度",
                                              "偏离(σ)", "文本", "结构",
                                              "公式内容", "内容完全相同", "判定"])
            w.writeheader()
            for r in all_pairs:
                w.writerow({"学生A": csv_safe_value(r["a"]),
                            "学生B": csv_safe_value(r["b"]),
                            "综合相似度": r["score"], "偏离(σ)": r["z"],
                            "文本": csv_safe_value(r["text"]),
                            "结构": csv_safe_value(r["struct"]),
                            "公式内容": csv_safe_value(r["formula"]),
                            "内容完全相同": "是" if r["identical"] else "",
                            "判定": VERDICT[r["verdict"]]})
        print(f"\n✅ 完整报告: {out_csv}")

    print("\n⚠️  提醒：操作题答案天然相似，绝对相似度高不等于抄袭。")
    print("    「内容完全相同」是最硬的证据；其余请人工复核后再下结论。")


def main():
    p = argparse.ArgumentParser(description="答卷防抄袭检测")
    p.add_argument("--exam", type=int, help="考试 ID（从数据库取）")
    p.add_argument("--dir", help="答卷目录")
    p.add_argument("--type", choices=list(EXTS), help="文件类型（--dir 时用）")
    p.add_argument("--high", type=float, default=HIGH_DEFAULT)
    p.add_argument("--warn", type=float, default=WARN_DEFAULT)
    p.add_argument("--z", type=float, default=2.0,
                   help="偏离均值多少个标准差算可疑（默认 2.0）")
    p.add_argument("--out", help="输出 CSV 路径")
    a = p.parse_args()

    if a.exam:
        import db
        db.init()
        ex = db.get_exam(a.exam)
        if not ex:
            print(f"考试 #{a.exam} 不存在")
            sys.exit(1)
        etype = ex["etype"]
        subs = db.list_submissions(a.exam)
        files = [(s["hostname"], s["saved_path"]) for s in subs
                 if s["saved_path"] and os.path.isfile(s["saved_path"])]
        out = a.out or os.path.join(ROOT, "exams", f"抄袭检测_exam{a.exam}.csv")
        title = ex["title"]
    elif a.dir:
        if not a.type:
            print("--dir 模式必须指定 --type")
            sys.exit(1)
        etype = a.type
        files = collect(a.dir, etype)
        out = a.out or os.path.join(ROOT, "exams", "抄袭检测.csv")
        title = a.dir
    else:
        print("必须指定 --exam 或 --dir")
        sys.exit(1)

    if len(files) < 2:
        print(f"只有 {len(files)} 份答卷，无法比对（至少需要 2 份）")
        sys.exit(0)

    print(f"考试: {title}")
    print(f"类型: {etype}")
    print(f"答卷: {len(files)} 份 → {len(files)*(len(files)-1)//2} 个组合")

    all_pairs, suspects, info = run(files, etype, a.high, a.warn,
                                     z_high=a.z, z_warn=a.z * 0.5)
    report(all_pairs, suspects, out, info=info)


if __name__ == "__main__":
    main()
