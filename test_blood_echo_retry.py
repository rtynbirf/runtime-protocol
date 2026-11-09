"""Ретрай-окно эха крови: гонка file_sha HTTP 409 (волна ×8 · TODO ×6 п.4).

Живой корень: между чтением летописи (file_sha) и PUT эха чужая клетка долила
свою строку — GitHub отвечает 409, эхо не долито, кровь остановлена. Канон
×6 (синтез с TH-KNOW-1): гонка лечится свежим срезом, не повтором вердикта
по старым байтам.

Приёмки:
(a) первая попытка 200 — PUT ровно один, эхо в теле PUT, крови дальше есть что нести;
(b) 409 → свежий срез с чужим добором → эхо пересобрано против НОВОГО хвоста
    (свежий prev_hash, seq учёл чужое эхо) → 2-й PUT 200; PUT-ов 2, пауз 1;
(c) 409 → свежий срез уже содержит НАШЕ эхо (тот же src, тот же pulse) —
    идемпотентный повтор: второго PUT нет, дубля нет;
(d) окно исчерпано (409 в каждой попытке, срез цел) — честный exit 3,
    PUT-ов ровно N, пауз N-1 (инъекция sleep_fn — тесты не спят);
(e) постоянная рана HTTP 403 — отказ сразу: PUT-ов 1, пауз 0;
(f) свежий срез FORGED — отказ сразу (подделка не лечится повтором), PUT-ов 1;
(g) свежий срез STRUCTURE — переходящая гонка переписи: окно исчерпано → exit 3;
(h) 422 с sha-миссматчем — переходящая, как 409 → 2-й PUT 200;
(i) флаги --echo-retries/--echo-pause: мусор — честный отказ ДО сетевого шага;
    дефолты 3/10 живы.

Все приёмки офлайн: инъекции api/read_ledger/_sleep, subprocess — только для
гарды флагов. Апстрим-файлы (test_blood*, blood.py, blood_cell.py-сверка) не
тронуты — окно добавлено дополнением (закон §3).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
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


def incoming_for(prev_hash, seq=3):
    """Входящий конверт крови от предыдущей клетки (живой ttl)."""
    return blood.make_envelope("polymath-os", "runtime-protocol", seq,
                               {"pulse": seq,
                                "cells": {"runtime-protocol": "cell-sha-8"}},
                               prev_hash)


class AppendEcho(unittest.TestCase):

    def setUp(self):
        self.sleeps = []
        self.pause = self.sleeps.append
        self.ledger, self.tail = ledger_lines("polymath-os", 2)
        self.incoming = incoming_for(self.tail)

    def echo_of(self, body):
        """Последняя строка эха в теле PUT (content — base64 JSONL)."""
        content = json.loads(body)["content"]
        text = __import__("base64").b64decode(content).decode()
        return json.loads(text.strip().splitlines()[-1])

    def test_a_first_try_200_single_put(self):
        puts = []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            return 200, {"commit": {"sha": "new1"}}

        with mock.patch.object(blood_cell, "api", side_effect=fake_api), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            echo = blood_cell.append_echo("t", self.ledger[:], "f/sha",
                                          self.incoming, "runtime-protocol",
                                          "cell-sha-8", blood.VERIFIED)
        self.assertEqual(len(puts), 1, "зелёный путь — ровно один PUT")
        landed = self.echo_of(json.dumps(puts[0]))
        self.assertEqual(landed["src"], "runtime-protocol")
        self.assertEqual(landed["payload"]["pulse"], 3)
        self.assertEqual(landed["payload"]["status"], blood.VERIFIED)
        self.assertEqual(landed["prev_hash"], self.tail)
        self.assertEqual(echo["seq"], 1, "наше эхо первое от этой клетки: seq=1")
        self.assertEqual(puts[0]["sha"], "f/sha")
        self.assertEqual(self.sleeps, [], "зелёный путь не спит")

    def test_a_seq_counts_only_own_cell(self):
        puts = []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            return 200, {}

        with mock.patch.object(blood_cell, "api", side_effect=fake_api):
            echo = blood_cell.append_echo("t", self.ledger[:], "f/sha",
                                          self.incoming, "runtime-protocol",
                                          "cell-sha-8", blood.VERIFIED)
        self.assertEqual(echo["seq"], 1, "чужие эха (polymath-os) не съедают нашу нумерацию")

    def test_b_409_race_rebuilds_against_fresh_tail(self):
        # Чужая клетка долила эхо runtime-protocol seq=5 между чтением и PUT
        payload = {"observed": "sha-x", "expected": None,
                   "status": blood_cell.OBSERVED, "pulse": 2}
        other = blood.make_envelope("runtime-protocol", "cell-swarm", 5,
                                    payload, self.tail)
        fresh = self.ledger + [blood.canonical_json(other)]
        fresh_tail = other["hash"]
        puts = []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            if len(puts) == 1:
                return 409, {"message": "does not match the current file_sha"}
            return 200, {}

        def fake_read(token):
            return fresh[:], "f/sha-new", "cell-swarm/blood/CELLS-LEDGER.jsonl"

        with mock.patch.object(blood_cell, "api", side_effect=fake_api), \
             mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            echo = blood_cell.append_echo("t", self.ledger[:], "f/sha-old",
                                          self.incoming, "runtime-protocol",
                                          "cell-sha-8", blood.VERIFIED)
        self.assertEqual(len(puts), 2, "гонка лечится переслом PUT по свежему срезу")
        self.assertEqual(self.sleeps, [10], "одна пауза дефолта 10с")
        landed = self.echo_of(json.dumps(puts[1]))
        self.assertEqual(landed["prev_hash"], fresh_tail,
                         "эхо пересобрано против НОВОГО хвоста, не старых байт")
        self.assertEqual(landed["seq"], 6, "свежий seq учёл чужое эхо клетки (5 → 6)")
        self.assertEqual(echo["hash"], landed["hash"])
        self.assertEqual(puts[1]["sha"], "f/sha-new", "PUT со свежим sha")

    def test_c_idempotent_repeat_no_double_echo(self):
        # Наше эхо уже стоит в свежем срезе (прошлый PUT долетел, ответ потерялся)
        mine = blood.make_envelope(
            "runtime-protocol", "cell-swarm", 1,
            {"observed": "cell-sha-8", "expected": "cell-sha-8",
             "status": blood.VERIFIED, "pulse": 3}, self.tail)
        fresh = self.ledger + [blood.canonical_json(mine)]
        puts = []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            return 409, {"message": "does not match the current file_sha"}

        def fake_read(token):
            return fresh[:], "f/sha-new", "cell-swarm/blood/CELLS-LEDGER.jsonl"

        with mock.patch.object(blood_cell, "api", side_effect=fake_api), \
             mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            echo = blood_cell.append_echo("t", self.ledger[:], "f/sha-old",
                                          self.incoming, "runtime-protocol",
                                          "cell-sha-8", blood.VERIFIED)
        self.assertEqual(len(puts), 1, "второго PUT нет — дубля нет")
        self.assertEqual(self.sleeps, [], "идемпотентный повтор не спит")
        self.assertEqual(echo["payload"]["pulse"], 3)
        self.assertEqual(echo["hash"], mine["hash"], "вернулось именно долетевшее эхо")

    def test_d_window_exhausted_honest_exit_3(self):
        puts = []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            return 409, {"message": "does not match the current file_sha"}

        def fake_read(token):
            return self.ledger[:], "f/sha", "cell-swarm/blood/CELLS-LEDGER.jsonl"

        with mock.patch.object(blood_cell, "api", side_effect=fake_api), \
             mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            with self.assertRaises(SystemExit) as cm:
                blood_cell.append_echo("t", self.ledger[:], "f/sha",
                                       self.incoming, "runtime-protocol",
                                       "cell-sha-8", blood.VERIFIED,
                                       echo_retries=3, echo_pause=7)
        self.assertEqual(cm.exception.code, 3, "исчерпание окна — честный красный 3")
        self.assertEqual(len(puts), 3, "PUT-ов ровно по числу попыток")
        self.assertEqual(self.sleeps, [7, 7], "пауз N-1, инъекция честная")

    def test_e_permanent_4xx_immediate_refusal(self):
        puts = []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            return 403, {"message": "Forbidden"}

        with mock.patch.object(blood_cell, "api", side_effect=fake_api), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            with self.assertRaises(SystemExit) as cm:
                blood_cell.append_echo("t", self.ledger[:], "f/sha",
                                       self.incoming, "runtime-protocol",
                                       "cell-sha-8", blood.VERIFIED)
        self.assertIn("постоянная рана", str(cm.exception))
        self.assertEqual(len(puts), 1, "прочие 4xx не ретраятся")
        self.assertEqual(self.sleeps, [])

    def test_f_forged_fresh_slice_immediate_refusal(self):
        puts = []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            return 409, {"message": "does not match the current file_sha"}

        def fake_read(token):
            # Подмена тела первого конверта рвёт хеш-цепь → приговор FORGED
            tampered = [json.loads(l) for l in self.ledger]
            tampered[0]["seq"] = 999
            return [blood.canonical_json(e) for e in tampered], "f/x", "ledger"

        with mock.patch.object(blood_cell, "api", side_effect=fake_api), \
             mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            with self.assertRaises(SystemExit) as cm:
                blood_cell.append_echo("t", self.ledger[:], "f/sha",
                                       self.incoming, "runtime-protocol",
                                       "cell-sha-8", blood.VERIFIED)
        self.assertIn("не лечатся повтором", str(cm.exception))
        self.assertEqual(len(puts), 1, "FORGED не ретраится")
        self.assertEqual(self.sleeps, [])

    def test_g_structure_race_retried_then_honest_exit(self):
        puts, reads = [], []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            return 409, {"message": "does not match the current file_sha"}

        def fake_read(token):
            reads.append(1)
            return ['{"это": "не конверт"}'], "f/x", "ledger"

        with mock.patch.object(blood_cell, "api", side_effect=fake_api), \
             mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            with self.assertRaises(SystemExit) as cm:
                blood_cell.append_echo("t", self.ledger[:], "f/sha",
                                       self.incoming, "runtime-protocol",
                                       "cell-sha-8", blood.VERIFIED,
                                       echo_retries=3, echo_pause=1)
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(len(puts), 3, "STRUCTURE — переходящая: окно честно горит")
        self.assertEqual(self.sleeps, [1, 1])

    def test_h_422_sha_mismatch_is_transient(self):
        puts = []

        def fake_api(method, url, token, payload=None):
            puts.append(payload)
            if len(puts) == 1:
                return 422, {"message": "sha wasn't supplied or does not match"}
            return 200, {}

        def fake_read(token):
            return self.ledger[:], "f/sha-new", "ledger"

        with mock.patch.object(blood_cell, "api", side_effect=fake_api), \
             mock.patch.object(blood_cell, "read_ledger", side_effect=fake_read), \
             mock.patch.object(blood_cell, "_sleep", self.pause):
            echo = blood_cell.append_echo("t", self.ledger[:], "f/stale",
                                          self.incoming, "runtime-protocol",
                                          "cell-sha-8", blood.VERIFIED)
        self.assertEqual(len(puts), 2, "422 с sha-миссматчем лечится как 409")
        self.assertEqual(puts[1]["sha"], "f/sha-new")

    def test_i_flags_guard_before_network(self):
        env = dict(os.environ)
        env.update({
            "BLOOD_ENVELOPE": json.dumps(self.incoming),
            "CELL_NAME": "runtime-protocol",
            "CELL_SHA": "cell-sha-8",
            "GH_TOKEN": "t",
        })
        for flags, why in (
            (["--echo-retries", "мусор"], "не целое"),
            (["--echo-retries", "0"], "ниже минимума"),
            (["--echo-pause", "-1"], "ниже минимума"),
        ):
            with self.subTest(flags=flags):
                p = subprocess.run(
                    [sys.executable, str(ROOT / "blood_cell.py")] + flags,
                    capture_output=True, text=True, env=env, timeout=60)
                self.assertEqual(p.returncode, 1, why)
                self.assertIn("ФАКТ", p.stdout + p.stderr)

    def test_i_defaults_live(self):
        import inspect
        sig = inspect.signature(blood_cell.append_echo)
        self.assertEqual(sig.parameters["echo_retries"].default, 3)
        self.assertEqual(sig.parameters["echo_pause"].default, 10)


if __name__ == "__main__":
    unittest.main()
