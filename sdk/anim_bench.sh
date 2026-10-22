#!/bin/bash
# 桌面动画流畅度打分（真实交互触发 + kmsgrab 旁路采样）
#
# 触发的是 GNOME 自己的合成器动画（概览开关、工作区切换、窗口开合），
# 通过 uinput 注入真实按键 / 启动真实窗口，不注入任何"假渲染负载"；
# 同时用 frame_monitor.py 从 DRM 主平面旁路采样画面，统计有效帧率与卡顿。
#
# 用法:
#   anim_bench.sh [--seconds 10] [--items overview,workspace,window]
set -uo pipefail

SDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SDK_DIR/common.sh"

SECONDS_PER_ITEM=10
ITEMS="overview,workspace,window"

while [ $# -gt 0 ]; do
    case "$1" in
        --seconds) SECONDS_PER_ITEM="$2"; shift 2 ;;
        --items) ITEMS="$2"; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

require_root
ensure_dirs
detect_session

# 生成 overview 动作脚本（每行一条指令，交给 input_inject.py - 复用同一个虚拟键盘）
gen_overview_script() {
    local rounds="$1" i
    for i in $(seq 1 "$rounds"); do
        echo "tap leftmeta"; echo "sleep 1.2"
        echo "tap escape";   echo "sleep 1.2"
    done
}

# 窗口开合动画：直接拉起/关闭真实应用窗口
trigger_window_item() {
    local dur="$1" end
    end=$(( $(date +%s) + dur ))
    while [ "$(date +%s)" -lt "$end" ]; do
        as_user gnome-text-editor >/dev/null 2>&1 &
        sleep 2.5
        pkill -u "$BENCH_USER" -f gnome-text-editor 2>/dev/null
        sleep 1
    done
    pkill -u "$BENCH_USER" -f gnome-text-editor 2>/dev/null
}

# 工作区切换动画：本板各工作区壁纸相同，若工作区里没有窗口，切换时画面完全没变化
# （采样会判定"无动画"）。因此先在当前工作区留一个窗口，再来回切工作区。
# 按键用 <Super>Page_Down/Page_Up（本板实测生效并产生滑动动画）；
# 绑定的 <Control><Alt>Down/Up 在本板注入后不生效，不可用。
trigger_workspace_item() {
    local dur="$1" rounds i
    rounds=$(( dur / 3 + 1 ))
    as_user gnome-text-editor >/dev/null 2>&1 &
    sleep 3
    {
        for i in $(seq 1 "$rounds"); do
            echo "combo leftmeta pagedown"; echo "sleep 1.2"
            echo "combo leftmeta pageup";   echo "sleep 1.2"
        done
    } | "$SDK_DIR/input_inject.py" - --max-seconds "$dur" >/dev/null 2>&1
    pkill -u "$BENCH_USER" -f gnome-text-editor 2>/dev/null
}

run_item() {
    local item="$1" dur="$2" rounds
    log "动画场景 [$item] 采样 ${dur}s"

    frame_monitor_py --duration "$dur" --label "anim_$item" \
        --out "$RESULT_DIR/anim_${item}.json" &
    local mon=$!
    sleep 1

    case "$item" in
        overview)
            rounds=$(( dur / 3 + 1 ))
            gen_overview_script "$rounds" | "$SDK_DIR/input_inject.py" - --max-seconds "$dur" >/dev/null 2>&1 &
            ;;
        workspace) trigger_workspace_item "$dur" & ;;
        window)    trigger_window_item "$dur" & ;;
        *)
            log "跳过未知场景: $item"
            kill "$mon" 2>/dev/null
            return 1
            ;;
    esac
    local inj=$!

    wait "$mon" || log "警告：[$item] 采样异常，见上方输出"
    wait "$inj" 2>/dev/null
    pkill -u "$BENCH_USER" -f gnome-text-editor 2>/dev/null
    if [ -f "$RESULT_DIR/anim_${item}.json" ]; then
        log "[$item] 完成 -> $RESULT_DIR/anim_${item}.json"
    else
        log "警告：[$item] 未生成结果（kmsgrab 采样失败）"
    fi
}

# 允许用环境变量覆盖 python 解释器（板上就是 python3）
frame_monitor_py() {
    python3 "$SDK_DIR/frame_monitor.py" "$@"
}

# 预热：让 LibreOffice/文本编辑器首次启动的冷启动开销不影响采样
as_user gnome-text-editor >/dev/null 2>&1 &
sleep 3
pkill -u "$BENCH_USER" -f gnome-text-editor 2>/dev/null
sleep 2

IFS=',' read -r -a ITEM_LIST <<< "$ITEMS"
for item in "${ITEM_LIST[@]}"; do
    run_item "$item" "$SECONDS_PER_ITEM"
    sleep 2   # 场景之间留出恢复时间
done

log "桌面动画场景采样完成，结果目录: $RESULT_DIR"
