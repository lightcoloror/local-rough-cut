# Copyright 2026 lightcoloror. SPDX-License-Identifier: Apache-2.0
"""Strict source-clock SRT parsing and half-open edit mapping."""
from __future__ import annotations

import re
from fractions import Fraction

from .errors import ContractError
from .render_kernel import timeline_mapping

TIMING = re.compile(r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3}) --> (\d{2,}):([0-5]\d):([0-5]\d),(\d{3})")


def parse_srt(text):
    text = text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    if not text.strip():
        return []
    cues, seen = [], set()
    for position, block in enumerate(re.split(r'\n[ \t]*\n', text.strip('\n')), 1):
        lines = block.split('\n')
        if len(lines) < 3 or not re.fullmatch(r'[0-9]+', lines[0].strip()):
            raise ContractError(f'SRT block {position}: expected index, timestamp and text')
        index = int(lines[0].strip())
        match = TIMING.fullmatch(lines[1].strip())
        if index <= 0 or index in seen or not match:
            raise ContractError(f'SRT block {position}: invalid/duplicate index or timestamp')
        values = [int(value) for value in match.groups()]
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        body = '\n'.join(lines[2:])
        if start >= end or not body.strip():
            raise ContractError(f'SRT cue {index}: nonpositive duration or empty text')
        if cues and start < cues[-1]['end_ms']:
            raise ContractError(f'SRT cues {cues[-1]["source_index"]}/{index}: overlapping or out of order')
        seen.add(index)
        cues.append({'source_index': index, 'start_ms': start, 'end_ms': end, 'text': body})
    return cues


def timestamp(milliseconds):
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02},{ms:03}'


def format_srt(cues):
    return '\n\n'.join(
        f'{index}\n{timestamp(cue["start_ms"])} --> {timestamp(cue["end_ms"])}\n{cue["text"]}'
        for index, cue in enumerate(cues, 1)
    ) + ('\n' if cues else '')


def retime_cues(cues, plan):
    timeline = timeline_mapping(plan)
    duration = Fraction(str(plan['source_profile']['duration_s']))
    exported, mappings = [], []
    for cue in cues:
        start, end = Fraction(cue['start_ms'], 1000), Fraction(cue['end_ms'], 1000)
        if end > duration:
            raise ContractError(f'SRT cue {cue["source_index"]}: timestamp exceeds source duration')
        spans = []
        for row in timeline:
            left = max(start, Fraction(str(row['source_start'])))
            right = min(end, Fraction(str(row['source_end'])))
            if left >= right:
                continue
            offset = Fraction(str(row['target_start'])) - Fraction(str(row['source_start']))
            spans.append({'source_start': left, 'source_end': right,
                          'target_start': left + offset, 'target_end': right + offset,
                          'segment_ids': [row['segment_id']]})
        crosses_cut = len(spans) > 1
        groups = []
        for span in spans:
            if (groups and groups[-1]['source_end'] == span['source_start']
                    and groups[-1]['target_end'] == span['target_start']):
                groups[-1].update(source_end=span['source_end'], target_end=span['target_end'])
                groups[-1]['segment_ids'].extend(span['segment_ids'])
            else:
                groups.append(dict(span))
        kept = sum((span['source_end'] - span['source_start'] for span in spans), Fraction())
        truncated = bool(spans) and kept < end - start
        outputs, collapsed = [], []
        for span in groups:
            # Round inward, so the exported subtitle never extends into a removed interval.
            exact_start, exact_end = span['target_start'] * 1000, span['target_end'] * 1000
            left = -(-exact_start.numerator // exact_start.denominator)
            right = exact_end.numerator // exact_end.denominator
            detail = {key: float(value) for key, value in span.items() if key != 'segment_ids'}
            detail['segment_ids'] = span['segment_ids']
            if left >= right:
                collapsed.append(detail)
                continue
            exported.append({'start_ms': left, 'end_ms': right, 'text': cue['text']})
            outputs.append({**detail, 'output_index': len(exported), 'start_ms': left, 'end_ms': right})
        review = crosses_cut or truncated or bool(collapsed)
        action = 'retained'
        if not spans:
            action = 'removed'
        elif len(groups) > 1:
            action = 'split'
        elif truncated:
            action = 'truncated'
        elif collapsed:
            action = 'omitted_after_millisecond_rounding'
        mappings.append({
            'source_index': cue['source_index'], 'source_start_ms': cue['start_ms'],
            'source_end_ms': cue['end_ms'], 'text': cue['text'], 'action': action,
            'crosses_cut': crosses_cut, 'truncated': truncated,
            'review_status': 'pending' if review else 'not_requested',
            'text_assignment': 'full_text_repeated_pending_review' if len(outputs) > 1 else 'source_text_preserved',
            'reason': 'outside_all_kept_intervals' if not spans else 'half_open_intersection_with_effective_frame_ranges',
            'outputs': outputs, 'submillisecond_spans_not_exported': collapsed,
        })
    return exported, mappings
