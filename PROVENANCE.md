# 来源与改动

维护者：lightcoloror；整理工具：Codex；2026-11-07。

本包从 mvp-video-pipeline 的 Apache-2.0 自有代码按白名单提取。它是独立文件包，不含原仓库 Git 历史，也不需要访问原仓库。

来源基线 HEAD：`e55650b4b47d7d1c36334d71bb72f16684c5ca73`；实际取材为冻结时的工作区文件，存在未提交改动，因此每个来源文件以 SHA-256 为准，不能将基线提交当作全部来源字节。

| 本包 | 来源与修改 |
|---|---|
| `local_rough_cut/render_kernel.py` | 从 spoken_delivery.py 提取 timeline_mapping、_render_command；删除完整生产处理分支；视频边界改按帧；映射保留 9 位小数；限制输入协议与解码线程 |
| `local_rough_cut/execution_receipts.py` | 提取 FFmpeg 执行、探测、回执校验的依赖闭包；保留单一 FFmpeg 出口，删除语音模型/无关操作；ffprobe 限本地文件协议 |
| `local_rough_cut/contract_io.py` | 提取哈希、JSON、原子写入和引用校验；仅 .json 文件尝试解析 JSON，避免把视频整体读入文本内存 |
| `local_rough_cut/errors.py`、`review_resource_guard.py` | 保留原实现，添加本包出处声明；未降低原资源预检阈值 |
| `local_rough_cut/editing_presets.py` | 提取 number、fade_filter；保留原 audio_splices.maximum_fade_s=0.02；默认淡化改由选段显式选择 |
| `local_rough_cut/cli.py`、`rough_cut.py`、测试与文档 | 新增轻量参数/输入校验、复核单、输出验证、独立 CLI 胶水；合成配方复用 test_production_presets_media.py 的 testsrc2/sine，扩为两个音调以验证选段 |

源文件清单（原相对路径；仅列出处，不表示将整个来源文件分发）：

| 来源相对路径 | SHA-256 |
|---|---|
| `LICENSE` | `c5da478ea01ea29aeccbd67c7987df14e279f9c471f9f9d8eb38046b32c66d18` |
| `THIRD_PARTY_NOTICES.md` | `8736401a82a8ba1f3285797896964bcebbab577955f68c0a698a941a7306a2a4` |
| `MODEL_ASSET_LICENSES.md` | `20007770fbfd653979ef6fbf0f34e1ef483039f823d756bc7546f1b5a54d8a7f` |
| `src/video_creation_pipeline/contract_io.py` | `e34eccf3bf856496134492c53b4aff0dc20cb6c0368b7b79c5df2159be7bc361` |
| `src/video_creation_pipeline/errors.py` | `70016c24c3aa401d9876184133ee421301cccd9252fd1b883d0585e84ccb1201` |
| `src/video_creation_pipeline/review_resource_guard.py` | `ea5afa827f0235cca981ae4773acc1e6949da4658313f432e6979ae1a3b04072` |
| `src/video_creation_pipeline/execution_receipts.py` | `38d155977463248acc4b39944210ab990a9385f2e2a0431c04a0b53d61d760f5` |
| `src/video_creation_pipeline/spoken_delivery.py` | `d223f63be51b9a846051a9934d8d81d16d72adea5bb2e6a221ff83352947db50` |
| `src/video_creation_pipeline/editing_presets.py` | `35335c80502b573d0016369a50f2cea81ad4a841a0638837521fd4ea34323488` |
| `src/video_creation_pipeline/policies/editing-presets.v1.json` | `67705a9e837de7bc0a5742fbd7cc2b1a6187d9d6d25df00e2eea2c0986893305` |
| `tests/test_production_presets_media.py` | `e2ae16fa3d9e0e6c0c331e95109972a9d203078a57d58df8091c6d09975d0a25` |

LICENSE 沿用来源 Apache-2.0 文本；第三方通知仅保留本包相关依赖，未改写第三方许可。无媒体、模型、字体、私人记录、机器配置或原 Git 历史。

明确未提取完整 spoken_rough_cut.py：它需要固定 videocut-kit 和生命周期模块；首版用人工选段替代其语义判断入口，仍调用已有剪辑内核与执行出口。不是 videocut-kit 的独立发行，也不生成编辑器原生工程。
