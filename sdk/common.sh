#!/bin/bash
# GPU 渲染基准与界面体验打分 —— 板端公共库
#
# 本 SDK 是自包含的：push 到开发板任意目录后直接执行，不依赖主机环境。
# 所有产物（results/logs/assets）都落在 SDK 目录内部。
#
# 被 install.sh / run-all.sh / glmark2_bench.sh / anim_bench.sh / web_bench.sh /
# video_bench.sh source。
# 约定：所有脚本以 root 运行（adb shell 默认就是 root）；涉及桌面/GUI 的部分
#       统一通过 runuser 切到"已登录的桌面用户"执行，保证走真实的合成器路径。
#
# 相关环境变量（可在调用前覆盖）：
#   BENCH_USER   桌面登录用户名；留空则自动探测（取有 wayland-0 的普通用户）

# shellcheck disable=SC2034
BENCH_USER="${BENCH_USER:-}"
SDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULT_DIR="$SDK_DIR/results"
LOG_DIR="$SDK_DIR/logs"
ASSET_DIR="$SDK_DIR/assets"

WAYLAND_DISPLAY_USER=""
BENCH_UID=""

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

require_root() {
    [ "$(id -u)" = "0" ] || die "需要 root 权限运行（adb shell 默认为 root）"
}

ensure_dirs() {
    mkdir -p "$RESULT_DIR" "$LOG_DIR" "$ASSET_DIR"
}

# 探测桌面会话：找到属于普通用户（uid>=1000）的 wayland-0 socket
detect_session() {
    local uid sock passwd_line
    if [ -n "$BENCH_USER" ]; then
        uid="$(id -u "$BENCH_USER" 2>/dev/null)" || die "用户 $BENCH_USER 不存在"
    else
        for sock in /run/user/*/wayland-0; do
            [ -S "$sock" ] || continue
            uid="$(basename "$(dirname "$sock")")"
            [ "$uid" -ge 1000 ] 2>/dev/null || continue
            passwd_line="$(getent passwd "$uid")" || continue
            BENCH_USER="$(printf '%s' "$passwd_line" | cut -d: -f1)"
            break
        done
    fi
    [ -n "$BENCH_USER" ] || die "没有探测到已登录的桌面用户，请先在板子屏幕上登录 GNOME 桌面"

    uid="$(id -u "$BENCH_USER")"
    sock="/run/user/$uid/wayland-0"
    [ -S "$sock" ] || die "用户 $BENCH_USER 没有 wayland-0（uid=$uid），请确认已在屏幕上登录 GNOME Wayland 会话"

    BENCH_UID="$uid"
    WAYLAND_DISPLAY_USER="$(basename "$sock")"
    log "桌面会话: user=$BENCH_USER uid=$BENCH_UID WAYLAND_DISPLAY=$WAYLAND_DISPLAY_USER"
}

# 以桌面用户身份执行命令（继承可用的 Wayland/XWayland 环境）
as_user() {
    [ -n "$BENCH_UID" ] || die "请先调用 detect_session"
    runuser -u "$BENCH_USER" -- env \
        HOME="/home/$BENCH_USER" \
        XDG_RUNTIME_DIR="/run/user/$BENCH_UID" \
        WAYLAND_DISPLAY="$WAYLAND_DISPLAY_USER" \
        DISPLAY="${DISPLAY_FOR_USER:-:0}" \
        MOZ_ENABLE_WAYLAND=1 \
        "$@"
}

# 桌面用户的 home 目录（测试素材、firefox profile 等落地位置）
user_home() {
    getent passwd "$BENCH_UID" | cut -d: -f6
}

# 环境快照：跑分/采集前后各存一份，便于解释数据差异
snapshot_env() {
    local f="$1" d c
    {
        echo "# 采集时间: $(date -Is)"
        echo "kernel      : $(uname -r)"
        echo "os          : $(grep -m1 PRETTY_NAME /etc/os-release | cut -d'"' -f2)"
        echo "cpu_governor: $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor 2>/dev/null)"
        echo "cpu_cur_freq: $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq 2>/dev/null)"
        for d in /sys/class/devfreq/*/; do
            printf 'devfreq     : %s cur=%s max=%s gov=%s\n' \
                "$(basename "$d")" \
                "$(cat "$d/cur_freq" 2>/dev/null)" \
                "$(cat "$d/max_freq" 2>/dev/null)" \
                "$(cat "$d/governor" 2>/dev/null)"
        done
        echo "thermal     : $(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | tr '\n' ' ')"
        for c in /sys/class/drm/card*-*/status; do
            [ -e "$c" ] || continue
            printf 'connector   : %s=%s\n' "$(basename "$(dirname "$c")")" "$(cat "$c")"
        done
        echo "glmark2     : $(glmark2 --version 2>&1 | head -1)"
        echo "gpu_renderer: $(eglinfo 2>/dev/null | grep -m1 'OpenGL ES profile renderer' | sed 's/.*: //')"
    } > "$f" 2>/dev/null
    log "环境快照: $f"
}

# 可选：把 GPU governor 锁到 performance（需要 root，跑完请自行恢复）
lock_gpu_governor() {
    local d=/sys/class/devfreq/27800000.gpu
    [ -w "$d/governor" ] || { log "跳过 GPU 锁频（$d 不可写）"; return 0; }
    echo performance > "$d/governor" 2>/dev/null && log "GPU governor -> performance"
}
