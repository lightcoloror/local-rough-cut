# Copyright 2026 lightcoloror. SPDX-License-Identifier: Apache-2.0
"""Frame-based opening, splice and ending locators on both clocks."""
from __future__ import annotations

import math
from pathlib import Path

from .contract_io import artifact_reference, atomic_write_json, validate_artifact_reference
from .errors import ContractError
from .execution_receipts import execute_ffmpeg_with_receipt, probe_media_metadata
from .render_kernel import _render_command, timeline_mapping


def boundary_review(plan, radius_s=0.6, reviewed=False):
    if type(radius_s) not in (int, float) or not math.isfinite(radius_s) or not 0 < radius_s <= 5:
        raise ContractError('Review radius must be finite and between 0 and 5 seconds')
    rows = timeline_mapping(plan)
    fps = plan['source_profile']['fps']
    frame_radius = max(2, math.ceil(radius_s * fps))
    windows = []
    for segment, row in zip(plan['nle_segments'], rows):
        a, b = segment['start_frame'], segment['end_frame']
        windows.append({
            'head': [round(a / fps, 9), round(min(b, a + frame_radius) / fps, 9)],
            'tail': [round(max(a, b - frame_radius) / fps, 9), round(b / fps, 9)],
        })
    items = [{
        'review_id': 'opening', 'kind': 'opening', 'target_boundary_s': 0.0,
        'target_window': [0.0, round(windows[0]['head'][1] - windows[0]['head'][0], 9)],
        'target_seek_s': 0.0,
        'source_windows': [{'segment_id': rows[0]['segment_id'], 'side': 'head',
                            'range_s': windows[0]['head'], 'seek_s': rows[0]['source_start']}],
    }]
    for index, (left, right) in enumerate(zip(rows, rows[1:]), 1):
        focus = right['target_start']
        tail, head = windows[index - 1]['tail'], windows[index]['head']
        items.append({
            'review_id': f'splice-{index:03}', 'kind': 'splice',
            'target_boundary_s': focus, 'target_seek_s': focus,
            'target_window': [round(focus - (tail[1] - tail[0]), 9), round(focus + head[1] - head[0], 9)],
            'source_gap_s': round(right['source_start'] - left['source_end'], 9),
            'source_windows': [
                {'segment_id': left['segment_id'], 'side': 'left', 'range_s': tail,
                 'seek_s': round(left['source_end'] - 1 / fps, 9)},
                {'segment_id': right['segment_id'], 'side': 'right', 'range_s': head,
                 'seek_s': right['source_start']},
            ],
        })
    end = rows[-1]['target_end']
    tail = windows[-1]['tail']
    items.append({
        'review_id': 'ending', 'kind': 'ending', 'target_boundary_s': end,
        'target_seek_s': round(end - 1 / fps, 9),
        'target_window': [round(end - (tail[1] - tail[0]), 9), end],
        'source_windows': [{'segment_id': rows[-1]['segment_id'], 'side': 'tail',
                            'range_s': tail, 'seek_s': round(rows[-1]['source_end'] - 1 / fps, 9)}],
    })
    for item in items:
        item['review_status'] = 'caller_asserted' if reviewed else 'pending'
        item['human_playback'] = 'not_verified'
    return {'schema': 'local_rough_cut.boundary_review.v1', 'plan_sha256': plan['plan_sha256'],
            'source_video': plan['source_video'], 'timeline': rows,
            'requested_radius_s': radius_s, 'interval_convention': '[start,end)',
            'boundary_endpoints_may_be_exclusive': True,
            'items': items, 'clips_status': 'not_requested'}


def attach_rendered(report, validation_path, plan):
    from .cli import source_profile
    from .contract_io import read_json_object

    validation = read_json_object(validation_path, 'render validation')
    if (validation.get('status') != 'technical_checks_passed'
            or validation.get('plan_sha256') != plan['plan_sha256']):
        raise ContractError('Rendered candidate must have successful validation for this plan')
    candidate = validation['candidate']
    path = validate_artifact_reference(candidate, 'rendered candidate')
    metadata = probe_media_metadata(path)
    profile = source_profile(metadata)
    frames = [s for s in metadata['streams'] if s['codec_type'] == 'video'][0].get('nb_frames')
    if (profile['fps_fraction'] != plan['source_profile']['fps_fraction']
            or int(frames or -1) != plan['expected_frames']):
        raise ContractError('Rendered candidate clock does not match the plan')
    report['rendered_video'] = candidate
    report['render_validation'] = artifact_reference(validation_path, 'render validation')
    return profile


def export_review_clips(report, plan, output, rendered_profile=None):
    from .cli import compose_plan

    target = Path(output)
    report['clips_status'] = 'incomplete'
    report['clips'] = []
    atomic_write_json(report, target / 'review.json')
    for item in report['items']:
        requests = [('source-' + row['side'], row['range_s'], plan['source_video'], plan['source_profile'])
                    for row in item['source_windows']]
        if rendered_profile:
            requests.append(('target', item['target_window'], report['rendered_video'], rendered_profile))
        for side, bounds, reference, profile in requests:
            name = item['review_id'] + '-' + side
            folder = target / 'clips' / name
            folder.mkdir(parents=True, exist_ok=False)
            clip_plan = compose_plan(reference, {'keep': [{'start': bounds[0], 'end': bounds[1]}]}, profile)
            movie = folder / 'preview.mp4'
            atomic_write_json(clip_plan, folder / 'plan.json')
            execute_ffmpeg_with_receipt('trim', _render_command(clip_plan, movie, profile['has_audio']),
                [{'role': 'source', 'artifact': reference}], [{'role': 'preview', 'path': str(movie)}],
                folder / 'render-receipt.json', capture_log_path=folder / 'render.log', guard_resource=True)
            metadata = probe_media_metadata(movie)
            video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
            if int(video.get('nb_frames', -1)) != clip_plan['expected_frames']:
                raise ContractError('Review clip frame count mismatch')
            execute_ffmpeg_with_receipt('technical_qa',
                ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '1', '-protocol_whitelist', 'file',
                 '-i', str(movie), '-map', '0:v:0', '-map', '0:a:0?', '-f', 'null', '-'],
                [{'role': 'preview', 'path': str(movie)}], [{'role': 'decode_log', 'path': str(folder / 'decode.log')}],
                folder / 'decode-receipt.json', capture_log_path=folder / 'decode.log', guard_resource=True)
            validate_artifact_reference(reference, 'review source after render')
            report['clips'].append({'review_id': item['review_id'], 'clock': side,
                                    'requested_range_s': bounds, 'effective_range_s':
                                    [clip_plan['timeline'][0]['source_start'], clip_plan['timeline'][0]['source_end']],
                                    'artifact': artifact_reference(movie, 'review preview'), 'full_decode': 'passed'})
            atomic_write_json(report, target / 'review.json')
    report['clips_status'] = 'technical_checks_passed'
    atomic_write_json(report, target / 'review.json')
