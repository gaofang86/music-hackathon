# 手势 MIDI 乐器 🎹

用摄像头捕捉手势，实时生成 MIDI 信号，驱动 MRT2 Jam 演奏音乐。

---

## 系统要求

- macOS（已在 macOS 14+ 测试）
- Python 3.9+
- 摄像头（内置或外接均可）
- MRT2 Jam（App Store，Bundle ID: com.google.mrt2.jam）

---

## 安装依赖

```bash
cd /Users/gaoyingzi/music-hackathon

# 建议使用虚拟环境
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

> **注意**：`mediapipe` 在 Apple Silicon 上需要 Python 3.9 或 3.10。
> 如遇安装问题，可尝试 `pip install mediapipe-silicon`。

---

## 快速开始

### 第一步：打开 MRT2 Jam

1. 启动 MRT2 Jam
2. 进入 **设置 → MIDI 输入**，选择 **GestureInstrument**（运行脚本后会自动出现）

### 第二步：运行手势识别脚本

```bash
source .venv/bin/activate
python gesture_midi.py
```

### 第三步：开始演奏

摄像头画面会弹出，右侧显示钢琴卷帘（Piano Roll）。

---

## 手势操作说明

| 手 | 动作 | 效果 |
|---|---|---|
| **左手** | 手腕上下移动 | 控制**音高**（手腕越高 = 音越高） |
| **右手** | 食指伸直 | **发出音符（Note ON）** |
| **右手** | 握拳 | **停止发音（Note OFF）** |
| **右手** | 拇指与食指间距 | 控制**力度（Velocity）**：间距越大越响 |

> 如果只有一只手进入画面，该手同时控制音高和触发。

### 音域

- 屏幕**底部** → C3（MIDI 48）
- 屏幕**顶部** → C6（MIDI 84）
- 共 36 个半音，横跨 3 个八度

---

## 界面说明

```
┌─────────────────────────────┬──────────┐
│  当前音符: G4   力度: 95    │ 钢琴卷帘 │
│  状态: ON                   │  C6      │
│                             │  ...     │
│     [ 摄像头画面 ]          │  C3      │
└─────────────────────────────┴──────────┘
```

- 左侧：摄像头画面 + MediaPipe 手部关键点
- 右侧：钢琴卷帘，当前激活音符用绿色高亮显示
- 顶部状态栏：当前音符名称、力度、开/关状态

按 **`q`** 退出。

---

## MIDI 虚拟端口说明

脚本运行后会自动创建名为 **`GestureInstrument`** 的虚拟 MIDI 端口（通过 `python-rtmidi`），
无需额外配置 IAC Driver。

如果 MRT2 Jam 找不到该端口，可运行辅助脚本手动创建 IAC 总线：

```bash
bash setup_midi.sh
```

按照终端提示，在"音频 MIDI 设置"中启用 IAC Driver 并添加总线即可。

---

## 故障排除

| 问题 | 解决方法 |
|---|---|
| 摄像头无法打开 | 系统偏好设置 → 隐私 → 摄像头 → 允许 Python/终端 |
| MRT2 看不到 MIDI 端口 | 先运行 `gesture_midi.py`，再在 MRT2 中刷新 MIDI 输入列表 |
| 手势识别不准 | 确保光线充足，手部背景简洁，与摄像头保持 30–60cm 距离 |
| mediapipe 安装失败 | 尝试 `pip install mediapipe==0.10.9` 或使用 Python 3.10 |
| 音符持续不停 | 用右手做握拳手势发送 Note OFF，或按 `q` 重启 |

---

## 文件结构

```
music-hackathon/
├── gesture_midi.py   # 主程序
├── requirements.txt  # Python 依赖
├── setup_midi.sh     # macOS MIDI 虚拟总线辅助脚本
└── README.md         # 本文档
```

---

## 技术原理

1. **MediaPipe Hands**：实时检测手部 21 个关键点（landmark）
2. **音高映射**：取左手手腕的归一化 Y 坐标，线性映射到 MIDI 音符 48–84
3. **触发检测**：比较食指指尖（tip）与中间关节（PIP）的 Y 坐标，尖端更高则判定为"伸直"
4. **力度映射**：拇指尖与食指尖的欧氏距离，映射到 MIDI velocity 30–127
5. **防抖**：100ms 内同一音符不重复触发，避免音符闪烁
6. **MIDI 输出**：`python-rtmidi` 开启虚拟端口，发送标准 Note On/Off 消息（通道 1）
