//! F1 port in Rust: notebook Section 2 (load), the cleaning that 6.4 needs,
//! 6.4 (driver/constructor form) and 5.1 (Q1). Same algorithm as the C port
//! in impl/c/, so the two compare safe and unsafe compiled code.
//!
//! Usage:
//!   cargo build --release --manifest-path impl/rust/Cargo.toml
//!   impl/rust/target/release/f1 data/

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

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: {} DATA_DIR", args[0]);
        return ExitCode::from(2);
    }
    let (_tables, t_parse) = match load_tables(Path::new(&args[1])) {
        Ok(loaded) => loaded,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::FAILURE;
        }
    };

    println!("\n{:<22} {t_parse:8.4} s", "parse (Section 2)");
    ExitCode::SUCCESS
}
