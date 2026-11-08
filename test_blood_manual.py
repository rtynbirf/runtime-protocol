#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Приёмки честного нуля ручного прогона крови (TH-KNOW-1).

Рана: workflow_dispatch не может нести client_payload, гарда конверта честно
отказывала («кровь не течёт вслепую») — и каждый ручной прогон старел в
красный, а красный > 24 ч по закону дома = инцидент. Честный ноль: ручной
прогон сверяет целостность летописи живьём (обязанность клетки), не пишет
эхо и не передаёт кровь дальше.

Приёмка офлайн: subprocess без токена обязан отказать честно (сеть недоступна
— нечего сверять); в имени процесса blood_cell.py не должно быть traceback.
Зелёная ветка с токеном проверяется в CI живым dispatch (закон живого прогона),
здесь она покрывается юнит-приёмкой с подменой read_ledger.

Офлайн: закон 4 «Храповик» — файл добавлен, старые приёмки клетки не тронуты.

Запуск: python3 -m unittest test_blood_manual -v
"""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

CELL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blood_cell.py")


def _subprocess_run(extra_args):
    env = dict(os.environ)
    env.update({"CELL_NAME": "runtime-protocol", "CELL_SHA": "a" * 40})
    env.pop("GH_TOKEN", None)   # офлайн: честный отказ, не traceback
    env.pop("BLOOD_ENVELOPE", None)
    return subprocess.run([sys.executable, CELL] + extra_args,
                          capture_output=True, text=True, env=env, timeout=60)


class TestManualCheck(unittest.TestCase):
    def test_manual_check_without_token_honest_refusal(self):
        """Без токена ручная сверка не может прочитать летопись — честный отказ."""
        r = _subprocess_run(["--manual-check"])
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1,
                         f"код {r.returncode} вместо 1, вывод: {out[:300]}")
        self.assertIn("ФАКТ", out)
        self.assertIn("кровь не течёт вслепую", out)
        self.assertNotIn("Traceback", out)

    def test_manual_check_takes_precedence_over_envelope_guard(self):
        """--manual-check игнорирует конверт: гарда не срабатывает, отказ только
        по отсутствию токена (не «конверт не объект»)."""
        r = _subprocess_run(["--manual-check"])
        out = r.stdout + r.stderr
        self.assertNotIn("конверт не объект", out)
        self.assertIn("ручная сверка", out)

    def test_manual_check_green_with_intact_ledger(self):
        """С токеном и целой летописью — честный ноль: код 0, цепь целая,
        эхо не писано (сетевые шаги подменены — офлайн)."""
        from blood import make_envelope
        import json as _json
        env1 = make_envelope(src="cell-swarm", dst="runtime-protocol", seq=1,
                             payload={"observed": "x"}, prev_hash="0" * 64)
        lines = [_json.dumps(env1, sort_keys=True,
                  separators=(",", ":"), ensure_ascii=False)]
        import io, contextlib
        import blood_cell
        buf = io.StringIO()
        with patch.dict(os.environ, {"CELL_NAME": "runtime-protocol",
                                     "CELL_SHA": "b" * 40,
                                     "GH_TOKEN": "offline-fake"}), \
             patch("blood_cell.read_ledger",
                   return_value=(lines, "filesha", "rtynbirf/cell-swarm")):
            with contextlib.redirect_stdout(buf):
                code = blood_cell.manual_check()
        out = buf.getvalue()
        self.assertEqual(code, 0, f"код {code}, вывод: {out[:300]}")
        self.assertIn("цепь целая", out)
        self.assertIn("эхо не писано", out)

    def test_manual_check_broken_ledger_is_drift_not_lie(self):
        """Сломанная летопись при ручной сверке — код 2 (факт дрейфа), не ложь."""
        import io, contextlib
        import blood_cell
        buf = io.StringIO()
        with patch.dict(os.environ, {"CELL_NAME": "runtime-protocol",
                                     "CELL_SHA": "b" * 40,
                                     "GH_TOKEN": "offline-fake"}), \
             patch("blood_cell.read_ledger",
                   return_value=(["мусор"], "filesha", "rtynbirf/cell-swarm")):
            with contextlib.redirect_stdout(buf):
                code = blood_cell.manual_check()
        self.assertEqual(code, 2)
        self.assertIn("СЛОМАНА", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
