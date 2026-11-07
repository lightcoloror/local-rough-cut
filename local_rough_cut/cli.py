# Copyright 2026 lightcoloror. SPDX-License-Identifier: Apache-2.0
"""Small manual-selection adapter for the existing FFmpeg outlet and cut kernel."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from .contract_io import (artifact_reference, atomic_write_json, atomic_write_text,
                          payload_sha256, read_json_object, validate_artifact_reference)
from .errors import ContractError
from .execution_receipts import (execute_ffmpeg_with_receipt, probe_media_metadata,
                                 resolve_media_tool)
from .render_kernel import _render_command, timeline_mapping
from .review_resource_guard import ResourcePressurePauseRequired

SCHEMA = 'local_rough_cut.plan.v1'
MAX_SEGMENTS = 32


def finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ContractError(f'{label}: expected a finite JSON number')
    return float(value)


def new_directory(value):
    path = Path(value).expanduser().resolve()
    if path.exists():
        raise ContractError(f'Output directory already exists; choose a new directory: {path}')
    path.mkdir(parents=True, exist_ok=False)
    return path


def source_profile(metadata):
    streams = metadata.get('streams', [])
    videos = [s for s in streams if s.get('codec_type') == 'video']
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    if len(videos) != 1 or len(audios) > 1:
        raise ContractError('First version supports exactly one video and at most one audio stream')
    video = videos[0]
    if video.get('disposition', {}).get('attached_pic'):
        raise ContractError('Attached cover pictures are not supported')
    width, height = int(video['width']), int(video['height'])
    if min(width, height) < 16 or width * height > 1920 * 1080 or width % 2 or height % 2:
        raise ContractError('Use even dimensions, at most 1920x1080 pixels (portrait equivalent allowed)')
    if video.get('sample_aspect_ratio', '1:1') not in ('1:1', 'N/A'):
        raise ContractError('Non-square pixels are not supported')
    if video.get('pix_fmt') not in ('yuv420p', 'yuvj420p'):
        raise ContractError('First version supports 8-bit 4:2:0 SDR input only')
    if video.get('color_transfer') in ('smpte2084', 'arib-std-b67') or video.get('color_primaries') == 'bt2020':
        raise ContractError('HDR/wide-gamut input is not supported')
    rotations = [video.get('tags', {}).get('rotate', 0)]
    rotations += [r.get('rotation', 0) for r in video.get('side_data_list', [])]
    if any(abs(float(r)) > 0.001 for r in rotations):
        raise ContractError('Rotation metadata is not supported; normalize orientation first')
    if video.get('field_order') not in (None, 'unknown', 'progressive'):
        raise ContractError('Interlaced input is not supported')
    try:
        fps = float(Fraction(video['avg_frame_rate']))
        duration = float(video.get('duration') or metadata['format']['duration'])
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise ContractError('Cannot establish input frame rate and duration') from exc
    if not math.isfinite(fps) or not 1 <= fps <= 60 or not math.isfinite(duration) or not 0 < duration <= 1800:
        raise ContractError('Input must have 1-60 fps and duration at most 30 minutes')
    for stream in [video, *audios]:
        if abs(float(stream.get('start_time', 0))) > 0.001:
            raise ContractError('Non-zero stream start times are not supported')
    if audios:
        if int(audios[0].get('channels', 0)) not in (1, 2):
            raise ContractError('Audio must be mono or stereo')
        if audios[0].get('duration') and abs(float(audios[0]['duration']) - duration) > max(0.1, 2 / fps):
            raise ContractError('Audio/video durations differ; normalize the source first')
    formats = set(metadata.get('format', {}).get('format_name', '').split(','))
    if not formats.intersection({'mov', 'mp4', 'matroska', 'webm'}):
        raise ContractError('Use an MP4/MOV/MKV/WebM container')
    return {'width': width, 'height': height, 'fps': fps, 'duration_s': duration,
            'has_audio': bool(audios), 'fps_fraction': video['avg_frame_rate']}


def verify_cadence(source, profile):
    """Inspect all video packet PTS; do not infer constant cadence from avg_frame_rate."""
    command = [resolve_media_tool('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file',
               '-select_streams', 'v:0', '-show_entries', 'packet=pts_time', '-of', 'csv=p=0', str(source)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f'Cadence inspection failed: {exc}') from exc
    if result.returncode:
        raise ContractError(f'Cadence inspection failed: {result.stderr[-1000:]}')
    try:
        points = sorted(float(line.strip()) for line in result.stdout.splitlines() if line.strip())
    except ValueError as exc:
        raise ContractError('Missing/non-numeric video packet timestamps') from exc
    step = 1 / profile['fps']
    if len(points) < 2 or abs(points[0]) > 0.001 or any(not math.isfinite(t) for t in points):
        raise ContractError('Video packet timestamps must start at zero and be finite')
    if any(abs((b-a) - step) > max(0.00001, step * 0.01) for a, b in zip(points, points[1:])):
        raise ContractError('Variable, missing, or duplicate frame cadence is unsupported')
    if abs(len(points) * step - profile['duration_s']) > step * 1.1:
        raise ContractError('Packet count and declared duration disagree')
    return {'method': 'all_video_packet_pts', 'packets': len(points), 'fps': profile['fps'], 'status': 'passed'}


def compose_plan(source_ref, selections, profile):
    if set(selections) - {'keep', 'audio_edge_fade_ms'}:
        raise ContractError('Unknown selection fields')
    rows = selections.get('keep')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SEGMENTS:
        raise ContractError(f'keep must contain 1-{MAX_SEGMENTS} intervals')
    fade_ms = finite(selections.get('audio_edge_fade_ms', 0), 'audio_edge_fade_ms')
    if not 0 <= fade_ms <= 20:
        raise ContractError('Audio edge fade must be between 0 and 20 ms')
    segments, splices, previous_end = [], [], 0.0
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) - {'start', 'end', 'note'} or not {'start','end'} <= set(row):
            raise ContractError('Each keep row needs start/end, with optional note')
        start, end = finite(row['start'], 'start'), finite(row['end'], 'end')
        note = row.get('note', '')
        if not isinstance(note, str) or len(note) > 500:
            raise ContractError('note must be text, at most 500 characters')
        if not 0 <= start < end <= profile['duration_s'] + 0.000001 or start < previous_end - 1e-7:
            raise ContractError('Intervals must be nonempty, chronological, nonoverlapping, and inside the source')
        previous_end = end
        # Nearest frame, ties upward. Record both requested and effective boundaries.
        a = math.floor(start * profile['fps'] + 0.5)
        b = math.floor(end * profile['fps'] + 0.5)
        if b - a < 2:
            raise ContractError('Each interval must retain at least two frames after alignment')
        left, right = round(a / profile['fps'], 9), round(b / profile['fps'], 9)
        segments.append({'segment_id': f'keep-{index+1:03}', 'source_path': source_ref['path'],
                         'source_sha256': source_ref['sha256'], 'in_s': left, 'out_s': right,
                         'duration_s': round(right-left, 9), 'start_frame': a, 'end_frame': b,
                         'requested_start_s': start, 'requested_end_s': end, 'note': note})
        fade = min(fade_ms / 1000, (right-left) / 4)
        splices.append({'start': left, 'end': right, 'fade_in_s': fade if index else 0,
                        'fade_out_s': fade if index < len(rows)-1 else 0,
                        'audition_required': len(rows) > 1})
    plan = {'schema': SCHEMA, 'source_video': source_ref, 'source_profile': profile,
            'selections': selections, 'nle_segments': segments, 'audio_splices': splices,
            'review_status': 'pending', 'automatic_semantic_cutting': False}
    plan['timeline'] = timeline_mapping(plan)
    plan['expected_duration_s'] = plan['timeline'][-1]['target_end']
    plan['expected_frames'] = sum(s['end_frame'] - s['start_frame'] for s in segments)
    plan['plan_sha256'] = payload_sha256(plan, 'plan_sha256')
    return plan


def make_plan(source, selection_path, output):
    source = Path(source).expanduser().resolve()
    if source.suffix.lower() not in {'.mp4', '.mov', '.mkv', '.webm'}:
        raise ContractError('Use a local MP4/MOV/MKV/WebM file')
    ref = artifact_reference(source, 'source video')
    profile = source_profile(probe_media_metadata(source))
    cadence = verify_cadence(source, profile)
    plan = compose_plan(ref, read_json_object(selection_path, 'selections'), profile)
    target = new_directory(output)
    atomic_write_json(plan, target / 'plan.json')
    atomic_write_json(cadence, target / 'cadence.json')
    lines = ['# 选段复核单', '', '状态：待人工复核。请回看原片的开头、结尾和每个切口，确认句意、气口、反应与动作完整。', '',
             '|段|请求原片秒数|实际保留秒数（对齐帧）|成片秒数|', '|---|---|---|---|']
    for s, t in zip(plan['nle_segments'], plan['timeline']):
        lines.append(f"|{s['segment_id']}|{s['requested_start_s']:.6f}–{s['requested_end_s']:.6f}|{s['in_s']:.6f}–{s['out_s']:.6f}|{t['target_start']:.6f}–{t['target_end']:.6f}|")
    lines += ['', f"预计时长：{plan['expected_duration_s']:.6f} 秒；{plan['expected_frames']} 帧。", '',
              '输入时间从原片第一帧算起，区间为 [start,end)。按最近帧对齐，误差最多约半帧。',
              '声音微淡化默认关闭；可在选段 JSON 中显式设置 audio_edge_fade_ms（0–20）。它不保护词头，必须试听。',
              '修改选段请重新生成计划；不要手改 plan.json。--reviewed 仅记录使用者确认，不证明完整观看。']
    atomic_write_text('\n'.join(lines) + '\n', target / 'REVIEW.md')
    return target / 'plan.json'


def validate_plan(plan):
    if plan.get('schema') != SCHEMA or plan.get('plan_sha256') != payload_sha256(plan, 'plan_sha256'):
        raise ContractError('Plan schema/hash mismatch')
    validate_artifact_reference(plan['source_video'], 'source video')
    rebuilt = compose_plan(plan['source_video'], plan['selections'], plan['source_profile'])
    if rebuilt != plan:
        raise ContractError('Plan fields do not match the reviewed selections')


def render(plan_path, output, reviewed=False):
    if not reviewed:
        raise ContractError('Review REVIEW.md and source boundaries first, then pass --reviewed')
    plan = read_json_object(plan_path, 'plan')
    validate_plan(plan)
    current = source_profile(probe_media_metadata(plan['source_video']['path']))
    if current != plan['source_profile']:
        raise ContractError('Source profile changed')
    verify_cadence(plan['source_video']['path'], current)
    target = new_directory(output)
    movie = target / 'candidate.mp4'
    status = {'status': 'incomplete', 'plan_sha256': plan['plan_sha256'], 'review_confirmation': 'caller_asserted',
              'human_playback': 'not_verified', 'source_modified': False}
    atomic_write_json(status, target / 'validation.json')
    try:
        command = _render_command(plan, movie, current['has_audio'])
        execute_ffmpeg_with_receipt('trim', command,
            [{'role': 'source', 'artifact': plan['source_video']}], [{'role': 'candidate', 'path': str(movie)}],
            target / 'render-receipt.json', capture_log_path=target / 'render.log', timeout_s=600, guard_resource=True)
        metadata = probe_media_metadata(movie)
        streams = metadata['streams']
        videos = [s for s in streams if s['codec_type'] == 'video']
        audios = [s for s in streams if s['codec_type'] == 'audio']
        if len(videos) != 1 or len(audios) != int(current['has_audio']):
            raise ContractError('Output stream count mismatch')
        video = videos[0]
        if video['codec_name'] != 'h264' or video['pix_fmt'] != 'yuv420p' or (video['width'], video['height']) != (current['width'], current['height']):
            raise ContractError('Output video profile mismatch')
        if int(video.get('nb_frames', -1)) != plan['expected_frames']:
            raise ContractError('Output frame count mismatch')
        duration = float(metadata['format']['duration'])
        if abs(duration - plan['expected_duration_s']) > max(0.1, 2 / current['fps']):
            raise ContractError('Output duration mismatch')
        if audios and (audios[0]['codec_name'] != 'aac' or audios[0]['sample_rate'] != '48000'):
            raise ContractError('Output audio profile mismatch')
        qa_log = target / 'decode.log'
        execute_ffmpeg_with_receipt('technical_qa',
            ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '1', '-protocol_whitelist', 'file',
             '-i', str(movie), '-map', '0:v:0', '-map', '0:a:0?', '-f', 'null', '-'],
            [{'role': 'candidate', 'path': str(movie)}], [{'role': 'decode_log', 'path': str(qa_log)}],
            target / 'decode-receipt.json', capture_log_path=qa_log, timeout_s=600, guard_resource=True)
        validate_artifact_reference(plan['source_video'], 'source after render')
        status.update(status='technical_checks_passed', output_duration_s=duration,
                      expected_duration_s=plan['expected_duration_s'], frames=int(video['nb_frames']),
                      full_decode='passed', video_streams=1, audio_streams=len(audios),
                      source_sha256=plan['source_video']['sha256'], candidate=artifact_reference(movie, 'candidate'))
        atomic_write_json(metadata, target / 'ffprobe.json')
        atomic_write_json({'timeline': plan['timeline']}, target / 'timeline.json')
        atomic_write_json(status, target / 'validation.json')
    except Exception as exc:
        status.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        atomic_write_json(status, target / 'validation.json')
        raise
    return movie


def demo(output):
    target = new_directory(output)
    source = target / 'synthetic-source.mp4'
    spec = target / 'synthetic-spec.json'
    atomic_write_json({'source': 'FFmpeg lavfi testsrc2 and sine, 440 Hz then 880 Hz at 4 s', 'duration_s': 8, 'size': '320x240', 'fps': 25}, spec)
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-f', 'lavfi', '-i',
               'testsrc2=size=320x240:rate=25:duration=8', '-f', 'lavfi', '-i',
               'sine=frequency=440:sample_rate=48000:duration=4[a];sine=frequency=880:sample_rate=48000:duration=4[b];[a][b]concat=n=2:v=0:a=1', '-c:v', 'libx264', '-preset', 'ultrafast',
               '-pix_fmt', 'yuv420p', '-threads', '1', '-c:a', 'aac', '-ar', '48000', str(source)]
    execute_ffmpeg_with_receipt('render', command, [{'role':'recipe','path':str(spec)}],
        [{'role':'synthetic_source','path':str(source)}], target / 'source-receipt.json',
        capture_log_path=target / 'source.log', guard_resource=True)
    selections = target / 'selections.json'
    atomic_write_json({'keep':[{'start':0.4,'end':2.0,'note':'synthetic section A'},
                              {'start':4.0,'end':6.4,'note':'synthetic section B'}]}, selections)
    plan = make_plan(source, selections, target / 'plan')
    return render(plan, target / 'render', reviewed=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='本地人工选段粗剪；原片只读，无模型、联网或自动语义删减。')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='生成选段计划与复核单')
    p.add_argument('--source', required=True)
    p.add_argument('--selections', required=True)
    p.add_argument('--output', required=True)
    p = sub.add_parser('render', help='复核后输出 MP4 候选')
    p.add_argument('--plan', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--reviewed', action='store_true')
    p = sub.add_parser('demo', help='仅使用自生成合成片跑通整条链')
    p.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            result = make_plan(args.source, args.selections, args.output)
        elif args.command == 'render':
            result = render(args.plan, args.output, args.reviewed)
        else:
            result = demo(args.output)
        print(json.dumps({'status': 'completed', 'output': str(result)}, ensure_ascii=True))
        return 0
    except ResourcePressurePauseRequired as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=True), file=sys.stderr)
        return 3
    except (ContractError, OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'status':'failed','error':str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2

