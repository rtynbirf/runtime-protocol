#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Приёмки статуса OBSERVED (ADR-0013 ядра cell-swarm): клетка без явного
ожидания payload.cells[cell] не сверяется вслепую и не врёт DRIFT — она
честно OBSERVED. Офлайн, stdlib-only, чистая функция без сети."""
import unittest

from blood_cell import OBSERVED, DRIFT, VERIFIED, verdict_for


def _env(payload):
    # Конверт-минимум: verdict_for смотрит только payload (хеш/цепь/ttl —
    # забота verify_envelope, покрыт test_blood.py).
    return {"v": 1, "src": "model", "dst": "runtime-protocol", "seq": 1,
            "ts": "2026-11-07T00:00:00Z", "ttl_s": 900,
            "payload": payload, "prev_hash": "0" * 64, "hash": "x" * 64}


class TestObservedVerdict(unittest.TestCase):
    def test_no_expectation_is_observed_not_drift(self):
        """Эхо предыдущей клетки несёт observed/expected, но не cells —
        вторая клетка цепи наблюдает, а не сравнивает чужое со своим."""
        incoming = _env({"observed": "a" * 40, "expected": "b" * 40,
                         "status": VERIFIED, "pulse": 1})
        v = verdict_for(incoming, "polymath-os", "c" * 40)
        self.assertEqual(v["status"], OBSERVED)
        self.assertIsNone(v["violation"])

    def test_explicit_expectation_verified(self):
        incoming = _env({"cells": {"runtime-protocol": "abc123"}})
        v = verdict_for(incoming, "runtime-protocol", "abc123")
        self.assertEqual(v["status"], VERIFIED)

    def test_explicit_expectation_drift(self):
        incoming = _env({"cells": {"runtime-protocol": "abc123"}})
        v = verdict_for(incoming, "runtime-protocol", "def456")
        self.assertEqual(v["status"], DRIFT)

    def test_other_cell_expectation_is_not_mine(self):
        """Ожидание для другой клетки — не моё: я наблюдаю, не подставляю."""
        incoming = _env({"cells": {"runtime-protocol": "abc123"}})
        v = verdict_for(incoming, "polymath-os", "abc123")
        self.assertEqual(v["status"], OBSERVED)


if __name__ == "__main__":
    unittest.main()
