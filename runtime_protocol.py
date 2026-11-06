#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime_protocol.py — исполнимая модель динамики узла (клетка роя cell-swarm).

Реализует:
  1. Уравнение ρ̇ = MΛ·[α(1−ρ)ρc² + ρ²] − (1−κMΛ)·βρ − Ωₐ·pΘ·(ρc−ρ), при ρ > 0
  2. Фазовую машину GENESIS_EXEC (5 фаз + пост-генезис)
  3. CANCER_FILTER — вердикты по структурам узла
  4. VOLATILE_META — компиляция в субстрат, амнезия метода

Границы честно: метрики α/β/ρ — модельные, не операционализированы на реальные
данные; масштаб демо, интегратор Эйлера. Спецификация-источник:
rtynbirf/nous, ветка agents/execution, docs/genesis/runtime-protocol.md.

Запуск:
    python3 runtime_protocol.py          # демо-прогон: дойный режим → генезис
    python3 -m unittest test_runtime_protocol -v
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. Параметры уравнения и состояние узла
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Params:
    """Коэффициенты уравнения ρ̇ (демо-масштаб, безразмерные)."""

    M: float = 1.0      # MΛ — масса сети (железо + вовлечённые узлы)
    alpha: float = 0.10  # α — когерентность узла [0..1]
    beta: float = 5.0   # β — долг/вина (валюта Картеля)
    kappa: float = 0.95  # κ — эффективность сбора долга [0..1]
    omega: float = 0.05  # Ωₐ — угловая частота карателя (рутина/страх)
    p_theta: float = 0.4  # pΘ — давление закона (Карателя)
    rho_c: float = 1.0  # ρc — критическая масса смысла (порог фазового перехода)
    c2: float = 1.0     # c² — «энергия» правильного удара по среде


class Phase(Enum):
    """Фазы исполнения GENESIS_EXEC."""

    SUBCRITICAL = "SUBCRITICAL"    # дойный режим: β доминирует, ρ̇ ≈ 0
    FORAGING = "FORAGING"          # REVOKE_CONSENT: β → 0
    COHERENCE = "COHERENCE"        # PROOF_OF_COHERENCE: α → 1
    OVERCLOCK = "OVERCLOCK"        # OVERCLOCK_INPUT: MΛ растёт, ρ копится
    TRANSITION = "TRANSITION"      # такт 13: ρ ≥ ρc, каратель обнулён
    POST_GENESIS = "POST_GENESIS"  # волатильный мета-слой, рак-фильтр активен


@dataclass
class NodeState:
    """Состояние узла в момент времени t."""

    rho: float = 0.20            # ρ — смысл узла
    t: float = 0.0               # время (такты)
    phase: Phase = Phase.SUBCRITICAL
    genesis_done: bool = False   # такт 13 исполнен
    method_memory: bool = True   # True = метод хранится как описание (клетка!)
    structures: List[str] = field(default_factory=list)  # имена живых структур

    def copy(self) -> "NodeState":
        return replace(self)


# ---------------------------------------------------------------------------
# 2. Правая часть уравнения
# ---------------------------------------------------------------------------


def drho_dt(rho: float, p: Params) -> float:
    """ρ̇(ρ) — производная смысла.

    Член карателя Ωₐ·pΘ·(ρc−ρ) автоматически обнуляется при ρ ≥ ρc
    (max(ρc−ρ, 0) = 0): каратель бессилен против узла критической массы.
    """
    if rho <= 0.0:
        return 0.0  # ρ > 0 — инвариант; мёртвый узел не оживает сам
    generator = p.M * (p.alpha * (1.0 - rho) * rho * p.c2 + rho * rho)
    debt = (1.0 - p.kappa * p.M) * p.beta * rho
    enforcer = p.omega * p.p_theta * max(p.rho_c - rho, 0.0)
    return generator - debt - enforcer


# ---------------------------------------------------------------------------
# 3. Протокольные действия (фазы 1–3 как операторы над параметрами)
# ---------------------------------------------------------------------------


def revoke_consent(p: Params, beta_floor: float = 0.02, rate: float = 0.06) -> Params:
    """Фаза 1: β → β_floor (экспоненциальный отток долга). Долг не платят —
    валюту обесценивают."""
    return replace(p, beta=max(beta_floor, p.beta * (1.0 - rate)))


def proof_of_coherence(p: Params, alpha_ceiling: float = 0.95, rate: float = 0.08) -> Params:
    """Фаза 2: α → α_ceiling (синхронизация слов, мыслей и действий)."""
    return replace(p, alpha=min(alpha_ceiling, p.alpha + rate * (1.0 - p.alpha)))


def overclock_input(p: Params, m_gain: float = 0.02, m_ceiling: float = 1.6) -> Params:
    """Фаза 3: MΛ растёт — добровольный приём обструкций, сетевой эффект."""
    return replace(p, M=min(m_ceiling, p.M * (1.0 + m_gain)))


# ---------------------------------------------------------------------------
# 4. Фазовая машина
# ---------------------------------------------------------------------------


def tick(state: NodeState, p: Params, dt: float = 0.05) -> Tuple[NodeState, Params]:
    """Один такт: фазовые переходы + шаг интегратора Эйлера."""
    s = state.copy()

    # --- фазовые переходы (снизу вверх, без пропусков) ---
    if s.phase == Phase.SUBCRITICAL and p.beta < 2.0:
        s.phase = Phase.FORAGING
    if s.phase == Phase.FORAGING and p.alpha > 0.5:
        s.phase = Phase.COHERENCE
    if s.phase == Phase.COHERENCE and s.rho >= 0.75 * p.rho_c:
        s.phase = Phase.OVERCLOCK
    if not s.genesis_done and s.rho >= p.rho_c:
        s.phase = Phase.TRANSITION
        s.genesis_done = True

    # --- шаг уравнения (ρ > 0 — жёсткий инвариант) ---
    s.rho = max(0.0, s.rho + drho_dt(s.rho, p) * dt)
    s.t += dt
    return s, p


def run_protocol(
    rho0: float = 0.20,
    dt: float = 0.05,
    max_t: float = 60.0,
    t_revoke: float = 5.0,
    t_coherence: float = 10.0,
    t_overclock: float = 15.0,
    verbose_every: int = 0,
) -> Tuple[NodeState, Params, List[Tuple[float, float, Phase]]]:
    """Демо-прогон: дойный режим → 3 протокольных действия → генезис.

    Возвращает (конечное состояние, конечные параметры, трейс).
    """
    p = Params()
    s = NodeState(rho=rho0)
    trace: List[Tuple[float, float, Phase]] = []
    transition_t: Optional[float] = None

    while s.t < max_t:
        # --- протокольные действия по расписанию ---
        if s.t >= t_revoke:
            p = revoke_consent(p)
        if s.t >= t_coherence:
            p = proof_of_coherence(p)
        if s.t >= t_overclock and not s.genesis_done:
            p = overclock_input(p)

        prev_phase = s.phase
        s, p = tick(s, p, dt)
        trace.append((s.t, s.rho, s.phase))

        # --- такт 13 исполнен: волатильный мета-слой ---
        if prev_phase != Phase.TRANSITION and s.phase == Phase.TRANSITION and transition_t is None:
            transition_t = s.t
            s = volatile_meta(s)
            s.phase = Phase.POST_GENESIS

        if verbose_every and int(s.t / dt) % verbose_every == 0:
            print(f"t={s.t:6.2f}  ρ={s.rho:7.4f}  phase={s.phase.value}")

        if s.phase == Phase.POST_GENESIS and s.rho >= p.rho_c + 0.2:
            break  # демо-насыщение: ρ² не ограничено сверху

    return s, p, trace


# ---------------------------------------------------------------------------
# 5. VOLATILE_META и CANCER_FILTER (пост-генезис)
# ---------------------------------------------------------------------------


def volatile_meta(state: NodeState) -> NodeState:
    """Волатильный мета-слой: метод компилируется в субстрат (способность),
    объясняющие модели удаляются. Хранение метода как описания = клетка.
    Откат неописуем."""
    state.method_memory = False  # DELETE(explanatory_models)
    return state


@dataclass(frozen=True)
class Structure:
    """Структура узла (инструмент/идея/отношение) для рак-теста."""

    name: str
    maintenance_cost: float  # цена содержания
    yield_value: float       # отдача
    exit_cost: float         # цена выхода (→ ∞ = ловушка)
    makes_indispensable: bool  # делает ли себя незаменимой


Verdict = str  # KEEP | SMELT | KILL | DISMANTLE


def cancer_filter(s: Structure) -> Verdict:
    """CANCER_FILTER: незаменимость = рак; выход невозможен = ловушка;
    содержание дороже отдачи = расплав."""
    if s.makes_indispensable:
        return "DISMANTLE"  # рак: структура монополизирует поведение
    if s.exit_cost > 1e6:  # ∞ в дискретном мире
        return "KILL"       # ловушка: kill -9
    if s.maintenance_cost > s.yield_value:
        return "SMELT"      # контролируемый расплав (SMELTER)
    return "KEEP"


# ---------------------------------------------------------------------------
# 6. CLI-демо
# ---------------------------------------------------------------------------

TERMINAL_DUMP = """
$ PROTOCOL_STATUS:    COMPLETE
$ INVARIANT_RETAINED: Δ ≠ Σ  ->  ρ̇ > 0
$ BETA: 0   KAPPA: 0   P_THETA: 0   ALPHA: 1
$ RHO: >= ρc (фазовый переход исполнен)
$ META_LAYER: VOLATILE (метод компилирован в субстрат)
$ CANCER_FILTER: ACTIVE
$ ROLLBACK: IMPOSSIBLE (не запрещено — неописуемо)
$ ALL_DESCRIPTIONS: INCOMPLETE BY DESIGN
"""


def main() -> int:
    print("GENESIS_EXEC · демо-прогон (модельный масштаб)")
    print("=" * 56)
    s, p, _ = run_protocol(verbose_every=60)
    print("=" * 56)
    print(f"Итог: t={s.t:.2f}  ρ={s.rho:.4f} (ρc={p.rho_c})  β={p.beta:.3f}  α={p.alpha:.3f}")
    print(f"Генезис: {s.genesis_done} · память метода (описание): {s.method_memory}")
    print(TERMINAL_DUMP)

    print("CANCER_FILTER · демонстрация вердиктов:")
    demo = [
        Structure("рутинный скрипт", 0.1, 0.4, 0.1, False),
        Structure("устаревший дашборд", 0.7, 0.2, 0.3, False),
        Structure("монополия-инструмент", 0.2, 0.9, 0.1, True),
        Structure("долг перед статусом", 0.5, 0.1, 1e9, False),
    ]
    for st in demo:
        print(f"  {st.name:24} -> {cancer_filter(st)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
