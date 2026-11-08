# P1 source and license record

Public baseline `ff9173d7e4813afd7d1b472979c7089f6df5c734` of `lightcoloror/local-rough-cut`.
Private MVP reference commit `e55650b4b47d7d1c36334d71bb72f16684c5ca73`. Only tracked blobs at this commit were read.

| Source | Referenced functions | SHA-256 |
|---|---|---|
| `LICENSE` |  | `6a3c0321661d755c0c90855cae3c1204d1bccc10661a148c27fb19b6358b238a` |
| `src/video_creation_pipeline/editing_presets.py` | `boundary_windows` | `35335c80502b573d0016369a50f2cea81ad4a841a0638837521fd4ea34323488` |
| `src/video_creation_pipeline/technical_qa.py` | `_subtitle_metrics` | `6fb9b37b9ee999049d2f5b392cf14d054f47d7e5be42ca98fa685967fdf890a0` |
| `src/video_creation_pipeline/music_pipeline.py` | `_load_srt_subtitles` | `10292d71c600299b519f952f5a687e4105c5b868eeba59a5a7d06118afa14da6` |
| `src/video_creation_pipeline/video_review_executor.py` |  | `6526a74a8500ee21d7f91a98b437e827831fbd4459b5a6c8c6623a59e397c77c` |

All referenced first-party code uses Apache-2.0. The public baseline LICENSE and NOTICE are retained.

`local_rough_cut/review.py` adapts the opening/splice/ending review design to the public frame-aligned timeline. It uses the existing `timeline_mapping`, `_render_command`, `execute_ffmpeg_with_receipt`, hash references and resource guard.
`local_rough_cut/subtitles.py` is a new standard-library adapter. The older SRT helpers were inspected as references. Their tolerant parsing and silent-skipping behavior were not copied. It intersects source cues with the effective half-open keep intervals and records text review for cut cues.
`local_rough_cut/cli.py` adds `review` and `retime-srt`. The existing `plan`, `render` and `demo` behavior is retained. Tests and two Chinese SRT files are project-owned synthetic material.

No third-party code, fonts, models, media or binaries are newly bundled. Dependencies remain Python, psutil and external FFmpeg/ffprobe. See `THIRD_PARTY_NOTICES.md`.

The P1 candidate is prepared for owner review. Proposed destination is `lightcoloror/local-rough-cut`, branch `codex/p1-review-srt-20261107`. Publication of this additional scope is pending owner approval.
