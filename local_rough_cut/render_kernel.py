# Copyright 2026 lightcoloror
# SPDX-License-Identifier: Apache-2.0
# Extracted/adapted from mvp-video-pipeline on 2026-11-07; see PROVENANCE.md.
from .editing_presets import fade_filter

def timeline_mapping(plan):
    cursor, rows = 0.0, []
    for segment in plan["nle_segments"]:
        end = round(cursor + segment["duration_s"], 9)
        rows.append(
            {
                "segment_id": segment["segment_id"],
                "source_path": segment["source_path"],
                "source_sha256": segment["source_sha256"],
                "source_start": segment["in_s"],
                "source_end": segment["out_s"],
                "target_start": cursor,
                "target_end": end,
            }
        )
        cursor = end
    return rows


def _render_command(plan, output, has_audio):
    filters = []
    for i, s in enumerate(plan["nle_segments"]):
        filters.append(
            f"[0:v]trim=start_frame={s['start_frame']}:end_frame={s['end_frame']},setpts=PTS-STARTPTS[v{i}]"
        )
        if has_audio:
            splice = plan.get("audio_splices", [])
            fade = fade_filter(splice[i]) if splice else ""
            gains = plan.get("clip_gains", [])
            gain = f",volume={gains[i]['gain_db']}dB" if gains else ""
            filters.append(
                f"[0:a]atrim=start={s['in_s']}:end={s['out_s']},asetpts=PTS-STARTPTS,aresample=48000"
                + gain + ("," + fade if fade else "") + f"[a{i}]"
            )
    n = len(plan["nle_segments"])
    inputs = "".join(f"[v{i}]" + (f"[a{i}]" if has_audio else "") for i in range(n))
    filters.append(
        inputs
        + f"concat=n={n}:v=1:a={int(has_audio)}[v]"
        + ("[a]" if has_audio else "")
    )
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-filter_complex_threads",
        "1",
        "-threads", "1",
        "-protocol_whitelist", "file",
        "-i",
        plan["source_video"]["path"],
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
    ]
    if has_audio:
        command += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    return command + [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(output),
    ]
