//! Золотые пробы тропы: байты Python-ядра = байты Rust-тропы (ADR-0005).
//! Источник проб — tools/make_golden.py (генерит из blood.py, канон ядра).
//! Тропа обязана: (1) дать те же канонические байты, (2) дать тот же хеш.
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use rust_trail::{canonical_json, envelope_hash, J};

fn golden_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/golden")
}

fn read_golden(name: &str) -> (Vec<u8>, String) {
    let dir = golden_dir();
    let body = fs::read(dir.join(format!("{name}.body.json")))
        .unwrap_or_else(|e| panic!("ФАКТ: золотое тело {name} не прочитано: {e}"));
    let hash = fs::read_to_string(dir.join(format!("{name}.hash")))
        .unwrap_or_else(|e| panic!("ФАКТ: золотой хеш {name} не прочитан: {e}"))
        .trim()
        .to_string();
    (body, hash)
}

fn obj(pairs: Vec<(&str, J)>) -> J {
    let mut m = BTreeMap::new();
    for (k, v) in pairs {
        m.insert(k.to_string(), v);
    }
    J::Obj(m)
}

fn s(x: &str) -> J {
    J::Str(x.to_string())
}

/// Те же тела, что CASES в tools/make_golden.py — держим рядом, сверяем байтами.
fn case0() -> J {
    obj(vec![
        ("v", J::Int(1)),
        ("src", s("model")),
        ("dst", s("runtime-protocol")),
        ("seq", J::Int(1)),
        ("ts", s("2026-11-07T00:00:00Z")),
        ("ttl_s", J::Int(900)),
        ("payload", obj(vec![("note", s("рой из модели в кровь"))])),
        ("prev_hash", s(&"0".repeat(64))),
    ])
}

fn case1() -> J {
    obj(vec![
        ("v", J::Int(1)),
        ("src", s("model")),
        ("dst", s("runtime-protocol")),
        ("seq", J::Int(2)),
        ("ts", s("2026-11-07T17:59:00Z")),
        ("ttl_s", J::Int(900)),
        (
            "payload",
            obj(vec![
                (
                    "cells",
                    obj(vec![("runtime-protocol", s("2337fc2fa053deadbeef"))]),
                ),
                (
                    "tol",
                    obj(vec![("eps", J::Int(0)), ("deadband", J::Int(0))]),
                ),
            ]),
        ),
        ("prev_hash", s(&"a".repeat(64))),
    ])
}

fn case2() -> J {
    obj(vec![
        ("v", J::Int(1)),
        ("src", s("клетка-роя")),
        ("dst", s("cell-swarm")),
        ("seq", J::Int(3)),
        ("ts", s("2026-11-07T18:00:00Z")),
        ("ttl_s", J::Int(60)),
        (
            "payload",
            obj(vec![
                ("s", s("a\"b\\c\nd\u{1}e\u{8}")),
                (
                    "list",
                    J::Arr(vec![
                        J::Int(1),
                        J::Int(2),
                        J::Int(3),
                        J::Bool(true),
                        J::Bool(false),
                        J::Null,
                    ]),
                ),
                ("empty", s("")),
                ("nested", obj(vec![("z", J::Arr(vec![])), ("a", obj(vec![]))])),
            ]),
        ),
        ("prev_hash", s(&"b".repeat(64))),
    ])
}

fn case3() -> J {
    obj(vec![
        ("v", J::Int(1)),
        ("src", s("polymath-os")),
        ("dst", s("cell-swarm")),
        ("seq", J::Int(1)),
        ("ts", s("2026-11-07T18:01:00Z")),
        ("ttl_s", J::Int(900)),
        (
            "payload",
            obj(vec![
                ("observed", s(&"c".repeat(40))),
                ("expected", s(&"d".repeat(40))),
                ("status", s("OBSERVED")),
                ("pulse", J::Int(3)),
            ]),
        ),
        ("prev_hash", s(&"e".repeat(64))),
    ])
}

fn assert_case(name: &str, body: &J) {
    let (bytes, hash) = read_golden(name);
    let canon = canonical_json(body);
    assert!(
        canon.as_bytes() == bytes.as_slice(),
        "ФАКТ: {name} — канон тропы не совпал с байтами ядра:\n тропа: {canon}\n ядро: {}",
        String::from_utf8_lossy(&bytes)
    );
    let mine = envelope_hash(body);
    assert!(
        mine == hash,
        "ФАКТ: {name} — хеш тропы {mine} не совпал с хешем ядра {hash}"
    );
}

#[test]
fn golden_bodies_match_python_byte_for_byte() {
    assert_case("case0", &case0());
    assert_case("case1", &case1());
    assert_case("case2", &case2());
    assert_case("case3", &case3());
}
