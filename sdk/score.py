#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总打分：把 results/ 下的原始数据整理成"GPU 渲染基准 + 界面体验"打分表

打分规则（阈值集中在本文件顶部常量，首轮实测后可按需校准）：

  场景流畅度(0-10) = 10 * [ 0.5 * min(1, 有效FPS / 目标刷新率)
                          + 0.5 * max(0, 1 - jank占比 / JANK_TOLERANCE) ]
      —— 一半权重看"跑不跑得满帧"，一半权重看"卡不卡"

  视频播放(0-10)   = 10 * [ 0.5 * (1 - 丢帧率) + 0.5 * min(1, 平均FPS / 片源帧率) ]

  glmark2(0-10)    = min(10, 总分 / (GLMARK2_FULL_SCORE / 10))
      —— 总分 = 各场景 FPS 的几何平均（与 glmark2 官方口径一致）

  综合(0-10)       = 0.4*glmark2 + 0.2*动画 + 0.2*网页 + 0.2*视频

用法:
  score.py --results-dir ./results --out ./results/score_report.md
"""

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
import unicodedata

TARGET_FPS = 60.0            # 面板刷新率（板子实测 1280x800@60.03Hz）
JANK_TOLERANCE = 0.10        # jank 占比达到 10% 该项记 0 分
GLMARK2_FULL_SCORE = 100.0   # 参考满分线：官方总分 100 视为满分（本板 Wayland 实测 58 → 5.8 分）
WEIGHTS = {"glmark2": 0.4, "anim": 0.2, "web": 0.2, "video": 0.2}
ANIM_ITEMS = ["overview", "workspace", "window"]


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            print("解析失败 %s: %s" % (path, exc), file=sys.stderr)
            return None


def parse_glmark2_csv(path):
    """解析 glmark2 --results-file 输出的 CSV。

    glmark2 2023.01 在 `--results fps` 下输出的是**无表头** CSV：
        "[build] use-vbo=false","59","17.074","Success"
    部分版本/口径会带 scene/score 表头，这里两种都兼容。
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.reader(fh))

    # 情况一：带表头，按列名定位
    for i, row in enumerate(rows):
        low = [c.strip().lower() for c in row]
        if "scene" in low and ("score" in low or "fps" in low):
            si = low.index("scene")
            ci = low.index("score") if "score" in low else low.index("fps")
            scenes = {}
            for r in rows[i + 1:]:
                if len(r) <= max(si, ci):
                    continue
                scene = r[si].strip()
                try:
                    scenes[scene] = float(r[ci])
                except ValueError:
                    continue
            return {k: v for k, v in scenes.items() if k}

    # 情况二：无表头，第 0 列场景、第 1 列 FPS/得分
    scenes = {}
    for row in rows:
        if len(row) < 2:
            continue
        name = row[0].strip()
        try:
            value = float(row[1])
        except ValueError:
            continue
        if name:
            scenes[name] = value
    return scenes


def geometric_mean(values):
    values = [v for v in values if v and v > 0]
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


def smoothness_score(effective_fps, jank_ratio, target_fps=TARGET_FPS):
    if effective_fps is None:
        return None
    frame_part = min(1.0, effective_fps / target_fps)
    jank_part = max(0.0, 1.0 - (jank_ratio or 0.0) / JANK_TOLERANCE)
    return round(10.0 * (0.5 * frame_part + 0.5 * jank_part), 1)


def grade_of(score):
    """把 0-10 得分映射成等级文案（与 README 的等级对照表一致）。"""
    if score is None:
        return "-"
    for threshold, label in ((9.0, "优秀"), (7.0, "良好"), (5.0, "一般"),
                             (3.0, "较差"), (0.0, "不可用")):
        if score >= threshold:
            return label
    return "-"


def _disp_width(text):
    """按终端显示宽度计算（CJK 字符占 2 列），用于中文表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def _pad(text, width):
    return text + " " * max(0, width - _disp_width(text))


def render_summary(rows, total_point, report_path):
    """跑分末尾的精简得分横幅。rows: [(名称, 权重, 得分)]。"""
    name_w = max(_disp_width(r[0]) for r in rows)
    bar = "=" * 60
    lines = ["", bar, "  GPU 渲染基准与界面体验打分 · 最终得分", bar]
    for name, weight, score in rows:
        lines.append("  %s  %4s  %5s  %s" % (
            _pad(name, name_w), weight,
            score if score is not None else "-", grade_of(score)))
    lines.append("-" * 60)
    lines.append("  %s  %4s  %5s  %s" % (
        _pad("综合", name_w), "100%",
        total_point if total_point is not None else "-", grade_of(total_point)))
    lines.append(bar)
    if report_path:
        lines.append("  完整报告: %s" % report_path)
    return lines


def video_score(drop_rate, average_fps, source_fps):
    if drop_rate is None and average_fps is None:
        return None
    drop_part = 1.0 - (drop_rate or 0.0)
    fps_part = min(1.0, (average_fps or 0.0) / (source_fps or 30.0))
    return round(10.0 * (0.5 * drop_part + 0.5 * fps_part), 1)


def read_official_score(results_dir, backend, run):
    """glmark2 官方总分（glmark2_bench.sh 从 stdout 抽出来单独存的 .score 文件）。"""
    path = os.path.join(results_dir, "glmark2_%s_run%d.score" % (backend, run))
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        try:
            return float(fh.read().strip())
        except ValueError:
            return None


def collect_glmark2(results_dir):
    out = {}
    for name in sorted(os.listdir(results_dir)):
        m = re.match(r"glmark2_(\w+)_run(\d+)\.csv$", name)
        if not m:
            continue
        backend, run = m.group(1), int(m.group(2))
        scenes = parse_glmark2_csv(os.path.join(results_dir, name))
        if not scenes:
            continue
        official = read_official_score(results_dir, backend, run)
        out.setdefault(backend, []).append({
            "run": run,
            "scenes": scenes,
            "official": official,
            # 总分优先用官方值；缺失时退回各场景 FPS 几何平均（可比性略差）
            "total": official if official is not None
                     else geometric_mean(list(scenes.values())),
        })
    for backend, runs in out.items():
        runs.sort(key=lambda r: r["run"])
    return out


def median_of(runs, key_fn):
    vals = [key_fn(r) for r in runs if key_fn(r) is not None]
    return round(statistics.median(vals), 2) if vals else None


def main():
    ap = argparse.ArgumentParser(description="GPU 渲染基准与界面体验打分汇总")
    ap.add_argument("--results-dir", default="./results")
    ap.add_argument("--out", default="", help="markdown 报告输出路径")
    ap.add_argument("--summary", action="store_true",
                    help="只打印跑分末尾的精简得分横幅（不生成完整报告）")
    ap.add_argument("--json", default="",
                    help="把汇总结果另存为机器可读 JSON（供 score_window.py 弹窗展示）")
    args = ap.parse_args()

    rd = args.results_dir
    if not os.path.isdir(rd):
        raise SystemExit("结果目录不存在: %s" % rd)

    lines = []
    add = lines.append

    # ---------- glmark2 ----------
    gl = collect_glmark2(rd)
    add("# GPU 渲染基准与界面体验打分（自动生成）")
    add("")
    add("## 1. glmark2 全项跑分（默认 17 场景）")
    add("")
    if not gl:
        add("_未采集到 glmark2 CSV 结果_")
    gl_scores = {}
    for backend, runs in sorted(gl.items()):
        total = median_of(runs, lambda r: r["total"])
        gl_scores[backend] = total
        src = "官方总分" if any(r["official"] is not None for r in runs) \
              else "各场景 FPS 几何平均"
        add("- **%s**：%d 轮，总分取%s的中位数 → **%.1f**"
            % (backend, len(runs), src, total or 0))
    if gl:
        add("")
        add("| 场景 | " + " | ".join(sorted(gl)) + " |")
        add("|---" * (1 + len(gl)) + "|")
        scene_names = sorted({s for runs in gl.values() for s in runs[0]["scenes"]})
        for scene in scene_names:
            cells = []
            for backend in sorted(gl):
                vals = [r["scenes"].get(scene) for r in gl[backend]]
                vals = [v for v in vals if v is not None]
                cells.append("%.0f" % statistics.median(vals) if vals else "-")
            add("| %s | %s |" % (scene, " | ".join(cells)))
    add("")

    # ---------- 三场景 ----------
    add("## 2. 真实场景流畅度")
    add("")
    add("| 场景 | 有效FPS | 间隔 p95(ms) | jank 占比 | 最长卡顿(ms) | 丢帧估算 | 得分(0-10) |")
    add("|---|---|---|---|---|---|---|")

    anim_rows = []
    for item in ANIM_ITEMS:
        data = load_json(os.path.join(rd, "anim_%s.json" % item))
        if not data:
            anim_rows.append((item, None, None, None, None, None, None))
            continue
        score = smoothness_score(data.get("effective_fps"),
                                (data.get("jank") or {}).get("ratio"),
                                data.get("target_fps") or TARGET_FPS)
        anim_rows.append((
            item, data.get("effective_fps"),
            (data.get("interval_ms") or {}).get("p95"),
            (data.get("jank") or {}).get("ratio"),
            (data.get("jank") or {}).get("longest_stall_ms"),
            data.get("dropped_frames_est"), score))
    for row in anim_rows:
        add("| 桌面动画-{0} | {1} | {2} | {3} | {4} | {5} | {6} |".format(
            row[0], row[1] if row[1] is not None else "-", row[2] or "-",
            ("%.2f%%" % (row[3] * 100)) if row[3] is not None else "-",
            row[4] or "-", row[5] if row[5] is not None else "-",
            row[6] if row[6] is not None else "-"))

    web = load_json(os.path.join(rd, "web_scroll.json"))
    web_score = None
    if web:
        target = web.get("refresh_hz_est") or TARGET_FPS
        web_score = smoothness_score(web.get("avg_fps"),
                                     (web.get("jank") or {}).get("ratio"), target)
        add("| 网页滚动(firefox rAF) | {0} | {1} | {2} | {3} | {4} | {5} |".format(
            web.get("avg_fps"), (web.get("interval_ms") or {}).get("p95"),
            ("%.2f%%" % ((web.get("jank") or {}).get("ratio", 0) * 100)),
            (web.get("jank") or {}).get("longest_stall_ms"),
            web.get("dropped_frames_est"), web_score))
    else:
        add("| 网页滚动(firefox rAF) | - | - | - | - | - | - |")
    web_cap = load_json(os.path.join(rd, "web_scroll_capture.json"))
    if web_cap:
        add("| 网页滚动(kmsgrab旁路) | {0} | {1} | {2} | {3} | {4} | {5} |".format(
            web_cap.get("effective_fps"), (web_cap.get("interval_ms") or {}).get("p95"),
            ("%.2f%%" % ((web_cap.get("jank") or {}).get("ratio", 0) * 100)),
            (web_cap.get("jank") or {}).get("longest_stall_ms"),
            web_cap.get("dropped_frames_est"),
            smoothness_score(web_cap.get("effective_fps"),
                             (web_cap.get("jank") or {}).get("ratio"))))

    video_files = sorted(f for f in os.listdir(rd)
                         if re.match(r"video_.*\.json$", f))
    video_scores = []
    video_fps_vals = []
    video_drop_vals = []
    for name in video_files:
        data = load_json(os.path.join(rd, name))
        if not data:
            continue
        m = re.search(r"(\d+)fps", data.get("file", ""))
        src_fps = float(m.group(1)) if m else 30.0
        score = video_score(data.get("drop_rate"), data.get("average_fps"), src_fps)
        if score is not None:
            video_scores.append(score)
        if data.get("average_fps") is not None:
            video_fps_vals.append(data["average_fps"])
        if data.get("drop_rate") is not None:
            video_drop_vals.append(data["drop_rate"])
        total = (data.get("frames_rendered") or 0) + (data.get("frames_dropped") or 0)
        add("| 视频播放-{0} | {1} | - | - | - | {2}/{3}({4}) | {5} |".format(
            os.path.splitext(name)[0].replace("video_", ""),
            data.get("average_fps"),
            data.get("frames_dropped"), total,
            ("%.2f%%" % ((data.get("drop_rate") or 0) * 100)),
            score))
    add("")

    # ---------- 综合 ----------
    add("## 3. 综合得分")
    add("")
    gl_val = gl_scores.get("wayland") or next(iter(gl_scores.values()), None)
    gl_point = (min(10.0, gl_val / (GLMARK2_FULL_SCORE / 10.0))
                if gl_val is not None else None)
    anim_vals = [r[6] for r in anim_rows if r[6] is not None]
    anim_point = round(statistics.mean(anim_vals), 1) if anim_vals else None

    components = {
        "glmark2(GPU 渲染能力)": gl_point,
        "桌面动画": anim_point,
        "网页滚动": web_score,
        "视频播放": round(statistics.mean(video_scores), 1) if video_scores else None,
    }
    add("| 维度 | 权重 | 得分(0-10) |")
    add("|---|---|---|")
    add("| glmark2(GPU 渲染能力) | %.0f%% | %s |" % (
        WEIGHTS["glmark2"] * 100, gl_point if gl_point is not None else "-"))
    add("| 桌面动画 | %.0f%% | %s |" % (
        WEIGHTS["anim"] * 100, anim_point if anim_point is not None else "-"))
    add("| 网页滚动 | %.0f%% | %s |" % (
        WEIGHTS["web"] * 100, web_score if web_score is not None else "-"))
    add("| 视频播放 | %.0f%% | %s |" % (
        WEIGHTS["video"] * 100,
        round(statistics.mean(video_scores), 1) if video_scores else "-"))

    weighted, weight_sum = 0.0, 0.0
    for key, value in (("glmark2", gl_point), ("anim", anim_point),
                       ("web", web_score),
                       ("video", round(statistics.mean(video_scores), 1) if video_scores else None)):
        if value is not None:
            weighted += WEIGHTS[key] * value
            weight_sum += WEIGHTS[key]
    total_point = round(weighted / weight_sum, 1) if weight_sum else None
    add("| **综合** | 100%% | **%s** |" % (total_point if total_point is not None else "-"))

    video_point = round(statistics.mean(video_scores), 1) if video_scores else None

    if args.json:
        # 机器可读汇总：供 score_window.py 在开发板屏幕上弹窗横行展示
        anim_ok = [r for r in anim_rows if r[1] is not None]
        anim_fps = round(statistics.mean([r[1] for r in anim_ok]), 1) if anim_ok else None
        anim_stall = max([r[4] for r in anim_ok if r[4] is not None], default=None)
        gl_backend = "wayland" if "wayland" in gl_scores else (next(iter(gl_scores), "-"))
        gl_runs = len(gl.get(gl_backend, [])) if gl_backend != "-" else 0

        def _fmt(value, fmt, suffix=""):
            return (fmt % value) + suffix if value is not None else "-"

        payload = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": {"score": total_point, "grade": grade_of(total_point)},
            "items": [
                {"name": "glmark2（GPU 渲染能力）", "weight": "40%", "score": gl_point,
                 "grade": grade_of(gl_point),
                 "detail": "官方总分 %s（%s，%d 轮）" % (_fmt(gl_val, "%.1f"), gl_backend, gl_runs)},
                {"name": "桌面动画", "weight": "20%", "score": anim_point,
                 "grade": grade_of(anim_point),
                 "detail": "有效 %s，最长卡顿 %s" % (_fmt(anim_fps, "%.1f fps"),
                                                     _fmt(anim_stall, "%.1f ms"))},
                {"name": "网页滚动", "weight": "20%", "score": web_score,
                 "grade": grade_of(web_score),
                 "detail": "有效 %s，jank %s" % (
                     _fmt(web.get("avg_fps") if web else None, "%.1f fps"),
                     _fmt((web.get("jank") or {}).get("ratio") * 100 if web and web.get("jank") else None,
                          "%.1f%%"))},
                {"name": "视频播放", "weight": "20%", "score": video_point,
                 "grade": grade_of(video_point),
                 "detail": "平均 %s，丢帧 %s" % (
                     _fmt(round(statistics.mean(video_fps_vals), 1) if video_fps_vals else None, "%.1f fps"),
                     _fmt(round(statistics.mean(video_drop_vals) * 100, 2) if video_drop_vals else None, "%.2f%%"))},
            ],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print("[score] 汇总 JSON 已写入 %s" % args.json, file=sys.stderr)

    if args.summary:
        print("\n".join(render_summary([
            ("glmark2（GPU 渲染能力）", "40%", gl_point),
            ("桌面动画", "20%", anim_point),
            ("网页滚动", "20%", web_score),
            ("视频播放", "20%", video_point),
        ], total_point, os.path.join(rd, "score_report.md"))))
        return

    add("")
    add("## 4. 打分规则")
    add("")
    add("- 场景流畅度 = 10 × [0.5 × min(1, 有效FPS/%g) + 0.5 × max(0, 1 − jank占比/%g)]"
        % (TARGET_FPS, JANK_TOLERANCE))
    add("- 视频播放 = 10 × [0.5 × (1 − 丢帧率) + 0.5 × min(1, 平均FPS/片源帧率)]")
    add("- glmark2 = min(10, 总分 / %.0f)，总分满分线为 %.0f 分"
        % (GLMARK2_FULL_SCORE / 10.0, GLMARK2_FULL_SCORE))
    add("- jank 定义：帧间隔 > 1.5 × 目标帧间隔（60Hz → 25ms）")
    add("- 综合 = 0.4×glmark2 + 0.2×动画 + 0.2×网页 + 0.2×视频（缺失项自动按权重归一）")

    report = "\n".join(lines)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print("\n[score] 报告已写入 %s" % args.out, file=sys.stderr)


if __name__ == "__main__":
    main()
