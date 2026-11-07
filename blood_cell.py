#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Клеточная нога крови: получил конверт → сверил с собой и летописью →
дописал эхо в летопись роя → подготовил кровь для следующей клетки.

Работает в CI клетки. stdlib-only. Все шаги печатают ФАКТ (HTTP-код, хеш,
статус) — кровь не бывает «примерно доставлена».

Переменные окружения:
  BLOOD_ENVELOPE   — JSON конверта от предыдущего тела (client_payload.envelope)
  CELL_NAME        — имя клетки (например runtime-protocol)
  CELL_SHA         — наблюдаемый HEAD клетки (github.sha)
  GH_TOKEN         — токен с доступом к летописи и dispatch
  BLOOD_LEDGER_REPO  — репо летописи (по умолчанию rtynbirf/cell-swarm)
  BLOOD_LEDGER_PATH  — путь летописи (по умолчанию blood/LEDGER.jsonl)

Флаги:
  --dry-run            — сверить и напечатать, ничего не писать
  --next-repo NAME     — собрать dispatch-тело для следующей клетки в файл
  --next-output PATH   — куда положить dispatch-тело (по умолчанию /tmp/next_dispatch.json)
"""
import base64
import json
import os
import sys
import urllib.request

from blood import (
    DRIFT,
    STALE,
    VERIFIED,
    accept,
    canonical_json,
    chain_verify,
    make_envelope,
    tail_hash,
    verify_envelope,
)

API = "https://api.github.com"


def api(method, url, token, payload=None):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def read_ledger(token):
    repo = os.environ.get("BLOOD_LEDGER_REPO", "rtynbirf/cell-swarm")
    path = os.environ.get("BLOOD_LEDGER_PATH", "blood/LEDGER.jsonl")
    st, data = api("GET", f"{API}/repos/{repo}/contents/{path}?ref=main", token)
    if st == 404:
        return [], None, f"{repo}/{path} ещё не существует"
    if st != 200:
        sys.exit(f"ФАКТ: летопись не прочитана, HTTP {st}")
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content.splitlines(), data["sha"], f"{repo}/{path}"


def main():
    dry = "--dry-run" in sys.argv
    env_json = os.environ.get("BLOOD_ENVELOPE", "").strip()
    cell = os.environ.get("CELL_NAME", "")
    cell_sha = os.environ.get("CELL_SHA", "")
    token = os.environ.get("GH_TOKEN", "")
    if not env_json or not cell or not cell_sha:
        sys.exit("ФАКТ: нет конверта/имени клетки/SHA — кровь не течёт вслепую")

    incoming = json.loads(env_json)

    # 1. Летопись: целостность до прививки (подделанная летопись не принимает кровь)
    lines, file_sha, ledger_where = read_ledger(token)
    ok, reason, n = chain_verify(lines)
    print(f"ФАКТ: летопись {ledger_where}: цепь {'целая' if ok else 'СЛОМАНА'} "
          f"({reason}), конвертов {n}")
    if not ok:
        sys.exit("ФАКТ: летопись подделана — кровь остановлена до разбора")

    # 2. Входящий конверт: хеш/цепь/ttl (просрочка = STALE, это честный факт)
    ok, reason = verify_envelope(incoming, tail_hash(lines))
    if not ok:
        print(f"ФАКТ: входящий конверт отклонён: {reason} "
              f"(seq={incoming.get('seq')}, ts={incoming.get('ts')})")
        sys.exit(0 if reason == STALE else 2)

    # 3. Сверка ЖЕЛАЕМОЕ↔НАБЛЮДАЕМОЕ: моё живое SHA против того, что несёт кровь
    expected = (incoming.get("payload", {}).get("cells") or {}).get(cell)
    verdict = accept(incoming, cell_sha) if expected is None else {
        "status": VERIFIED if expected == cell_sha else DRIFT,
        "violation": 0 if expected == cell_sha else None, "tol": {"eps": 0, "deadband": 0},
    }
    print(f"ФАКТ: сверка {cell}: желаемое={expected} наблюдаемое={cell_sha} "
          f"→ {verdict['status']}")

    # 4. Эхо-конверт: клетка возвращает в летопись свой факт (не нарратив)
    seq = 1
    for raw in lines:
        raw = raw.strip()
        if raw:
            e = json.loads(raw)
            if e.get("src") == cell:
                seq = max(seq, e["seq"] + 1)
    echo = make_envelope(
        src=cell, dst="cell-swarm", seq=seq,
        payload={"observed": cell_sha,
                 "expected": expected,
                 "status": verdict["status"],
                 "pulse": incoming["seq"]},
        prev_hash=tail_hash(lines), ttl_s=900)
    echo_line = canonical_json(echo)
    print(f"ФАКТ: эхо-конверт слит: seq={seq} hash={echo['hash'][:12]}…")

    if dry:
        print("ФАКТ: dry-run — летопись не тронута")
    else:
        repo = os.environ.get("BLOOD_LEDGER_REPO", "rtynbirf/cell-swarm")
        path = os.environ.get("BLOOD_LEDGER_PATH", "blood/LEDGER.jsonl")
        new_content = ("\n".join(lines + [echo_line]) + "\n").encode("utf-8")
        st, resp = api("PUT", f"{API}/repos/{repo}/contents/{path}", token, {
            "message": f"blood: эхо клетки {cell} (пульс #{incoming['seq']}, "
                       f"{verdict['status']})",
            "content": base64.b64encode(new_content).decode(),
            "sha": file_sha,
        })
        print(f"ФАКТ: эхо в летопись: HTTP {st} (200/201=дописано)")
        if st not in (200, 201):
            sys.exit(3)

    # 5. Кровь для следующей клетки (если цепь задана)
    if "--next-repo" in sys.argv:
        next_repo = sys.argv[sys.argv.index("--next-repo") + 1]
        out = sys.argv[sys.argv.index("--next-output") + 1] if "--next-output" in sys.argv \
            else "/tmp/next_dispatch.json"
        body = {"event_type": "blood-pulse", "client_payload": {"envelope": echo}}
        with open(out, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
        print(f"ФАКТ: dispatch-тело для {next_repo} записано в {out}")


if __name__ == "__main__":
    main()
