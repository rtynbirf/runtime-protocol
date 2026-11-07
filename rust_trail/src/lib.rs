//! Тропа роя (Rust) — первый шаг тропа, не шоссе (ADR-0005).
//!
//! Кровь роя живёт в Python (`blood.py`); тропа доказывает, что протокол
//! живой: клетку можно родить в другом языке, и байты совпадут байт-в-байт.
//! std-only: никаких крейтов, никакого сети — офлайн-first клетки.
//!
//! Доказывается золотыми пробами (`tests/golden_match.rs`), источник которых —
//! Python-ядро (`tools/make_golden.py` из `blood.py`):
//! 1. канонический JSON (сортировка ключей, компактные разделители,
//!    экранирование как в `json.dumps(..., ensure_ascii=False)`);
//! 2. sha256 — чистая реализация на std;
//! 3. `envelope_hash` — sha256 канонических байтов тела конверта без поля
//!    `hash` (само на себя не ссылается — ADR-0004).
use std::collections::BTreeMap;

/// Минимальное JSON-значение без чисел с плавающей точкой:
/// канон крови не содержит float (факты — целые, строки, логика).
#[derive(Clone, Debug, PartialEq)]
pub enum J {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Arr(Vec<J>),
    Obj(BTreeMap<String, J>),
}

/// Канонический JSON: порядок ключей не имеет значения, байты — имеют.
/// Эквивалент `json.dumps(obj, sort_keys=True, separators=(",", ":"),
/// ensure_ascii=False)` для значений без float.
pub fn canonical_json(v: &J) -> String {
    let mut s = String::new();
    write_j(v, &mut s);
    s
}

fn write_j(v: &J, s: &mut String) {
    match v {
        J::Null => s.push_str("null"),
        J::Bool(true) => s.push_str("true"),
        J::Bool(false) => s.push_str("false"),
        J::Int(i) => s.push_str(&i.to_string()),
        J::Str(x) => write_str(x, s),
        J::Arr(items) => {
            s.push('[');
            for (i, it) in items.iter().enumerate() {
                if i > 0 {
                    s.push(',');
                }
                write_j(it, s);
            }
            s.push(']');
        }
        J::Obj(map) => {
            // BTreeMap хранит ключи отсортированными — как sort_keys в Python.
            s.push('{');
            for (i, (k, val)) in map.iter().enumerate() {
                if i > 0 {
                    s.push(',');
                }
                write_str(k, s);
                s.push(':');
                write_j(val, s);
            }
            s.push('}');
        }
    }
}

fn write_str(x: &str, s: &mut String) {
    s.push('"');
    for c in x.chars() {
        match c {
            '"' => s.push_str("\\\""),
            '\\' => s.push_str("\\\\"),
            '\u{08}' => s.push_str("\\b"),
            '\t' => s.push_str("\\t"),
            '\n' => s.push_str("\\n"),
            '\u{0c}' => s.push_str("\\f"),
            '\r' => s.push_str("\\r"),
            c if (c as u32) < 0x20 => s.push_str(&format!("\\u{:04x}", c as u32)),
            c => s.push(c),
        }
    }
    s.push('"');
}

/// Хеш конверта крови: sha256 канонических байтов тела БЕЗ поля `hash`.
/// Вызывающий строит тело без `hash` (в Python — deepcopy+pop, ADR-0004).
pub fn envelope_hash(body: &J) -> String {
    sha256_hex(canonical_json(body).as_bytes())
}

/// sha256 → hex (lowercase), чистая реализация на std.
pub fn sha256_hex(data: &[u8]) -> String {
    sha256(data)
        .iter()
        .map(|b| format!("{:02x}", b))
        .collect()
}

const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

fn sha256(msg: &[u8]) -> [u8; 32] {
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    let bitlen = (msg.len() as u64).wrapping_mul(8);
    let mut m = msg.to_vec();
    m.push(0x80);
    while m.len() % 64 != 56 {
        m.push(0);
    }
    m.extend_from_slice(&bitlen.to_be_bytes());
    for chunk in m.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[4 * i],
                chunk[4 * i + 1],
                chunk[4 * i + 2],
                chunk[4 * i + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d) = (h[0], h[1], h[2], h[3]);
        let (mut e, mut f, mut g, mut hh) = (h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }
    let mut out = [0u8; 32];
    for i in 0..8 {
        out[4 * i..4 * i + 4].copy_from_slice(&h[i].to_be_bytes());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha256_empty_and_abc() {
        // Векторы FIPS 180-2 — тропа не идёт дальше, пока не совпали они.
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn canonical_sorted_and_compact() {
        let mut m = BTreeMap::new();
        m.insert("v".to_string(), J::Int(1));
        m.insert("src".to_string(), J::Str("модель".to_string()));
        let o = J::Obj(m);
        assert_eq!(canonical_json(&o), "{\"src\":\"модель\",\"v\":1}");
    }

    #[test]
    fn escaping_matches_python() {
        let s = J::Str("a\"b\\c\nd\u{1}e\u{8}f".to_string());
        assert_eq!(
            canonical_json(&J::Arr(vec![s])),
            "[\"a\\\"b\\\\c\\nd\\u0001e\\bf\"]"
        );
    }
}
