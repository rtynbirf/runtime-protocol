#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живые приёмки крови blood.py (закон 4 «Храповик»: файл добавлен,
существующие приёмки клетки не изменены).

Запуск: python3 -m unittest test_blood -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from blood import (
    DRIFT,
    FORGED,
    GENESIS_HASH,
    STALE,
    VERIFIED,
    accept,
    canonical_json,
    chain_verify,
    envelope_hash,
    make_envelope,
    tail_hash,
    verify_envelope,
)


def _lines(*envs):
    return [canonical_json(e) for e in envs]


class TestCanonical(unittest.TestCase):
    def test_order_of_keys_does_not_change_bytes(self):
        """Равные состояния = равные байты: форма записи не сверяется, состояние сверяется."""
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        self.assertEqual(canonical_json(a), canonical_json(b))


class TestEnvelope(unittest.TestCase):
    def test_make_and_verify_ok(self):
        env = make_envelope("cell-swarm", "runtime-protocol", 1, {"expected": "sha123"},
                            GENESIS_HASH)
        ok, reason = verify_envelope(env, GENESIS_HASH)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, VERIFIED)

    def test_tampered_payload_is_forged(self):
        """Подделка тела конверта ловится хешем — кровь несёт только правду или останавливается."""
        env = make_envelope("cell-swarm", "runtime-protocol", 1, {"expected": "sha123"},
                            GENESIS_HASH)
        env["payload"]["expected"] = "sha999"
        ok, reason = verify_envelope(env, GENESIS_HASH)
        self.assertFalse(ok)
        self.assertEqual(reason, FORGED)

    def test_wrong_prev_hash_breaks_chain(self):
        env = make_envelope("cell-swarm", "runtime-protocol", 1, {}, GENESIS_HASH)
        ok, reason = verify_envelope(env, "1" * 64)
        self.assertFalse(ok)
        self.assertEqual(reason, "CHAIN_BROKEN")

    def test_stale_envelope_rejected(self):
        """Просроченный факт не верифицируют: скорость сверки > скорости территории."""
        old = (datetime.now(timezone.utc) - timedelta(seconds=3600)).strftime("%Y-%m-%dT%H:%M:%SZ")
        env = make_envelope("a", "b", 1, {}, GENESIS_HASH, ttl_s=900, ts=old)
        ok, reason = verify_envelope(env, GENESIS_HASH)
        self.assertFalse(ok)
        self.assertEqual(reason, STALE)

    def test_seq_must_be_positive(self):
        with self.assertRaises(ValueError):
            make_envelope("a", "b", 0, {}, GENESIS_HASH)


class TestAccept(unittest.TestCase):
    def test_inside_deadband_verified(self):
        """Мёртвая зона: клетка не дёргается от шума внутри deadband."""
        env = make_envelope("a", "b", 1,
                            {"expected": 0.50, "tol": {"eps": 0.05, "deadband": 0.02}},
                            GENESIS_HASH)
        self.assertEqual(accept(env, 0.505)["status"], VERIFIED)

    def test_within_epsilon_verified(self):
        env = make_envelope("a", "b", 1,
                            {"expected": 0.50, "tol": {"eps": 0.05, "deadband": 0.02}},
                            GENESIS_HASH)
        self.assertEqual(accept(env, 0.53)["status"], VERIFIED)

    def test_beyond_epsilon_is_drift(self):
        env = make_envelope("a", "b", 1,
                            {"expected": 0.50, "tol": {"eps": 0.05, "deadband": 0.02}},
                            GENESIS_HASH)
        self.assertEqual(accept(env, 0.60)["status"], DRIFT)

    def test_discrete_pin_exact(self):
        """SHA сверяется точно: eps=0, совпадение байтов или дрейф."""
        env = make_envelope("a", "b", 1,
                            {"expected": "137eb62", "tol": {"eps": 0, "deadband": 0}},
                            GENESIS_HASH)
        self.assertEqual(accept(env, "137eb62")["status"], VERIFIED)
        self.assertEqual(accept(env, "c5517ef")["status"], DRIFT)


class TestChain(unittest.TestCase):
    def _chain(self, n=3):
        envs = []
        prev = GENESIS_HASH
        for i in range(1, n + 1):
            env = make_envelope(f"cell-{i}", "cell-swarm", i, {"i": i}, prev)
            envs.append(env)
            prev = env["hash"]
        return envs

    def test_chain_build_and_verify(self):
        ok, reason, n = chain_verify(_lines(*self._chain(3)))
        self.assertTrue(ok, reason)
        self.assertEqual(n, 3)

    def test_empty_ledger_is_valid(self):
        """Нет крови — не ложь: пустая летопись целостна (n=0)."""
        ok, reason, n = chain_verify([])
        self.assertTrue(ok)
        self.assertEqual(n, 0)

    def test_tampered_middle_line_caught(self):
        envs = self._chain(3)
        envs[1]["payload"]["i"] = 999
        ok, reason, n = chain_verify(_lines(*envs))
        self.assertFalse(ok)
        self.assertEqual(reason, FORGED)
        self.assertEqual(n, 1)  # первый цел, дальше кровь остановлена

    def test_removed_middle_line_caught(self):
        envs = self._chain(3)
        del envs[1]
        ok, reason, n = chain_verify(_lines(*envs))
        self.assertFalse(ok)

    def test_seq_must_grow_per_pair(self):
        a = make_envelope("x", "y", 1, {}, GENESIS_HASH)
        b = make_envelope("x", "y", 1, {}, a["hash"])  # тот же seq по той же паре
        ok, reason, n = chain_verify(_lines(a, b))
        self.assertFalse(ok)
        self.assertEqual(reason, "SEQ_STAGNANT")

    def test_tail_hash_is_last_link(self):
        envs = self._chain(2)
        self.assertEqual(tail_hash(_lines(*envs)), envs[-1]["hash"])


class TestHashIndependence(unittest.TestCase):
    def test_hash_ignores_hash_field(self):
        """Хеш не ссылается сам на себя: поле hash исключено из свёртки."""
        env = make_envelope("a", "b", 1, {}, GENESIS_HASH)
        h1 = envelope_hash(env)
        env["hash"] = "не важно что"
        self.assertEqual(envelope_hash(env), h1)


if __name__ == "__main__":
    unittest.main()
