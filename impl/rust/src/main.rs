//! F1 port in Rust: notebook Section 2 (load), the cleaning that 6.4 needs,
//! 6.4 (driver/constructor form) and 5.1 (Q1). Same algorithm as the C port
//! in impl/c/, so the two compare safe and unsafe compiled code.
//!
//! Usage:
//!   cargo build --release --manifest-path impl/rust/Cargo.toml
//!   impl/rust/target/release/f1 data/
//!   impl/rust/target/release/f1 data/ --out bench/outputs/rust.csv   # for bench/compare_outputs.py
//!   impl/rust/target/release/f1 data/ --q1-out bench/outputs/rust_q1.csv

mod form;
mod q1;
mod table;

use std::collections::HashMap;
use std::path::Path;
use std::process::ExitCode;
use std::time::Instant;

use table::Table;

/// Section 2: the 14 tables, same list and order as impl/python/load.py
const TABLE_NAMES: [&str; 14] = [
    "results", "races", "drivers", "constructors", "qualifying", "status",
    "circuits", "constructor_results", "constructor_standings",
    "driver_standings", "lap_times", "pit_stops", "sprint_results", "seasons",
];

/// Read all tables, timing each one. Returns the tables by name and the total seconds.
fn load_tables(data_dir: &Path) -> Result<(HashMap<&'static str, Table>, f64), Box<dyn std::error::Error>> {
    let mut tables = HashMap::new();
    let mut total = 0.0;
    for name in TABLE_NAMES {
        let start = Instant::now();
        let table = Table::read(&data_dir.join(format!("{name}.csv")))?;
        let seconds = start.elapsed().as_secs_f64();
        total += seconds;
        println!("{name:<22} {:>9} rows  {:>3} cols  {seconds:8.4} s", table.nrows(), table.ncols());
        tables.insert(name, table);
    }
    println!("{:<22} {total:>34.4} s", "load total");
    Ok((tables, total))
}

/// Lookup from an id (raceId, statusId, ...) to the row with that id. Ids are
/// small non-negative integers (also in the scaled sets), so a Vec indexed by
/// the id is the simplest hash table: one memory access.
struct IdIndex(Vec<Option<usize>>);

impl IdIndex {
    fn new(t: &Table, column: &str) -> IdIndex {
        let col = t.col(column);
        let ids: Vec<usize> = (0..t.nrows()).map(|r| t.req(r, col) as usize).collect();
        let mut index = vec![None; ids.iter().max().map_or(0, |m| m + 1)];
        for (row, &id) in ids.iter().enumerate() {
            assert!(index[id].replace(row).is_none(), "{column}: duplicate id {id}");
        }
        IdIndex(index)
    }

    fn get(&self, id: i64) -> Option<usize> {
        self.0.get(usize::try_from(id).ok()?).copied().flatten()
    }
}

/// "2009-03-29" -> 20090329: sorts like the date, no time library needed
fn parse_date(s: &str) -> i32 {
    let parts: Vec<i32> = s.split('-').map(|p| p.parse().expect("bad date")).collect();
    assert_eq!(parts.len(), 3, "bad date '{s}'");
    parts[0] * 10000 + parts[1] * 100 + parts[2]
}

// ---------------------------------------------------------------------------
// 4.9: results_clean, only the part that 6.4 and Q1 need
// ---------------------------------------------------------------------------

/// One row of results_clean, joined with its race (circuit, year, date)
#[allow(dead_code)] // result_id and round are kept for parity with the C struct
struct RaceResult {
    result_id: i64,
    race_id: i64,
    driver_id: i64,
    constructor_id: i64,
    circuit_id: i64,
    year: i32,
    round: i32,
    date: i32, // yyyymmdd
    grid: i64,
    position_order: i64,
    podium: i64,
}

/// 4.8: the status names that mean the car never started the race
const DID_NOT_START: [&str; 3] = ["Did not qualify", "Did not prequalify", "Withdrew"];

/// Qualifying row by (raceId, driverId), shared by 4.9.4 and Q1
type QualiIndex = HashMap<(i64, i64), usize>;

fn index_qualifying(quali: &Table) -> QualiIndex {
    let (c_race, c_driver) = (quali.col("raceId"), quali.col("driverId"));
    let mut index = HashMap::with_capacity(quali.nrows());
    for r in 0..quali.nrows() {
        let key = (quali.req(r, c_race), quali.req(r, c_driver));
        assert!(index.insert(key, r).is_none(), "qualifying: duplicate {key:?}");
    }
    index
}

fn clean_results(tables: &HashMap<&str, Table>, quali_index: &QualiIndex) -> Vec<RaceResult> {
    let res = &tables["results"];
    let races = &tables["races"];
    let status = &tables["status"];
    let quali = &tables["qualifying"];
    let n = res.nrows();

    let (c_result, c_race, c_driver, c_cons, c_number, c_grid, c_order, c_status) = (
        res.col("resultId"), res.col("raceId"), res.col("driverId"), res.col("constructorId"),
        res.col("number"), res.col("grid"), res.col("positionOrder"), res.col("statusId"),
    );
    let (q_number, q_cons) = (quali.col("number"), quali.col("constructorId"));

    // 4.9.4 One row per driver per race. A driver can have several rows in a
    // race (shared drives in the 1950s). Keep the row whose car number and
    // constructor match the driver's qualifying entry, if exactly one row
    // matches; otherwise the first row in the file. The race result itself is
    // never used, so the choice does not leak the target.
    let mut keys: Vec<(i64, i64, usize)> =
        (0..n).map(|r| (res.req(r, c_race), res.req(r, c_driver), r)).collect();
    keys.sort_unstable(); // (raceId, driverId, file order)

    let mut chosen = Vec::with_capacity(n);
    let (mut n_single, mut n_matched, mut n_fallback) = (0, 0, 0);
    for group in keys.chunk_by(|a, b| (a.0, a.1) == (b.0, b.1)) {
        let (race_id, driver_id, first_row) = group[0];
        if group.len() == 1 {
            n_single += 1;
            chosen.push(first_row);
            continue;
        }
        // An Option is never equal to a missing number: None == None would be,
        // so missing numbers are filtered out first (NaN != NaN in pandas)
        let matches: Vec<usize> = match quali_index.get(&(race_id, driver_id)) {
            None => Vec::new(),
            Some(&q) => group
                .iter()
                .map(|&(_, _, r)| r)
                .filter(|&r| {
                    res.int(r, c_number).is_some()
                        && res.int(r, c_number) == quali.int(q, q_number)
                        && res.int(r, c_cons) == quali.int(q, q_cons)
                })
                .collect(),
        };
        if matches.len() == 1 {
            n_matched += 1;
            chosen.push(matches[0]);
        } else {
            n_fallback += 1;
            chosen.push(first_row);
        }
    }
    let after_dedup = chosen.len();

    // 4.9.7 Remove non-starters and the Indianapolis 500 (1950-1960, a
    // separate race with its own field)
    let status_index = IdIndex::new(status, "statusId");
    let race_index = IdIndex::new(races, "raceId");
    let s_name = status.col("status");
    let (r_name, r_circuit, r_year, r_round, r_date) = (
        races.col("name"), races.col("circuitId"), races.col("year"), races.col("round"), races.col("date"),
    );

    let mut out = Vec::with_capacity(after_dedup);
    let mut after_starters = 0;
    for r in chosen {
        let sr = status_index.get(res.req(r, c_status)).expect("unknown statusId");
        if DID_NOT_START.contains(&status.get(sr, s_name).unwrap_or("")) {
            continue;
        }
        after_starters += 1;

        let race_id = res.req(r, c_race);
        let rr = race_index.get(race_id).unwrap_or_else(|| panic!("unknown raceId {race_id}"));
        if races.get(rr, r_name) == Some("Indianapolis 500") {
            continue;
        }

        // 4.9.11 Target: a podium is a finishing position of 1-3
        let position_order = res.req(r, c_order);
        out.push(RaceResult {
            result_id: res.req(r, c_result),
            race_id,
            driver_id: res.req(r, c_driver),
            constructor_id: res.req(r, c_cons),
            circuit_id: races.req(rr, r_circuit),
            year: races.req(rr, r_year) as i32,
            round: races.req(rr, r_round) as i32,
            date: parse_date(races.get(rr, r_date).expect("missing race date")),
            grid: res.req(r, c_grid),
            position_order,
            podium: (1..=3).contains(&position_order) as i64,
        });
    }

    println!("results: raw results                  {n:>8} rows");
    println!(
        "results: after duplicate resolution   {after_dedup:>8} rows  \
         ({n_single} single, {n_matched} qualifying match, {n_fallback} fallback)"
    );
    println!("results: after removing non-starters  {after_starters:>8} rows");
    println!("results: after excluding Indy 500     {:>8} rows", out.len());
    out
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let (mut data_dir, mut out_path, mut q1_path) = (None, None, None);
    let mut rest = args.iter().skip(1);
    while let Some(arg) = rest.next() {
        match arg.as_str() {
            "--out" => out_path = rest.next(),
            "--q1-out" => q1_path = rest.next(),
            _ if data_dir.is_none() && !arg.starts_with('-') => data_dir = Some(arg),
            _ => {
                data_dir = None;
                break;
            }
        }
    }
    let Some(data_dir) = data_dir else {
        eprintln!("usage: {} DATA_DIR [--out CSV] [--q1-out CSV]", args[0]);
        return ExitCode::from(2);
    };
    let (tables, t_parse) = match load_tables(Path::new(data_dir)) {
        Ok(loaded) => loaded,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::FAILURE;
        }
    };

    let start = Instant::now();
    let quali_index = index_qualifying(&tables["qualifying"]);
    let results = clean_results(&tables, &quali_index);
    let t_clean = start.elapsed().as_secs_f64();

    let start = Instant::now();
    let form = form::build_form(&results);
    let t_form = start.elapsed().as_secs_f64();

    let with_driver_form = form.iter().filter(|f| f.drv_podium_rate_last5.is_some()).count();
    let with_team_form = form.iter().filter(|f| f.cons_podiums_last5.is_some()).count();
    println!("6.4 rows with driver form {with_driver_form:>8}");
    println!("6.4 rows with team form   {with_team_form:>8}");

    let start = Instant::now();
    let answer = q1::q1(&tables, &results, &quali_index);
    let t_q1 = start.elapsed().as_secs_f64();

    for (def, table) in answer.by.iter().enumerate() {
        println!(
            "Q1 by {:<10} {:>2} circuits, rank correlation between eras {:+.2}",
            q1::FRONT_ROW_BY[def],
            table.len(),
            q1::rank_correlation(table)
        );
    }

    println!("\n{:<22} {t_parse:8.4} s", "parse (Section 2)");
    println!("{:<22} {t_clean:8.4} s", "clean (4.9)");
    println!("{:<22} {t_form:8.4} s", "form (6.4)");
    println!("{:<22} {t_q1:8.4} s", "Q1 (5.1)");
    println!("{:<22} {:8.4} s", "compute total", t_clean + t_form + t_q1);

    if let Some(path) = out_path {
        if let Err(e) = form::write_output(Path::new(path), &results, &form) {
            eprintln!("{path}: {e}");
            return ExitCode::FAILURE;
        }
        println!("\nWrote {path} (output format for bench/compare_outputs.py)");
    }
    if let Some(path) = q1_path {
        if let Err(e) = q1::write_q1(Path::new(path), &answer) {
            eprintln!("{path}: {e}");
            return ExitCode::FAILURE;
        }
        println!("Wrote {path} (same layout as q1.csv from impl/python/questions.py --out)");
    }
    ExitCode::SUCCESS
}
