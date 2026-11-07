# 本地人工选段粗剪 · Local Rough Cut

**给一个本地视频和“保留哪些秒”的 JSON，先生成可复核的剪辑计划，再导出真实 MP4。**

这是现有 mvp-video-pipeline 的独立小型提取版，复用选段映射、FFmpeg trim/concat 和统一执行出口。没有 ASR、语言模型或联网调用，不需要整套视频工作流、Obsidian、浏览器或 GPU。它不会决定哪句话应该删除。

当前版本：`0.1.0rc1`。发布候选；技术测试不代表用户已经完整观看成片。

## 两分钟跑一个合成示例

环境：Python 3.11+、FFmpeg/ffprobe 在 PATH 中，FFmpeg 带 `libx264` 和 AAC 编码器。唯一 Python 依赖是 psutil。包内不附 FFmpeg、模型、字体或媒体。

```sh
git clone https://github.com/lightcoloror/local-rough-cut.git
cd local-rough-cut
python -m pip install -r requirements.txt
python rough_cut.py demo --output demo-output
```

依赖安装命令仅供缺少依赖的新使用者安装；示例制作本身离线。也可从仓库下载 ZIP 后解压进入目录。`demo-output` 必须不存在。

得到一个 **8 秒 320×240、25 fps 的合成测试源**，保留 `[0.4,2.0)` 与 `[4.0,6.4)`，输出 **4 秒、100 帧**、H.264 + AAC 的候选：

- `demo-output/render/candidate.mp4`：可用播放器观看的真实视频。
- `demo-output/plan/REVIEW.md`：原片时间、帧对齐后的时间、成片时间。
- `demo-output/render/timeline.json`：机器可读时间映射。
- `demo-output/render/validation.json`：时长、帧数、流、完整解码检查结果。

合成示例自动确认的是已知选段。自己的视频需先复核，再运行下一步。

## 剪自己的素材

复制 `examples/selections.json`，将 `keep` 改成你想保留的区间。时间单位为秒，从原片第一帧开始；每段包含起点、不含终点。按原片顺序填写，不能重叠，不需要把原片放进代码目录。

```sh
python rough_cut.py plan --source "my video.mp4" --selections selections.json --output work/plan-01
```

查看 `work/plan-01/REVIEW.md`，在播放器中回看原片开头、结尾和每个切口，尤其注意气口、未说完的词、笑声、回应与动作尾部。工具会按最近帧对齐边界，最多约半帧误差；请求值和实际值都会列出。要调整就改选段 JSON，用一个新输出目录重新生成计划。

确认后：

```sh
python rough_cut.py render --plan work/plan-01/plan.json --output work/render-01 --reviewed
```

`--reviewed` 是使用者的明确确认，不是程序证明你看过。请正常速度试听成片；技术通过不能判断意思是否完整、笑声是否自然。所有切点默认硬切；`audio_edge_fade_ms` 可显式设为 0–20 ms，用于内部切口的短淡入淡出，不交叠、不改变时长，仍需试听，不能代替保留完整词头词尾。

## 支持边界

| 项目 | 首版范围 |
|---|---|
| 输入 | 本地 MP4/MOV/MKV/WebM；一个视频流、零或一个单声道/双声道音轨 |
| 画面 | 8-bit YUV 4:2:0 SDR、逐行、方形像素、无旋转元数据；偶数尺寸，像素数不超过 1920×1080（含等像素数竖屏） |
| 时基 | 恒定帧率 1–60 fps，原点为 0；逐个视频包 PTS 检查，不只相信平均帧率；30 分钟以内 |
| 选段 | 1–32 段，每段至少两帧；原顺序拼接；不变速、不重排 |
| 输出 | H.264/yuv420p + AAC 48 kHz（原片无声则仍无声）；保留尺寸；CRF 18；重新编码，有损 |
| 原片与覆盖 | 原片只读，渲染前后核对 SHA-256；输出必须是新目录；不用 `-y` 覆盖 |
| 不包含 | 转写、自动语义删减、字幕/歌词、配乐、降噪、自动响度统一、调色/HDR 映射、竖屏重构图、多机位、NLE 原生工程、上传/发布 |
| 已验证平台 | Windows + Python 3.13 + 本机 FFmpeg；Linux/macOS 未实测 |

未知色彩标记不会被推断成正确色彩：本工具不做色彩管理，颜色敏感项目请先在完整编辑器中规范化。手机 VFR、HDR、旋转标记及非零起点素材会拒绝或不在首版支持范围；需要先另行转换。程序验证技术支持范围，不能保证每种容器/编码组合都已实测。

## 失败、资源和隐私

复用原有资源预检和跨进程互斥：默认 CPU <70%、可用内存至少 8 GiB 且 ≥20%、磁盘忙碌度 <80%，FFmpeg 线程选项受限；这只是运行前采样，不是进程内存或 OS 线程的硬上限。资源不足/采样失败返回退出码 3，不自动重试或下载。默认参数来自原系统，保守而非适用于所有机器的性能保证；高级环境变量见 `local_rough_cut/review_resource_guard.py`。不要为继续运行盲目放宽本机管理规则。

渲染和完整解码各限时 600 秒，慢机器或长片可能超时；首版先用短片验证。失败目录保留用于排错，不自动删除；换新目录重试。**仅当 `validation.json` 为 `technical_checks_passed` 时，技术流程才完成**，单有 MP4 文件不算成功。

所有输入输出都在本地。运行时计划与回执包含本机绝对路径、文件哈希和你写的选段备注：它们是私人工作数据，反馈问题前请脱敏，不要直接提交自己的媒体、真实文稿和日志。

## 参与改进

```sh
python -m unittest discover -s tests -v
```

优先欢迎：Linux/macOS 合成示例验证、手机 VFR/旋转素材的明确支持方案、更多切点映射反例。请以合成材料复现问题，写清 Python/FFmpeg 版本、预期与实际结果。完整贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

代码采用 Apache-2.0，见 [LICENSE](LICENSE)、[NOTICE](NOTICE)、[来源说明](PROVENANCE.md) 和 [外部依赖说明](THIRD_PARTY_NOTICES.md)。示例源由 FFmpeg 自行生成；没有第三方歌曲、人物、音视频或模型权重。
