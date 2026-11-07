# Copyright 2026 lightcoloror. SPDX-License-Identifier: Apache-2.0
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from local_rough_cut.cli import compose_plan, render, source_profile, validate_plan, verify_cadence
from local_rough_cut.contract_io import artifact_reference, payload_sha256
from local_rough_cut.errors import ContractError
from local_rough_cut.render_kernel import _render_command


class CutContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()).resolve() / ('local-rough-cut-test-' + uuid4().hex)
        self.root.mkdir()
        self.source = self.root / 'synthetic.bin'
        self.source.write_bytes(b'synthetic fixture, no media')
        self.ref = artifact_reference(self.source, 'test')
        self.profile = {'width':320,'height':240,'fps':25.0,'duration_s':8.0,'has_audio':True,'fps_fraction':'25/1'}
        self.choices = {'keep':[{'start':0.4,'end':2.0},{'start':4.0,'end':6.4}]}
        self.plan = compose_plan(self.ref, self.choices, self.profile)

    def tearDown(self):
        self.source.unlink()
        self.root.rmdir()

    def test_correct_mapping_and_frame_count(self):
        self.assertEqual(self.plan['expected_frames'], 100)
        self.assertEqual(self.plan['expected_duration_s'], 4.0)
        self.assertEqual(self.plan['timeline'][1]['target_start'], 1.6)
        self.assertEqual(self.plan['timeline'][1]['source_start'], 4.0)

    def test_reject_malformed_or_dangerous_intervals(self):
        cases = [[], [{'start':0,'end':0}], [{'start':-1,'end':1}], [{'start':0,'end':9}],
                 [{'start':True,'end':1}], [{'start':0,'end':float('nan')}],
                 [{'start':0,'end':float('inf')}], [{'start':'0;movie=x','end':1}],
                 [{'start':2,'end':4},{'start':3,'end':5}], [{'start':0,'end':0.01}],
                 [{'start':0,'end':1,'command':'anything'}]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ContractError):
                compose_plan(self.ref, {'keep':rows}, self.profile)

    def test_rounding_is_recorded_and_not_semantic_deletion(self):
        p = compose_plan(self.ref, {'keep':[{'start':0.411,'end':2.012}]}, self.profile)
        self.assertEqual(p['nle_segments'][0]['in_s'], 0.4)
        self.assertEqual(p['nle_segments'][0]['requested_start_s'], 0.411)
        self.assertFalse(p['automatic_semantic_cutting'])

    def test_modified_plan_rejected_even_if_rehashed(self):
        p = copy.deepcopy(self.plan)
        p['nle_segments'][0]['in_s'] = 7
        p['plan_sha256'] = payload_sha256(p, 'plan_sha256')
        with self.assertRaises(ContractError):
            validate_plan(p)

    def test_changed_source_rejected(self):
        self.source.write_bytes(b'changed synthetic bytes')
        with self.assertRaises(ContractError):
            validate_plan(self.plan)

    def test_render_requires_explicit_review(self):
        with self.assertRaises(ContractError):
            render(self.root / 'absent.json', self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_unknown_top_level_and_unsafe_fade_rejected(self):
        for extra in ({'execute':'anything'}, {'audio_edge_fade_ms':21}, {'audio_edge_fade_ms':True}):
            with self.subTest(extra=extra), self.assertRaises(ContractError):
                compose_plan(self.ref, {**self.choices, **extra}, self.profile)

    def test_original_kernel_has_no_overwrite_or_shell_command(self):
        args = _render_command(self.plan, self.root / 'out.mp4', True)
        self.assertIn('-n', args)
        self.assertNotIn('-y', args)
        self.assertIn('trim=start_frame=10:end_frame=50', args[args.index('-filter_complex')+1])

    def test_silent_source_does_not_invent_audio(self):
        args = _render_command(self.plan, self.root / 'out.mp4', False)
        self.assertNotIn('-c:a', args)
        self.assertNotIn('[a]', args)

    def test_vfr_packets_rejected(self):
        class Result:
            returncode = 0
            stdout = '0.0\n0.04\n0.11\n'
            stderr = ''
        with patch('local_rough_cut.cli.resolve_media_tool', return_value='ffprobe'), patch('local_rough_cut.cli.subprocess.run', return_value=Result()), self.assertRaises(ContractError):
            verify_cadence(self.source, self.profile)

    def test_rotation_hdr_and_multitrack_rejected(self):
        base = {'streams':[{'codec_type':'video','width':320,'height':240,'pix_fmt':'yuv420p','avg_frame_rate':'25/1','duration':'8'}], 'format':{'format_name':'mov,mp4','duration':'8'}}
        for change in ({'tags':{'rotate':'90'}}, {'color_transfer':'smpte2084'}, {'start_time':'1'}, {'pix_fmt':'yuv420p10le'}):
            case = copy.deepcopy(base)
            case['streams'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ContractError):
                source_profile(case)
        base['streams'] += [{'codec_type':'audio'}, {'codec_type':'audio'}]
        with self.assertRaises(ContractError):
            source_profile(base)


if __name__ == '__main__':
    unittest.main()
