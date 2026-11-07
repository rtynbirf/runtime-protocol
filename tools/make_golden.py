#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Золотые пробы тропы роя (ADR-0005): канонические тела конвертов + хеши,
источник — Python-ядро blood.py. Rust-тропа (rust_trail/) обязана давать
те же байты и те же хеши. CI: `make_golden.py --check` регенерирует во
временный каталог и сверяет с закоммиченным — расхождение = красная проба,
два языка должны двигаться вместе."""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from blood import canonical_json, envelope_hash  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "rust_trail", "tests", "golden")

# Тела конвертов БЕЗ поля hash (envelope_hash сам на себя не ссылается).
# Зеркала этих значений захардкожены в rust_trail/tests/golden_match.rs.
CASES = [
    {
        "v": 1, "src": "model", "dst": "runtime-protocol", "seq": 1,
        "ts": "2026-11-07T00:00:00Z", "ttl_s": 900,
        "payload": {"note": "рой из модели в кровь"},
        "prev_hash": "0" * 64,
    },
    {
        "v": 1, "src": "model", "dst": "runtime-protocol", "seq": 2,
        "ts": "2026-11-07T17:59:00Z", "ttl_s": 900,
        "payload": {"cells": {"runtime-protocol": "2337fc2fa053deadbeef"},
                    "tol": {"eps": 0, "deadband": 0}},
        "prev_hash": "a" * 64,
    },
    {
        "v": 1, "src": "клетка-роя", "dst": "cell-swarm", "seq": 3,
        "ts": "2026-11-07T18:00:00Z", "ttl_s": 60,
        "payload": {"s": "a\"b\\c\nd\x01e\x08",
                    "list": [1, 2, 3, True, False, None],
                    "empty": "",
                    "nested": {"z": [], "a": {}}},
        "prev_hash": "b" * 64,
    },
    {
        "v": 1, "src": "polymath-os", "dst": "cell-swarm", "seq": 1,
        "ts": "2026-11-07T18:01:00Z", "ttl_s": 900,
        "payload": {"observed": "c" * 40, "expected": "d" * 40,
                    "status": "OBSERVED", "pulse": 3},
        "prev_hash": "e" * 64,
    },
]


def generate(outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    for i, body in enumerate(CASES):
        canon = canonical_json(body)
        with open(os.path.join(outdir, f"case{i}.body.json"), "w",
                  encoding="utf-8", newline="") as f:
            f.write(canon)
        with open(os.path.join(outdir, f"case{i}.hash"), "w",
                  encoding="utf-8", newline="") as f:
            f.write(envelope_hash(body))


def check() -> int:
    with tempfile.TemporaryDirectory() as td:
        generate(td)
        bad = []
        for name in sorted(os.listdir(td)):
            with open(os.path.join(td, name), "rb") as f:
                fresh = f.read()
            path = os.path.join(GOLDEN, name)
            if not os.path.exists(path):
                bad.append(f"{name}: нет в rust_trail/tests/golden")
                continue
            with open(path, "rb") as f:
                committed = f.read()
            if fresh != committed:
                bad.append(f"{name}: байты ядра ушли от закоммиченных")
        if bad:
            print("ФАКТ: золотые пробы разошлись с ядром:")
            for b in bad:
                print(" -", b)
            print("Правка: python3 tools/make_golden.py && коммит (оба языка вместе)")
            return 1
    print(f"ФАКТ: золотые пробы {len(CASES)} — байты ядра и закоммиченные совпадают")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    generate(GOLDEN)
    print(f"ФАКТ: золотые пробы записаны → {GOLDEN} ({len(CASES)} случаев)")
