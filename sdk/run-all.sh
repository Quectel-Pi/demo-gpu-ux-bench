#!/bin/bash
# 板端一键执行完整评测：桌面动画 / 网页滚动 / 视频播放 + glmark2 全项跑分 + 汇总打分
#
# 全程在开发板上运行，不依赖主机环境；日志落在 $LOG_DIR，原始数据落在 $RESULT_DIR，
# 最后由 score.py 生成 $RESULT_DIR/score_report.md。
#
# 执行顺序刻意把 glmark2 放最后：若带 --with-drm，DRM 口径需要停掉 gdm 独占 KMS，
# 会终结当前桌面会话，之后必须先重新登录桌面才能再做 GUI 场景。
#
# 用法:
#   run-all.sh [--with-drm] [--runs N] [--quick]
#              [--skip-anim] [--skip-web] [--skip-video] [--skip-gpu]
set -uo pipefail

SDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SDK_DIR/common.sh"

BACKEND="wayland"
RUNS=3
ANIM_SEC=10
WEB_SEC=10
VIDEO_SOURCE="both"
VIDEO_DECODER="both"
SKIP_ANIM=0
SKIP_WEB=0
SKIP_VIDEO=0
SKIP_GPU=0

while [ $# -gt 0 ]; do
    case "$1" in
        --with-drm) BACKEND="both"; shift ;;
        --runs) RUNS="$2"; shift 2 ;;
        --quick) RUNS=1; ANIM_SEC=5; WEB_SEC=5
                 VIDEO_SOURCE="normal"; VIDEO_DECODER="mpp"; shift ;;
        --skip-anim) SKIP_ANIM=1; shift ;;
        --skip-web) SKIP_WEB=1; shift ;;
        --skip-video) SKIP_VIDEO=1; shift ;;
        --skip-gpu) SKIP_GPU=1; shift ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

require_root
ensure_dirs

run_stage() {
    local name="$1"; shift
    local logf="$LOG_DIR/${name}_$(date +%Y%m%d_%H%M%S).log"
    log "===== $name ====="
    bash "$@" 2>&1 | tee "$logf"
    local rc=${PIPESTATUS[0]}
    [ "$rc" = "0" ] || log "$name 返回码 $rc（详见 $logf）"
    return 0
}

[ "$SKIP_ANIM"  = "0" ] && run_stage anim    "$SDK_DIR/anim_bench.sh" --seconds "$ANIM_SEC"
[ "$SKIP_WEB"   = "0" ] && run_stage web     "$SDK_DIR/web_bench.sh" --seconds "$WEB_SEC"
[ "$SKIP_VIDEO" = "0" ] && run_stage video   "$SDK_DIR/video_bench.sh" --source "$VIDEO_SOURCE" --decoder "$VIDEO_DECODER"
[ "$SKIP_GPU"   = "0" ] && run_stage glmark2 "$SDK_DIR/glmark2_bench.sh" --backend "$BACKEND" --runs "$RUNS"

log "生成打分报告"
python3 "$SDK_DIR/score.py" --results-dir "$RESULT_DIR" \
    --out "$RESULT_DIR/score_report.md" \
    --json "$RESULT_DIR/score_summary.json"

cat <<EOF

[run-all] 完成
  打分报告: $RESULT_DIR/score_report.md
  原始数据: $RESULT_DIR
  运行日志: $LOG_DIR
EOF

if [ "$BACKEND" = "both" ]; then
    cat <<'EOF'

[run-all] 注意：DRM 口径停过 gdm，桌面会话已重启。
          如需再次采集 GUI 场景，请先在开发板屏幕上重新登录 GNOME 桌面。
EOF
fi

# 最后再打一次精简的"最终得分"横幅，避免被前面的 glmark2 明细淹没
python3 "$SDK_DIR/score.py" --results-dir "$RESULT_DIR" --summary

# 在开发板屏幕上弹出得分窗口（横向展示各项结果）。
# setsid 必须放在 as_user 之后（as_user 是 shell 函数，不能被 setsid 直接 exec）：
# 它让窗口脱离当前会话，既不会被 adb shell 退出时杀掉，也不会抓住 adb 的 stdout 导致命令不返回。
show_score_window() {
    local sock
    for sock in /run/user/*/wayland-0; do
        [ -S "$sock" ] && break
    done
    [ -S "$sock" ] || { log "跳过得分窗口（未检测到桌面会话）"; return 0; }

    detect_session
    pkill -u "$BENCH_USER" -f "score_window.py" 2>/dev/null   # 避免重复跑堆积窗口
    log "弹出得分窗口（见开发板屏幕），日志: $LOG_DIR/score_window.log"
    as_user setsid python3 "$SDK_DIR/score_window.py" \
        --summary-json "$RESULT_DIR/score_summary.json" \
        </dev/null >>"$LOG_DIR/score_window.log" 2>&1 &
}

show_score_window
