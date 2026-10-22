#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屏幕帧间隔采样器（基于 ffmpeg kmsgrab）

原理
----
用 ffmpeg 的 kmsgrab 采集器按固定频率（默认 60fps）抓取 DRM 主平面上的画面。
kmsgrab 只需要 CAP_SYS_ADMIN，不需要 DRM master，因此**不会打断正在运行的
GNOME 会话**，可以在真实桌面状态下旁路采样。

每帧缩小成 WxH 的灰度小图，并记录读到该帧时的墙钟时间戳（CLOCK_MONOTONIC）。
相邻两帧的平均绝对差超过阈值即认为"合成器提交了新内容"，于是可以得到：

  * effective_fps     —— 动画/滚动期间的帧率（= 1000 / 有效帧间隔均值）
  * interval_ms       —— 相邻"新内容帧"之间间隔的分布（p50/p95/p99/max）
  * jank              —— 间隔 > 1.5 x 目标帧间隔 的次数与占比
  * dropped_frames    —— 按目标帧间隔估算的丢帧数
  * longest_stall_ms  —— 最长单次卡顿

重要口径
--------
静止画面不会产生新帧（合成器不重绘），因此把间隔 >= --idle-ms（默认 200ms）的
"空闲间隔"排除在帧率/卡顿统计之外 —— 否则一段 5s 里只动画 0.5s 的场景会被算成
几 FPS。被排除的个数记在 idle_gaps 里，用来判断动作是否真的发生了。

另外，主平面像素格式可能在采集中途改变（全屏视频/浏览器进入 direct scanout 时
ARGB2101010 -> XRGB8888），kmsgrab 会直接报错退出。此时本脚本会重新探测格式并
接着采集，各段用墙钟时间戳拼接（format_changes 记录切换次数）。

精度说明
--------
采集侧按固定频率轮询，时间戳精度约 ±1 帧（≈ ±16.7ms @60Hz）；
对"掉帧/卡顿占比"这类统计足够，但不适合做 sub-frame 级分析。

用法
----
  frame_monitor.py --duration 10 --label anim_overview \
      --out results/anim_overview.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time


def probe_sw_format(device, fps=60):
    """探测 kmsgrab hwframe 的实际像素格式（如 x2rgb10le / nv12）。

    背景：本板 ffmpeg 7.1 里 `hwdownload` 的格式协商有缺陷 —— 不指定 format 时它会
    挑中 `monow`，然后报 "Invalid output format monow for hwframe download"。
    DRM hwframe 只能下载成与自身 sw_format 相同的格式，所以先用 debug 日志把
    sw_format 探出来，再显式写进 filter。
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "debug",
        "-f", "kmsgrab", "-framerate", str(fps), "-device", device,
        "-i", "-", "-frames:v", "1", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=10)
    except Exception:
        return None
    out = proc.stderr.decode("utf-8", "replace")
    m = re.search(r"Format is (\w+), from DRM format", out)
    return m.group(1) if m else None


def read_exact(stream, size):
    """从管道精确读取 size 字节；EOF 返回 None。"""
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def percentile(sorted_values, ratio):
    if not sorted_values:
        return None
    idx = int(round((len(sorted_values) - 1) * ratio))
    return sorted_values[idx]


def build_ffmpeg_cmd(args, download_format):
    vf = "hwdownload"
    if download_format:
        vf += ",format=%s" % download_format
    vf += ",scale=%d:%d,format=gray" % (args.width, args.height)

    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "kmsgrab", "-framerate", str(args.fps), "-device", args.device,
    ]
    if args.crtc_id:
        cmd += ["-crtc_id", str(args.crtc_id)]
    if args.plane_id:
        cmd += ["-plane_id", str(args.plane_id)]
    cmd += ["-i", "-", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    return cmd


def run_capture(args, download_format, deadline, prev):
    """采集一段，直到 deadline 或 ffmpeg 退出（例如主 plane 像素格式变了）。

    返回 (samples, prev_frame, stderr_text)。samples 是 (时间戳, 是否新内容, 平均绝对差)。
    """
    cmd = build_ffmpeg_cmd(args, download_format)
    frame_size = args.width * args.height
    stderr_file = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_file)
    samples = []
    try:
        while True:
            frame = read_exact(proc.stdout, frame_size)
            if frame is None:
                break
            now = time.monotonic()
            if prev is None:
                mad, changed = 0.0, True
            else:
                diff = 0
                for a, b in zip(frame, prev):
                    diff += abs(a - b)
                mad = diff / frame_size
                changed = mad >= args.threshold
            samples.append((now, changed, mad))
            prev = frame
            if now >= deadline:
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.stdout.close()
        except Exception:
            pass
        stderr_file.seek(0)
        text = stderr_file.read().decode("utf-8", "replace").strip()
        stderr_file.close()
    return samples, prev, text


def main():
    ap = argparse.ArgumentParser(description="屏幕帧间隔采样器（kmsgrab）")
    ap.add_argument("--duration", type=float, default=10.0, help="采样时长（秒），默认 10")
    ap.add_argument("--fps", type=int, default=60, help="采集频率，默认 60")
    ap.add_argument("--device", default="/dev/dri/card0", help="DRM 设备，默认 /dev/dri/card0")
    ap.add_argument("--crtc-id", type=int, default=0, help="指定 CRTC id（默认自动）")
    ap.add_argument("--plane-id", type=int, default=0, help="指定 plane id（默认自动）")
    ap.add_argument("--width", type=int, default=64, help="降采样宽度，默认 64")
    ap.add_argument("--height", type=int, default=44, help="降采样高度，默认 44")
    ap.add_argument("--threshold", type=float, default=1.0,
                    help="判定'有新内容'的平均绝对差阈值（0-255），默认 1.0")
    ap.add_argument("--idle-ms", type=float, default=200.0,
                    help="超过该间隔视为'场景静止/空闲'，不计入帧率与卡顿统计（默认 200ms）")
    ap.add_argument("--max-restarts", type=int, default=8,
                    help="主 plane 像素格式变化导致采集中断时，重新探测格式续采的最大次数")
    ap.add_argument("--download-format", default="",
                    help="hwdownload 后的像素格式；默认自动探测（如 x2rgb10le/nv12）")
    ap.add_argument("--label", default="capture", help="本次采样的标签")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args()

    deadline = time.monotonic() + args.duration
    samples = []
    prev = None
    formats_used = []
    stderr_msgs = []
    download_format = args.download_format
    restarts = 0
    while True:
        if not download_format:
            download_format = probe_sw_format(args.device, args.fps)
            if not download_format:
                print("警告：未能探测到 kmsgrab 像素格式，hwdownload 可能失败"
                      "（可用 --download-format 指定）", file=sys.stderr)
        if download_format and download_format not in formats_used:
            formats_used.append(download_format)
        seg, prev, err = run_capture(args, download_format, deadline, prev)
        samples += seg
        if err and err not in stderr_msgs:
            stderr_msgs.append(err)
        if time.monotonic() >= deadline or not seg:
            break
        # 采集提前结束：典型原因是主 plane 像素格式变了（全屏视频/浏览器进入 direct
        # scanout 时 ARGB2101010 -> XRGB8888），此时重新探测格式后接着采，
        # 各段用墙钟时间戳天然拼接。
        if restarts >= args.max_restarts:
            break
        restarts += 1
        download_format = args.download_format

    if not samples:
        print(stderr_msgs[0] if stderr_msgs else "采集失败：kmsgrab 没有拿到任何帧",
              file=sys.stderr)
        sys.exit(2)

    span = samples[-1][0] - samples[0][0]
    changed_ts = [t for t, c, _ in samples if c]
    all_intervals = [(changed_ts[i] - changed_ts[i - 1]) * 1000.0
                     for i in range(1, len(changed_ts))]
    # 场景静止时不会有新帧（合成器不重绘），由此产生的长间隔是"空闲"而不是"掉帧"，
    # 统计动画/滚动的帧率与卡顿时必须把它们排除，否则静止画面会把分数拉到 0。
    active_intervals = sorted(iv for iv in all_intervals if iv < args.idle_ms)
    idle_gaps = len(all_intervals) - len(active_intervals)
    base_ms = 1000.0 / args.fps

    def p(ratio):
        v = percentile(active_intervals, ratio)
        return round(v, 2) if v is not None else None

    jank_threshold = base_ms * 1.5
    jank = [iv for iv in active_intervals if iv > jank_threshold]
    dropped = sum(max(0, round(iv / base_ms) - 1) for iv in active_intervals)
    mad_avg = sum(m for _, _, m in samples) / len(samples)
    mean_interval = (sum(active_intervals) / len(active_intervals)
                     if active_intervals else None)

    result = {
        "label": args.label,
        "target_fps": args.fps,
        "device": args.device,
        "download_format": formats_used[0] if formats_used else None,
        "format_changes": len(formats_used) - 1,
        "duration_s": round(span, 3),
        "samples": len(samples),
        "changed_frames": len(changed_ts),
        "active_intervals": len(active_intervals),
        "idle_gaps": idle_gaps,
        "effective_fps": round(1000.0 / mean_interval, 2) if mean_interval else None,
        "mean_interval_ms": round(mean_interval, 2) if mean_interval else None,
        "interval_ms": {
            "p50": p(0.50), "p95": p(0.95), "p99": p(0.99),
            "max": round(active_intervals[-1], 2) if active_intervals else None,
        },
        "jank": {
            "threshold_ms": round(jank_threshold, 2),
            "count": len(jank),
            "ratio": round(len(jank) / len(active_intervals), 4) if active_intervals else None,
            "longest_stall_ms": round(active_intervals[-1], 2) if active_intervals else None,
        },
        "dropped_frames_est": dropped,
        "mean_abs_diff_avg": round(mad_avg, 3),
        "notes": [],
    }

    if len(changed_ts) < 2:
        result["notes"].append("画面几乎没有变化：请确认被测量的动作/内容确实在动")
    elif len(active_intervals) < 2:
        result["notes"].append("有效动画帧太少，未能形成可统计的帧间隔序列")
    if stderr_msgs:
        result["capture_stderr"] = "\n".join(stderr_msgs)[:2000]

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)

    print("[frame_monitor] %s: 动画帧率=%.2f jank=%d(%.2f%%) 最长卡顿=%sms 新内容帧=%d/%d"
          % (args.label,
             result["effective_fps"] or 0.0,
             result["jank"]["count"],
             (result["jank"]["ratio"] or 0.0) * 100,
             result["jank"]["longest_stall_ms"],
             len(changed_ts), len(samples)), file=sys.stderr)


if __name__ == "__main__":
    main()
