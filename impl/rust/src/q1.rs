//! 5.1 Q1: which circuits convert a front-row start into a podium most
//! often, and did that change after 2014? Same steps as impl/c/f1.c.

use std::collections::{BTreeMap, HashMap};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

use crate::table::Table;
use crate::{IdIndex, QualiIndex, RaceResult};

const Q1_FIRST_YEAR: i32 = 1994; // qualifying data starts in 1994
const Q1_HYBRID_YEAR: i32 = 2014;
const MIN_STARTS_Q1: i64 = 10; // front-row starts per circuit in EACH era

pub const ERAS: [&str; 2] = ["1994–2013", "2014–2024"];
pub const FRONT_ROW_BY: [&str; 2] = ["grid", "qualifying"];

/// Front-row starts and podiums of one circuit in the two eras
#[derive(Default, Clone, Copy)]
pub struct Counts {
    pub starts: [i64; 2],
    pub podiums: [i64; 2],
}

impl Counts {
    fn kept(&self) -> bool {
        self.starts.iter().all(|&s| s >= MIN_STARTS_Q1)
    }

    /// same operation order as pandas: podiums / starts * 100
    pub fn rate(&self, era: usize) -> f64 {
        self.podiums[era] as f64 / self.starts[era] as f64 * 100.0
    }
}

/// Per definition (grid, qualifying): circuit name -> counts. A BTreeMap
/// keeps the circuits sorted by name, like the sorted pandas output.
pub struct Q1 {
    pub by: [BTreeMap<String, Counts>; 2],
}

pub fn q1(tables: &HashMap<&str, Table>, res: &[RaceResult], quali_index: &QualiIndex) -> Q1 {
    let circuits = &tables["circuits"];
    let quali = &tables["qualifying"];
    let circuit_index = IdIndex::new(circuits, "circuitId");
    let c_name = circuits.col("name");
    let q_position = quali.col("position");

    // Count per circuit row (circuits are few, so a dense Vec is the GROUP BY);
    // the names are attached once at the end
    let mut counts = [vec![Counts::default(); circuits.nrows()], vec![Counts::default(); circuits.nrows()]];
    for r in res.iter().filter(|r| r.year >= Q1_FIRST_YEAR) {
        let era = (r.year >= Q1_HYBRID_YEAR) as usize;
        let c = circuit_index.get(r.circuit_id).expect("unknown circuitId");

        // HashMap join with qualifying on (raceId, driverId): probe the map
        // built once from qualifying. LEFT JOIN: no qualifying row means no
        // qualifying position, so not on the front row by qualifying.
        let quali_position = quali_index
            .get(&(r.race_id, r.driver_id))
            .and_then(|&q| quali.int(q, q_position));

        let front = [matches!(r.grid, 1 | 2), matches!(quali_position, Some(1 | 2))];
        for def in 0..2 {
            if front[def] {
                counts[def][c].starts[era] += 1;
                counts[def][c].podiums[era] += r.podium;
            }
        }
    }
    // keep circuits with enough front-row starts in BOTH eras
    let by = counts.map(|per_circuit| {
        per_circuit
            .into_iter()
            .enumerate()
            .filter(|(_, c)| c.kept())
            .map(|(row, c)| (circuits.get(row, c_name).expect("circuit without a name").to_string(), c))
            .collect()
    });
    Q1 { by }
}

/// Average ranks (ties share the mean of their ranks, like pandas'
/// rank(method="average")); 1 = smallest.
fn average_ranks(values: &[f64]) -> Vec<f64> {
    values
        .iter()
        .map(|v| {
            let below = values.iter().filter(|&w| w < v).count();
            let equal = values.iter().filter(|&w| w == v).count();
            // the tied block takes ranks below+1 .. below+equal
            below as f64 + (equal + 1) as f64 / 2.0
        })
        .collect()
}

/// Spearman correlation of the circuits' rates between the two eras
/// (1 = same ranking): the Pearson correlation of the average ranks.
pub fn rank_correlation(table: &BTreeMap<String, Counts>) -> f64 {
    let before: Vec<f64> = table.values().map(|c| c.rate(0)).collect();
    let after: Vec<f64> = table.values().map(|c| c.rate(1)).collect();
    let (rb, ra) = (average_ranks(&before), average_ranks(&after));
    let n = rb.len() as f64;
    let (mb, ma) = (rb.iter().sum::<f64>() / n, ra.iter().sum::<f64>() / n);
    let (mut sab, mut sbb, mut saa) = (0.0, 0.0, 0.0);
    for (b, a) in rb.iter().zip(&ra) {
        sab += (b - mb) * (a - ma);
        sbb += (b - mb) * (b - mb);
        saa += (a - ma) * (a - ma);
    }
    sab / (sbb * saa).sqrt()
}

/// Q1 in the long format of impl/python/questions.py --out (q1.csv):
/// front_row_by, circuit, era, starts, podiums, rate; sorted by
/// (front_row_by, circuit, era). Rates with 6 decimals.
pub fn write_q1(path: &Path, answer: &Q1) -> std::io::Result<()> {
    let mut out = BufWriter::new(File::create(path)?);
    writeln!(out, "front_row_by,circuit,era,starts,podiums,rate")?;
    for (def, table) in answer.by.iter().enumerate() {
        // BTreeMap order = byte order of the UTF-8 names = Python's str order
        for (circuit, counts) in table {
            for era in 0..2 {
                // circuit names contain no comma or quote, so no CSV quoting
                writeln!(
                    out,
                    "{},{circuit},{},{},{},{:.6}",
                    FRONT_ROW_BY[def], ERAS[era], counts.starts[era], counts.podiums[era], counts.rate(era)
                )?;
            }
        }
    }
    out.flush()
}
