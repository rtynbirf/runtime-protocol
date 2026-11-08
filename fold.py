#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fold.py — обратно-нить как свёртка журнала (Волна ×4 · П50, issue #3).

Обратно-нить (ответ клетки на урон) — ЧИСТАЯ функция свёртки журнала
событий: без часов, без сети, без случайности, без мутаций входа.
Из этого следуют три обещания:
  ДЕТЕРМИНИЗМ  — одинаковый журнал → одинаковые байты состояния;
  REPLAY       — свёртка журнала с genesis == состоянию клетки
                 (журнал — единственный носитель правды);
  КОНТРОЛЬ     — двойной прогон свёртки идентичен (инвариант свёртки).

ОБЩИЙ КАНОН СВЁРТКИ (для пропагатора task-graph-propagator и клеток роя):
  CANON_VERSION = 1;
  fold_with(reducer, events, init) — свёртка в каноническую форму:
    reducer(state, event) -> state; state — JSON-совместимый;
    reducer обязан быть чистым: не читать время/сеть/глобальные, не мутировать
    event и state (копии), целые числа вместо плавающих (канон крови, ADR-0005);
  fold(events) — частный случай: обратно-нить (редьюсер ниже);
  inverse_step(state_after, event) — обратный шаг: для каждого события
    inverse(fold_step(state_before, event), event) == state_before
    (инвариант обратимости — свойство редьюсера, проверяется property-тестом).

События журнала (JSONL, каждое — факт урона/ответа):
  {"t": 1, "kind": "strike",    "src": "carTel", "dmg": 3}   — урон: hp↓, debt[src]↑
  {"t": 2, "kind": "heal",      "amount": 2}                 — регенерация: hp↑ (cap max)
  {"t": 3, "kind": "retaliate", "src": "carTel"}             — ответ: debt[src] → 0 (списание)
Состояние: {"hp", "max_hp", "debt" (сортирован), "events", "last_t"}.

Запуск:
    python3 fold.py --selftest          # property-приёмки канона (rc 0/1)
    python3 fold.py demo                # контрольный прогон: свёртка дважды
    python3 fold.py fold LOG.jsonl      # свёртка журнала → состояние (rc 0/1)
    python3 -m unittest test_fold -v    # юнит-приёмки (входят в make verify)
"""
import argparse
import copy
import hashlib
import json
import sys

CANON_VERSION = 1
INIT = {"hp": 10, "max_hp": 10, "debt": {}, "events": 0, "last_t": 0}
KINDS = ("strike", "heal", "retaliate")


# --- ОБЩИЙ КАНОН -------------------------------------------------------------

def fold_with(reducer, events, init):
    """Свёртка журнала редьюсером (общий канон; пропагатор использует это же).

    reducer получает ГЛУБОКИЕ КОПИИ: редьюсер не может испортить журнал
    или предыдущее состояние — чистота не обещание, а механика.
    """
    state = copy.deepcopy(init)
    for event in events:
        state = reducer(state, copy.deepcopy(event))
    return state


def canonical(state) -> bytes:
    """Канонические байты состояния (как в крови: сортировка ключей)."""
    return json.dumps(state, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


# --- ОБРАТНО-НИТЬ (частный редьюсер) -----------------------------------------

def _nat(x):
    """Целое неотрицательное: урон/лечение/долг не бывают дробными."""
    if not isinstance(x, int) or isinstance(x, bool) or x < 0:
        raise ValueError("ожидается целое >= 0")


def fold_step(state, event):
    """Один шаг обратно-нити (чистая функция; исключение = битый журнал)."""
    kind = event.get("kind")
    t = event.get("t")
    if not isinstance(t, int) or isinstance(t, bool) or t <= state["last_t"]:
        raise ValueError(f"событие t={t!r}: счёт журнала должен расти")
    s = copy.deepcopy(state)
    s["events"] += 1
    s["last_t"] = t
    if kind == "strike":
        src = event.get("src")
        dmg = event.get("dmg")
        _nat(dmg)
        if not isinstance(src, str) or not src:
            raise ValueError("strike: пустой src")
        s["hp"] = max(0, s["hp"] - dmg)
        new_debt = s["debt"].get(src, 0) + dmg
        # канон долга: ноль = отсутствие записи (иначе 0-удар плодил бы
        # фантомные ключи и ломал обратимость)
        if new_debt > 0:
            s["debt"][src] = new_debt
        else:
            s["debt"].pop(src, None)
    elif kind == "heal":
        amount = event.get("amount")
        _nat(amount)
        s["hp"] = min(s["max_hp"], s["hp"] + amount)
    elif kind == "retaliate":
        src = event.get("src")
        if not isinstance(src, str) or not src:
            raise ValueError("retaliate: пустой src")
        s["debt"] = dict(s["debt"])
        s["debt"].pop(src, None)  # ответ списывает нить долга источника
    else:
        raise ValueError(f"неизвестный kind: {kind!r} (канон: {KINDS})")
    return s


def inverse_step(state_after, event):
    """Обратный шаг: восстановить состояние ДО события из состояния ПОСЛЕ.

    Инвариант обратимости: inverse(fold_step(s, e), e) == s для всех s, e.
    Точность требует аннотированного журнала (annotate): удар может cl'ампить
    hp к нулю, лечение — к максимуму, ответ — стирать долг; эти «до»-значения
    несёт журнал (hp_before, prev_t, had), не глобальная память.
    Без аннотации — ValueError (молчаливая неточность запрещена).
    """
    kind = event.get("kind")
    if kind not in KINDS:
        raise ValueError(f"неизвестный kind: {kind!r}")
    for key in ("prev_t",):
        if key not in event:
            raise ValueError(f"обратимость требует аннотации {key} (см. annotate)")
    if kind in ("strike", "heal") and "hp_before" not in event:
        raise ValueError("обратимость требует аннотации hp_before")
    if kind == "retaliate" and "had" not in event:
        raise ValueError("обратимость требует аннотации had")
    s = copy.deepcopy(state_after)
    s["events"] -= 1
    s["last_t"] = event["prev_t"]
    if kind == "strike":
        src, dmg = event["src"], event["dmg"]
        debt = s["debt"].get(src, 0) - dmg
        if debt == 0:
            s["debt"].pop(src, None)
        else:
            s["debt"][src] = debt
        s["hp"] = event["hp_before"]
    elif kind == "heal":
        s["hp"] = event["hp_before"]
    else:  # retaliate
        if event["had"] > 0:
            s["debt"][event["src"]] = event["had"]
        else:
            s["debt"].pop(event["src"], None)
    return s


def annotate(events):
    """Аннотировать журнал обратимыми деталями (prev_t/hp_before/had).

    Делает ЖУРНАЛ самодостаточным для обратного прохода: свёртка остаётся
    чистой, обратимость — свойство журнала, не глобальной памяти.
    """
    state = copy.deepcopy(INIT)
    out = []
    for event in events:
        e = copy.deepcopy(event)
        kind = e.get("kind")
        e["prev_t"] = state["last_t"]
        if kind in ("strike", "heal"):
            e["hp_before"] = state["hp"]
        elif kind == "retaliate":
            e["had"] = state["debt"].get(e.get("src"), 0)
        out.append(e)
        state = fold_step(state, e)
    return out


def fold(events, init=None):
    """Свёртка журнала в обратно-нить (состояние клетки)."""
    return fold_with(fold_step, events, INIT if init is None else init)


def replay(log_text):
    """Replay: журнал (JSONL/список) → состояние. Падение = битый журнал."""
    if isinstance(log_text, str):
        events = [json.loads(line) for line in log_text.splitlines() if line.strip()]
    else:
        events = list(log_text)
    return fold(events)


# --- КОНТРОЛЬНЫЙ ПРОГОН ------------------------------------------------------

def sample_log():
    return annotate([
        {"t": 1, "kind": "strike", "src": "carTel", "dmg": 4},
        {"t": 2, "kind": "heal", "amount": 2},
        {"t": 3, "kind": "strike", "src": "rival", "dmg": 5},
        {"t": 4, "kind": "retaliate", "src": "carTel"},
        {"t": 5, "kind": "heal", "amount": 7},
        {"t": 6, "kind": "retaliate", "src": "rival"},
    ])


def control_run(events=None):
    """Контрольный прогон: двойная свёртка идентична, replay == состояние."""
    events = sample_log() if events is None else events
    s1 = fold(events)
    b1 = canonical(s1)
    s2 = fold(events)
    b2 = canonical(s2)
    rp = replay("\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True)
                          for e in events))
    return {
        "canon_version": CANON_VERSION,
        "events": len(events),
        "double_fold_identical": b1 == b2,
        "replay_equals_state": b1 == canonical(rp),
        "state": s1,
        "state_sha256": hashlib.sha256(b1).hexdigest(),
    }


def selftest():
    """Property-приёмки канона (детерминированный PRNG, std-only)."""
    import random
    ok = True

    def need(cond, msg):
        nonlocal ok
        print(("✓ " if cond else "✗ ") + msg)
        ok = ok and bool(cond)

    rnd = random.Random(23)  # зерно фиксировано: приёмка воспроизводима
    names = ["carTel", "rival", "ghost", "sentinel"]
    logs, states = [], []
    for _ in range(200):
        n = rnd.randrange(0, 40)
        events = []
        t = 0
        for _ in range(n):
            t += 1
            k = rnd.choice(KINDS)
            e = {"t": t, "kind": k}
            if k == "strike":
                e["src"] = rnd.choice(names)
                e["dmg"] = rnd.randrange(0, 6)
            elif k == "heal":
                e["amount"] = rnd.randrange(0, 6)
            else:
                e["src"] = rnd.choice(names)
            events.append(e)
        logs.append(events)
        states.append(fold(events))

    # 1) двойной прогон свёртки идентичен (байт в байт)
    need(all(canonical(fold(l)) == canonical(s) for l, s in zip(logs, states)),
         "200 случайных журналов: двойная свёртка идентична байт в байт")

    # 2) replay журнала == состояние клетки
    need(all(canonical(replay(l)) == canonical(s) for l, s in zip(logs, states)),
         "replay журнала == состояние клетки (200 журналов)")

    # 3) инвариант обратимости на каждом префиксе
    bad = 0
    for l, s in zip(logs, states):
        ann = annotate(l)
        st = copy.deepcopy(INIT)
        for e, e_ann in zip(l, ann):
            nxt = fold_step(st, e)
            back = inverse_step(nxt, e_ann)
            if canonical(back) != canonical(st):
                bad += 1
                break
            st = nxt
        else:
            if canonical(st) != canonical(s):
                bad += 1
    need(bad == 0, "обратимость: inverse(fold_step(s,e),e) == s на каждом шаге")

    # 4) чистота: журнал и состояние не мутируются свёрткой
    l0 = logs[7] if len(logs) > 7 else logs[0]
    snap = copy.deepcopy(l0)
    fold(l0)
    need(l0 == snap, "чистота: свёртка не мутирует журнал")

    # 5) подмена события меняет состояние (журнал — носитель правды)
    if logs and logs[0]:
        tampered = copy.deepcopy(logs[0])
        tampered[0]["kind"] = "heal" if tampered[0]["kind"] != "heal" else "strike"
        tampered[0].setdefault("amount", 0) if tampered[0]["kind"] == "heal" else \
            tampered[0].setdefault("dmg", 1)
        tampered[0].pop("src", None) if tampered[0]["kind"] != "strike" else None
        need(canonical(fold(tampered)) != canonical(fold(logs[0])) or
             tampered == logs[0], "подмена события меняет свёртку")

    # 6) монотонный счёт журнала обязателен (t не отматывается)
    try:
        fold([{"t": 5, "kind": "heal", "amount": 1},
              {"t": 5, "kind": "heal", "amount": 1}])
        need(False, "повторный t=5 отвергается (журнал не отматывается)")
    except ValueError:
        need(True, "повторный t=5 отвергается (журнал не отматывается)")

    print("SELFTEST:", "OK" if ok else "FAILED")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Обратно-нить как свёртка журнала (П50)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("cmd", nargs="?", choices=["demo", "fold"])
    ap.add_argument("log", nargs="?", help="JSONL-журнал для fold")
    args = ap.parse_args()

    if args.selftest:
        return 0 if selftest() else 1
    if args.cmd in (None, "demo"):
        r = control_run()
        print(json.dumps(r, ensure_ascii=False, sort_keys=True, indent=2))
        if not (r["double_fold_identical"] and r["replay_equals_state"]):
            print("КОНТРОЛЬ ПРОВАЛЕН", file=sys.stderr)
            return 1
        print("ФАКТ: двойная свёртка идентична; replay == состояние")
        return 0
    if args.cmd == "fold":
        if not args.log:
            print("fold: укажи журнал JSONL", file=sys.stderr)
            return 2
        text = open(args.log, encoding="utf-8").read()
        try:
            state = replay(text)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"ФАКТ: журнал не сворачивается: {e}", file=sys.stderr)
            return 1
        sys.stdout.buffer.write(canonical(state) + b"\n")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
