//! 6.4: driver and constructor form, from past races only. Same algorithm
//! as impl/c/f1.c: sort by (group, date), then one pass with a sliding sum.

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

use crate::RaceResult;

/// "Last 5 races", as FORM_WINDOW in impl/python/features.py
const FORM_WINDOW: usize = 5;

/// The 6.4 features of one results row; `None` = no past race (empty in the CSV).
#[derive(Clone, Default)]
pub struct Form {
    pub drv_prev_races: Option<f64>,
    pub drv_podium_rate_last5: Option<f64>,
    pub drv_avg_finish_last5: Option<f64>,
    pub drv_career_podium_rate: Option<f64>,
    pub drv_circuit_podium_rate: Option<f64>,
    pub cons_podiums_last5: Option<f64>,
    pub cons_avg_finish_last5: Option<f64>,
}

/// Mean of the last (up to) FORM_WINDOW values of one group, kept as a
/// sliding sum: each new value is added and the value that leaves the
/// window is subtracted, so every step is O(1). This is pandas'
/// shift(1).rolling(5, min_periods=1).mean(): the window holds only past
/// values, and the mean is None before the first one.
#[derive(Default)]
struct Window {
    values: [f64; FORM_WINDOW],
    sum: f64,
    count: usize, // values in the window, at most FORM_WINDOW
    next: usize,  // ring-buffer slot the next value goes into
}

impl Window {
    fn mean(&self) -> Option<f64> {
        (self.count > 0).then(|| self.sum / self.count as f64)
    }

    fn push(&mut self, v: f64) {
        if self.count == FORM_WINDOW {
            self.sum -= self.values[self.next]; // the oldest value leaves
        } else {
            self.count += 1;
        }
        self.values[self.next] = v;
        self.sum += v;
        self.next = (self.next + 1) % FORM_WINDOW;
    }
}

fn rate(hits: i64, total: i64) -> Option<f64> {
    (total > 0).then(|| hits as f64 / total as f64)
}

/// Row indices sorted by (group, date, raceId): the order in which each
/// group's races happened. `group` is the driverId or constructorId.
fn time_order(res: &[RaceResult], group: impl Fn(&RaceResult) -> i64) -> Vec<usize> {
    let mut order: Vec<usize> = (0..res.len()).collect();
    order.sort_unstable_by_key(|&i| (group(&res[i]), res[i].date, res[i].race_id, i));
    order
}

/// Driver form: one pass over the rows sorted by (driverId, date).
fn driver_form(res: &[RaceResult], form: &mut [Form]) {
    // (driverId, circuitId) -> (podiums, races): a driver's record at one
    // circuit over the whole career
    let mut circuit: HashMap<(i64, i64), (i64, i64)> = HashMap::new();
    let mut podiums = Window::default();
    let mut finishes = Window::default();
    let (mut career_races, mut career_podiums) = (0, 0);

    let order = time_order(res, |r| r.driver_id);
    for (k, &i) in order.iter().enumerate() {
        let r = &res[i];
        if k == 0 || res[order[k - 1]].driver_id != r.driver_id {
            // a new driver: start from no history
            podiums = Window::default();
            finishes = Window::default();
            career_races = 0;
            career_podiums = 0;
        }
        let at_circuit = circuit.entry((r.driver_id, r.circuit_id)).or_insert((0, 0));

        // features first, from the races before this one ...
        let f = &mut form[i];
        f.drv_prev_races = Some(career_races as f64);
        f.drv_podium_rate_last5 = podiums.mean();
        f.drv_avg_finish_last5 = finishes.mean();
        f.drv_career_podium_rate = rate(career_podiums, career_races);
        f.drv_circuit_podium_rate = rate(at_circuit.0, at_circuit.1);

        // ... then add this race to the history (the shift(1) of pandas)
        podiums.push(r.podium as f64);
        finishes.push(r.position_order as f64);
        career_races += 1;
        career_podiums += r.podium;
        at_circuit.0 += r.podium;
        at_circuit.1 += 1;
    }
}

/// Constructor form: first one entry per team per race (podiums of both
/// cars, mean finish of the team's cars), then the same sliding window over
/// each team's races. Every car of the team gets its team's value.
fn constructor_form(res: &[RaceResult], form: &mut [Form]) {
    let order = time_order(res, |r| r.constructor_id);
    let mut podiums = Window::default();
    let mut finishes = Window::default();

    // chunk_by: the rows of one team in one race are next to each other
    let team_races = order.chunk_by(|&a, &b| {
        (res[a].constructor_id, res[a].race_id) == (res[b].constructor_id, res[b].race_id)
    });
    let mut previous_team = None;
    for rows in team_races {
        let team = res[rows[0]].constructor_id;
        if previous_team != Some(team) {
            podiums = Window::default();
            finishes = Window::default();
            previous_team = Some(team);
        }
        let (podiums_last5, finish_last5) = (podiums.mean(), finishes.mean());
        for &i in rows {
            form[i].cons_podiums_last5 = podiums_last5;
            form[i].cons_avg_finish_last5 = finish_last5;
        }
        let podium_sum: i64 = rows.iter().map(|&i| res[i].podium).sum();
        let finish_sum: i64 = rows.iter().map(|&i| res[i].position_order).sum();
        podiums.push(podium_sum as f64);
        finishes.push(finish_sum as f64 / rows.len() as f64);
    }
}

pub fn build_form(res: &[RaceResult]) -> Vec<Form> {
    let mut form = vec![Form::default(); res.len()];
    driver_form(res, &mut form);
    constructor_form(res, &mut form);
    form
}

/// Write the 6.4 columns in the shared format (README, "Output equivalence
/// check"): sorted by (raceId, driverId), 6 decimals, missing as empty.
pub fn write_output(path: &Path, res: &[RaceResult], form: &[Form]) -> std::io::Result<()> {
    let mut order: Vec<usize> = (0..res.len()).collect();
    order.sort_unstable_by_key(|&i| (res[i].race_id, res[i].driver_id));

    let mut out = BufWriter::new(File::create(path)?);
    // the 6.4 columns, in the canonical order of bench/compare_outputs.py
    writeln!(
        out,
        "raceId,driverId,drv_prev_races,drv_podium_rate_last5,drv_avg_finish_last5,\
         drv_career_podium_rate,drv_circuit_podium_rate,cons_podiums_last5,cons_avg_finish_last5"
    )?;
    for i in order {
        let f = &form[i];
        write!(out, "{},{}", res[i].race_id, res[i].driver_id)?;
        for v in [
            f.drv_prev_races,
            f.drv_podium_rate_last5,
            f.drv_avg_finish_last5,
            f.drv_career_podium_rate,
            f.drv_circuit_podium_rate,
            f.cons_podiums_last5,
            f.cons_avg_finish_last5,
        ] {
            match v {
                Some(x) => write!(out, ",{x:.6}")?,
                None => write!(out, ",")?, // NULL / NaN: an empty field
            }
        }
        writeln!(out)?;
    }
    out.flush()
}
