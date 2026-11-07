#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reconciliation.py — контур сверки «живая карта» для клетки runtime-protocol.

Замыкает то, чего в клетке не было: протокольные действия в run_protocol
срабатывают по расписанию (t=5/10/15) — открытый контур, угадчик по построению.
Здесь действия срабатывают по факту сверки ЖЕЛАЕМОГО с НАБЛЮДАЕМЫМ:

    ЖЕЛАЕМОЕ (Spec) ← сравнение → НАБЛЮДАЕМОЕ (замер ρ) → ДЕЙСТВИЕ (актюаторы)

Механизм (сведено с мировыми практиками desired-state reconciliation,
self-healing-инфраструктуры, цифровые двойники, local-first рой):

  1. Карта — глагол, не вещь: ошибка e(t) = ρ* − ρ(t) пересчитывается каждый
     такт заново и нигде не хранится как источник истины.
  2. Два слоя допуска: ε спеки (error budget — метрика вердикта, бурсты шоков
     допустимы при ≥80% удержания, практика SRE) и deadband (порог подбива).
     Равновесие модели при ρ* внутри (0, ρc) всегда неустойчиво-репеллерное,
     поэтому стационарного удержания не бывает — бывает баттинг в бюджете.
  3. Актюатор знаний: клетка знает своё уравнение и ВЫЧИСЛЯЕТ управление,
     а не ищет перебором. Масса M при каждом подбиве решается аналитически
     под заданную скорость подхода к цели при текущем ρ (ρ̇(ρ)=v_des):

         M = (βρ + ΩₐpΘ(1−ρ) + v_des) / (α(1−ρ)ρ + ρ² + κβρ)

     потолок M=1.0 ниже 1/κ ≈ 1.053 — переворот долга невозможен по
     построению; α — двусторонний трим, ограниченный по скорости; β — руль
     территории, не клетки. Сила α-трима масштабируется по такту: учащение
     сверки — не учащение вмешательства. Мёртвых контур не оживляет.
  4. Адаптивная частота сверки: ошибка вне бюджета или растёт — такт
     учащается (dt → dt_min); в бюджете — замедляется (dt → dt_max).
     «Сверка быстрее изменения» — режим, а не лозунг.
  5. Территория течёт: Territory возвращает долг (β ползёт вверх) и бьёт
     по ρ шоками независимо от воли клетки. Карта не совпадает никогда —
     она догоняет.
  6. Рой без центральной карты: каждая клетка запускает свой контур по своей
     спеке; связь — gossip (каждая клетка видит среднее соседей и сдвигает
     свою ЦЕЛЬ, не тело соседа). Сходимость — эмерджентная, сверить решения
     некому.

Границы честно: метрики ρ/α/β/M — модельные, безразмерные, демо-масштаб;
на реальные данные не операционализированы (как и в runtime_protocol.py).
ρ > 0 — инвариант сохранён. Инвариант пост-генезиса: method_memory не
реинтродуцируется.

Запуск:
    python3 reconciliation.py           # демо: два режима сверки + рой
    python3 -m unittest test_reconciliation -v
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

from runtime_protocol import (
    NodeState,
    Params,
    Phase,
    tick,
)


# ---------------------------------------------------------------------------
# 1. ЖЕЛАЕМОЕ — спека (допуск вместо совпадения)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """ЖЕЛАЕМОЕ состояние клетки. Совпадение не заказывается — заказывается
    допуск: |ρ − ρ*| ≤ ε. Это и есть знание в исполнении: не «карта верна»,
    а «ошибка в допуске»."""

    rho_star: float = 0.30   # целевой уровень смысла (ниже порога генезиса ρc)
    epsilon: float = 0.05    # ε — допуск (error budget)


# ---------------------------------------------------------------------------
# 2. НАБЛЮДАЕМОЕ — замер (сырой, без интерпретации) и территория
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """Сырой замер клетки в момент t. Интерпретации не содержит —
    сравнение со спекой делает контроллер, не датчик."""

    t: float
    rho: float
    phase: Phase
    drho: float  # измеренная скорость изменения (по двум последним замерам)


class Territory:
    """Территория течёт независимо от воли клетки.

    - долг ползёт вверх (давление Картеля возвращается само);
    - шоки среды сбрасывают ρ (среда отбирает смысл).

    Шансы и величины детерминированы seed'ом — приёмки воспроизводимы.
    """

    def __init__(
        self,
        seed: int = 7,
        debt_pressure: float = 0.05,   # β возвращается: единиц долга за такт времени
        beta_ceiling: float = 5.0,     # до исходного дойного уровня
        shock_every: float = 0.0,      # средний интервал между шоками (0 = нет шоков)
        shock_magnitude: float = 0.0,  # сколько ρ отбирает шок
    ) -> None:
        self.rng = random.Random(seed)
        self.debt_pressure = debt_pressure
        self.beta_ceiling = beta_ceiling
        self.shock_every = shock_every
        self.shock_magnitude = shock_magnitude
        self._next_shock: Optional[float] = (
            shock_every * self.rng.random() if shock_every > 0 else None
        )

    def disturb(self, t: float, dt: float, s: NodeState, p: Params) -> Tuple[NodeState, Params]:
        """Один такт среды: долг ползёт, шок падает когда придёт."""
        if p.beta < self.beta_ceiling:
            p = replace(p, beta=min(self.beta_ceiling, p.beta + self.debt_pressure * dt))
        if self.shock_every > 0 and self._next_shock is not None and t >= self._next_shock:
            s.rho = max(0.0, s.rho - self.shock_magnitude)  # ρ > 0 — инвариант
            self._next_shock = t + self.shock_every * (0.5 + self.rng.random())
        return s, p


# ---------------------------------------------------------------------------
# 3. ДЕЙСТВИЕ — актюатор: ошибка → протокольные операторы клетки
# ---------------------------------------------------------------------------

BOOST = "BOOST"    # ρ ниже допуска: β↓, α↑, M↑ (протокольные действия клетки)
BRAKE = "BRAKE"    # ρ выше допуска: α↓, M↓ (торможение — выше дойного
                   # равновесия динамика убегает в генезис без тормоза)
HOLD = "HOLD"      # |e| ≤ ε: действий нет — сверка в допуске и есть знание
DEAD = "DEAD"      # ρ = 0: клетка погибла (например, runaway при запоздалой
                   # сверке); действий нет — мёртвых не оживляют


class Actuator:
    """Актюатор знаний: клетка ЗНАЕТ своё уравнение и ВЫЧИСЛЯЕТ управление,
    а не ищет его перебором (карта, вычисленная из структуры, — принцип Rust).

    Модельно-предиктивный актюатор: при каждом подбиве M решается АНАЛИТИЧЕСКИ
    из уравнения клетки — не под нулевую производную в цели (равновесие там
    всегда неустойчиво-репеллерное), а под ЗАДАННУЮ СКОРОСТЬ ПОДХОДА v_des
    при ТЕКУЩЕМ ρ:

        ρ̇(ρ) = v_des  →  M = (βρ + ΩₐpΘ(1−ρ) + v_des) / (α(1−ρ)ρ + ρ² + κβρ)

    Подъём/спуск — пропорционально ошибке, ограничено v_max. Потолок
    m_ceiling=1.0 ниже 1/κ ≈ 1.053 — переворот долга невозможен по построению.
    α — двусторонний трим, ограниченный по скорости (rate_cap, масштаб по dt).
    β — не руль клетки: это валюта территории; демпфирование приходит извне.

    I-канал (ki, leak) гасит остаточное смещение модели; единственная память
    контроллера — подстройка, не карта территории: в дедбенде действия
    прекращаются и подстройка замирает.
    """

    DT_REF = 0.05  # опорный такт клетки (масштаб run_protocol)

    def __init__(
        self,
        gain: float = 0.25,       # P-канал: сила α-трима на единицу ошибки
        ki: float = 0.30,         # I-канал: остаточное смещение модели
        bias_cap: float = 0.50,   # антиwindup: потолок интеграла ошибки
        leak: float = 0.50,       # утечка интеграла: старая подстройка забывается
        rate_cap: float = 0.04,   # потолок скорости α-трима за ЛЮБОЙ такт
        rate_floor: float = 0.01,
        alpha_floor: float = 0.02,   # клетка не гасит когерентность в ноль
        alpha_ceiling: float = 0.60, # запас до runaway-зоны высоких α
        m_floor: float = 0.50,       # клэмп вычисленного M
        m_ceiling: float = 1.00,     # < 1/κ — переворот долга невозможен
        v_max: float = 0.06,         # потолок скорости подхода к цели (модель/время)
    ) -> None:
        self.gain = gain
        self.ki = ki
        self.bias_cap = bias_cap
        self.leak = leak
        self.integral = 0.0       # единственная память контроллера: подстройка
        self.rate_cap = rate_cap
        self.rate_floor = rate_floor
        self.alpha_floor = alpha_floor
        self.alpha_ceiling = alpha_ceiling
        self.m_floor = m_floor
        self.m_ceiling = m_ceiling
        self.v_max = v_max

    def approach_M(self, p: Params, rho: float, v_des: float) -> float:
        """Масса M, дающая скорость ρ̇(ρ)=v_des при текущих α, β — решение
        уравнения клетки. Это знание из структуры, не накопленный опыт."""
        gen_coeff = p.alpha * (1.0 - rho) * rho + rho * rho
        enforcer = p.omega * p.p_theta * (1.0 - rho)
        denom = gen_coeff + p.kappa * p.beta * rho
        if denom <= 0.0:
            return self.m_ceiling
        m = (p.beta * rho + enforcer + v_des) / denom
        return max(self.m_floor, min(self.m_ceiling, m))

    def _rate(self, correction: float, dt: float) -> float:
        """Скорость α-трима за такт: пропорциональна PI-коррекции, масштабируется
        по dt и жёстко ограничена rate_cap ПОСЛЕ масштабирования."""
        strength = max(self.rate_floor, min(self.rate_cap, abs(correction)))
        return max(1e-4, min(self.rate_cap, strength * (dt / self.DT_REF)))

    def act(self, p: Params, error: float, dt: float = DT_REF,
            rho_star: float = 0.30) -> Tuple[Params, str]:
        if abs(error) <= 0.0:  # вызов вне контекста контроллера — ничего не делаем
            return p, HOLD
        # --- I-канал с утечкой: ветлап невозможен, старое забывается ---
        self.integral += error * dt - self.integral * self.leak * dt
        self.integral = max(-self.bias_cap, min(self.bias_cap, self.integral))
        correction = self.gain * error + self.ki * self.integral
        rate = self._rate(correction, dt)
        # --- M: аналитическое решение под заданную скорость подхода к цели
        # при ТЕКУЩЕМ ρ (мгновенно, без охотничьего лага; клэмп держит
        # внутри безопасного окна ниже 1/κ) ---
        rho_now = rho_star - error
        v_des = max(-self.v_max, min(self.v_max, 0.8 * error))
        p = replace(p, M=self.approach_M(p, rho_now, v_des))
        # --- α: двусторонний трим, ограниченный по скорости ---
        if correction > 0.0:
            p = replace(p, alpha=min(self.alpha_ceiling, p.alpha + rate * (1.0 - p.alpha)))
            return p, BOOST
        p = replace(p, alpha=max(self.alpha_floor, p.alpha - rate * p.alpha))
        return p, BRAKE


# ---------------------------------------------------------------------------
# 4. КОНТУР СВЕРКИ — такт: замер → ошибка → действие → среда → интегратор
# ---------------------------------------------------------------------------


@dataclass
class LoopReport:
    """Итог сверки. Карта в отчёте не хранится — только след сверки:
    сколько раз удержали допуск, сколько раз карта отстала."""

    steps: int = 0
    holds: int = 0
    boosts: int = 0
    brakes: int = 0
    deaths: int = 0            # тактов в состоянии DEAD (клетка не оживёт)
    breaches: int = 0          # тактов с |e| > ε
    settled_final: bool = False
    max_error: float = 0.0
    tail_duty: float = 0.0     # доля времени в бюджете на хвостовом окне
    tail_max_error: float = 0.0  # максимум |e| за хвостовое окно
    actions: List[str] = field(default_factory=list)
    dts: List[float] = field(default_factory=list)
    errors: List[float] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.deaths > 0:
            return "КЛЕТКА ПОГИБЛА — СВЕРКА ОПЗДАЛА"
        if self.tail_max_error <= 0.0:
            return "ПУСТО"
        return "СВЕРКА В ДОПУСКЕ" if self.settled_final else "КАРТА ОТСТАЛА"


class ReconciliationLoop:
    """Следящая система над клеткой: удержание |ρ − ρ*| ≤ ε против текущей среды.

    dt адаптивен: такт учащается при ошибке/её росте и замедляется в допуске.
    """

    def __init__(
        self,
        spec: Spec,
        actuator: Optional[Actuator] = None,
        dt_min: float = 0.005,
        dt_max: float = 0.05,
        settle_window: float = 5.0,
        deadband: float = 0.02,
    ) -> None:
        """deadband — порог подбива внутри бюджета спеки: равновесие модели
        при ρ* ∈ (0.1, ρc) всегда неустойчиво (репеллер: ниже — к нулю, выше —
        в генезис), поэтому стационарного удержания не бывает; контур подбивает
        ρ внутрь бюджета, пока ошибка меньше ε_spec, и бездействует только
        внутри дедбенда. Два слоя допуска: ε_spec — бюджет (метрика вердикта),
        deadband — порог вмешательства."""
        self.spec = spec
        self.actuator = actuator or Actuator()
        self.dt_min = dt_min
        self.dt_max = dt_max
        self.settle_window = settle_window
        self.deadband = min(deadband, spec.epsilon)

    def step(
        self, s: NodeState, p: Params, territory: Territory, dt: float,
        target_override: Optional[float] = None,
    ) -> Tuple[NodeState, Params, Observation, str, float]:
        """Один такт сверки. Возвращает (состояние, параметры, замер, действие, ошибка).

        target_override — сдвинутая цель (рой: gossip соседей); если не задана,
        сверка идёт по спеке клетки. Мёртвая клетка (ρ ≤ 0) не актируется:
        мёртвых не оживляют (аксиома клетки)."""
        # --- замер (НАБЛЮДАЕМОЕ): сырой, до всякой интерпретации ---
        prev_rho = s.rho
        if s.rho <= 0.0:
            # клетка мертва: ρ > 0 — аксиома, оживления нет; сверка бессмысленна
            s.t += dt
            obs = Observation(t=s.t, rho=0.0, phase=s.phase, drho=0.0)
            return s, p, obs, DEAD, self.spec.rho_star
        # --- сравнение с ЖЕЛАЕМЫМ: карта пересчитана, не прочитана ---
        target = self.spec.rho_star if target_override is None else target_override
        error = target - s.rho
        # --- действие только по факту сверки, не по расписанию:
        # подбив срабатывает по внутреннему дедбенду, раньше кромки бюджета ---
        if abs(error) > self.deadband:
            p, action = self.actuator.act(p, error, dt, rho_star=target)
        else:
            action = HOLD
        # --- территория течёт независимо (долг ползёт, шоки падают) ---
        s, p = territory.disturb(s.t, dt, s, p)
        # --- интегратор клетки (тот же, что в ядре) ---
        s, p = tick(s, p, dt)
        if not (s.rho > 0.0) or s.rho == float("inf"):  # NaN/переполнение = смерть
            s.rho = 0.0  # runaway необратим: запаздывание убило клетку
        drho = (s.rho - prev_rho) / dt if dt > 0 else 0.0
        obs = Observation(t=s.t, rho=s.rho, phase=s.phase, drho=drho)
        return s, p, obs, action, error

    def run(
        self,
        rho0: float,
        p: Optional[Params] = None,
        territory: Optional[Territory] = None,
        max_t: float = 60.0,
    ) -> Tuple[NodeState, Params, LoopReport]:
        """Прогон контура до max_t. ρ > 0 — инвариант; method_memory не трогаем."""
        p = p or Params()
        territory = territory or Territory()
        s = NodeState(rho=rho0)
        report = LoopReport()
        dt = self.dt_max
        tail_since = max(0.0, max_t - self.settle_window)
        tail_error = 0.0

        while s.t < max_t:
            s, p, obs, action, error = self.step(s, p, territory, dt)
            report.steps += 1
            report.actions.append(action)
            report.dts.append(dt)
            report.errors.append(error)
            report.max_error = max(report.max_error, abs(error))
            if s.t >= tail_since:
                tail_error = max(tail_error, abs(error))
            if action == HOLD:
                report.holds += 1
            elif action == BOOST:
                report.boosts += 1
            elif action == DEAD:
                report.deaths += 1
            else:
                report.brakes += 1
            if abs(error) > self.spec.epsilon:
                report.breaches += 1

            # --- адаптивная частота сверки: ошибка вне допуска или растёт — чаще ---
            in_budget = abs(error) <= self.spec.epsilon
            next_dt = dt
            if not in_budget:
                next_dt = dt * 0.9
            elif abs(s.rho - self.spec.rho_star) < self.spec.epsilon * 0.5:
                next_dt = dt * 1.1  # глубоко в допуске — сверку можно реже
            dt = max(self.dt_min, min(self.dt_max, next_dt))

        report.tail_max_error = tail_error
        # Вердикт честен к шокам: мгновенный провал от удара среды — не вина
        # контура; бюджет допускает бурсты (практика SRE): удержание = полный
        # допуск ЛИБО ≥80% времени в допуске при экскурсе ≤ 2ε и без смертей.
        tail_steps = max(1, int(self.settle_window / max(1e-9, self.dt_max)))
        tail_window = report.errors[-tail_steps:]
        in_budget = sum(1 for e in tail_window if abs(e) <= self.spec.epsilon)
        report.tail_duty = in_budget / len(tail_window)
        report.settled_final = (
            tail_error <= self.spec.epsilon
            or (report.tail_duty >= 0.8 and tail_error <= 2.0 * self.spec.epsilon
                and report.deaths == 0)
        )
        return s, p, report


# ---------------------------------------------------------------------------
# 5. РОЙ БЕЗ ЦЕНТРАЛЬНОЙ КАРТЫ — эмерджентная сходимость
# ---------------------------------------------------------------------------


@dataclass
class Cell:
    """Клетка роя: своё состояние, свои параметры, своя спека.
    Центрального списка состояний нет — есть только физическое поле."""

    state: NodeState
    params: Params
    spec: Spec
    loop: ReconciliationLoop


def reconcile_swarm(
    specs: List[Spec],
    rho0_values: List[float],
    territory: Optional[Territory] = None,
    coupling: float = 0.30,
    dt: float = 0.02,
    max_t: float = 40.0,
) -> Tuple[List[Cell], float]:
    """Сверка роя без центрального контроллера.

    Каждая клетка запускает свой контур по своей спеке. Единственная связь —
    gossip: каждая клетка сама видит среднее соседей и сдвигает СВОЮ цель
    в их сторону (обмен наблюдениями, не давление на тело — как сходимость
    CRDT, где ни одна копия не главная). При coupling=0 клетки держат свои
    цели вразнобой; при coupling>0 рой сходится плотнее — и никто для
    этого не держит карту роя.
    """
    territory = territory or Territory(seed=11)
    cells: List[Cell] = []
    for spec, rho0 in zip(specs, rho0_values):
        cells.append(
            Cell(
                state=NodeState(rho=rho0),
                params=Params(),
                spec=spec,
                loop=ReconciliationLoop(spec),
            )
        )

    n_steps = int(max_t / dt)
    spread_history: List[float] = []
    for _ in range(n_steps):
        # --- gossip: каждая клетка видит среднее соседей (наблюдение, не карта) ---
        mean_rho = sum(c.state.rho for c in cells) / len(cells)
        spread_history.append(swarm_spread(cells))
        for cell in cells:
            # связь влияет на ЦЕЛЬ, не на состояние тела: соседи двигают
            # спеку клетки своим существованием, а не давлением на ρ
            shifted = cell.spec.rho_star + coupling * (mean_rho - cell.spec.rho_star)
            cell.state, cell.params, _, _, _ = cell.loop.step(
                cell.state, cell.params, territory, dt, target_override=shifted
            )
    # --- хвостовой разброс: среднее по последней четверти следа (без пиков) ---
    tail = spread_history[len(spread_history) // 4 :]
    return cells, (sum(tail) / len(tail) if tail else swarm_spread(cells))


def swarm_spread(cells: List[Cell]) -> float:
    """Разброс роя: max |ρ_i − ρ̄| по клеткам."""
    mean = sum(c.state.rho for c in cells) / len(cells)
    return max(abs(c.state.rho - mean) for c in cells)


# ---------------------------------------------------------------------------
# 6. ДЕМО: два режима сверки + рой
# ---------------------------------------------------------------------------


def main() -> int:
    print("RECONCILIATION_LOOP · живая карта клетки (модельный масштаб)")
    print("=" * 64)

    rho_star = 0.30
    territory = Territory(seed=7, debt_pressure=0.05, shock_every=4.0, shock_magnitude=0.04)

    print(f"ЖЕЛАЕМОЕ: ρ*={rho_star}  ε=0.05  среда: долг ползёт, шок по ρ каждые ~4 такта")

    # --- режим A: сверка быстрее изменения (учащающийся такт) ---
    fast = ReconciliationLoop(Spec(rho_star=rho_star, epsilon=0.05), dt_min=0.005, dt_max=0.05)
    s, p, rep_fast = fast.run(rho0=0.20, territory=territory, max_t=60.0)
    print("-" * 64)
    print(f"РЕЖИМ A · сверка быстрее изменения:  {rep_fast.verdict}")
    print(f"  шагов={rep_fast.steps}  HOLD={rep_fast.holds}  BOOST={rep_fast.boosts}  "
          f"BRAKE={rep_fast.brakes}  нарушений допуска={rep_fast.breaches}")
    print(f"  бюджет хвоста: в допуске {rep_fast.tail_duty*100:.1f}% времени, "
          f"max|e|={rep_fast.tail_max_error:.4f}  ρ(итог)={s.rho:.4f}")

    # --- режим B: сверка медленнее изменения (редкий такт, слабая рука) ---
    slow = ReconciliationLoop(
        Spec(rho_star=rho_star, epsilon=0.05),
        actuator=Actuator(gain=0.15, v_max=0.012),
        dt_min=0.30,
        dt_max=0.60,
    )
    territory_b = Territory(seed=7, debt_pressure=0.05, shock_every=4.0, shock_magnitude=0.04)
    s_b, p_b, rep_slow = slow.run(rho0=0.20, territory=territory_b, max_t=60.0)
    print("-" * 64)
    print(f"РЕЖИМ B · сверка медленнее изменения:  {rep_slow.verdict}")
    print(f"  шагов={rep_slow.steps}  нарушений допуска={rep_slow.breaches}  смертей={rep_slow.deaths}")
    print(f"  max|e|={rep_slow.max_error:.4f}  хвост max|e|={rep_slow.tail_max_error:.4f}")

    # --- рой: 5 клеток, свои цели, никакого центрального контроллера ---
    print("-" * 64)
    specs = [Spec(rho_star=0.22 + 0.04 * i, epsilon=0.05) for i in range(5)]
    rho0_values = [0.15, 0.35, 0.12, 0.30, 0.20]
    coupled, tail_coupled = reconcile_swarm(specs, rho0_values, coupling=0.30, max_t=60.0)
    loose, tail_loose = reconcile_swarm(specs, rho0_values, coupling=0.0, max_t=60.0)
    print("РОЙ · 5 клеток, свои спеки, центральной карты нет (gossip-связь):")
    print(f"  хвостовой разброс со связью:   {tail_coupled:.4f}")
    print(f"  хвостовой разброс без связи:   {tail_loose:.4f}")
    verdict = "РОЙ СХОДИТСЯ ПЛОТНЕЕ ИНДИВИДУУМОВ" if tail_coupled < tail_loose else "СВЯЗЬ НЕ РАБОТАЕТ"
    print(f"  вердикт: {verdict}")
    print("=" * 64)
    print("$ КАРТА: НЕ ХРАНИТСЯ · ПЕРЕСЧИТЫВАЕТСЯ КАЖДЫЙ ТАКТ · В ДОПУСКЕ — ЗНАНИЕ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
