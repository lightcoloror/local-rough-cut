# Copyright 2026 lightcoloror. SPDX-License-Identifier: Apache-2.0
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from local_rough_cut.cli import compose_plan, retime_srt, review
from local_rough_cut.contract_io import artifact_reference, atomic_write_json, file_sha256
from local_rough_cut.errors import ContractError
from local_rough_cut.review import attach_rendered, boundary_review
from local_rough_cut.subtitles import format_srt, parse_srt, retime_cues


class P1Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()).resolve() / ('rough-cut-p1-test-' + uuid4().hex)
        self.root.mkdir()
        self.source = self.root / 'source.bin'
        self.source.write_bytes(b'synthetic test source')
        self.profile = {'width': 320, 'height': 240, 'fps': 25.0, 'duration_s': 8.0,
                        'has_audio': True, 'fps_fraction': '25/1'}
        self.ref = artifact_reference(self.source, 'synthetic source')
        self.plan = compose_plan(self.ref, {'keep': [{'start': 0.4, 'end': 2.0}, {'start': 4.0, 'end': 6.4}]}, self.profile)
        self.plan_path = self.root / 'plan.json'
        atomic_write_json(self.plan, self.plan_path)

    def tearDown(self):
        assert self.root.parent == Path(tempfile.gettempdir()).resolve()
        assert self.root.name.startswith('rough-cut-p1-test-')
        shutil.rmtree(self.root)

    def test_opening_splice_and_ending_locate_both_clocks(self):
        report = boundary_review(self.plan)
        opening, splice, ending = report['items']
        self.assertEqual(opening['source_windows'][0]['range_s'], [0.4, 1.0])
        self.assertEqual(splice['target_boundary_s'], 1.6)
        self.assertEqual(splice['target_window'], [1.0, 2.2])
        self.assertEqual([r['seek_s'] for r in splice['source_windows']], [1.96, 4.0])
        self.assertEqual(ending['target_boundary_s'], 4.0)
        self.assertEqual(ending['target_seek_s'], 3.96)
        self.assertEqual(ending['source_windows'][0]['range_s'], [5.8, 6.4])
        self.assertTrue(all(r['review_status'] == 'pending' for r in report['items']))

    def test_confirmation_does_not_claim_playback(self):
        report = boundary_review(self.plan, reviewed=True)
        self.assertTrue(all(r['review_status'] == 'caller_asserted' for r in report['items']))
        self.assertTrue(all(r['human_playback'] == 'not_verified' for r in report['items']))

    def test_small_segments_and_radius_validation(self):
        plan = compose_plan(self.ref, {'keep': [{'start': 0.4, 'end': 0.48}]}, self.profile)
        report = boundary_review(plan)
        self.assertEqual(report['items'][0]['target_window'], [0.0, 0.08])
        self.assertEqual(len(report['items']), 2)
        for value in (0, -1, True, float('nan'), float('inf'), 5.1):
            with self.subTest(value=value), self.assertRaises(ContractError):
                boundary_review(self.plan, value)

    def test_review_entry_and_changed_source_protection(self):
        output = review(self.plan_path, self.root / 'review')
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['plan_sha256'], self.plan['plan_sha256'])
        self.source.write_bytes(b'changed')
        with self.assertRaises(ContractError):
            review(self.plan_path, self.root / 'invalid-review')
        self.assertFalse((self.root / 'invalid-review').exists())

    def test_kept_removed_and_multiline(self):
        text = '1\r\n00:00:00,600 --> 00:00:01,100\r\nline one\r\nline two\r\n\r\n2\r\n00:00:02,400 --> 00:00:03,500\r\nremoved\r\n'
        exported, report = retime_cues(parse_srt(text), self.plan)
        self.assertEqual((exported[0]['start_ms'], exported[0]['end_ms']), (200, 700))
        self.assertEqual(exported[0]['text'], 'line one\nline two')
        self.assertEqual(report[1]['action'], 'removed')
        self.assertEqual(report[1]['reason'], 'outside_all_kept_intervals')
        self.assertEqual(parse_srt(format_srt(exported))[0]['text'], exported[0]['text'])

    def test_cross_cut_is_split_without_inventing_word_alignment(self):
        exported, report = retime_cues(parse_srt('7\n00:00:01,800 --> 00:00:04,200\nwhole sentence\n'), self.plan)
        self.assertEqual([(r['start_ms'], r['end_ms']) for r in exported], [(1400, 1600), (1600, 1800)])
        self.assertEqual([r['text'] for r in exported], ['whole sentence', 'whole sentence'])
        self.assertTrue(report[0]['crosses_cut'])
        self.assertEqual(report[0]['review_status'], 'pending')
        self.assertEqual(report[0]['text_assignment'], 'full_text_repeated_pending_review')

    def test_half_open_edges_and_single_side_truncation(self):
        cases = [(200, 600, [(0, 200)], 'truncated'),
                 (1800, 2000, [(1400, 1600)], 'retained'),
                 (2000, 4000, [], 'removed'),
                 (4000, 4200, [(1600, 1800)], 'retained'),
                 (6200, 6800, [(3800, 4000)], 'truncated')]
        for start, end, expected, action in cases:
            cue = {'source_index': 1, 'start_ms': start, 'end_ms': end, 'text': 'edge'}
            exported, report = retime_cues([cue], self.plan)
            self.assertEqual([(r['start_ms'], r['end_ms']) for r in exported], expected)
            self.assertEqual(report[0]['action'], action)

    def test_contiguous_keeps_do_not_repeat_text(self):
        plan = compose_plan(self.ref, {'keep': [{'start': 0.4, 'end': 2}, {'start': 2, 'end': 4}]}, self.profile)
        exported, report = retime_cues(parse_srt('1\n00:00:01,800 --> 00:00:02,200\ncontinuous\n'), plan)
        self.assertEqual(len(exported), 1)
        self.assertEqual((exported[0]['start_ms'], exported[0]['end_ms']), (1400, 1800))
        self.assertTrue(report[0]['crosses_cut'])

    def test_millisecond_quantization_is_reported(self):
        profile = dict(self.profile, fps=30000 / 1001, fps_fraction='30000/1001')
        plan = compose_plan(self.ref, {'keep': [{'start': 1001 / 30000, 'end': 4004 / 30000}]}, profile)
        exported, report = retime_cues(parse_srt('1\n00:00:00,000 --> 00:00:00,034\ntiny\n'), plan)
        self.assertEqual(exported, [])
        self.assertEqual(len(report[0]['submillisecond_spans_not_exported']), 1)
        self.assertEqual(report[0]['review_status'], 'pending')

    def test_bad_timestamps_conflicts_and_empty_text_are_rejected(self):
        cases = ['1\n00:00:00.600 --> 00:00:01,100\ntext',
                 '1\n00:60:00,000 --> 01:00:01,000\ntext',
                 '1\n00:00:01,000 --> 00:00:00,000\ntext',
                 '1\n00:00:00,000 --> 00:00:01,000 X1:0\ntext',
                 '1\n00:00:00,000 --> 00:00:01,000\n  ',
                 '1\n00:00:00,000 --> 00:00:02,000\na\n\n2\n00:00:01,000 --> 00:00:03,000\nb',
                 '1\n00:00:00,000 --> 00:00:01,000\na\n\n1\n00:00:02,000 --> 00:00:03,000\nb']
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ContractError):
                parse_srt(text)

    def test_export_entry_preserves_inputs_and_refuses_existing_output(self):
        srt = self.root / 'source.srt'
        srt.write_text('1\n00:00:01,800 --> 00:00:04,200\nwhole sentence\n', encoding='utf-8')
        before = file_sha256(srt)
        output = retime_srt(self.plan_path, srt, self.root / 'subtitles', reviewed=True)
        report = json.loads((output.parent / 'mapping.json').read_text(encoding='utf-8'))
        self.assertEqual(report['pending_source_indexes'], [1])
        self.assertEqual(report['status'], 'text_review_required')
        self.assertEqual(file_sha256(srt), before)
        self.assertEqual(file_sha256(self.source), self.ref['sha256'])
        with self.assertRaises(ContractError):
            retime_srt(self.plan_path, srt, output.parent, reviewed=True)

    def test_confirmation_and_out_of_bounds_fail_before_output(self):
        srt = self.root / 'source.srt'
        srt.write_text('1\n00:00:07,000 --> 00:00:09,000\ninvalid\n', encoding='utf-8')
        with self.assertRaises(ContractError):
            retime_srt(self.plan_path, srt, self.root / 'out')
        with self.assertRaises(ContractError):
            retime_srt(self.plan_path, srt, self.root / 'out', reviewed=True)
        self.assertFalse((self.root / 'out').exists())

    def test_all_removed_produces_explicit_empty_output(self):
        exported, report = retime_cues(parse_srt('1\n00:00:02,400 --> 00:00:03,500\nremoved\n'), self.plan)
        self.assertEqual(format_srt(exported), '')
        self.assertEqual(report[0]['outputs'], [])
        self.assertEqual(parse_srt('\ufeff'), [])

    def test_rendered_review_rejects_unbound_or_changed_candidate(self):
        movie = self.root / 'candidate.mp4'
        movie.write_bytes(b'synthetic reference only')
        validation = self.root / 'validation.json'
        payload = {'status': 'technical_checks_passed', 'plan_sha256': 'wrong',
                   'candidate': artifact_reference(movie, 'candidate')}
        atomic_write_json(payload, validation)
        with self.assertRaises(ContractError):
            attach_rendered({}, validation, self.plan)
        payload['plan_sha256'] = self.plan['plan_sha256']
        atomic_write_json(payload, validation)
        movie.write_bytes(b'changed candidate')
        with self.assertRaises(ContractError):
            attach_rendered({}, validation, self.plan)

    def test_frame_rounded_plan_controls_subtitles(self):
        plan = compose_plan(self.ref, {'keep': [{'start': 0.411, 'end': 2.012}]}, self.profile)
        exported, _ = retime_cues(parse_srt('1\n00:00:00,400 --> 00:00:00,440\nframe edge\n'), plan)
        self.assertEqual((exported[0]['start_ms'], exported[0]['end_ms']), (0, 40))


if __name__ == '__main__':
    unittest.main()
