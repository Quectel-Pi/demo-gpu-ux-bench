#!/bin/bash
# 板端一键安装：把 SDK 原地布置到可用状态
#
# 本 SDK 自包含，push 到开发板任意目录后直接执行本脚本即可，不依赖主机环境：
#   1) 建好 results/ logs/ assets/ 目录
#   2) 补上可执行权限
#   3) 检查依赖（--apt 时尝试用 apt 补装缺失包）
#   4) 跑一遍环境自检
#
# 用法:
#   install.sh            # 建目录 + 权限 + 依赖检查 + 自检
#   install.sh --apt      # 额外尝试 apt 安装缺失依赖（需要有软件源/外网）
set -uo pipefail

SDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SDK_DIR/common.sh"

USE_APT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --apt) USE_APT=1; shift ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

require_root
log "SDK 目录: $SDK_DIR"

log "创建产物目录"
ensure_dirs

log "设置可执行权限"
chmod 755 "$SDK_DIR" "$RESULT_DIR" "$LOG_DIR" "$ASSET_DIR" 2>/dev/null
chmod +x "$SDK_DIR"/*.sh "$SDK_DIR"/*.py 2>/dev/null

# 依赖清单：命令名 -> apt 包名
DEP_CMDS=(glmark2-es2-wayland glmark2-es2-drm ffmpeg modetest firefox-esr
          gst-launch-1.0 gst-inspect-1.0 python3 eglinfo)
DEP_PKGS=(glmark2-wayland glmark2-drm ffmpeg libdrm-tests firefox-esr
          gstreamer1.0-tools gstreamer1.0-tools python3 mesa-utils)
# GStreamer 元素对应的包（gst-inspect 检查）
DEP_ELEMS=(mppvideodec avdec_h264 waylandsink fpsdisplaysink qtdemux h264parse)
DEP_ELEM_PKGS=(gstreamer1.0-rockchip1 gstreamer1.0-libav gstreamer1.0-plugins-good
               gstreamer1.0-plugins-good gstreamer1.0-plugins-good gstreamer1.0-plugins-good)

log "检查依赖"
MISSING_PKGS=()
i=0
while [ "$i" -lt "${#DEP_CMDS[@]}" ]; do
    cmd="${DEP_CMDS[$i]}"; pkg="${DEP_PKGS[$i]}"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log "  缺少命令 $cmd（apt 包: $pkg）"
        MISSING_PKGS+=("$pkg")
    fi
    i=$((i + 1))
done
i=0
while [ "$i" -lt "${#DEP_ELEMS[@]}" ]; do
    elem="${DEP_ELEMS[$i]}"; pkg="${DEP_ELEM_PKGS[$i]}"
    if command -v gst-inspect-1.0 >/dev/null 2>&1 && \
       ! gst-inspect-1.0 "$elem" >/dev/null 2>&1; then
        log "  缺少 GStreamer 元素 $elem（apt 包: $pkg）"
        MISSING_PKGS+=("$pkg")
    fi
    i=$((i + 1))
done
[ -w /dev/uinput ] || log "  /dev/uinput 不可写：桌面动画场景的按键注入会失败（内核需 CONFIG_INPUT_UINPUT=y）"
python3 -c 'import gi; gi.require_version("Gst","1.0"); from gi.repository import Gst' 2>/dev/null \
    || { log "  python3 无法 import Gst（apt 包: python3-gi gir1.2-gstreamer-1.0）"
         MISSING_PKGS+=(python3-gi gir1.2-gstreamer-1.0); }
# score_window.py 需要 GTK3（跑分结束时在屏幕上弹出得分窗口）
python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' 2>/dev/null \
    || { log "  python3 无法 import Gtk 3.0（apt 包: python3-gi gir1.2-gtk-3.0）"
         MISSING_PKGS+=(python3-gi gir1.2-gtk-3.0); }

if [ "${#MISSING_PKGS[@]}" -gt 0 ]; then
    # 去重
    mapfile -t MISSING_PKGS < <(printf '%s\n' "${MISSING_PKGS[@]}" | awk '!seen[$0]++')
    if [ "$USE_APT" = "1" ]; then
        log "apt 安装缺失依赖: ${MISSING_PKGS[*]}"
        apt-get update && apt-get install -y "${MISSING_PKGS[@]}" \
            || log "apt 安装失败（板子可能无外网/无源），请自行预置依赖"
    else
        log "缺失依赖: ${MISSING_PKGS[*]}（可加 --apt 尝试安装，或自行预置）"
    fi
else
    log "依赖齐全"
fi

log "运行环境自检"
"$SDK_DIR/selfcheck.sh"
RC=$?

cat <<EOF

[install] 完成（自检退出码 $RC）
  SDK 位置 : $SDK_DIR
  产物目录 : $RESULT_DIR / $LOG_DIR / $ASSET_DIR
  下一步   :
    1) 确认开发板屏幕上已登录 GNOME 桌面（Wayland 口径必需）
    2) 一键评测:  $SDK_DIR/run-all.sh
    3) 单项评测:  $SDK_DIR/glmark2_bench.sh --backend wayland --runs 3
EOF
exit "$RC"
