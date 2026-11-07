//! blood_hash — CLI тропы роя: печать sha256 файла и самопроверка векторов.
//! Офлайн, std-only. Использование:
//!   blood_hash <файл>        — sha256 (hex) содержимого файла
//!   blood_hash --self-test   — векторы FIPS 180-2, код 0/1
use std::process::exit;

use rust_trail::sha256_hex;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(|s| s.as_str()) {
        Some("--self-test") => {
            let ok = sha256_hex(b"")
                == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                && sha256_hex(b"abc")
                    == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
            println!(
                "ФАКТ: самопроверка sha256 — {}",
                if ok { "ЗЕЛЁНАЯ" } else { "КРАСНАЯ" }
            );
            exit(if ok { 0 } else { 1 });
        }
        Some(path) => {
            let data = std::fs::read(path).unwrap_or_else(|e| {
                eprintln!("ФАКТ: файл не прочитан: {e}");
                exit(2);
            });
            println!("{}", sha256_hex(&data));
        }
        None => {
            eprintln!("ФАКТ: нет аргумента — blood_hash <файл> | --self-test");
            exit(2);
        }
    }
}
