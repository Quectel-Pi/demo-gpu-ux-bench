#!/bin/bash
# glmark2 全项跑分（Wayland 主口径 + DRM 对照）
#
# Wayland 口径：在已登录的 GNOME Wayland 会话内跑 glmark2-es2-wayland，
#               含合成器开销，最贴近真实桌面体验。
# DRM 口径   ：停掉 gdm 后直接跑 glmark2-es2-drm，独占 KMS，作为纯 GPU 上限对照。
#               （会让当前桌面会话退出，跑完自动拉起 gdm，但需要重新登录桌面）
#
# 用法:
#   glmark2_bench.sh [--backend wayland|drm|both] [--runs N] [--lock-gpu] [--validate] [--size WxH]
set -uo pipefail

SDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SDK_DIR/common.sh"

BACKEND="wayland"
RUNS=3
LOCK_GPU=0
VALIDATE=0
SIZE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --backend) BACKEND="$2"; shift 2 ;;
        --runs) RUNS="$2"; shift 2 ;;
        --lock-gpu) LOCK_GPU=1; shift ;;
        --validate) VALIDATE=1; shift ;;
        --size) SIZE="$2"; shift 2 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

require_root
ensure_dirs
snapshot_env "$LOG_DIR/env_before_glmark2.txt"
[ "$LOCK_GPU" = "1" ] && lock_gpu_governor

GDM_UNIT=""
detect_gdm_unit() {
    for u in gdm3 gdm; do
        if systemctl list-unit-files 2>/dev/null | grep -q "^$u.service"; then
            GDM_UNIT="$u"
            return 0
        fi
    done
    return 1
}

restore_gdm() {
    if [ -n "${GDM_STOPPED:-}" ]; then
        log "恢复 $GDM_UNIT ..."
        systemctl start "$GDM_UNIT" || log "警告：$GDM_UNIT 启动失败，请手动检查"
        GDM_STOPPED=""
    fi
}

GLMARK2_ARGS=()
[ -n "$SIZE" ] && GLMARK2_ARGS+=(--size "$SIZE") || GLMARK2_ARGS+=(--fullscreen)

# glmark2 把官方总分打在 stdout（"... glmark2 Score: 58"）。它和各场景 FPS 的
# 几何平均不是一回事（官方口径有归一化），所以单独落一份，score.py 优先取它。
extract_score() {
    local logf="$1" outf="$2" s
    s="$(sed -n 's/.*glmark2 Score:[[:space:]]*\([0-9.]*\).*/\1/p' "$logf" | tail -1)"
    [ -n "$s" ] && printf '%s\n' "$s" > "$outf"
}

run_wayland() {
    detect_session
    if [ "$VALIDATE" = "1" ]; then
        log "预检：glmark2-es2-wayland --validate"
        as_user glmark2-es2-wayland --validate 2>&1 | tail -5 | tee "$LOG_DIR/glmark2_wayland_validate.log"
    fi
    local i csv
    for i in $(seq 1 "$RUNS"); do
        csv="/tmp/gm_wayland_run${i}.csv"
        log "Wayland 第 $i/$RUNS 轮：glmark2-es2-wayland ${GLMARK2_ARGS[*]}（全项 17 场景）"
        as_user glmark2-es2-wayland "${GLMARK2_ARGS[@]}" \
            --results-file "$csv" --results fps \
            2>&1 | tee "$LOG_DIR/glmark2_wayland_run${i}.log" | tail -3
        [ -f "$csv" ] && cp "$csv" "$RESULT_DIR/glmark2_wayland_run${i}.csv" \
            || log "警告：第 $i 轮没有生成结果文件（见 $LOG_DIR/glmark2_wayland_run${i}.log）"
        rm -f "$csv"
        extract_score "$LOG_DIR/glmark2_wayland_run${i}.log" \
                      "$RESULT_DIR/glmark2_wayland_run${i}.score"
    done
}

run_drm() {
    detect_gdm_unit || log "未找到 gdm 服务单元，DRM 口径需要手动停止显示管理器"
    if [ -n "$GDM_UNIT" ] && systemctl is-active --quiet "$GDM_UNIT"; then
        log "停止 $GDM_UNIT 以让出 DRM master"
        systemctl stop "$GDM_UNIT"
        GDM_STOPPED=1
        sleep 3
    fi
    trap restore_gdm EXIT INT TERM

    local i csv
    for i in $(seq 1 "$RUNS"); do
        csv="/tmp/gm_drm_run${i}.csv"
        log "DRM 第 $i/$RUNS 轮：glmark2-es2-drm ${GLMARK2_ARGS[*]}"
        glmark2-es2-drm "${GLMARK2_ARGS[@]}" \
            --results-file "$csv" --results fps \
            2>&1 | tee "$LOG_DIR/glmark2_drm_run${i}.log" | tail -3
        [ -f "$csv" ] && cp "$csv" "$RESULT_DIR/glmark2_drm_run${i}.csv" \
            || log "警告：第 $i 轮 DRM 失败，可尝试先 modetest -s <connector_id>:1280x800 -d & 再重跑"
        rm -f "$csv"
        extract_score "$LOG_DIR/glmark2_drm_run${i}.log" \
                      "$RESULT_DIR/glmark2_drm_run${i}.score"
    done

    restore_gdm
    trap - EXIT INT TERM
    log "注意：桌面会话已重启，需要重新在屏幕上登录 GNOME 后，才能继续跑 Wayland 口径"
}

case "$BACKEND" in
    wayland) run_wayland ;;
    drm) run_drm ;;
    both) run_wayland; run_drm ;;
    *) die "--backend 只支持 wayland|drm|both" ;;
esac

snapshot_env "$LOG_DIR/env_after_glmark2.txt"
log "结果目录: $RESULT_DIR"
