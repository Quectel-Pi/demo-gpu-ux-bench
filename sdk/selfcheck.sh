#!/bin/bash
# 板端环境自检：把"能不能跑起来"需要的前提一次性查清楚
#
# 用法: selfcheck.sh
set -uo pipefail

SDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SDK_DIR/common.sh"

OK=0
FAIL=0
WARN=0

ok()   { printf '  [ OK ] %s\n' "$*"; OK=$((OK + 1)); }
warn() { printf '  [WARN] %s\n' "$*"; WARN=$((WARN + 1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=$((FAIL + 1)); }
sec()  { printf '\n== %s ==\n' "$*"; }

sec "运行环境"
[ "$(id -u)" = "0" ] && ok "root 权限（adb shell 默认）" || bad "需要 root 权限"

sec "显示与 GPU"
if [ -e /dev/dri/card0 ]; then
    ok "DRM 设备 /dev/dri/card0 存在"
    for c in /sys/class/drm/card*-*/status; do
        [ -e "$c" ] || continue
        n="$(basename "$(dirname "$c")")"
        s="$(cat "$c")"
        [ "$s" = "connected" ] && ok "显示接口 $n 已连接（mode: $(head -1 "$(dirname "$c")/modes" 2>/dev/null)）" \
                               || warn "显示接口 $n 未连接"
    done
else
    bad "缺少 /dev/dri/card0"
fi
renderer="$(eglinfo 2>/dev/null | grep -m1 'OpenGL ES profile renderer' | sed 's/.*: //')"
[ -n "$renderer" ] && ok "GLES renderer: $renderer" || warn "eglinfo 未取到 GLES renderer 信息"
gpu_freq="$(cat /sys/class/devfreq/27800000.gpu/cur_freq 2>/dev/null)"
gpu_gov="$(cat /sys/class/devfreq/27800000.gpu/governor 2>/dev/null)"
[ -n "$gpu_freq" ] && ok "GPU devfreq: cur=${gpu_freq} governor=${gpu_gov}（跑分前建议记录/锁定）" \
                   || warn "未找到 GPU devfreq 节点（不同内核路径可能不同）"

sec "桌面会话（Wayland 口径必需）"
if detect_session >/dev/null 2>&1; then
    detect_session
    ok "已登录桌面用户: $BENCH_USER (uid=$BENCH_UID, WAYLAND_DISPLAY=$WAYLAND_DISPLAY_USER)"
    procs="$(ps -u "$BENCH_USER" -o comm 2>/dev/null)"
    case "$procs" in
        *gnome-shell*) ok "gnome-shell 正在运行" ;;
        *) warn "未检测到 gnome-shell 进程，桌面动画/网页滚动场景可能无效果" ;;
    esac
    command -v runuser >/dev/null && ok "runuser 可用（切桌面用户执行）" || bad "缺少 runuser"
else
    bad "未探测到已登录的 GNOME Wayland 会话：请先在板子屏幕上登录桌面后重试"
fi

sec "基准工具"
for b in glmark2-es2-wayland glmark2-wayland glmark2-es2-drm glmark2-drm; do
    command -v "$b" >/dev/null && ok "$b" || warn "缺少 $b"
done

sec "采集工具"
command -v ffmpeg >/dev/null && ok "ffmpeg" || bad "缺少 ffmpeg（桌面动画/网页场景旁路采样依赖它）"
# 注意：不能用 `ffmpeg ... | grep -q`，grep 提前退出会让 ffmpeg 收到 EPIPE，
#       在 set -o pipefail 下会被误判为失败，因此统一先落变量再匹配。
ffmpeg_devices="$(ffmpeg -hide_banner -devices 2>/dev/null)"
case "$ffmpeg_devices" in
    *kmsgrab*) ok "ffmpeg 支持 kmsgrab（屏幕旁路采样）" ;;
    *) bad "ffmpeg 不支持 kmsgrab" ;;
esac
command -v modetest >/dev/null && ok "modetest（DRM 口径排障用）" || warn "缺少 modetest"
if [ -w /dev/uinput ]; then
    ok "/dev/uinput 可写（真实按键注入）"
else
    bad "/dev/uinput 不可写（桌面动画触发依赖它）"
fi

sec "网页场景"
command -v firefox-esr >/dev/null && ok "firefox-esr" || bad "缺少 firefox-esr"
python3 -c 'import http.server' 2>/dev/null && ok "python3 http.server（结果回传服务）" \
                                            || bad "python3 标准库异常"

sec "视频场景"
command -v gst-launch-1.0 >/dev/null && ok "gst-launch-1.0" || bad "缺少 gstreamer"
if python3 -c "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst" 2>/dev/null; then
    ok "python3-gi + GStreamer 绑定可用"
else
    bad "python3 无法 import Gst（缺 gir1.2-gstreamer-1.0）"
fi
for e in mppvideodec avdec_h264 waylandsink fpsdisplaysink qtdemux h264parse; do
    gst-inspect-1.0 "$e" >/dev/null 2>&1 && ok "GStreamer 元素 $e" || warn "缺少 $e"
done
ffmpeg_encoders="$(ffmpeg -hide_banner -encoders 2>/dev/null)"
case "$ffmpeg_encoders" in
    *libx264*) ok "ffmpeg libx264（片源生成）" ;;
    *) warn "缺少 libx264，需自带片源(--video)" ;;
esac

sec "目录"
for d in "$SDK_DIR" "$RESULT_DIR" "$LOG_DIR" "$ASSET_DIR"; do
    [ -d "$d" ] && ok "$d" || warn "$d 不存在（跑 install.sh 会自动创建）"
done

printf '\n== 汇总: OK=%d WARN=%d FAIL=%d ==\n' "$OK" "$WARN" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
