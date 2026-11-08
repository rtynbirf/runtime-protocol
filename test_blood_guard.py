#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Приёмки гарды конверта (issue #2.1): dispatch без конверта отвечает
честным ФАКТ-exit, а не AttributeError.

GitHub Actions передаёт конверт так: toJson(github.event.client_payload.envelope).
Когда конверта в dispatch нет, это строка «null», а не пустая строка —
проверка на пустоту её пропускала, json.loads давал None, и клетка
взрывалась AttributeError на первом .get(). Честное поведение: ФАКТ-exit
с ясной причиной, код 1, без traceback.

Офлайн: гарда стоит до первого сетевого шага, поэтому приёмка — subprocess
без токена и без сети (закон 4 «Храповик»: файл добавлен, старые приёмки
клетки не тронуты).

Запуск: python3 -m unittest test_blood_guard -v
"""
import os
import subprocess
import sys
import unittest

CELL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blood_cell.py")


def _run(envelope: str):
    env = dict(os.environ)
    env.update({
        "BLOOD_ENVELOPE": envelope,
        "CELL_NAME": "runtime-protocol",
        "CELL_SHA": "a" * 40,
    })
    env.pop("GH_TOKEN", None)  # гарда обязана сработать до любого сетевого шага
    return subprocess.run([sys.executable, CELL, "--dry-run"],
                          capture_output=True, text=True, env=env, timeout=60)


class TestEnvelopeGuard(unittest.TestCase):
    def _honest_refusal(self, envelope, note):
        r = _run(envelope)
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1,
                         f"{note}: код {r.returncode} вместо 1, вывод: {out[:300]}")
        self.assertIn("ФАКТ", out, note)
        self.assertNotIn("Traceback", out, f"{note}: отказ без traceback")
        self.assertNotIn("AttributeError", out, note)

    def test_dispatch_without_envelope_null(self):
        """Точечный случай issue #2: toJson(null) — строка «null», не AttributeError."""
        r = _run("null")
        self._honest_refusal("null", "dispatch без конверта")
        self.assertIn("dispatch без конверта", r.stderr)

    def test_empty_envelope(self):
        """Пустой BLOOD_ENVELOPE — прежняя честная отказная ветка (теперь под приёмкой)."""
        self._honest_refusal("", "пустой конверт")

    def test_malformed_json(self):
        """Обрезанный JSON — отказ с позицией раны, не traceback."""
        self._honest_refusal('{"src": ', "обрезанный JSON")

    def test_non_object_json(self):
        """JSON не-объект (массив/строка/число) — не конверт, кровь не течёт."""
        self._honest_refusal("[1,2]", "JSON-массив вместо объекта")
        self._honest_refusal('"строка"', "JSON-строка вместо объекта")


if __name__ == "__main__":
    unittest.main()
