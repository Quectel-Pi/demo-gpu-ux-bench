# GPU 渲染基准与界面体验打分（自动生成）

## 1. glmark2 全项跑分（默认 17 场景）

- **wayland**：3 轮，总分取官方总分的中位数 → **57.0**

| 场景 | wayland |
|---|---|
| [buffer] columns=200:interleave=false:update-dispersion=0.9:update-fraction=0.5:update-method=map | 49 |
| [buffer] columns=200:interleave=false:update-dispersion=0.9:update-fraction=0.5:update-method=subdata | 49 |
| [buffer] columns=200:interleave=true:update-dispersion=0.9:update-fraction=0.5:update-method=map | 49 |
| [build] use-vbo=false | 59 |
| [build] use-vbo=true | 60 |
| [bump] bump-render=height | 84 |
| [bump] bump-render=high-poly | 24 |
| [bump] bump-render=normals | 73 |
| [conditionals] fragment-steps=0:vertex-steps=0 | 58 |
| [conditionals] fragment-steps=0:vertex-steps=5 | 59 |
| [conditionals] fragment-steps=5:vertex-steps=0 | 60 |
| [desktop] blur-radius=5:effect=blur:passes=1:separable=true:windows=4 | 61 |
| [desktop] effect=shadow:windows=4 | 69 |
| [effect2d] kernel=0,1,0;1,-4,1;0,1,0; | 79 |
| [effect2d] kernel=1,1,1,1,1;1,1,1,1,1;1,1,1,1,1; | 66 |
| [function] fragment-complexity=low:fragment-steps=5 | 60 |
| [function] fragment-complexity=medium:fragment-steps=5 | 60 |
| [ideas] speed=duration | 59 |
| [jellyfish] <default> | 58 |
| [loop] fragment-loop=false:fragment-steps=5:vertex-steps=5 | 60 |
| [loop] fragment-steps=5:fragment-uniform=false:vertex-steps=5 | 61 |
| [loop] fragment-steps=5:fragment-uniform=true:vertex-steps=5 | 61 |
| [pulsar] light=false:quads=5:texture=false | 61 |
| [refract] <default> | 3 |
| [shading] shading=blinn-phong-inf | 58 |
| [shading] shading=cel | 58 |
| [shading] shading=gouraud | 58 |
| [shading] shading=phong | 58 |
| [shadow] <default> | 25 |
| [terrain] <default> | 16 |
| [texture] texture-filter=linear | 92 |
| [texture] texture-filter=mipmap | 96 |
| [texture] texture-filter=nearest | 100 |

## 2. 真实场景流畅度

| 场景 | 有效FPS | 间隔 p95(ms) | jank 占比 | 最长卡顿(ms) | 丢帧估算 | 得分(0-10) |
|---|---|---|---|---|---|---|
| 桌面动画-overview | 57.79 | 21.8 | 3.64% | 33.43 | 2 | 8.0 |
| 桌面动画-workspace | 57.88 | 17.17 | 3.57% | 33.53 | 2 | 8.0 |
| 桌面动画-window | 56.32 | 18.03 | 6.25% | 34.54 | 2 | 6.6 |
| 网页滚动(firefox rAF) | 73.04 | 33.04 | 14.00% | 47.74 | 86 | 5.0 |
| 网页滚动(kmsgrab旁路) | 55.65 | 32.52 | 7.31% | 55.73 | 24 | 6.0 |
| 视频播放-normal_mpp | 30.03 | - | - | - | 0/1800(0.00%) | 10.0 |
| 视频播放-normal_sw | 30.03 | - | - | - | 0/1800(0.00%) | 10.0 |
| 视频播放-stress_mpp | 56.97 | - | - | - | 90/1798(5.01%) | 9.5 |
| 视频播放-stress_sw | 56.97 | - | - | - | 93/1800(5.17%) | 9.5 |

## 3. 综合得分

| 维度 | 权重 | 得分(0-10) |
|---|---|---|
| glmark2(GPU 渲染能力) | 40% | 5.7 |
| 桌面动画 | 20% | 7.5 |
| 网页滚动 | 20% | 5.0 |
| 视频播放 | 20% | 9.8 |
| **综合** | 100% | **6.7** |

## 4. 打分规则

- 场景流畅度 = 10 × [0.5 × min(1, 有效FPS/60) + 0.5 × max(0, 1 − jank占比/0.1)]
- 视频播放 = 10 × [0.5 × (1 − 丢帧率) + 0.5 × min(1, 平均FPS/片源帧率)]
- glmark2 = min(10, 总分 / 10)，总分满分线为 100 分
- jank 定义：帧间隔 > 1.5 × 目标帧间隔（60Hz → 25ms）
- 综合 = 0.4×glmark2 + 0.2×动画 + 0.2×网页 + 0.2×视频（缺失项自动按权重归一）
