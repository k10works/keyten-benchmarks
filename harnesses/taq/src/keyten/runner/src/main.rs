//! NYSE TAQ dataset preparation for keyten.
//!
//! Converts the suite's PSV files into sorted native tables:
//!   keyten-runner --prepare --csvdir <dir> --dst <dir>
//!
//! Benchmarking runs through the shared python queryrunner
//! (pysrc/queryrunner, -engine keyten) via the keyten python bindings.

use std::collections::HashMap;

mod prepare;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut opts: HashMap<String, String> = HashMap::new();
    let mut flags: Vec<String> = Vec::new();
    let mut i = 0;
    while i < args.len() {
        if let Some(name) = args[i].strip_prefix("--") {
            if i + 1 < args.len() && !args[i + 1].starts_with("--") {
                opts.insert(name.to_string(), args[i + 1].clone());
                i += 2;
            } else {
                flags.push(name.to_string());
                i += 1;
            }
        } else {
            i += 1;
        }
    }
    let get = |k: &str| -> String {
        opts.get(k).unwrap_or_else(|| panic!("missing --{k}")).clone()
    };
    if !flags.iter().any(|f| f == "prepare") {
        eprintln!("keyten-runner: only --prepare is supported (benchmarks run via pysrc/queryrunner)");
        std::process::exit(2);
    }
    prepare::prepare(&get("csvdir"), &get("dst"));
}
