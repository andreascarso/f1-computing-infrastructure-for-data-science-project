
# F1 Project - R implementation
# Section 2: Data loading with data.table

library(data.table)

table_names <- c(
  "results", "races", "drivers", "constructors",
  "qualifying", "status", "circuits",
  "constructor_results", "constructor_standings",
  "driver_standings", "lap_times", "pit_stops",
  "sprint_results", "seasons"
)

load_tables <- function(data_dir = "data", verbose = TRUE) {
  missing <- table_names[!file.exists(file.path(data_dir, paste0(table_names, ".csv")))]
  if (length(missing)) stop("Missing CSV files: ", paste(missing, collapse = ", "))

  tables <- list()
  timings <- numeric(length(table_names))
  names(timings) <- table_names

  for (name in table_names) {

    file_path <- file.path(data_dir, paste0(name, ".csv"))

    start <- proc.time()[["elapsed"]]

    tables[[name]] <- fread(
      file_path,
      na.strings = c("\\N", ""),
      strip.white = TRUE
    )

    # Convert remaining empty strings into NA (a loop over columns, not rows).
    for (col in names(tables[[name]])) {
      if (is.character(tables[[name]][[col]])) {
        set(
          tables[[name]],
          i = which(trimws(tables[[name]][[col]]) == ""),
          j = col,
          value = NA_character_
        )
      }
    }
    timings[name] <- proc.time()[["elapsed"]] - start

    if (verbose) cat(sprintf(
      "%-22s %9d rows %3d cols %8.4f s\n",
      name,
      nrow(tables[[name]]),
      ncol(tables[[name]]),
      timings[name]
    ))
  }

  return(list(tables = tables, timings = timings))
}

# Running directly preserves the original loading and missing-value report.
if (sys.nframe() == 0L) {
  # Run the loading process
  total_start <- proc.time()[["elapsed"]]

  args <- commandArgs(trailingOnly = TRUE)
  result <- load_tables(if (length(args)) args[1] else "data")

  total_time <- proc.time()[["elapsed"]] - total_start

  cat("\n----------------------------------------\n")
  cat(sprintf("Sum of table loads: %.4f s\n",
              sum(result$timings)))
  cat(sprintf("Total wall-clock time: %.4f s\n",
              total_time))


  # Section 2.1 - Missing values check

  cat("\nMissing values by dataset:\n")

  for (name in table_names) {
    missing <- sum(is.na(result$tables[[name]]))

    cat(sprintf(
      "%-22s %9d missing values\n",
      name,
      missing
    ))
  }

}
