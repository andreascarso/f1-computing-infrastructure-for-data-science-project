//! One CSV table in memory, read with the `csv` crate.
//!
//! The F1 CSVs write a missing value as the text `\N`; empty fields are
//! missing too. Both come back as `None` from [`Table::get`], like pandas'
//! `read_csv(na_values=["\\N"])`. Quoted fields and `""` escapes are handled
//! by the `csv` crate.
//!
//! Layout: like the C port, all fields of the table live in one `String`,
//! and `ends[i]` is where field i ends (row-major). Reading reuses a single
//! `ByteRecord`, so there is no allocation per row; collecting one
//! `StringRecord` per row was about 2x slower (lap_times, 589k rows).

use std::error::Error;
use std::path::Path;

pub struct Table {
    pub header: Vec<String>,
    text: String,      // every field, back to back
    ends: Vec<usize>,  // end offset of each field in `text`
    nrows: usize,
}

impl Table {
    pub fn read(path: &Path) -> Result<Table, Box<dyn Error>> {
        let err = |e: csv::Error| format!("{}: {e}", path.display());
        let mut reader = csv::ReaderBuilder::new()
            .has_headers(true)
            .from_path(path)
            .map_err(err)?;
        let header: Vec<String> = reader.headers().map_err(err)?.iter().map(str::to_string).collect();

        let mut bytes = Vec::new();
        let mut ends = Vec::new();
        let mut nrows = 0;
        let mut record = csv::ByteRecord::new();
        // read_byte_record checks that every row has as many fields as the header
        while reader.read_byte_record(&mut record).map_err(err)? {
            for field in record.iter() {
                bytes.extend_from_slice(field);
                ends.push(bytes.len());
            }
            nrows += 1;
        }
        // One UTF-8 check for the whole table instead of one per field
        let text = String::from_utf8(bytes).map_err(|e| format!("{}: {e}", path.display()))?;
        Ok(Table { header, text, ends, nrows })
    }

    pub fn nrows(&self) -> usize {
        self.nrows
    }

    pub fn ncols(&self) -> usize {
        self.header.len()
    }

    /// Index of a column; panics with the column name if there is none.
    pub fn col(&self, name: &str) -> usize {
        self.header
            .iter()
            .position(|h| h == name)
            .unwrap_or_else(|| panic!("no column '{name}'"))
    }

    /// Field (row, col); `None` for `\N` and empty fields.
    pub fn get(&self, row: usize, col: usize) -> Option<&str> {
        let i = row * self.ncols() + col;
        let start = if i == 0 { 0 } else { self.ends[i - 1] };
        // Field boundaries came from whole fields, so they are char boundaries
        match &self.text[start..self.ends[i]] {
            "" | "\\N" => None,
            s => Some(s),
        }
    }

    /// Field as an integer; `None` when missing. Panics if it is not a number.
    pub fn int(&self, row: usize, col: usize) -> Option<i64> {
        self.get(row, col).map(|s| {
            s.parse().unwrap_or_else(|_| {
                panic!("row {}, column '{}': '{s}' is not an integer", row + 1, self.header[col])
            })
        })
    }

    /// Field as an integer that must be present (ids, positions).
    pub fn req(&self, row: usize, col: usize) -> i64 {
        self.int(row, col)
            .unwrap_or_else(|| panic!("row {}, column '{}' is missing", row + 1, self.header[col]))
    }
}
