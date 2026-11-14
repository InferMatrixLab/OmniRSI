#!/usr/bin/env python3
"""Minimal positive/negative cases for portable trial-bytes acceptance.

Run: python3 docs/reviews/pr437-s01-reproduction/test_acceptance.py
This module is not collected by the repository pytest suite (testpaths=tests).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reproduce

PIN_F = reproduce.EXPECTED_FILE_DIGEST
PIN_R = reproduce.EXPECTED_RESULT_DIGEST
OTHER_F = "0" * 64
OTHER_R = "1" * 64


class AcceptTrialBytesTests(unittest.TestCase):
    def _run(self, **kwargs):
        defaults = dict(
            compare_rc=0,
            trial_count=58,
            file_count=448,
            digest_files=OTHER_F,
            digest_results=OTHER_R,
            require_historical=False,
        )
        defaults.update(kwargs)
        return reproduce.accept_trial_bytes(**defaults)

    def test_fresh_scratch_448_zero_diffs_exits_0_without_historical_pin(self):
        # The reported portable defect: 0 diffs but path-bearing digests differ.
        code, lines = self._run()
        self.assertEqual(code, 0)
        self.assertTrue(any("same-run parity: PASS" in line for line in lines))
        self.assertTrue(any("historical-reference digest: not matched" in line for line in lines))

    def test_fresh_scratch_447_zero_diffs_exits_0(self):
        # Observed on Windows and on a Linux Python 3.12.14 review host.
        code, lines = self._run(file_count=447)
        self.assertEqual(code, 0)
        self.assertTrue(any("same-run parity: PASS" in line for line in lines))
        self.assertTrue(any("retained file count is 447" in line for line in lines))

    def test_historical_pin_match_exits_0_and_reports_match(self):
        code, lines = self._run(digest_files=PIN_F, digest_results=PIN_R)
        self.assertEqual(code, 0)
        self.assertTrue(any("historical-reference digest: MATCH" in line for line in lines))

    def test_require_historical_mismatch_exits_2(self):
        code, lines = self._run(require_historical=True)
        self.assertEqual(code, 2)
        self.assertTrue(any("--require-historical-digest" in line for line in lines))

    def test_require_historical_match_exits_0(self):
        code, _ = self._run(
            digest_files=PIN_F, digest_results=PIN_R, require_historical=True,
        )
        self.assertEqual(code, 0)

    def test_compare_failure_exits_2_even_if_digests_match(self):
        code, lines = self._run(
            compare_rc=1, digest_files=PIN_F, digest_results=PIN_R,
        )
        self.assertEqual(code, 2)
        self.assertTrue(any("same-run parity: FAIL" in line for line in lines))

    def test_wrong_trial_count_exits_2(self):
        code, _ = self._run(trial_count=57)
        self.assertEqual(code, 2)

    def test_undocumented_file_count_exits_2(self):
        code, _ = self._run(file_count=400)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
