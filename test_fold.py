#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Приёмки обратно-нити как свёртки журнала (Волна ×4 · П50, issue #3).

Закон 4 «Храповик»: файл добавлен, существующие приёмки клетки не изменены.
Запуск: python3 -m unittest test_fold -v  (входит в make verify)
"""
import copy
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fold import (  # noqa: E402
    CANON_VERSION,
    INIT,
    KINDS,
    annotate,
    canonical,
    control_run,
    fold,
    fold_step,
    fold_with,
    inverse_step,
    replay,
    sample_log,
    selftest,
)


def _random_log(rnd, n):
    events, t = [], 0
    for _ in range(n):
        t += 1
        k = rnd.choice(KINDS)
        e = {"t": t, "kind": k}
        if k == "strike":
            e["src"] = rnd.choice(["carTel", "rival", "ghost"])
            e["dmg"] = rnd.randrange(0, 6)
        elif k == "heal":
            e["amount"] = rnd.randrange(0, 6)
        else:
            e["src"] = rnd.choice(["carTel", "rival", "ghost"])
        events.append(e)
    return events


class TestCanon(unittest.TestCase):
    def test_double_fold_identical_bytes(self):
        """Двойной прогон свёртки идентичен (приёмка issue, ч.2)."""
        log = sample_log()
        self.assertEqual(canonical(fold(log)), canonical(fold(log)))

    def test_replay_equals_state(self):
        """Replay журнала == состояние клетки (приёмка issue, ч.1)."""
        log = sample_log()
        text = "\n".join(json.dumps(e, sort_keys=True, ensure_ascii=False) for e in log)
        self.assertEqual(canonical(replay(text)), canonical(fold(log)))

    def test_fold_with_is_generic_canon(self):
        """Общий канон fold_with: сумма редьюсером — канон для пропагатора."""
        total = fold_with(lambda st, e: st + e["v"], [{"v": 2}, {"v": 3}], 0)
        self.assertEqual(total, 5)
        self.assertEqual(CANON_VERSION, 1)


class TestPurity(unittest.TestCase):
    def test_fold_does_not_mutate_log(self):
        rnd = random.Random(23)
        log = _random_log(rnd, 25)
        snap = copy.deepcopy(log)
        fold(log)
        self.assertEqual(log, snap)

    def test_reducer_receives_copies(self):
        """Редьюсер не может испортить журнал: канон чистоты механикой."""

        def evil(state, event):
            event["t"] = -1  # попытка мутации
            state["hp"] = -100
            return {"hp": 1, "max_hp": 1, "debt": {}, "events": 1, "last_t": 1}

        log = [{"t": 1, "kind": "strike", "src": "x", "dmg": 1}]
        snap = copy.deepcopy(log)
        fold_with(evil, log, INIT)
        self.assertEqual(log, snap)


class TestReversibility(unittest.TestCase):
    def test_inverse_roundtrip_every_prefix(self):
        """Инвариант обратимости: inverse(fold_step(s,e),e)==s на каждом шаге."""
        rnd = random.Random(42)
        for _ in range(100):
            log = _random_log(rnd, rnd.randrange(0, 30))
            ann = annotate(log)
            st = copy.deepcopy(INIT)
            for e, e_ann in zip(log, ann):
                nxt = fold_step(st, e)
                self.assertEqual(canonical(inverse_step(nxt, e_ann)),
                                 canonical(st),
                                 f"шаг t={e.get('t')} не обратим")
                st = nxt

    def test_inverse_requires_annotation(self):
        """Без аннотации обратность отказывается (не молчит неточно)."""
        with self.assertRaises(ValueError):
            inverse_step(fold([{"t": 1, "kind": "strike", "src": "x", "dmg": 1}]),
                         {"t": 1, "kind": "strike", "src": "x", "dmg": 1})


class TestDeterminism(unittest.TestCase):
    def test_property_200_logs(self):
        """200 случайных журналов: двойная свёртка + replay сходятся."""
        rnd = random.Random(23)
        for _ in range(200):
            log = _random_log(rnd, rnd.randrange(0, 40))
            s1 = canonical(fold(log))
            self.assertEqual(s1, canonical(fold(log)))
            self.assertEqual(s1, canonical(replay(log)))

    def test_tamper_changes_state(self):
        log = sample_log()
        tampered = copy.deepcopy(log)
        tampered[0]["dmg"] += 1
        self.assertNotEqual(canonical(fold(log)), canonical(fold(tampered)))

    def test_monotonic_t_enforced(self):
        with self.assertRaises(ValueError):
            fold([{"t": 5, "kind": "heal", "amount": 1},
                  {"t": 5, "kind": "heal", "amount": 1}])

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            fold([{"t": 1, "kind": "loot", "amount": 1}])


class TestSelftest(unittest.TestCase):
    def test_selftest_green(self):
        self.assertTrue(selftest())

    def test_control_run(self):
        r = control_run()
        self.assertTrue(r["double_fold_identical"])
        self.assertTrue(r["replay_equals_state"])
        self.assertEqual(r["events"], len(sample_log()))


if __name__ == "__main__":
    unittest.main()
