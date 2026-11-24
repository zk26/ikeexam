"""
考试包解析与自动评分
把「题目文件(考试包)」和「评分器」接起来
"""
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from graders import grader_for  # noqa: E402


# ---------------- 考试包 ----------------
def load_pack(path):
    """
    读取考试包，支持：
      .json  完整考试包
      .yaml  之前那份评分规则（缺题干，用默认填充）
    """
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    if ext == ".json":
        pack = json.loads(text)
    elif ext in (".yaml", ".yml"):
        import yaml
        cfg = yaml.safe_load(text)
        ex = cfg.get("exam", {})
        pack = {
            "title": ex.get("name", os.path.basename(path)),
            "etype": ex.get("type", "word"),
            "duration": 60,
            "requirement": "（本题目由评分规则导入，请在后台补全考试要求）",
            "materials": [],
            "save_as": "",
            "checks": cfg.get("checks", []),
        }
    else:
        raise ValueError(f"不支持的题目文件: {ext}（支持 .json / .yaml）")

    # 校验
    if pack.get("etype") not in ("word", "excel", "ppt"):
        raise ValueError(f"etype 必须是 word/excel/ppt，当前: {pack.get('etype')}")
    if not pack.get("checks"):
        raise ValueError("没有评分点（checks 为空）")
    return pack


def pack_to_json(pack):
    return json.dumps(pack, ensure_ascii=False, indent=2)


def make_template(etype):
    """生成考试包模板"""
    common = {
        "title": f"WPS{'文字' if etype == 'word' else '表格' if etype == 'excel' else '演示'}操作考试",
        "etype": etype,
        "duration": 60,
        "requirement": "1. 打开下发的素材文件\n2. 按要求完成操作\n3. 保存并按指定文件名提交",
        "materials": [],
        "save_as": "学号_姓名." + ("docx" if etype == "word" else "xlsx" if etype == "excel" else "pptx"),
        "checks": [],
    }
    if etype == "word":
        common["checks"] = [
            {"id": "标题字体", "type": "font", "contains": "标题关键词",
             "expect": {"name": "黑体", "size": "二号", "bold": True, "color": "橙色"},
             "score": 20},
            {"id": "正文段落", "type": "paragraph", "contains": "正文关键词",
             "expect": {"align": "居中", "first_line_indent": 24, "line_spacing": 1.5},
             "score": 15},
            {"id": "纸张", "type": "page_setup",
             "expect": {"width": 21.0, "height": 29.7, "orientation": "portrait"},
             "score": 10},
        ]
    elif etype == "excel":
        common["checks"] = [
            {"id": "总分公式", "type": "formula", "cell": "E2",
             "expect": {"function": "SUM"}, "score": 20},
            {"id": "平均分公式", "type": "formula", "cell": "F2",
             "expect": {"function": ["AVERAGE", "ROUND"]}, "score": 15},
            {"id": "表头字体", "type": "font", "range": "A1:F1",
             "expect": {"name": "黑体", "size": 14, "bold": True}, "score": 15},
            {"id": "图表", "type": "chart", "expect": {"count": 1}, "score": 10},
        ]
    else:
        common["checks"] = [
            {"id": "页数", "type": "slide_count", "count": 5, "score": 15},
            {"id": "首页标题", "type": "title", "index": 0, "text": "标题文字", "score": 15},
            {"id": "字体", "type": "font", "index": 0,
             "expect": {"name": "微软雅黑", "size": 40, "bold": True}, "score": 15},
            {"id": "图片", "type": "image", "count": 1, "score": 10},
        ]
    return common


# ---------------- 评分 ----------------
def grade_file(file_path, checks):
    """对单个学生文件评分"""
    g = grader_for(file_path)
    total, detail = g.run(checks)
    full = sum(float(c.get("score", 0)) for c in checks)
    return round(total, 2), round(full, 2), detail


def grade_exam(exam_id, sub_dir):
    """
    批量评分某场考试
    :return: (成功数, 失败数, 汇总行)
    """
    import db
    exam = db.get_exam(exam_id)
    if not exam:
        raise ValueError("考试不存在")
    subs = db.list_submissions(exam_id)

    ok = fail = 0
    rows = []
    for s in subs:
        path = s["saved_path"]
        if not path or not os.path.isfile(path):
            fail += 1
            rows.append({"hostname": s["hostname"], "score": 0, "full": 0, "msg": "文件缺失"})
            continue
        # 多题考试按每道题各自的评分规则，别统一套 exams.checks
        checks = db.checks_for_submission(exam, s)
        try:
            total, full, detail = grade_file(path, checks)
            db.update_score(s["id"], total, full, detail)
            ok += 1
            rows.append({"hostname": s["hostname"], "score": total, "full": full, "msg": "已评分"})
        except Exception as e:
            fail += 1
            # 不覆盖已有成绩 —— 重评分失败时保留上次分数，只记日志
            try:
                db.log(s["hostname"], "error", f"重评分失败: {e}")
            except Exception:
                pass
            rows.append({"hostname": s["hostname"], "score": s["score"] or 0,
                         "full": s["full"] or 0, "msg": f"评分出错（保留原成绩）: {e}"})
    return ok, fail, rows


def export_csv(exam_id, out_path):
    """导出成绩 CSV"""
    import csv
    import db
    subs = db.list_submissions(exam_id)
    exam = db.get_exam(exam_id)
    checks = json.loads(exam["checks"] or "[]")
    ids = [c.get("id", f"第{i+1}项") for i, c in enumerate(checks)]

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["机器名"] + ids + ["总分", "满分", "得分率"])
        for s in subs:
            detail = json.loads(s["detail"] or "[]")
            dmap = {d["id"]: d["got"] for d in detail}
            line = [s["hostname"]] + [dmap.get(i, "") for i in ids]
            full = s["full"] or 0
            score = s["score"]
            # full 或 score 为空时都不做除法（此前只防了 full==0，score=None 会 TypeError）
            rate = f"{score / full * 100:.1f}%" if (full and score is not None) else "-"
            line += [score if score is not None else "", full, rate]
            w.writerow(line)
    return out_path


def collect_zip(exam_id, out_path, from_dir=None, exam_code="", klass=""):
    """
    打包学生答卷。

    默认：按 submissions 表收集本场考试的所有答卷（zip 里叫 机器名_文件名）。

    :param from_dir:  指定归档目录时，直接从目录里收本场考试的文件
                      （文件名以「编号_」开头），zip 里保留「班级/文件名」结构 ——
                      老师要的是归档目录里的原件，可能还有手工放进去的补充文件。
    :param exam_code: 本场考试的编号，用来从班级目录里挑出属于这一场的答卷。
    :param klass:     班级名，只用于 zip 内的目录名。
    """
    import db
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        if from_dir and os.path.isdir(from_dir) and exam_code:
            prefix = f"{exam_code}_"
            sub = os.path.basename(from_dir) if not klass else klass
            for name in sorted(os.listdir(from_dir)):
                p = os.path.join(from_dir, name)
                if os.path.isfile(p) and name.startswith(prefix):
                    z.write(p, f"{sub}/{name}")
            return out_path
        subs = db.list_submissions(exam_id)
        for s in subs:
            if s["saved_path"] and os.path.isfile(s["saved_path"]):
                z.write(s["saved_path"], f"{s['hostname']}_{s['filename']}")
        # 画板作品（一张压缩图）一并打包：画板/{机器名}_画板.png
        for d in db.drawings_for(exam_id):
            if d.get("saved_path") and os.path.isfile(d["saved_path"]):
                name = d.get("filename") or "画板.png"
                z.write(d["saved_path"], f"画板/{d['hostname']}_{name}")
    return out_path
