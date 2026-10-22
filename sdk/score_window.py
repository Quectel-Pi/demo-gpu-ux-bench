#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最终得分窗口（GTK3）：在开发板屏幕上横向展示各项结果

数据来自 score.py --json 产出的汇总 JSON，本脚本只负责展示，不做任何计算。

布局（列 = 综合 + 4 个维度，行 = 得分/等级/权重/关键数据）：

          综合     glmark2（GPU 渲染能力）  桌面动画   网页滚动   视频播放
  得分     6.7      5.7                     7.5       5.0       9.8
  等级     一般     一般                    良好      一般      优秀
  权重     100%     40%                     20%       20%       20%
  关键数据 四项加权 官方总分 57.0（wayland…） 有效 57.4… 有效 73.0… 平均 43.5…

用法:
  score_window.py --summary-json /opt/gpu-bench/sdk/results/score_summary.json

注意：需要在已登录的 GNOME Wayland 会话下、以桌面用户身份运行
      （run-all.sh 会自动用 runuser 切到桌面用户执行）。
"""

import argparse
import json
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

# 等级配色（与 README 的等级对照表一致）
GRADE_COLORS = {
    "优秀": "#2e7d32",
    "良好": "#558b2f",
    "一般": "#ef6c00",
    "较差": "#e64a19",
    "不可用": "#c62828",
}


def esc(text):
    return GLib.markup_escape_text(str(text))


def add_row(grid, row, label, columns, markup_of):
    """加一行：第 0 列是行标签，之后每列一个数据单元格。"""
    lab = Gtk.Label()
    lab.set_markup('<span foreground="#666666">%s</span>' % esc(label))
    lab.set_xalign(0.0)
    lab.set_valign(Gtk.Align.CENTER)
    grid.attach(lab, 0, row, 1, 1)

    for col, item in enumerate(columns):
        cell = Gtk.Label()
        cell.set_markup(markup_of(item, col == 0))
        cell.set_line_wrap(True)
        cell.set_max_width_chars(22)
        grid.attach(cell, col + 1, row, 1, 1)


def main():
    ap = argparse.ArgumentParser(description="最终得分窗口（GTK3 横向展示）")
    ap.add_argument("--summary-json", default="score_summary.json",
                    help="score.py --json 产出的汇总 JSON 路径")
    args = ap.parse_args()

    if not os.path.isfile(args.summary_json):
        print("找不到汇总 JSON: %s（请先跑 score.py --json）" % args.summary_json,
              file=sys.stderr)
        return 1
    with open(args.summary_json, encoding="utf-8") as fh:
        data = json.load(fh)

    total = data.get("total") or {}
    columns = [{"name": "综合", "score": total.get("score"),
                "grade": total.get("grade", "-"), "weight": "100%",
                "detail": "四项加权综合"}]
    columns += data.get("items", [])

    win = Gtk.Window(title="GPU 渲染基准与界面体验打分 · 最终得分")
    win.set_border_width(22)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.set_keep_above(True)
    win.connect("destroy", Gtk.main_quit)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    win.add(box)

    title = Gtk.Label()
    title.set_markup('<span size="x-large" weight="bold">'
                     'GPU 渲染基准与界面体验打分 · 最终得分</span>')
    box.pack_start(title, False, False, 0)

    # 不设 expand：窗口按内容自适应大小，避免底部留大片空白
    grid = Gtk.Grid(column_spacing=26, row_spacing=12)
    grid.set_halign(Gtk.Align.CENTER)
    box.pack_start(grid, False, False, 0)

    # --- 表头（列名） ---
    for col, item in enumerate(columns):
        cell = Gtk.Label()
        cell.set_markup('<span size="%s" weight="bold">%s</span>'
                        % ("large" if col == 0 else "medium", esc(item.get("name", "-"))))
        cell.set_line_wrap(True)
        cell.set_max_width_chars(20)
        cell.set_valign(Gtk.Align.CENTER)
        grid.attach(cell, col + 1, 0, 1, 1)

    def score_markup(item, is_total):
        color = GRADE_COLORS.get(item.get("grade"), "#333333")
        score = item.get("score")
        return ('<span size="%s" weight="bold" foreground="%s">%s</span>'
                % ("xx-large" if is_total else "x-large", color,
                   "-" if score is None else score))

    def grade_markup(item, is_total):
        color = GRADE_COLORS.get(item.get("grade"), "#333333")
        return ('<span size="%s" weight="bold" foreground="%s">%s</span>'
                % ("large" if is_total else "medium", color, esc(item.get("grade", "-"))))

    def weight_markup(item, _is_total):
        return '<span foreground="#666666">%s</span>' % esc(item.get("weight", "-"))

    def detail_markup(item, _is_total):
        return ('<span size="small" foreground="#444444">%s</span>'
                % esc(item.get("detail", "-")))

    add_row(grid, 1, "得分", columns, score_markup)
    add_row(grid, 2, "等级", columns, grade_markup)
    add_row(grid, 3, "权重", columns, weight_markup)
    add_row(grid, 4, "关键数据", columns, detail_markup)

    footer = Gtk.Label()
    footer.set_markup('<span size="small" foreground="#888888">生成时间 %s · '
                      '完整报告见 results/score_report.md</span>'
                      % esc(data.get("generated_at", "-")))
    box.pack_start(footer, False, False, 0)

    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
