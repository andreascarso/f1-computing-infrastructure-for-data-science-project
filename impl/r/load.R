
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

load_tables <- function(data_dir = "data") {

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

# Convert remaining empty strings into NA
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

    cat(sprintf(
      "%-22s %9d rows %3d cols %8.4f s\n",
      name,
      nrow(tables[[name]]),
      ncol(tables[[name]]),
      timings[name]
    ))
  }

  return(list(tables = tables, timings = timings))
}

# Run the loading process
total_start <- proc.time()[["elapsed"]]

result <- load_tables("data")

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
