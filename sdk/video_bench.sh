#!/bin/bash
# 视频播放流畅度打分
#
# 默认由板端 ffmpeg 现场生成两条可复现片源（普通项 + 压力项），
# 再用 GStreamer 分别以硬解(mppvideodec)/软解(avdec_h264) 播放，
# 由 fpsdisplaysink 给出 rendered/dropped/average_fps。
#
# 用法:
#   video_bench.sh [--source normal|stress|both] [--decoder mpp|sw|both]
#                  [--video /path/to/real.mp4] [--seconds 60]
set -uo pipefail

SDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "$SDK_DIR/common.sh"

SOURCE="both"
DECODER="both"
VIDEO=""
SECONDS_PER_CLIP=60

while [ $# -gt 0 ]; do
    case "$1" in
        --source) SOURCE="$2"; shift 2 ;;
        --decoder) DECODER="$2"; shift 2 ;;
        --video) VIDEO="$2"; shift 2 ;;
        --seconds) SECONDS_PER_CLIP="$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

require_root
ensure_dirs
detect_session

# 生成可复现片源（testsrc2 有持续运动细节，能真实压出解码+合成负载）
gen_clip() {
    local w="$1" h="$2" fps="$3" dur="$4" out="$5"
    [ -f "$out" ] && { log "复用已有片源 $out"; return 0; }
    log "生成测试片源 $out (${w}x${h}@${fps}fps ${dur}s)"
    ffmpeg -y -hide_banner -loglevel error \
        -f lavfi -i "testsrc2=size=${w}x${h}:rate=${fps}" \
        -t "$dur" -c:v libx264 -preset ultrafast -g $((fps * 2)) -pix_fmt yuv420p \
        "$out" || die "片源生成失败（可改用 --video 指定自带素材）"
    chmod 644 "$out"
}

run_one() {
    local file="$1" dec="$2" label="$3"
    local tmpout="/tmp/${label}.json"      # 以桌面用户身份运行，先写 /tmp 再由 root 归档
    log "播放 [$label] decoder=$dec file=$(basename "$file")"
    rm -f "$tmpout"
    as_user python3 "$SDK_DIR/video_probe.py" \
        --file "$file" --decoder "$dec" --label "$label" \
        --out "$tmpout" \
        2>&1 | tee "$LOG_DIR/${label}.log" | tail -3
    [ -f "$tmpout" ] && mv -f "$tmpout" "$RESULT_DIR/${label}.json"
    [ -f "$RESULT_DIR/${label}.json" ] || log "警告：${label} 未生成结果，见 $LOG_DIR/${label}.log"
}

declare -a CLIPS=()
if [ -n "$VIDEO" ]; then
    [ -f "$VIDEO" ] || die "指定的视频不存在: $VIDEO"
    CLIPS+=("$VIDEO|real")
else
    case "$SOURCE" in
        normal)
            CLIPS+=("$ASSET_DIR/video_1280x800_30fps_${SECONDS_PER_CLIP}s.mp4|normal")
            ;;
        stress)
            CLIPS+=("$ASSET_DIR/video_1920x1080_60fps_30s.mp4|stress")
            ;;
        both)
            CLIPS+=("$ASSET_DIR/video_1280x800_30fps_${SECONDS_PER_CLIP}s.mp4|normal")
            CLIPS+=("$ASSET_DIR/video_1920x1080_60fps_30s.mp4|stress")
            ;;
        *) die "--source 只支持 normal|stress|both" ;;
    esac
fi

if [ -z "$VIDEO" ]; then
    case "$SOURCE" in
        normal|both)
            gen_clip 1280 800 30 "$SECONDS_PER_CLIP" \
                "$ASSET_DIR/video_1280x800_30fps_${SECONDS_PER_CLIP}s.mp4" ;;
    esac
    case "$SOURCE" in
        stress|both)
            gen_clip 1920 1080 60 30 "$ASSET_DIR/video_1920x1080_60fps_30s.mp4" ;;
    esac
fi

for entry in "${CLIPS[@]}"; do
    file="${entry%%|*}"
    tag="${entry##*|}"
    [ -f "$file" ] || continue
    case "$DECODER" in
        mpp)  run_one "$file" mpp "video_${tag}_mpp" ;;
        sw)   run_one "$file" sw  "video_${tag}_sw" ;;
        both) run_one "$file" mpp "video_${tag}_mpp"
              run_one "$file" sw  "video_${tag}_sw" ;;
        *) die "--decoder 只支持 mpp|sw|both" ;;
    esac
    sleep 3
done

log "视频播放场景完成，结果目录: $RESULT_DIR"
