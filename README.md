# GPU 渲染基准与界面体验打分（RK3576 / QuecPi M2 Debian）

面向 **Quectel PI M2（RK3576）+ Debian 13 + GNOME Wayland** 开发板的图形性能评测包：
一套 **glmark2 全项跑分**，加上 **桌面动画 / 网页滚动 / 视频播放** 三个真实场景的流畅度打分表。

参考项目：[glmark2/glmark2](https://github.com/glmark2/glmark2)（OpenGL 2.0 / ES 2.0 benchmark suite，meson 构建）

---

## 1. 板端实测环境基线（本文档编写时实测）

| 项       | 实测值                                                                                                                                                       |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 设备      | Quectel PI M2，`Quectel PI M2 RK3576 Board`，kernel `6.1.118-rt36 PREEMPT_RT`，aarch64                                                                       |
| 系统      | Debian GNU/Linux 13 (trixie)                                                                                                                              |
| 桌面      | GNOME Shell 48.7，Wayland 会话（`gnome-wayland.desktop`）                                                                                                      |
| 显示      | 仅 MIPI DSI 接屏，**1280x800 @60.03Hz**；HDMI-A-1 未连接                                                                                                          |
| GPU     | Mali-G52 r1，驱动 **panfrost**（`/dev/dri/card1`、`renderD129`），Mesa 25.0.7，GLES 3.1                                                                           |
| 显示控制器   | 独立的 `rockchip-drm`（`/dev/dri/card0`，DSI-1 connector id = 430）                                                                                             |
| glmark2 | **2023.01（apt 已装齐 6 个 flavor）**，默认 17 个场景                                                                                                                 |
| 其它      | ffmpeg（含 kmsgrab + libx264）、GStreamer 1.26.2（rockchipmpp/waylandsink/fpsdisplaysink）、firefox-esr 153、python3+gi、`/dev/uinput`（内核 `CONFIG_INPUT_UINPUT=y`） |

> glmark2 的 6 个 flavor 由包提供：`glmark2`/`glmark2-es2`（X11）、`glmark2-wayland`/`glmark2-es2-wayland`、
> `glmark2-drm`/`glmark2-es2-drm`。**本方案不需要交叉编译 glmark2**，直接用发行版包即可；
> 只有当需要 glmark2 上游 master 的新特性时才需要自己用 meson 交叉编译。

### 板载 GPU 与显示架构（实测）

RK3576 内置 **ARM Mali-G52 MC3**（Bifrost 架构）。板端只能报到 `Mali-G52 r1`，设备树
`/gpu@27800000` 的 `compatible = "arm,mali-bifrost"`；MC3 这个型号来自 RK3576 规格书。
GPU 频率有 **300–900 MHz 共 7 档**，由 `simple_ondemand` 调频——这也是做 GPU 锁频时唯一要关心的节点：

```bash
cat /sys/class/devfreq/27800000.gpu/{governor,cur_freq,available_frequencies}
```

**驱动栈是开源的 panfrost + Mesa 25.0.7**，不是 Arm 官方的 Mali blob 驱动。
本板实测暴露出的图形 API 能力（`eglinfo`）：

| 项 | 实测值 |
|---|---|
| renderer | `Mali-G52 r1 (Panfrost)` |
| EGL | 1.5（Mesa Project） |
| OpenGL | 3.1 Core Profile（GLSL 1.40） |
| OpenGL ES | 3.1（GLSL ES 3.10） |
| Vulkan | 无驱动（`/usr/share/vulkan/icd.d/` 为空） |

> SoC 标称 OpenGL ES 3.2 / Vulkan 1.1 / OpenCL 2.0，但**本板只暴露到 GLES 3.1、且没有 Vulkan**——
> 这是当前驱动栈的现状而不是硬件限制。所以本方案用 glmark2 的 **GLES 2.0 场景集**做基准，
> 不去碰标称值够不到的那部分。

**关键：本板是"分体式"DRM 架构**，3D GPU 与显示控制器是两个独立的 DRM 设备：

| 设备 | 驱动 | 角色 |
|---|---|---|
| `card0` / `renderD128` | `rockchip-drm` | 显示控制器（VOP）：DSI-1、HDMI-A-1、Writeback-1 |
| `card1` / `renderD129` | `panfrost` | 3D GPU（Mali-G52），**自身没有显示输出** |
| `card2` / `renderD130` | `RKNPU` | NPU（AI 推理），与图形无关 |

两者靠 Mesa 的 **kmsro** 串联：GPU 渲染到 DRM buffer，再由 rockchip-drm 扫描上屏。
这正是 glmark2 要跑两个口径的原因——**Wayland 口径**走完整合成链路（GPU → kmsro → 合成器 → VOP，
最贴近真实体验，作为主口径）；**DRM 口径**绕开合成器直接独占 KMS 输出，用来看链路本身损耗多少
（需先停 gdm 让出 DRM master，见 6.3 节）。

**别把隔壁的加速单元当成 GPU**——视频场景能硬解靠的不是它：

| 单元 | 设备节点 | 用途 |
|---|---|---|
| VPU（Rockchip MPP） | `/dev/mpp_service` | H.264/H.265/VP9/AV1 硬解硬编（`mppvideodec` 走这里） |
| NPU | `/dev/dri/card2` | 6 TOPS INT8 AI 推理 |
| RGA（2D） | `/dev/rga` | 2D 光栅加速：缩放/旋转/格式转换/合成 |

---

## 2. 目录结构

整个 `sdk/` 目录是**自包含的**：push 到开发板任意目录后即可原地执行，**不依赖主机环境**
（不需要主机 adb 编排、不需要网络下载）。所有产物都落在 SDK 目录内部：

```
sdk/                               # 板端 SDK（push 到板子任意目录即可，例如 /opt/gpu-bench/sdk）
├── install.sh                     # 【入口1】一键安装：建目录 + 补权限 + 依赖检查 + 环境自检
├── run-all.sh                     # 【入口2】一键评测：三场景采集 + glmark2 跑分 + 汇总打分
├── common.sh                      # 公共库：会话探测、以桌面用户执行、环境快照
├── selfcheck.sh                   # 环境自检（install.sh 会自动调用）
├── glmark2_bench.sh               # glmark2 全项跑分（Wayland 主口径 + DRM 对照）
├── frame_monitor.py               # kmsgrab 屏幕帧间隔采样器（桌面动画/网页旁路交叉验证）
├── input_inject.py                # uinput 虚拟键盘（真实按键触发动画/交互）
├── anim_bench.sh                  # 场景一：桌面动画
├── web_bench.sh                   # 场景二：网页滚动
├── web/scroll_test.html           #   网页场景测试页（页面内 rAF 采集）
├── web/collect_server.py          #   结果回传服务（仅监听 127.0.0.1）
├── video_bench.sh                 # 场景三：视频播放（片源生成 + 硬解/软解）
├── video_probe.py                 #   GStreamer fpsdisplaysink 播放统计
├── score.py                       # 汇总打分（生成 results/score_report.md + score_summary.json）
├── score_window.py                # 【GTK3】在开发板屏幕上横向弹窗展示最终得分
├── results/                       # 【运行后生成】原始数据 + 打分报告
├── logs/                          # 【运行后生成】每步完整日志 + 环境快照
└── assets/                        # 【运行后生成】现场生成的测试片源
```

顶层只有本 `README.md` 与 `sdk/`。

---

## 3. 快速开始

```bash
# 0) 前提：开发板屏幕上已登录 GNOME 桌面（Wayland 口径必需）

# 1) 把 SDK 推到板子（在主机上执行一次；目标目录任意，下面以 /opt/gpu-bench 为例）
#    注意 A：路径含空格/中文时**必须加引号**，否则 adb 会按空格拆成多个参数
#    注意 B：目标目录已存在时**先删掉**，否则 adb 会嵌套成 /opt/gpu-bench/sdk/sdk 且不覆盖旧文件
adb shell rm -rf /opt/gpu-bench/sdk
adb push "<本地路径>/sdk" /opt/gpu-bench/sdk

# 2) 进板子，一键安装（建目录 + 权限 + 依赖检查 + 环境自检）
adb shell /opt/gpu-bench/sdk/install.sh           # 缺依赖时加 --apt 可尝试自动补装

# 3) 一键评测（全程在板子上跑；--quick 是冒烟跑法）
adb shell /opt/gpu-bench/sdk/run-all.sh --quick
adb shell /opt/gpu-bench/sdk/run-all.sh                 # 正式：glmark2 3 轮，动画/网页各 10s，视频硬解+软解
adb shell /opt/gpu-bench/sdk/run-all.sh --with-drm      # 追加 DRM 直连 KMS 对照（注意第 6.3 节）

# 4) 取回结果（在主机上执行）
adb pull /opt/gpu-bench/sdk/results
```

> 也可以只在板子上执行——`install.sh` / `run-all.sh` 里没有任何主机侧依赖，
> 直接在板端 shell 里 `./install.sh`、`./run-all.sh` 同样可用。

**每个脚本的完整执行命令见第 4 节「命令速查」。**

---

## 4. 命令速查（全部脚本）

> - 所有脚本都在**板端**执行，并且需要 **root**（要用 `/dev/uinput`、kmsgrab、`systemctl` 等）。
> - 每个脚本都支持 `-h` / `--help`，直接打印脚本内嵌用法。
> - 下文统一用 `/opt/gpu-bench/sdk` 作为 SDK 路径，换成你自己的路径即可。

### 4.1 入口脚本

```bash
# 一键安装：建目录 + 补执行权限 + 依赖检查 + 环境自检（install.sh 内部会调 selfcheck.sh）
/opt/gpu-bench/sdk/install.sh
/opt/gpu-bench/sdk/install.sh --apt          # 依赖缺失时尝试 apt-get install 自动补装

# 环境自检：只读检查，不改动系统；输出 OK/WARN/FAIL 计数
/opt/gpu-bench/sdk/selfcheck.sh

# 一键评测：桌面动画 → 网页滚动 → 视频播放 → glmark2 → 汇总打分
/opt/gpu-bench/sdk/run-all.sh                     # 正式：glmark2 3 轮，动画/网页各 10s，视频硬解+软解
/opt/gpu-bench/sdk/run-all.sh --quick             # 冒烟：glmark2 1 轮，动画/网页各 5s，仅常规片源 + 硬解
/opt/gpu-bench/sdk/run-all.sh --runs 5            # glmark2 跑 5 轮
/opt/gpu-bench/sdk/run-all.sh --with-drm          # 追加 DRM 直连 KMS 对照口径（会停 gdm，见 6.3 节）
/opt/gpu-bench/sdk/run-all.sh --skip-web --skip-gpu                      # 只跑动画 + 视频
/opt/gpu-bench/sdk/run-all.sh --skip-anim --skip-web --skip-video        # 只跑 glmark2
```

`--skip-*` 可任意组合，用来复用已有结果只补跑其中一项。

### 4.2 单项脚本

```bash
# ---- glmark2 全项跑分（默认 --backend wayland --runs 3）----
/opt/gpu-bench/sdk/glmark2_bench.sh --backend wayland --runs 3
/opt/gpu-bench/sdk/glmark2_bench.sh --backend drm --runs 1        # DRM 对照口径（会停 gdm）
/opt/gpu-bench/sdk/glmark2_bench.sh --backend both --runs 3       # 两个口径都跑
/opt/gpu-bench/sdk/glmark2_bench.sh --backend wayland --runs 3 --lock-gpu   # GPU governor 锁 performance
/opt/gpu-bench/sdk/glmark2_bench.sh --backend wayland --runs 1 --validate   # 先跑 glmark2 --validate 预检 GL 上下文
/opt/gpu-bench/sdk/glmark2_bench.sh --size 800x600 --runs 1       # 指定画布尺寸（默认 --fullscreen）

# ---- 桌面动画（默认 --seconds 10 --items overview,workspace,window）----
/opt/gpu-bench/sdk/anim_bench.sh
/opt/gpu-bench/sdk/anim_bench.sh --seconds 15
/opt/gpu-bench/sdk/anim_bench.sh --items overview
/opt/gpu-bench/sdk/anim_bench.sh --items workspace,window

# ---- 网页滚动（默认 --seconds 10 --port 8799 --timeout 180）----
/opt/gpu-bench/sdk/web_bench.sh
/opt/gpu-bench/sdk/web_bench.sh --seconds 15
/opt/gpu-bench/sdk/web_bench.sh --timeout 300        # firefox 首次启动慢时加大等待
/opt/gpu-bench/sdk/web_bench.sh --no-capture         # 只跑页面内 rAF，不做 kmsgrab 旁路采样
/opt/gpu-bench/sdk/web_bench.sh --port 8800          # 换回传端口（默认 8799）

# ---- 视频播放（默认 --source both --decoder both --seconds 60）----
/opt/gpu-bench/sdk/video_bench.sh
/opt/gpu-bench/sdk/video_bench.sh --source normal --decoder mpp   # 只跑常规片源 + 硬解
/opt/gpu-bench/sdk/video_bench.sh --source stress --decoder sw    # 只跑压力片源 + 软解
/opt/gpu-bench/sdk/video_bench.sh --video /home/q/my.mp4 --decoder both   # 用真实素材（跳过现场生成）
/opt/gpu-bench/sdk/video_bench.sh --seconds 20       # 每条片源播放 20s

# ---- 汇总打分（一般由 run-all.sh 自动调用，也可单独跑）----
/opt/gpu-bench/sdk/score.py --results-dir /opt/gpu-bench/sdk/results \
                            --out /opt/gpu-bench/sdk/results/score_report.md \
                            --json /opt/gpu-bench/sdk/results/score_summary.json
/opt/gpu-bench/sdk/score.py --results-dir /opt/gpu-bench/sdk/results --summary   # 只打印末尾精简得分横幅
/opt/gpu-bench/sdk/score.py --results-dir /opt/gpu-bench/sdk/results \
                            --json /opt/gpu-bench/sdk/results/score_summary.json   # 只产出机器可读 JSON
```

### 4.3 底层工具（正常不用单独跑，被上面的脚本调用）

```bash
# kmsgrab 帧间隔采样（桌面动画 + 网页旁路共用）
python3 /opt/gpu-bench/sdk/frame_monitor.py --duration 10 --label demo --out /tmp/demo.json
python3 /opt/gpu-bench/sdk/frame_monitor.py --duration 10 --fps 60 \
        --idle-ms 200 --threshold 1.0 --max-restarts 8
python3 /opt/gpu-bench/sdk/frame_monitor.py --duration 10 --device /dev/dri/card0 \
        --download-format x2rgb10le --width 64 --height 44     # 一般让它自动探测

# uinput 虚拟键盘（真实按键注入，需 root）
python3 /opt/gpu-bench/sdk/input_inject.py tap leftmeta
python3 /opt/gpu-bench/sdk/input_inject.py combo leftmeta pagedown
python3 /opt/gpu-bench/sdk/input_inject.py tap pagedown --count 20 --interval 0.5
printf 'tap leftmeta\nsleep 1\ntap escape\n' | \
    python3 /opt/gpu-bench/sdk/input_inject.py - --max-seconds 10

# GStreamer 播放统计（video_bench.sh 内部调用）
python3 /opt/gpu-bench/sdk/video_probe.py \
    --file /opt/gpu-bench/sdk/assets/video_1280x800_30fps_60s.mp4 \
    --decoder mpp --label demo --out /tmp/demo.json
python3 /opt/gpu-bench/sdk/video_probe.py \
    --file /opt/gpu-bench/sdk/assets/video_1920x1080_60fps_30s.mp4 \
    --decoder sw --label stress_sw --out /tmp/stress_sw.json --no-sync   # 关时钟同步，测解码吞吐上限

# 网页结果接收服务（web_bench.sh 内部调用，仅监听 127.0.0.1）
python3 /opt/gpu-bench/sdk/web/collect_server.py --port 8799 --out /tmp/web.json --timeout 180
```

### 4.4 结果窗口（`score_window.py`）

GTK3 窗口，在开发板屏幕上**横向**并排展示综合分与 4 个维度的得分/等级/权重/关键数据。
`run-all.sh` 跑完会自动弹出（用 `setsid` 脱离会话，不会阻塞脚本退出）；也可单独执行：

```bash
# 注意：必须切到已登录的桌面用户，且带上 Wayland 环境（等价于 run-all.sh 内部的做法）
runuser -u q -- env HOME=/home/q XDG_RUNTIME_DIR=/run/user/1001 \
    WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 \
    python3 /opt/gpu-bench/sdk/score_window.py \
    --summary-json /opt/gpu-bench/sdk/results/score_summary.json
```

> 窗口数据来自 `score.py --json` 产出的 `results/score_summary.json`，
> 窗口本身不做任何计算；所以先跑一次 `score.py --json` 才有内容可显示。

---

## 5. 指标口径定义

| 指标 | 定义 |
|---|---|
| 动画帧率（有效 FPS） | 动画/滚动**期间**的帧率 = 1000 / 有效帧间隔均值。间隔 ≥ `--idle-ms`（默认 200ms）视为"场景静止"（合成器不重绘），不计入统计 |
| idle_gaps | 被排除的静止间隔个数，用来判断动作是否真的发生了 |
| 帧间隔 p50/p95/p99 | 相邻更新帧间隔的分位数（ms） |
| **jank** | 帧间隔 > **1.5 × 目标帧间隔**（60Hz → 25ms）的帧，报告次数与占比 |
| 最长卡顿 | 采样窗口内最大单次帧间隔（ms） |
| 丢帧估算 | `Σ max(0, round(间隔/目标间隔) − 1)` |
| drop_rate（视频） | `dropped / (rendered + dropped)`，由 GStreamer `fpsdisplaysink` 统计 |

> **为什么必须排除"静止间隔"**：GNOME 在画面不变时不重绘，所以一段 5s 采样里如果动画只持续
> 0.5s，把静止时间算进去会得出"几 FPS"的荒谬结果。排除后得到的是动画本身的帧率。

**目标刷新率以实测为准**：桌面动画场景取面板 60Hz；网页滚动场景取页面开始时自测的刷新率。

---

## 6. 各项怎么测

### 6.1 桌面动画（`anim_bench.sh`）

- **触发真实动画**（不注入假负载）：
  - `overview`：`input_inject.py` 通过 `/dev/uinput` 建虚拟键盘 → 注入 `Super`（概览开/关）。
  - `workspace`：先留一个 `gnome-text-editor` 窗口，再注入 `Super+Page_Down/Page_Up`（工作区切换）。
    本板各工作区壁纸相同，若工作区里没有窗口，切换时画面完全不变（采样会判定"无动画"）。
    > 注意：本板实测绑定的 `Ctrl+Alt+↓/↑` 注入后**不生效**（画面零变化），
    > 必须改用同样绑定的 `Super+Page_Down/Page_Up`，切换才会产生真实滑动动画。
  - `window`：真实拉起/关闭 `gnome-text-editor`（窗口开合动画）。
- **数据来源**：`frame_monitor.py` 用 ffmpeg `kmsgrab` 以 60fps 从 DRM 主平面旁路采样
  （**不需要 DRM master，不打断桌面**），降采样成 64x44 灰度小图后逐帧打时间戳、按平均绝对差
  判定"是否为新内容"，据此算动画帧率与卡顿分布；间隔 ≥200ms 的静止间隔不计入统计。
- 默认三个子场景：`overview`、`workspace`、`window`，各 10s。
- 精度：采集侧按固定频率轮询，时间戳精度约 ±1 帧（≈16.7ms@60Hz），适合看掉帧/卡顿占比，
  不适合做亚帧级分析。

### 6.2 网页滚动（`web_bench.sh`）

- 板上只有 `firefox-esr`（无 chromium、且当前无外网），因此用**本地 HTML + kiosk 模式**：
  `web/scroll_test.html` 生成 300 个渐变/圆角/阴影区块的长页面，预热 1.5s 后逐帧 `scrollBy` 滚完整页。
- **主口径**：页面内 `requestAnimationFrame` 时间戳 —— 合成器给页面的帧回调节拍，即滚动时的真实帧率。
- 结果通过 `new Image().src = 'http://127.0.0.1:8799/report?d=<json>'` 回传（GET 图片请求不受
  同源限制，最稳），`web/collect_server.py` 收到即落盘退出。
- 同时用 `frame_monitor.py` 做 **kmsgrab 旁路采样**做交叉验证（可用 `--no-capture` 关闭）。

### 6.3 glmark2 全项跑分（`glmark2_bench.sh`）

**Wayland 主口径（含合成器，最贴近真实体验）**

```bash
adb shell /opt/gpu-bench/sdk/glmark2_bench.sh --backend wayland --runs 3
```
在已登录的 GNOME Wayland 会话内以桌面用户身份跑 `glmark2-es2-wayland --fullscreen`，
默认跑全部 17 个场景，结果写 `results/glmark2_wayland_run{1..3}.csv`。

**DRM 对照口径（纯 GPU 上限，不含合成器）**

```bash
adb shell /opt/gpu-bench/sdk/glmark2_bench.sh --backend drm --runs 3
```
DRM flavor 需要 DRM master，脚本会自动 `systemctl stop gdm3` → 跑 `glmark2-es2-drm --fullscreen` →
再 `systemctl start gdm3`（已用 trap 保证异常时也会拉起 gdm）。

> ⚠️ **停 gdm 会终结当前桌面会话**，跑完需要**在屏幕上重新登录桌面**才能继续做 GUI 场景
> （板子 `AutomaticLoginEnable=false`）。因此 `run-all.sh` 刻意把 glmark2 排在最后。
>
> 若 DRM flavor 报 EGL/GBM 起不来（rockchip-drm 与 panfrost 是分体设备，靠 Mesa kmsro 串联），
> 先按 ST 的做法预置好模式再跑：
> ```bash
> adb shell "modetest -s 430:1280x800 -d &"
> adb shell /opt/gpu-bench/sdk/glmark2_bench.sh --backend drm --runs 1
> ```
> （430 是本板 DSI-1 的 connector id，可用 `modetest -c` 复核）

**跑分偏差控制**：脚本会记录 CPU/GPU governor 与频率、温度、显示模式快照（`logs/env_*.txt`）。
可选 `--lock-gpu` 把 GPU governor 锁到 performance。建议固定：同一分辨率、同一刷新率、
每次跑完等机器降温、3 轮取中位数（`score.py` 自动取中位）。

### 6.4 视频播放（`video_bench.sh`）

- 默认由板端 ffmpeg 现场生成两条可复现片源（已有则复用，也可用 `--video` 指定真实素材）：
  - 普通项 `video_1280x800_30fps_60s.mp4`（testsrc2，30fps）
  - 压力项 `video_1920x1080_60fps_30s.mp4`（60fps）
- 播放链路：`filesrc ! qtdemux ! h264parse ! <解码器> ! fpsdisplaysink video-sink=waylandsink`
  - 硬解：`mppvideodec`（Rockchip MPP / VPU）
  - 软解：`avdec_h264`（libav，纯 CPU，用于对比）
- 数据来源：`fpsdisplaysink` 的 `frames-rendered` / `frames-dropped` / `average-fps` +
  `fps-measurements` 信号，直接给出 drop_rate 与平均 FPS。

---

## 7. 打分表（`score.py` 自动生成 `results/score_report.md`）

| 维度 | 权重 | 计分公式 |
|---|---|---|
| glmark2（GPU 渲染能力） | 40% | `min(10, 官方总分/10)`，满分线 = 官方总分 100 |
| 桌面动画 | 20% | 三个子场景得分的平均 |
| 网页滚动 | 20% | 见下 |
| 视频播放 | 20% | 硬解/软解得分的平均 |

**场景流畅度**：

```
得分 = 10 × [ 0.5 × min(1, 有效FPS / 目标刷新率) + 0.5 × max(0, 1 − jank占比 / 10%) ]
```

**视频播放**：

```
得分 = 10 × [ 0.5 × (1 − 丢帧率) + 0.5 × min(1, 平均FPS / 片源帧率) ]
```

**综合**：`0.4×glmark2 + 0.2×动画 + 0.2×网页 + 0.2×视频`（缺失项自动按权重归一化）。

> 跑分结束时 `run-all.sh` 会在最后再打印一次精简的**"最终得分"横幅**（综合分 + 4 个维度 + 等级），
> 免得被前面 glmark2 的 17 个场景明细淹没；完整报告仍写入 `results/score_report.md`。
> 也可单独打印：`score.py --results-dir ./results --summary`。
> 跑分结束时还会在开发板屏幕上弹出**横向得分窗口**（见 4.4 节），数据源同为 `score_summary.json`。

等级对照：

| 得分 | 等级 | 含义 |
|---|---|---|
| 9.0 – 10 | 优秀 | 全程满帧，肉眼无卡顿 |
| 7.0 – 8.9 | 良好 | 偶发轻微掉帧，基本无感 |
| 5.0 – 6.9 | 一般 | 有明显掉帧/卡顿，可感知 |
| 3.0 – 4.9 | 较差 | 卡顿频繁，体验受影响 |
| 0 – 2.9 | 不可用 | 严重掉帧/丢帧 |

> 已按首轮实测校准的阈值（`sdk/score.py` 顶部常量）：
> `GLMARK2_FULL_SCORE = 100`（官方总分满分线，本板 Wayland 口径实测 **58** → 5.8 分）
> 与 `JANK_TOLERANCE = 0.10`（jank 占比容差）。换板子/换分辨率后建议按新基线重设 `GLMARK2_FULL_SCORE`。

---

## 8. 结果文件对照

| 文件 | 内容 |
|---|---|
| `results/glmark2_wayland_run*.csv` / `glmark2_drm_run*.csv` | glmark2 每轮各场景 FPS（官方 `--results-file` 格式） |
| `results/glmark2_wayland_run*.score` / `glmark2_drm_run*.score` | glmark2 每轮**官方总分**（从 stdout 抽出；score.py 优先用它） |
| `results/anim_{overview,workspace,window}.json` | 桌面动画三个子场景的帧间隔统计 |
| `results/web_scroll.json` | 网页滚动（页面内 rAF，主口径） |
| `results/web_scroll_capture.json` | 网页滚动（kmsgrab 旁路，交叉验证） |
| `results/video_*.json` | 视频播放 rendered/dropped/average_fps |
| `results/score_report.md` | 自动生成的打分表 |
| `results/score_summary.json` | 机器可读汇总（`score_window.py` 弹窗的数据源） |
| `logs/` | 每步完整日志 + 环境快照（板端在 `sdk/logs/`，`adb pull sdk/logs` 可取回） |

> 所有相对路径都相对于 **SDK 目录**（即 `install.sh` 所在目录），产物不会落到 SDK 之外。

---

## 9. 已知风险与待实测确认项

1. **glmark2-drm 能否直接跑通**：rockchip-drm(card0) 与 panfrost(renderD129) 是分体设备，依赖 Mesa kmsro；
   若报 EGL config/GBM 错误，按 6.3 节用 `modetest -s` 预置模式后再跑。
2. **kmsgrab 像素格式（已处理）**：ffmpeg 7.1 的 `hwdownload` 格式协商有缺陷（默认会挑中 `monow` 而报
   `Invalid output format`），脚本已改为先用 debug 日志探测主平面 sw_format 再显式指定。若全屏内容
   触发 direct scanout 导致主平面格式中途变化（ARGB2101010 → XRGB8888），kmsgrab 会报错退出，脚本会
   自动重新探测格式并续采（见 JSON 里的 `format_changes`）。
3. **uinput 注入是否被立即接受**：脚本建完虚拟键盘会等 0.4s 让 libinput 识别；若动画没被触发，
   先手动确认一次（`input_inject.py tap leftmeta`）看概览是否弹出。
   另注意：工作区切换只有在工作区里**有窗口**时才有可见变化（脚本已自动先开一个窗口）。
4. **firefox-esr 首启**：首次用独立 profile 启动较慢（已给 180s 超时）；若长时间没收到上报，
   用 `--timeout 300` 重跑并观察屏幕。
5. **网页场景的 rAF 帧率可能高于面板刷新率**：GNOME Wayland + firefox-esr 下实测 rAF 可跑到 ~70Hz
   （说明 rAF 节拍不完全锁 vsync）。打分对帧率部分做了 `min(1, fps/目标)` 封顶，并同时给出 kmsgrab
   旁路口径（真实上屏帧）作为交叉验证。
6. **数据可比性**：本方案分数用于同一板子、同一镜像版本下的横向对比（改前后 / 前后版本），
   跨平台对比需注明口径差异（是否含合成器、分辨率、刷新率）。

---

## 10. 参考

- glmark2 官方仓库：<https://github.com/glmark2/glmark2>
- glmark2-wayland 手册（Debian）：<https://manpages.debian.org/testing/glmark2-wayland/glmark2-wayland.1.en.html>
- glmark2-drm 手册（Ubuntu）：<https://manpages.ubuntu.com/manpages/noble/man1/glmark2-drm.1.html>
- ST Wiki《How to launch glmark2 benchmark》（DRM flavor 需让出 DRM master 的做法）：
  <https://wiki.stmicroelectronics.cn/stm32mpu/index.php?title=How_to_launch_glmark2_benchmark>
