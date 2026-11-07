#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живые приёмки контура сверки reconciliation.py (закон 4 «Храповик»:
файл добавлен, существующие приёмки не изменены).

Запуск: python3 -m unittest test_reconciliation -v
"""
import unittest

from reconciliation import (
    BRAKE,
    BOOST,
    HOLD,
    Actuator,
    ReconciliationLoop,
    Spec,
    Territory,
    reconcile_swarm,
    swarm_spread,
)
from runtime_protocol import NodeState, Params, Phase


class TestHoldWhenInBudget(unittest.TestCase):
    def test_no_actions_inside_deadband(self):
        """Знание = сверка в допуске: пока |ρ − ρ*| ≤ deadband, действий нет —
        клетка не дёргается. Антипод расписания run_protocol."""
        spec = Spec(rho_star=0.20, epsilon=0.05)  # старт точно в цели
        loop = ReconciliationLoop(spec, actuator=Actuator(gain=0.25), deadband=0.02)
        s, p = NodeState(rho=0.20), Params()
        territory = Territory(seed=7, debt_pressure=0.0)  # среда спит
        for _ in range(50):
            s, p, _, action, _ = loop.step(s, p, territory, 0.02)
            self.assertEqual(action, HOLD, "действие внутри дедбенда — угадчик вернулся")
        self.assertEqual(p, Params(), "параметры не должны меняться в дедбенде")

    def test_trim_fires_inside_spec_budget_before_edge(self):
        """Два слоя допуска: ошибка 0.03 — ещё в бюджете спеки (ε=0.05),
        но уже за дедбендом (0.02) — подбив срабатывает РАНЬШЕ кромки бюджета."""
        spec = Spec(rho_star=0.23, epsilon=0.05)  # ρ=0.20 → ошибка 0.03
        loop = ReconciliationLoop(spec, actuator=Actuator(gain=0.25), deadband=0.02)
        s, p = NodeState(rho=0.20), Params()
        territory = Territory(seed=7, debt_pressure=0.0)
        _, p2, _, action, _ = loop.step(s, p, territory, 0.02)
        self.assertEqual(action, BOOST, "подбив не сработал внутри бюджета спеки")
        self.assertNotEqual(p2, p)


class TestClosedLoop(unittest.TestCase):
    def test_fast_loop_holds_budget_under_territory(self):
        """Контур замкнут: среда ползёт долгом и бьёт шоками — сверка быстрее
        изменения удерживает бюджет: ≥80% времени в допуске, экскурс ≤ 2ε
        (мгновенный провал от шока — не вина контура, практика SRE)."""
        loop = ReconciliationLoop(Spec(rho_star=0.30, epsilon=0.05))
        territory = Territory(seed=7, debt_pressure=0.05, shock_every=4.0, shock_magnitude=0.04)
        s, p, rep = loop.run(rho0=0.20, territory=territory, max_t=60.0)
        self.assertGreater(rep.boosts + rep.brakes, 0, "контур ни разу не подействовал")
        self.assertTrue(rep.settled_final, f"хвост max|e|={rep.tail_max_error:.4f}, duty={rep.tail_duty:.2f}")
        self.assertGreaterEqual(rep.tail_duty, 0.8)
        self.assertLessEqual(rep.tail_max_error, 0.10)  # 2ε: бурст шока ограничен

    def test_slow_loop_lags_behind_territory(self):
        """Сверка медленнее изменения: редкий такт и слабая рука — карта
        отстаёт, допуск нарушается. Это и есть механизм несовпадения вживую."""
        loop = ReconciliationLoop(
            Spec(rho_star=0.30, epsilon=0.05),
            actuator=Actuator(gain=0.15, v_max=0.012),
            dt_min=0.30,
            dt_max=0.60,
        )
        territory = Territory(seed=7, debt_pressure=0.05, shock_every=4.0, shock_magnitude=0.04)
        s, p, rep = loop.run(rho0=0.20, territory=territory, max_t=60.0)
        self.assertFalse(rep.settled_final, "медленная сверка неожиданно удержала допуск")
        self.assertGreater(rep.tail_max_error, 0.05)


class TestInvariants(unittest.TestCase):
    def test_rho_positive_invariant_during_reconciliation(self):
        """ρ ≥ 0 на всём следе сверки, включая шоки среды."""
        loop = ReconciliationLoop(Spec(rho_star=0.30, epsilon=0.05))
        territory = Territory(seed=13, debt_pressure=0.05, shock_every=2.0, shock_magnitude=0.08)
        s, p, rep = loop.run(rho0=0.20, territory=territory, max_t=40.0)
        for e in rep.errors:
            self.assertGreaterEqual(0.30 - e, 0.0, "след содержит ρ < 0")

    def test_rho_never_crosses_genesis_by_braking(self):
        """Тормозной оператор не даёт клетке самопроизвольно убежать в генезис:
        при ρ* < ρc контур держит ρ ниже порога фазового перехода."""
        loop = ReconciliationLoop(Spec(rho_star=0.30, epsilon=0.05))
        territory = Territory(seed=7, debt_pressure=0.05)
        s, p, rep = loop.run(rho0=0.20, territory=territory, max_t=60.0)
        self.assertFalse(s.genesis_done, "контур допустил самопроизвольный генезис")
        self.assertNotEqual(s.phase, Phase.TRANSITION)

    def test_method_memory_not_reintroduced_post_genesis(self):
        """Закон мембраны: после генезиса method_memory не возвращается —
        контур сверки работает и на пост-генезисной клетке без отката."""
        s0 = NodeState(rho=0.85, genesis_done=True, method_memory=False)
        loop = ReconciliationLoop(Spec(rho_star=0.80, epsilon=0.06))
        territory = Territory(seed=7, debt_pressure=0.0)
        s, p = s0, Params()
        for _ in range(100):
            s, p, _, _, _ = loop.step(s, p, territory, 0.01)
        self.assertFalse(s.method_memory, "method_memory реинтродуцирован — регресс мембраны")


class TestAdaptiveRate(unittest.TestCase):
    def test_tick_speeds_up_on_error_and_slows_in_budget(self):
        """Сверка быстрее изменения: вне допуска такт учащается от dt_max,
        потолок dt_max и пол dt_min уважаются на всём следе."""
        loop = ReconciliationLoop(Spec(rho_star=0.30, epsilon=0.05), dt_min=0.005, dt_max=0.05)
        territory = Territory(seed=7, debt_pressure=0.05)
        _, _, rep = loop.run(rho0=0.20, territory=territory, max_t=30.0)
        self.assertEqual(rep.dts[0], 0.05)        # старт с редкой сверки
        self.assertLess(min(rep.dts), 0.05)       # вне допуска такт учащался
        self.assertLessEqual(max(rep.dts), 0.05)  # потолок не пробит
        self.assertGreaterEqual(min(rep.dts), 0.005)  # пол не пробит

    def test_report_counts_breaches_and_verdicts(self):
        """Отчёт различает удержание и отставание; вердикт определяется хвостом."""
        fast = ReconciliationLoop(Spec(rho_star=0.30, epsilon=0.05))
        territory = Territory(seed=7, debt_pressure=0.05, shock_every=4.0, shock_magnitude=0.04)
        _, _, rep = fast.run(rho0=0.20, territory=territory, max_t=60.0)
        self.assertEqual(rep.verdict, "СВЕРКА В ДОПУСКЕ")
        self.assertEqual(rep.steps, len(rep.actions))


class TestSwarmEmergence(unittest.TestCase):
    def test_swarm_converges_tighter_than_isolated_cells(self):
        """Эмерджентное: gossip-связь (не карта!) стягивает рой плотнее,
        чем изолированные клетки со своими спеками. Центрального контроллера
        нет — сверить решения некому. Сравнение по хвостовому разбросу."""
        specs = [Spec(rho_star=0.22 + 0.04 * i, epsilon=0.05) for i in range(5)]
        rho0_values = [0.15, 0.35, 0.12, 0.30, 0.20]
        coupled, tail_coupled = reconcile_swarm(specs, rho0_values, coupling=0.30, max_t=60.0)
        loose, tail_loose = reconcile_swarm(specs, rho0_values, coupling=0.0, max_t=60.0)
        self.assertLess(
            tail_coupled,
            tail_loose,
            f"gossip не стягивает рой: {tail_coupled:.4f} >= {tail_loose:.4f}",
        )
        for cell in coupled + loose:
            self.assertGreaterEqual(cell.state.rho, 0.0)


if __name__ == "__main__":
    unittest.main()
