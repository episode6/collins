# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
"""The probe's reports: parsing them off a pty's output, and the hex."""

import unittest

from conformance.kit import probe


class ReportParsingTests(unittest.TestCase):
    def test_reports_are_read_off_the_output_in_order(self):
        output = b"junk\x1b]777;READY\x1b\\more\x1b]777;R 1 1b5b306e\x07tail"
        self.assertEqual(probe.parse_reports(output), ["READY", "R 1 1b5b306e"])

    def test_a_report_with_an_empty_hex_is_no_bytes(self):
        self.assertEqual(probe.parse_hex_report("R 7 "), ("7", b""))
        self.assertEqual(probe.parse_hex_report("R 7"), ("7", b""))

    def test_hex_decodes_to_the_bytes(self):
        seq, data = probe.parse_hex_report("R 12 1b5b3f36313b313b32313b32323b323863")
        self.assertEqual(seq, "12")
        self.assertEqual(data, b"\x1b[?61;1;21;22;28c")

    def test_bad_hex_and_a_non_report_are_errors(self):
        with self.assertRaises(ValueError):
            probe.parse_hex_report("R 1 zz")
        with self.assertRaises(ValueError):
            probe.parse_hex_report("D 1")

    def test_a_report_cut_by_a_frame_boundary_waits_for_its_end(self):
        class Output:
            def __init__(self):
                self.output = {1: bytearray(b"\x1b]777;R 1 1b5b")}

            def pump(self, _timeout):
                pass

        p = probe.Probe(Output(), 1)
        p._scan()
        self.assertEqual(p.reports, [])
        self.assertEqual(p.scanned, 0)
        p.client.output[1].extend(b"306e\x1b\\")
        p._scan()
        self.assertEqual(p.reports, ["R 1 1b5b306e"])


if __name__ == "__main__":
    unittest.main()
