# bench-harnesses

Vendored benchmark harnesses with Keyten engine support, used by
[keyten-benchmarks](https://github.com/k10works/keyten-benchmarks) to produce
the numbers on [bench.k10.works](https://bench.k10.works/). Each harness is
carried here verbatim, plus the Keyten engine/query additions, until the
corresponding upstream pull requests are accepted — at which point the
runners will point back upstream.

| Dir | Upstream | Base | Additions |
| --- | --- | --- | --- |
| `taq/` | [singaraiona/NYSETAQBenchmarks](https://github.com/singaraiona/NYSETAQBenchmarks) | `19a9a64` | Keyten query engine (`pysrc/queryrunner/executors/inmemory/keyten.py` and wiring) |
| `pdsh/` | [pola-rs/polars-benchmark](https://github.com/pola-rs/polars-benchmark) | `e0b0746` | Keyten query set (`queries/keyten/`) |
| `clickbench/` | [ClickHouse/ClickBench](https://github.com/ClickHouse/ClickBench) | — | Nothing vendored: the 43-query suite is self-contained in keyten-benchmarks (`adapters/clickbench-*`); this directory documents the dataset derivation |

Both vendored harnesses are Apache-2.0; their LICENSE files are preserved in
place. The Keyten additions are MIT (Rayforce Technologies Inc.), matching the
rest of the k10works tooling.
