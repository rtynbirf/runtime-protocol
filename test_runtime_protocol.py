#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живые приёмки клетки runtime-protocol (закон 4 «Храповик»: тесты только добавляются).

Запуск: python3 -m unittest test_runtime_protocol -v
"""
import unittest

from runtime_protocol import (
    NodeState,
    Params,
    Phase,
    Structure,
    cancer_filter,
    drho_dt,
    run_protocol,
    tick,
    volatile_meta,
)


class TestEquation(unittest.TestCase):
    def test_enforcer_zero_at_or_above_rho_c(self):
        """Каратель обнуляется при ρ ≥ ρc — главный хак уравнения."""
        from dataclasses import replace
        p = Params()
        at = drho_dt(p.rho_c, p)
        above = drho_dt(p.rho_c + 0.3, p)
        no_enforcer = replace(p, omega=0.0)
        # при ρ ≥ ρc каратель ровно 0: с ним и без него производная совпадает
        self.assertEqual(at, drho_dt(p.rho_c, no_enforcer))
        self.assertEqual(above, drho_dt(p.rho_c + 0.3, no_enforcer))
        # ниже порога каратель давит: без него ρ̇ строго больше
        self.assertGreater(drho_dt(0.1, no_enforcer), drho_dt(0.1, p))
        self.assertGreater(drho_dt(0.5, no_enforcer), drho_dt(0.5, p))

    def test_dairy_regime_keeps_rho_low_without_actions(self):
        """Дойный режим: без протокольных действий система держит узел низко
        (равновесие ρ̇=0 существенно ниже ρc)."""
        s, p = NodeState(rho=0.20), Params()
        for _ in range(400):
            s, p = tick(s, p)
            self.assertLess(s.rho, 0.5, msg=f"ρ вышел за дойный коридор: {s.rho}")
        self.assertFalse(s.genesis_done)
        self.assertEqual(s.phase, Phase.SUBCRITICAL)

    def test_rho_positive_invariant(self):
        """ρ > 0: мёртвый узел не оживает сам, отрицательный ρ невозможен."""
        p = Params()
        self.assertEqual(drho_dt(0.0, p), 0.0)
        s = NodeState(rho=0.01)
        for _ in range(200):
            s, _ = tick(s, p)
            self.assertGreaterEqual(s.rho, 0.0)


class TestProtocol(unittest.TestCase):
    def test_full_protocol_reaches_genesis(self):
        """Полный прогон: дойный режим → 3 действия → такт 13 → пост-генезис."""
        s, p, trace = run_protocol(verbose_every=0)
        self.assertTrue(s.genesis_done, "фазовый переход не достигнут")
        self.assertEqual(s.phase, Phase.POST_GENESIS)
        self.assertGreaterEqual(s.rho, p.rho_c)
        phases_seen = {ph for _, _, ph in trace}
        self.assertIn(Phase.SUBCRITICAL, phases_seen)
        self.assertIn(Phase.POST_GENESIS, phases_seen)

    def test_volative_meta_forgets_method(self):
        """VOLATILE_META: после генезиса метод не хранится как описание."""
        s = NodeState(rho=1.2, genesis_done=True, method_memory=True)
        s = volatile_meta(s)
        self.assertFalse(s.method_memory)

    def test_trace_is_monotonic_in_time(self):
        _, _, trace = run_protocol(verbose_every=0)
        ts = [t for t, _, _ in trace]
        self.assertEqual(ts, sorted(ts))


class TestCancerFilter(unittest.TestCase):
    def test_verdicts(self):
        cases = [
            (Structure("здоровая", 0.1, 0.4, 0.1, False), "KEEP"),
            (Structure("устаревшая", 0.7, 0.2, 0.3, False), "SMELT"),
            (Structure("рак", 0.2, 0.9, 0.1, True), "DISMANTLE"),
            (Structure("ловушка", 0.5, 0.1, 1e9, False), "KILL"),
        ]
        for st, expected in cases:
            self.assertEqual(cancer_filter(st), expected, msg=st.name)


if __name__ == "__main__":
    unittest.main()
