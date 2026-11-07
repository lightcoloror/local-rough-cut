# 外部依赖与资产

本包自己的代码及从 mvp-video-pipeline 提取的代码沿用 Apache-2.0。

- **psutil**：BSD 3-Clause，Copyright (c) 2009, Jay Loden, Dave Daeschler, Giampaolo Rodola。仅声明依赖，不复制其代码或二进制。验证使用 7.2.2；完整条款以所安装分发包的 LICENSE 为准。上游：https://github.com/giampaolo/psutil
- **FFmpeg / ffprobe**：外部可执行程序，不随包提供。上游采用 LGPL，具体构建启用的组件可能使构建适用 GPL；本工具要求 `libx264`，不要据此把 FFmpeg 或编码器改标为 Apache-2.0。以使用者实际构建的 `ffmpeg -L`/`-buildconf` 为准。上游：https://ffmpeg.org/legal.html
- **Python**：外部运行时，不随包提供。上游：https://www.python.org/psf/license/

没有打包 videocut-kit、CrispASR、Remotion、模型、字体、歌曲、第三方视频或真实字幕；也不下载它们。演示只在本地调用 FFmpeg 的 testsrc2/sine 生成测试画面和单音。
