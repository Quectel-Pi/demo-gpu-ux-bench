#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频播放流畅度采集（GStreamer + fpsdisplaysink）

用 GStreamer 播放本地视频，由 fpsdisplaysink 统计真正提交到显示端的帧数：
    frames_rendered / frames_dropped / drop_rate / average_fps

硬解走 Rockchip MPP（mppvideodec），软解走 libav（avdec_h264），用于对比
VPU 通路与纯 CPU 通路的差异。两者最终都送 waylandsink，走真实桌面合成路径。

用法:
  video_probe.py --file /opt/gpu-bench/assets/video_1280x800_30fps_60s.mp4 \\
      --decoder mpp --label video_normal_mpp --out /opt/gpu-bench/results/video_normal_mpp.json
"""

import argparse
import json
import sys
import time

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

DECODER_ELEMENTS = {"mpp": "mppvideodec", "sw": "avdec_h264"}


def get_prop(element, name, default=None):
    """fpsdisplaysink 的统计属性名在不同版本有差异，取不到就退回默认值。"""
    try:
        return element.get_property(name)
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser(description="视频播放流畅度采集")
    ap.add_argument("--file", required=True, help="本地视频文件（H.264 MP4）")
    ap.add_argument("--decoder", default="mpp", choices=sorted(DECODER_ELEMENTS))
    ap.add_argument("--sink", default="waylandsink")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=float, default=180.0, help="播放超时（秒）")
    ap.add_argument("--no-sync", action="store_true", help="关闭时钟同步（测解码吞吐上限，非真实播放）")
    args = ap.parse_args()

    if '"' in args.file or "\\" in args.file:
        raise SystemExit("文件路径不要包含引号或反斜杠")

    Gst.init(None)
    decoder = DECODER_ELEMENTS[args.decoder]
    desc = ('filesrc location="%s" ! qtdemux ! h264parse ! %s ! '
            'fpsdisplaysink name=fps sync=%s text-overlay=false video-sink=%s'
            % (args.file, decoder, "false" if args.no_sync else "true", args.sink))

    pipeline = Gst.parse_launch(desc)
    fps_sink = pipeline.get_by_name("fps")
    try:
        fps_sink.set_property("signal-fps-measurements", True)
    except Exception:
        pass

    measurements = []

    def on_measurements(_sink, fps, drop_rate, avg_fps):
        measurements.append({
            "fps": round(float(fps), 2),
            "drop_rate": round(float(drop_rate), 4),
            "avg_fps": round(float(avg_fps), 2),
        })

    fps_sink.connect("fps-measurements", on_measurements)

    state = {"error": None, "eos": False}
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(_bus, msg):
        if msg.type == Gst.MessageType.EOS:
            state["eos"] = True
            loop.quit()
        elif msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            state["error"] = "%s (%s)" % (err.message, dbg or "")
            loop.quit()

    bus.connect("message", on_message)

    def on_timeout():
        state["error"] = state["error"] or "timeout after %.0fs" % args.timeout
        loop.quit()
        return False

    GLib.timeout_add(int(args.timeout * 1000), on_timeout)

    t0 = time.monotonic()
    pipeline.set_state(Gst.State.PLAYING)
    loop.run()
    wall_s = time.monotonic() - t0

    rendered = get_prop(fps_sink, "frames-rendered")
    dropped = get_prop(fps_sink, "frames-dropped")
    if dropped is None:
        dropped = get_prop(fps_sink, "drop-frame-count")
    average_fps = get_prop(fps_sink, "average-fps")
    # 本板 fpsdisplaysink 的 average-fps 属性取不到（返回 None），
    # 退回用最后一次 fps-measurements 信号里的 avg_fps。
    if average_fps is None and measurements:
        average_fps = measurements[-1]["avg_fps"]
    last_message = get_prop(fps_sink, "last-message")
    pipeline.set_state(Gst.State.NULL)

    result = {
        "label": args.label or "%s_%s" % (args.decoder, args.sink),
        "file": args.file,
        "decoder": decoder,
        "sink": args.sink,
        "sync": not args.no_sync,
        "wall_s": round(wall_s, 2),
        "frames_rendered": int(rendered) if rendered is not None else None,
        "frames_dropped": int(dropped) if dropped is not None else None,
        "average_fps": round(float(average_fps), 2) if average_fps is not None else None,
        "last_message": last_message,
        "measurements": len(measurements),
        "measurements_tail": measurements[-5:],
        "eos": state["eos"],
        "error": state["error"],
    }
    total = (result["frames_rendered"] or 0) + (result["frames_dropped"] or 0)
    result["drop_rate"] = (round((result["frames_dropped"] or 0) / total, 4)
                           if total > 0 else None)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    print("[video_probe] %s: rendered=%s dropped=%s drop_rate=%s avg_fps=%s 用时=%.1fs"
          % (result["label"], result["frames_rendered"], result["frames_dropped"],
             result["drop_rate"], result["average_fps"], wall_s), file=sys.stderr)

    if state["error"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
