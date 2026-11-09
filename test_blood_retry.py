"""Храповик ретрай-окна переходящих ран в крови (волна ×6 · TODO ×5 п.1).

Классификация ран (инцидент run 42401466454 «СЛОМАН (STALE), конвертов 0» —
гонка переписи чужой летописи параллельной волной):

(a) летопись STRUCTURE/CHAIN_BROKEN на 1-й попытке → свежий срез healed
    → 2-я попытка зелёная (гонка лечится повторным чтением, не повтором вердикта);
(b) летопись сломана во всех попытках → честный красный, ровно N чтений и (N-1)
    пауз по умолчанию 30с (инъекция sleep_fn — тесты не спят);
(c) входящий STALE во всех попытках → честный ноль (TH-KNOW-1): кровь не текла;
(d) постоянные раны БЕЗ ретрая: FORGED летописи/конверта, SEQ_STAGNANT —
    одна попытка, ноль пауз;
(e) идемпотентный повтор остаётся зелёным (апстрим-семантика сохранена);
(f) дефолты 3/30 живы, флаги управляют окном, мусорные флаги — честный отказ
    до любого сетевого шага;
(g) main честно маппит исходы окна: ledger_broken → exit 2, stale_exhausted
    → exit 0.

Все приёмки офлайн: инъекции read_ledger/_sleep, subprocess — только для
гарды флагов. Апстрим-файлы (test_blood*, blood.py) не тронуты.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import blood
import blood_cell

ROOT = Path(__file__).resolve().parent
GENESIS = "0" * 64


def ledger_lines(src_cell, n):
    """Целая летопись из n конвертов ядра (src_cell → cell-swarm)."""
    lines, prev = [], GENESIS
    for i in range(1, n + 1):
        env = blood.make_envelope(src_cell, "cell-swarm", i,
                                  {"observed": "sha-%d" % i}, prev)
        lines.append(blood.canonical_json(env))
        prev = env["hash"]
    return lines, prev


def stale_incoming(prev_hash, seq=99):
    """Входящий конверт с истёкшим ttl (рождён в прошлом, ttl маленький)."""
    return blood.make_envelope("polymath-os", "runtime-protocol", seq,
                               {"pulse": seq}, prev_hash, ttl_s=1,
                               ts="2026-01-01T00:00:00Z")


class ReconcilePulse(unittest.TestCase):

    def setUp(self):
        self.sleeps = []
        self.pause = self.sleeps.append
        self.ledger, self.tail = ledger_lines("polymath-os", 2)

    def test_a_broken_ledger_heals_on_fresh_slice(self):
        broken = ['{"это": "не конверт"}']
        reads = []

        def fake_read(token):
            reads.append(1)
            return (broken[:] if len(reads) == 1 else self.ledger[:],
                    "f/sha", "cell-swarm/blood/LEDGER.jsonl")

        incoming = blood.make_envelope("polymath-os", "runtime-protocol", 3,
                                       {"pulse": 3}, self.tail)
        with mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            out = blood_cell.reconcile_pulse("t", incoming)
        self.assertEqual(out["state"], "ok")
        self.assertEqual(len(reads), 2, "гонка лечится повторным ЧТЕНИЕМ")
        self.assertEqual(self.sleeps, [30], "одна пауза дефолта между попытками")

    def test_b_ledger_broken_all_attempts_is_honest_red(self):
        broken = ['{"это": "не конверт"}']
        reads = []

        def fake_read(token):
            reads.append(1)
            return (broken[:], "f/sha", "cell-swarm/ledger")

        incoming = blood.make_envelope("polymath-os", "runtime-protocol", 3,
                                       {"pulse": 3}, self.tail)
        with mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            out = blood_cell.reconcile_pulse("t", incoming)
        self.assertEqual(out["state"], "ledger_broken")
        self.assertEqual(len(reads), 3)
        self.assertEqual(self.sleeps, [30, 30])

    def test_c_incoming_stale_all_attempts_is_honest_zero(self):
        incoming = stale_incoming(self.tail)
        reads = []

        def fake_read(token):
            reads.append(1)
            return (self.ledger[:], "f/sha", "cell-swarm/ledger")

        with mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            out = blood_cell.reconcile_pulse("t", incoming)
        self.assertEqual(out["state"], "stale_exhausted")
        self.assertEqual(len(reads), 3, "окно отработано целиком до честного нуля")
        self.assertEqual(self.sleeps, [30, 30])

    def test_d_forged_envelope_no_retry(self):
        incoming = stale_incoming(self.tail)
        incoming["payload"]["pulse"] = 999  # подделка: хеш уже не сходится
        reads = []

        def fake_read(token):
            reads.append(1)
            return (self.ledger[:], "f/sha", "cell-swarm/ledger")

        with mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            with self.assertRaises(SystemExit) as cm:
                blood_cell.reconcile_pulse("t", incoming)
        self.assertIn("FORGED", str(cm.exception.code))
        self.assertEqual(reads, [1], "подделка не лечится повтором — одна попытка")
        self.assertEqual(self.sleeps, [])

    def test_d_forged_ledger_no_retry(self):
        lines, _ = ledger_lines("polymath-os", 2)
        env = json.loads(lines[0])
        env["payload"]["observed"] = "подмена"
        lines[0] = blood.canonical_json(env)
        reads = []

        def fake_read(token):
            reads.append(1)
            return (lines, "f/sha", "cell-swarm/ledger")

        incoming = blood.make_envelope("polymath-os", "runtime-protocol", 3,
                                       {"pulse": 3}, self.tail)
        with mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            with self.assertRaises(SystemExit):
                blood_cell.reconcile_pulse("t", incoming)
        self.assertEqual(reads, [1])
        self.assertEqual(self.sleeps, [])

    def test_e_idempotent_repeat_stays_green(self):
        incoming = blood.make_envelope("polymath-os", "runtime-protocol", 2,
                                       {"observed": "sha-2"}, GENESIS)
        incoming["hash"] = self.tail  # ровно последний конверт летописи
        reads = []

        def fake_read(token):
            reads.append(1)
            return (self.ledger[:], "f/sha", "cell-swarm/ledger")

        with mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            out = blood_cell.reconcile_pulse("t", incoming)
        self.assertEqual(out["state"], "ok")
        self.assertEqual((len(reads), self.sleeps), (1, []))

    def test_f_defaults_and_flags(self):
        incoming = stale_incoming(self.tail)
        reads = []

        def fake_read(token):
            reads.append(1)
            return (self.ledger[:], "f/sha", "cell-swarm/ledger")

        with mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            out = blood_cell.reconcile_pulse("t", incoming)  # дефолты 3/30
            self.assertEqual((out["state"], self.sleeps), ("stale_exhausted", [30, 30]))
            reads.clear(); self.sleeps.clear()
            out = blood_cell.reconcile_pulse("t", incoming, 1, 5)
            self.assertEqual((out["state"], self.sleeps), ("stale_exhausted", []))
            self.assertEqual(len(reads), 1)

    def test_f_flag_guards_before_any_network(self):
        for argv, needle in ((["--stale-retries", "abc"], "не целое"),
                             (["--stale-retries", "0"], "ниже минимума")):
            with tempfile.TemporaryDirectory() as tmp:
                r = subprocess.run([sys.executable, str(ROOT / "blood_cell.py")] + argv,
                                   capture_output=True, text=True,
                                   env={"PATH": os.environ.get("PATH", "")},
                                   cwd=str(ROOT))
            self.assertEqual(r.returncode, 1, argv)
            self.assertIn(needle, (r.stdout + r.stderr))

    def test_g_main_maps_window_outcomes(self):
        env = {"BLOOD_ENVELOPE": json.dumps({"v": 1}),
               "CELL_NAME": "runtime-protocol", "CELL_SHA": "abc",
               "GH_TOKEN": "t"}
        for state, code in (("ledger_broken", 2), ("stale_exhausted", 0)):
            with mock.patch.object(blood_cell, "reconcile_pulse",
                                   return_value={"state": state}), \
                 mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(sys, "argv", ["blood_cell.py"]), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    blood_cell.main()
            self.assertEqual(cm.exception.code, code, state)


if __name__ == "__main__":
    unittest.main()
