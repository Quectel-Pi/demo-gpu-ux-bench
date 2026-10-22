#!/bin/bash
# 网页滚动流畅度打分
#
# 数据来源：
#   1) 页面内 rAF 时间戳（主口径）—— 合成器给页面的帧回调节拍，即滚动时的真实帧率
#   2) kmsgrab 旁路采样（可选，用于交叉验证）—— 从 DRM 主平面看实际显示出来的帧
#
# 用法:
#   web_bench.sh [--seconds 10] [--port 8799] [--timeout 180] [--no-capture]
set -uo pipefail

SDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SDK_DIR/common.sh"

SCROLL_SECONDS=10
PORT=8799
TIMEOUT=180
CAPTURE=1

while [ $# -gt 0 ]; do
    case "$1" in
        --seconds) SCROLL_SECONDS="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --no-capture) CAPTURE=0; shift ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

require_root
ensure_dirs
detect_session

WEB_DIR="$SDK_DIR/web"
PROFILE="/tmp/ff-bench-profile"
mkdir -p "$PROFILE"
chown -R "$BENCH_USER:$BENCH_USER" "$PROFILE"

# 最小化首次启动干扰（不弹默认浏览器询问/欢迎页/遥测）
cat > "$PROFILE/user.js" <<'EOF'
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("browser.shell.shortcutFavicons", false);
user_pref("layout.frame_rate", 0);
EOF
chown "$BENCH_USER:$BENCH_USER" "$PROFILE/user.js"
rm -f "$PROFILE/lock" "$PROFILE/.parentlock"

log "启动结果接收服务 127.0.0.1:$PORT（最长等待 ${TIMEOUT}s）"
python3 "$WEB_DIR/collect_server.py" --port "$PORT" \
    --out "$RESULT_DIR/web_scroll.json" --timeout "$TIMEOUT" &
SRV_PID=$!

CAP_PID=""
if [ "$CAPTURE" = "1" ]; then
    log "旁路采样 kmsgrab（${SCROLL_SECONDS}s + 预热余量）"
    python3 "$SDK_DIR/frame_monitor.py" --duration $((SCROLL_SECONDS + 8)) \
        --label web_scroll --out "$RESULT_DIR/web_scroll_capture.json" \
        >/dev/null 2>&1 &
    CAP_PID=$!
fi

sleep 1
URL="file://$WEB_DIR/scroll_test.html?seconds=${SCROLL_SECONDS}&port=${PORT}"
log "以 $BENCH_USER 身份启动 firefox-esr（kiosk，独立 profile）"
as_user firefox-esr --kiosk --no-remote -profile "$PROFILE" "$URL" >/dev/null 2>&1 &
FF_PID=$!

wait "$SRV_PID"
RC=$?

pkill -u "$BENCH_USER" -f firefox-esr 2>/dev/null
wait "$FF_PID" 2>/dev/null
[ -n "$CAP_PID" ] && wait "$CAP_PID" 2>/dev/null

if [ "$RC" = "0" ]; then
    log "网页滚动场景完成 -> $RESULT_DIR/web_scroll.json"
else
    log "警告：没有收到网页上报。排查：确认屏幕上 firefox 是否正常打开并滚动（可加 --timeout 300）"
fi
exit "$RC"
