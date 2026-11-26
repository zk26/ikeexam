# -*- coding: utf-8 -*-
"""老师端可视化配置控件。"""
import tkinter as tk
from tkinter import ttk

from ikeexam.shared.config_schema import RULE_TYPES, RULE_TYPE_MAP, TYPING_TASKS


class ScrollableFrame(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                background=kwargs.get("background", "#FFFFFF"))
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda event: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(
            self._window, width=event.width))
        self._bind_wheel()

    def _bind_wheel(self):
        from ikeexam.shared.ui_theme import bind_mousewheel
        bind_mousewheel(self.canvas, self.canvas)
        bind_mousewheel(self.inner, self.canvas, recursive=True)

    def controls(self):
        return self.inner.winfo_children()


class RuleEditor(ScrollableFrame):
    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_change = on_change
        self.rows = []
        self.controls = []

    def _changed(self):
        if self.on_change:
            self.on_change()

    def _field_widget(self, parent, field, value):
        frame = ttk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=field["label"], style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        kind = field["kind"]
        if kind == "bool":
            var = tk.StringVar(value="" if value is None else str(value).lower())
            widget = ttk.Combobox(frame, textvariable=var, state="readonly", width=10,
                                  values=("", "true", "false"))
        elif kind == "select":
            var = tk.StringVar(value="" if value is None else str(value))
            widget = ttk.Combobox(frame, textvariable=var, state="readonly", width=16,
                                  values=("",) + tuple(field.get("options") or []))
        elif kind == "textarea":
            var = None
            widget = tk.Text(frame, height=3, wrap="word", font=("Microsoft YaHei", 9))
            widget.insert("1.0", "" if value is None else str(value))
        else:
            var = tk.StringVar(value="" if value is None else str(value))
            widget = ttk.Entry(frame, textvariable=var)
        widget.grid(row=0, column=1, sticky="ew")
        self.controls.append(widget)
        if var is not None:
            var.trace_add("write", lambda *args: self._changed())
        else:
            widget.bind("<KeyRelease>", lambda event: self._changed())
        return frame, widget, var

    @staticmethod
    def _value(rule, field):
        if field["key"] == "cell_row":
            return (rule.get("expect") or {}).get("cell", ["", "", ""])[0]
        if field["key"] == "cell_col":
            return (rule.get("expect") or {}).get("cell", ["", "", ""])[1]
        if field["key"] == "cell_text":
            return (rule.get("expect") or {}).get("cell", ["", "", ""])[2]
        box = rule if field.get("scope") == "rule" else (rule.get("expect") or {})
        value = box.get(field["key"], "")
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return value

    def _insert_row(self, index, rule):
        spec = RULE_TYPE_MAP.get(rule.get("type"), RULE_TYPES[0])
        outer = ttk.LabelFrame(self.inner, text=f"评分点 {index + 1}", padding=8)
        outer.grid(row=index, column=0, sticky="ew", pady=(0, 8))
        outer.columnconfigure(0, weight=1)
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(head, text="名称").pack(side="left")
        id_var = tk.StringVar(value=rule.get("id") or f"评分点{index + 1}")
        id_entry = ttk.Entry(head, textvariable=id_var, width=18)
        id_entry.pack(side="left", padx=4)
        ttk.Label(head, text="类型").pack(side="left", padx=(8, 0))
        type_var = tk.StringVar(value=spec["label"])
        type_box = ttk.Combobox(head, textvariable=type_var, state="readonly", width=16,
                                values=tuple(item["label"] for item in RULE_TYPES))
        type_box.pack(side="left", padx=4)
        ttk.Label(head, text=spec["app"], style="Muted.TLabel").pack(side="left", padx=6)
        actions = ttk.Frame(head)
        actions.pack(side="right")
        up_button = ttk.Button(actions, text="↑", width=3,
                               command=lambda: self._move(index, -1))
        down_button = ttk.Button(actions, text="↓", width=3,
                                 command=lambda: self._move(index, 1))
        delete_button = ttk.Button(actions, text="×", width=3,
                                   command=lambda: self._delete(index))
        up_button.pack(side="left")
        down_button.pack(side="left")
        delete_button.pack(side="left")
        self.controls.extend((id_entry, type_box, up_button, down_button,
                              delete_button))
        id_var.trace_add("write", lambda *args: self._changed())

        common = ttk.Frame(outer)
        common.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(common, text="分值").pack(side="left")
        score_var = tk.StringVar(value=str(rule.get("score", 0)))
        score_entry = ttk.Entry(common, textvariable=score_var, width=8)
        score_entry.pack(side="left", padx=4)
        ttk.Label(common, text="部分得分（可选）").pack(side="left", padx=(8, 0))
        partial_var = tk.StringVar(value="" if rule.get("partial") is None
                                   else str(rule.get("partial")))
        partial_entry = ttk.Entry(common, textvariable=partial_var, width=8)
        partial_entry.pack(side="left", padx=4)
        self.controls.extend((score_entry, partial_entry))
        score_var.trace_add("write", lambda *args: self._changed())
        partial_var.trace_add("write", lambda *args: self._changed())

        body = ttk.Frame(outer)
        body.grid(row=2, column=0, sticky="ew")
        fields = list(spec.get("locators") or []) + list(spec.get("fields") or [])
        widgets = []
        for field_index, field in enumerate(fields):
            field_frame, widget, var = self._field_widget(
                body, field, self._value(rule, field))
            field_frame.grid(row=field_index // 2, column=field_index % 2,
                             sticky="ew", padx=(0, 8), pady=4)
            widgets.append((field, widget, var))

        row = {"outer": outer, "spec": spec, "id": id_var, "type": type_var,
               "score": score_var, "partial": partial_var, "widgets": widgets}
        self.rows.insert(index, row)

        def choose_type(*args):
            current = self._read_row(row)
            label = type_var.get()
            selected = next((item for item in RULE_TYPES if item["label"] == label),
                            RULE_TYPES[0])
            current["type"] = selected["value"]
            position = self.rows.index(row)
            self.rows.pop(position)
            outer.destroy()
            self._insert_row(position, current)
            self._renumber()
            self._changed()

        type_var.trace_add("write", choose_type)

    def _renumber(self):
        for index, row in enumerate(self.rows):
            row["outer"].configure(text=f"评分点 {index + 1}")
            row["id"].set(row["id"].get() or f"评分点{index + 1}")
        for index, row in enumerate(self.rows):
            row["outer"].grid(row=index, column=0, sticky="ew", pady=(0, 8))
        self._bind_wheel()

    def _read_row(self, row):
        rule = {"id": row["id"].get().strip() or "评分点",
                "type": row["spec"]["value"]}
        try:
            rule["score"] = float(row["score"].get() or 0)
        except ValueError:
            rule["score"] = 0
        if row["partial"].get().strip():
            try:
                rule["partial"] = float(row["partial"].get())
            except ValueError:
                pass
        spec = row["spec"]
        table_values = {}
        for field, widget, var in row["widgets"]:
            if field["key"].startswith("cell_"):
                table_values[field["key"]] = widget.get()
                continue
            raw = widget.get("1.0", "end").strip() if var is None else var.get()
            if field["kind"] == "number":
                if raw == "":
                    continue
                try:
                    value = float(raw)
                    if value.is_integer():
                        value = int(value)
                except ValueError:
                    continue
            elif field["kind"] == "list":
                value = [part for part in raw.replace("，", ",").replace(
                    " ", ",").split(",") if part]
                if not value:
                    continue
            elif field["kind"] == "bool":
                if raw == "":
                    continue
                value = raw == "true"
            else:
                if raw == "":
                    continue
                value = raw
            if field.get("scope") == "rule":
                rule[field["key"]] = value
            else:
                rule.setdefault("expect", {})[field["key"]] = value
        if rule["type"] == "table" and any(table_values.values()):
            try:
                row_no = int(table_values.get("cell_row") or 0)
            except ValueError:
                row_no = 0
            try:
                col_no = int(table_values.get("cell_col") or 0)
            except ValueError:
                col_no = 0
            rule.setdefault("expect", {})["cell"] = [
                row_no, col_no, table_values.get("cell_text", "")]
        return rule

    def _move(self, index, delta):
        target = index + delta
        if target < 0 or target >= len(self.rows):
            return
        self.rows[index], self.rows[target] = self.rows[target], self.rows[index]
        self._renumber()
        self._changed()

    def _delete(self, index):
        self.rows.pop(index)["outer"].destroy()
        self._renumber()
        self._changed()

    def set_rules(self, rules):
        for row in self.rows:
            row["outer"].destroy()
        self.rows = []
        for rule in rules or []:
            self._insert_row(len(self.rows), dict(rule))
        self._renumber()

    def get_rules(self):
        return [self._read_row(row) for row in self.rows]


class TypingConfigEditor(ScrollableFrame):
    DEFAULTS = {
        "whack_mole": {"enabled": True, "level": "all", "rounds": 8, "grid": "3x3"},
        "key_press": {"enabled": True, "rounds": 10, "seconds": 5, "keys": []},
        "case_press": {"enabled": True, "rounds": 10, "seconds": 5, "keys": []},
        "typing_en": {"enabled": True, "seconds": 180, "text": ""},
        "typing_zh": {"enabled": True, "seconds": 180, "text": ""},
        "name_typing": {"enabled": True, "rounds": 5, "seconds": 0},
    }

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_change = on_change
        self.task_vars = {}
        self.fields = {}
        self.controls = []
        self.inner.columnconfigure(0, weight=1)
        for index, task in enumerate(TYPING_TASKS):
            box = ttk.LabelFrame(self.inner, text=task["label"], padding=8)
            box.grid(row=index, column=0, sticky="ew", pady=(0, 8))
            enabled = tk.BooleanVar(value=True)
            enabled_check = ttk.Checkbutton(box, text="本场考试启用",
                                            variable=enabled)
            enabled_check.grid(row=0, column=0, sticky="w", pady=(0, 6))
            self.task_vars[task["key"]] = enabled
            self.controls.append(enabled_check)
            enabled.trace_add("write", lambda *args: self._changed())
            body = ttk.Frame(box)
            body.grid(row=1, column=0, sticky="ew")
            body.columnconfigure(1, weight=1)
            body.columnconfigure(3, weight=1)
            self.fields[task["key"]] = []
            for field_index, field in enumerate(task["fields"]):
                target = body
                row_no = field_index // 2
                col_no = (field_index % 2) * 2
                if field["kind"] == "textarea":
                    ttk.Label(target, text=field["label"]).grid(
                        row=row_no, column=col_no, sticky="w")
                    widget = tk.Text(target, height=4, wrap="word",
                                     font=("Microsoft YaHei", 9))
                    widget.grid(row=row_no + 1, column=col_no, columnspan=2,
                                sticky="ew", pady=(2, 6))
                    var = None
                    widget.bind("<KeyRelease>", lambda event: self._changed())
                else:
                    ttk.Label(target, text=field["label"]).grid(
                        row=row_no, column=col_no, sticky="w", padx=(0, 6))
                    var = tk.StringVar()
                    if field["kind"] == "select":
                        widget = ttk.Combobox(target, textvariable=var,
                                              state="readonly", width=12,
                                              values=tuple(field.get("options") or []))
                    else:
                        widget = ttk.Entry(target, textvariable=var, width=14)
                    widget.grid(row=row_no, column=col_no + 1, sticky="ew",
                                padx=(0, 12), pady=3)
                    var.trace_add("write", lambda *args: self._changed())
                self.controls.append(widget)
                self.fields[task["key"]].append((field, widget, var))
        self.set_config({})

    def _changed(self):
        if self.on_change:
            self.on_change()

    def set_config(self, config):
        config = config or {}
        for task in TYPING_TASKS:
            values = dict(self.DEFAULTS[task["key"]])
            values.update(config.get(task["key"]) or {})
            self.task_vars[task["key"]].set(bool(values.get("enabled", True)))
            for field, widget, var in self.fields[task["key"]]:
                value = values.get(field["key"], "")
                if isinstance(value, list):
                    value = ", ".join(str(item) for item in value)
                if var is None:
                    widget.delete("1.0", "end")
                    widget.insert("1.0", str(value or ""))
                else:
                    var.set(str(value or ""))
        self._bind_wheel()

    def get_config(self):
        result = {}
        for task in TYPING_TASKS:
            item = {"enabled": bool(self.task_vars[task["key"]].get())}
            for field, widget, var in self.fields[task["key"]]:
                raw = widget.get("1.0", "end").strip() if var is None else var.get().strip()
                if field["kind"] == "number":
                    try:
                        value = int(float(raw))
                    except ValueError:
                        continue
                elif field["kind"] == "list":
                    value = [part for part in raw.replace("，", ",").replace(
                        " ", ",").split(",") if part]
                else:
                    value = raw
                item[field["key"]] = value
            result[task["key"]] = item
        return result


class DrawingConfigEditor(ttk.Frame):
    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_change = on_change
        self.controls = []
        self.draw_var = tk.BooleanVar(value=True)
        self.beads_var = tk.BooleanVar(value=False)
        self.grid_var = tk.StringVar(value="28")
        draw_check = ttk.Checkbutton(self, text="画板创作（自由画）",
                                     variable=self.draw_var)
        draw_check.grid(row=0, column=0, sticky="w", pady=4)
        beads_check = ttk.Checkbutton(self, text="拼豆题", variable=self.beads_var)
        beads_check.grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(self, text="拼豆格子数").grid(row=1, column=0, sticky="w", pady=4)
        grid_spin = ttk.Spinbox(self, from_=16, to=36,
                                textvariable=self.grid_var, width=8)
        grid_spin.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(self, text="画题要求").grid(row=2, column=0, columnspan=2,
                                               sticky="w", pady=(8, 2))
        self.brief = tk.Text(self, height=8, wrap="word", font=("Microsoft YaHei", 9))
        self.brief.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)
        self.controls.extend((draw_check, beads_check, grid_spin, self.brief))
        for var in (self.draw_var, self.beads_var, self.grid_var):
            var.trace_add("write", lambda *args: self._changed())
        self.brief.bind("<KeyRelease>", lambda event: self._changed())

    def _changed(self):
        if self.on_change:
            self.on_change()

    def set_config(self, config):
        config = config or {}
        styles = config.get("styles") or ["draw"]
        self.draw_var.set("draw" in styles)
        self.beads_var.set("beads" in styles)
        self.grid_var.set(str(config.get("grid") or 28))
        self.brief.delete("1.0", "end")
        self.brief.insert("1.0", config.get("brief") or "")

    def get_config(self):
        styles = []
        if self.draw_var.get():
            styles.append("draw")
        if self.beads_var.get():
            styles.append("beads")
        try:
            grid = max(16, min(36, int(self.grid_var.get())))
        except ValueError:
            grid = 28
        return {"brief": self.brief.get("1.0", "end").strip()[:500],
                "styles": styles or ["draw"], "grid": grid}
