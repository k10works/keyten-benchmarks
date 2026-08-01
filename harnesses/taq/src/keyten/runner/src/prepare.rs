//! PSV -> sorted native tables. The suite's raw time fields are packed
//! HHMMSSnnnnnnnnn integers; the prepare converts them to keyten's Time
//! kind (ns since midnight) and sorts trade/quote by (sym, time) -- the
//! same presorted layout the other engines' prepared databases use.

use keyten::io::csv::CsvOptions;
use keyten::plan::expr::col;
use keyten::plan::SortKey;
use keyten::{DataFrame, Kind, LazyFrame, Series};

// (raw column, table column, explicit kind) -- kinds pinned per the suite's
// reference schemas rather than inferred, exactly as the other engines'
// prepares declare theirs. Time columns ingest as Int (packed
// HHMMSSnnnnnnnnn) and convert to the Time kind below.
const TRADE_COLS: &[(&str, &str, Kind)] = &[
    ("Time", "time", Kind::Int),
    ("Exchange", "ex", Kind::Str),
    ("Symbol", "sym", Kind::Str),
    ("Sale Condition", "cond", Kind::Str),
    ("Trade Volume", "size", Kind::Float),
    ("Trade Price", "price", Kind::Float),
    ("Trade Stop Stock Indicator", "stop", Kind::Str),
    ("Trade Correction Indicator", "corr", Kind::Int),
    ("Sequence Number", "seq", Kind::Int),
    ("Trade Id", "tradeId", Kind::Int),
    ("Source of Trade", "source", Kind::Str),
    ("Trade Reporting Facility", "tradeReportingFacility", Kind::Str),
    ("Participant Timestamp", "participantTimestamp", Kind::Int),
    ("Trade Reporting Facility TRF Timestamp", "tradeReportingFacilityTRFTimestamp", Kind::Int),
    // The reference casts this 0/1 field to boolean; kept integral here
    // (the engine's csv bool parser accepts true/false literals only).
    ("Trade Through Exempt Indicator", "tradeThroughExemptIndicator", Kind::Int),
];

const QUOTE_COLS: &[(&str, &str, Kind)] = &[
    ("Time", "time", Kind::Int),
    ("Exchange", "ex", Kind::Str),
    ("Symbol", "sym", Kind::Str),
    ("Bid_Price", "bid", Kind::Float),
    ("Bid_Size", "bsize", Kind::Int),
    ("Offer_Price", "ask", Kind::Float),
    ("Offer_Size", "asize", Kind::Int),
    ("Quote_Condition", "cond", Kind::Str),
    ("Sequence_Number", "seq", Kind::Int),
    ("National_BBO_Ind", "nationalBBOInd", Kind::Str),
    ("FINRA_BBO_Indicator", "finraBBOIndicator", Kind::Str),
    ("FINRA_ADF_MPID_Indicator", "finraADFMPIDIndicator", Kind::Str),
    ("Quote_Cancel_Correction", "corr", Kind::Str),
    ("Source_Of_Quote", "source", Kind::Str),
    ("Retail_Interest_Indicator", "retailInterestIndicator", Kind::Str),
    ("Short_Sale_Restriction_Indicator", "shortSaleRestrictionIndicator", Kind::Str),
    ("LULD_BBO_Indicator", "LULDBBOIndicator", Kind::Str),
    ("SIP_Generated_Message_Identifier", "SIPGeneratedMessageIdentifier", Kind::Str),
    ("National_BBO_LULD_Indicator", "nationalBBOLULDIndicator", Kind::Str),
    ("Participant_Timestamp", "participantTimestamp", Kind::Int),
    ("FINRA_ADF_Timestamp", "FINRAADFTimestamp", Kind::Int),
    ("FINRA_ADF_Market_Participant_Quote_Indicator", "FINRAADFMarketParticipantQuoteIndicator", Kind::Str),
    ("Security_Status_Indicator", "securityStatusIndicator", Kind::Str),
];

// The reference dataset stores these as 32-bit floats; quantizing on
// ingest keeps both engines querying identical values (full-precision text
// would otherwise make equality-sensitive queries diverge).
const F32_COLS: &[&str] = &["size", "price", "bid", "ask"];

// Renamed columns holding packed HHMMSSnnnnnnnnn times; each converts to
// the engine's Time kind (ns since midnight) during prepare, exactly like
// the reference's time64 casts.
const TIME_COLS: &[&str] = &[
    "time",
    "participantTimestamp",
    "tradeReportingFacilityTRFTimestamp",
    "FINRAADFTimestamp",
];

fn psv_opts(cols: &[(&str, &str, Kind)]) -> CsvOptions {
    CsvOptions {
        delimiter: b'|',
        has_header: true,
        infer_rows: 64,
        kind_overrides: cols.iter().map(|(raw, _, k)| (raw.to_string(), *k)).collect(),
    }
}

/// Packed HHMMSSnnnnnnnnn -> nanoseconds since midnight.
fn packed_to_ns(v: i64) -> i64 {
    let ns = v % 1_000_000_000;
    let s = (v / 1_000_000_000) % 100;
    let m = (v / 100_000_000_000) % 100;
    let h = v / 10_000_000_000_000;
    keyten::temporal::time_ns(h, m, s, ns)
}

/// The reference prepare's symbol cleanup: every whitespace run becomes a
/// single dot ("ZION P" -> "ZION.P").
fn normalize_sym(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut in_ws = false;
    for ch in s.chars() {
        if ch.is_whitespace() {
            in_ws = true;
        } else {
            if in_ws && !out.is_empty() {
                out.push('.');
            }
            in_ws = false;
            out.push(ch);
        }
    }
    out
}

fn map_str(c: &Series, name: &str, f: impl Fn(&str) -> String) -> Series {
    let vals = c.to_str().unwrap_or_else(|_| panic!("{name}: not a text column"));
    let mapped: Vec<Option<String>> = vals.into_iter().map(|o| o.map(|v| f(&v))).collect();
    let refs: Vec<Option<&str>> = mapped.iter().map(|o| o.as_deref()).collect();
    Series::str_opt(name, &refs)
}

fn convert_one(src: &str, cols: &[(&str, &str, Kind)], trim_cols: &[&str]) -> DataFrame {
    // Projection pushdown keeps unlisted columns unparsed entirely.
    let df = LazyFrame::scan_csv(src, psv_opts(cols))
        .select(cols.iter().map(|(raw, _, _)| col(raw)).collect())
        .collect()
        .unwrap_or_else(|e| panic!("failed to read {src}: {e}"));
    let mut out: Vec<Series> = Vec::new();
    for (raw, name, _) in cols {
        let c = df
            .column(raw)
            .unwrap_or_else(|_| panic!("{src}: missing column {raw}"));
        if TIME_COLS.contains(name) {
            let packed = c.to_int().unwrap_or_else(|_| panic!("{src}: {name} column not integral"));
            let ns: Vec<Option<i64>> = packed.into_iter().map(|o| o.map(packed_to_ns)).collect();
            out.push(Series::time_opt(name, &ns));
        } else if *name == "sym" {
            out.push(map_str(&c, name, normalize_sym));
        } else if F32_COLS.contains(name) {
            let vals = c.to_float().unwrap_or_else(|_| panic!("{name}: not a float column"));
            let quantized: Vec<Option<f64>> =
                vals.into_iter().map(|o| o.map(|x| x as f32 as f64)).collect();
            out.push(Series::float_opt(name, &quantized));
        } else if trim_cols.contains(name) {
            out.push(map_str(&c, name, |v| v.trim().to_string()));
        } else {
            out.push(c.renamed(name));
        }
    }
    DataFrame::from_columns(out).expect("assemble converted table")
}

/// Symbols flagged as test instruments in the master file are excluded
/// from trade/quote, matching the reference prepare's default.
fn test_symbols(csvdir: &str) -> std::collections::HashSet<String> {
    let mut out = std::collections::HashSet::new();
    let Some(master) = std::fs::read_dir(csvdir).ok().and_then(|rd| {
        rd.filter_map(|e| e.ok())
            .map(|e| e.path())
            .find(|p| p.file_name().unwrap().to_string_lossy().to_uppercase().contains("MASTER"))
    }) else {
        return out;
    };
    let body = std::fs::read_to_string(&master).expect("read master");
    let mut lines = body.lines();
    let header: Vec<&str> = lines.next().expect("master header").split('|').collect();
    let sym_i = header.iter().position(|h| *h == "Symbol").expect("Symbol col");
    let flag_i = header.iter().position(|h| *h == "Test_Symbol_Flag").expect("flag col");
    for line in lines {
        let f: Vec<&str> = line.split('|').collect();
        if f.len() > flag_i && f[flag_i].trim() == "Y" {
            // Stored normalized because the filter runs on the converted
            // sym column (the reference filters raw-vs-raw before its own
            // normalization; same membership either way).
            out.insert(normalize_sym(f[sym_i].trim()));
        }
    }
    out
}

fn drop_test_symbols(df: DataFrame, test: &std::collections::HashSet<String>) -> DataFrame {
    if test.is_empty() {
        return df;
    }
    let names = df.names();
    let sym = df.column("sym").unwrap().to_str().unwrap();
    let keep: Vec<bool> = sym
        .iter()
        .map(|s| s.as_deref().map_or(true, |v| !test.contains(v)))
        .collect();
    let mut cols: Vec<Series> = names.iter().map(|n| df.column(n).unwrap()).collect();
    cols.push(Series::bool("__keep", &keep));
    let filtered = DataFrame::from_columns(cols)
        .unwrap()
        .lazy()
        .filter(col("__keep").eq(keyten::plan::expr::lit(true)))
        .select(names.iter().map(|n| col(n)).collect())
        .collect()
        .expect("test-symbol filter");
    filtered
}

pub fn prepare(csvdir: &str, dst: &str) {
    std::fs::create_dir_all(dst).expect("create dst");
    let test = test_symbols(csvdir);
    eprintln!("prepare: {} test symbols excluded", test.len());
    let mut trades: Vec<String> = Vec::new();
    let mut quotes: Vec<String> = Vec::new();
    for entry in std::fs::read_dir(csvdir).expect("read csvdir") {
        let p = entry.unwrap().path();
        let name = p.file_name().unwrap().to_string_lossy().to_uppercase();
        if !name.ends_with(".PSV") && !name.ends_with(".CSV") {
            continue;
        }
        if name.contains("TRADE") {
            trades.push(p.to_string_lossy().into_owned());
        } else if name.contains("BBO") || name.contains("QUOTE") {
            quotes.push(p.to_string_lossy().into_owned());
        }
    }
    trades.sort();
    quotes.sort();
    assert!(!trades.is_empty(), "no trade files under {csvdir}");
    assert!(!quotes.is_empty(), "no quote files under {csvdir}");

    // Mirrors the reference prepare's trims: trade's condition column and
    // the quote FINRA BBO indicator arrive whitespace-padded in the raw psv.
    for (label, files, cols, trims) in [
        ("trade", &trades, TRADE_COLS, &["cond"][..]),
        ("quote", &quotes, QUOTE_COLS, &["finraBBOIndicator"][..]),
    ] {
        eprintln!("prepare: {label} from {} file(s)", files.len());
        let out_path = format!("{dst}/{label}.k10t");
        let mut first = true;
        for f in files {
            let part = drop_test_symbols(convert_one(f, cols, trims), &test);
            if first {
                part.write_native(&out_path).expect("write native");
                first = false;
            } else {
                part.append_native(&out_path).expect("append native");
            }
        }
        eprintln!("prepare: sorting {label} by (sym, time)");
        let sorted_path = format!("{dst}/{label}.sorted.k10t");
        LazyFrame::scan_native(&out_path)
            .sort(vec![
                SortKey { name: "sym".into(), descending: false },
                SortKey { name: "time".into(), descending: false },
            ])
            .collect_to_native(&sorted_path)
            .expect("sort to native");
        std::fs::remove_dir_all(&out_path).expect("drop unsorted");
        std::fs::rename(&sorted_path, &out_path).expect("swap sorted");
    }
    eprintln!("prepare: done -> {dst}");
}
