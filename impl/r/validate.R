# Synthetic checks, followed optionally by unrounded diagnostics for Python.
script <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE)[1])
script_dir <- dirname(normalizePath(script))
for (file in c("load.R", "clean.R", "features.R", "questions.R")) source(file.path(script_dir, file))
setDTthreads(1)
stopifnot(is.na(past_rolling_mean(1)),
          isTRUE(all.equal(past_rolling_mean(1:7), c(NA, 1, 1.5, 2, 2.5, 3, 4))),
          isTRUE(all.equal(past_rolling_mean(c(1, NA, 3)), c(NA, 1, 1))),
          isTRUE(all.equal(past_expanding_mean(c(1, NA, 3)), c(NA, 1, 1))))
# Unequal team sizes distinguish a mean of race means from driver pooling.
base <- data.table(resultId = 1:5, raceId = c(1L,1L,2L,3L,3L),
                   driverId = c(1L,2L,1L,1L,2L), constructorId = 1L,
                   date = as.IDate(c("2020-01-01","2020-01-01","2020-02-01","2021-01-01","2021-01-01")),
                   circuitId = c(1L,1L,2L,1L,1L), positionOrder = c(1,9,3,8,4),
                   podium = c(1L,0L,1L,0L,0L))
form <- add_form_features(base)
check_leakage(form)
stopifnot(form[raceId == 3L & driverId == 1L, cons_avg_finish_last5] == 4,
          form[raceId == 3L & driverId == 1L, drv_prev_races] == 2L,
          form[raceId == 3L & driverId == 1L, drv_circuit_podium_rate] == 1)
# Mutating the current/future race cannot change features through that race.
changed <- copy(base)
changed[raceId == 3L, `:=`(podium = 1L, positionOrder = 1)]
stopifnot(identical(form[, ..FORM_COLUMNS], add_form_features(changed)[, ..FORM_COLUMNS]))
changed <- copy(base)
changed[raceId == 2L, `:=`(podium = 0L, positionOrder = 20)]
stopifnot(identical(form[raceId <= 2L, ..FORM_COLUMNS],
                    add_form_features(changed)[raceId <= 2L, ..FORM_COLUMNS]))
# A same-date prior entry is rejected by the strict source-date guard.
bad <- copy(form)
bad[3, drv_form_source_date := date]
stopifnot(inherits(try(check_leakage(bad), silent = TRUE), "try-error"))
# Cleaning: unique qualifying match wins; multiple/no matches use file order.
fixture <- list(
  results = data.table(resultId = 1:8, raceId = c(1L,1L,2L,2L,3L,3L,4L,5L),
                       driverId = 1L, constructorId = c(1L,2L,1L,1L,1L,2L,1L,1L),
                       number = c(1L,2L,1L,1L,1L,2L,1L,1L), statusId = c(rep(1L,6),2L,1L),
                       positionOrder = 1:8, grid = 1L, points = 0, laps = 1L),
  races = data.table(raceId = 1:5, year = 2020L, circuitId = 1L,
                    date = sprintf("2020-01-%02d",1:5), name = c(rep("Race",4),"Indianapolis 500")),
  qualifying = data.table(raceId = 1:3, driverId = 1L, number = c(2L,1L,99L), constructorId = c(2L,1L,1L)),
  status = data.table(statusId = 1:2, status = c("Finished","Withdrew")),
  circuits = data.table(circuitId = 1L, name = "Circuit"))
original <- copy(fixture$results)
selected <- clean_tables(fixture)
stopifnot(identical(fixture$results, original), identical(selected$results$resultId, c(2L,3L,5L)))
# Q1: ten starts in both eras required; missing qualifying never front row.
qfixture <- list(results = data.table(raceId = 1:40, driverId = 1L, grid = 1L, podium = 1L),
                 races = data.table(raceId = 1:40, year = rep(c(2000L,2020L),each=20), circuitId = rep(rep(1:2,each=10),2)),
                 circuits = data.table(circuitId = 1:2, name = c("A","B")),
                 qualifying = data.table(raceId = c(1:10,21:29),driverId = 1L,position = 1L))
qtest <- q1(qfixture)
stopifnot(nrow(qtest) == 4L, all(qtest$front_row_by == "grid"),
          all(is.na(q1_correlations(qtest)$correlation)))
tie_rates <- data.table(front_row_by = "grid", circuit = rep(c("A","B","C"), 2),
                        era = rep(Q1_ERAS, each = 3), rate = c(10,10,30,10,20,30))
stopifnot(abs(q1_correlations(tie_rates)[front_row_by == "grid", correlation] - sqrt(0.75)) < 1e-12)
cat("PASS: short/missing windows, career/circuit history, season continuity, team weighting,\n",
    "current/future mutations, strict dates, duplicate selection, raw preservation, Q1 thresholds/ties.\n")
args <- commandArgs(trailingOnly = TRUE)
if (length(args)) {
  stopifnot(length(args) == 2L)
  loaded <- load_tables(args[1], verbose = FALSE)$tables
  clean <- clean_tables(loaded)
  df <- add_form_features(base_table(clean))
  check_leakage(df)
  dir.create(args[2], recursive = TRUE, showWarnings = FALSE)
  missing <- rbindlist(lapply(names(loaded), function(name) {
    data.table(dataset = name, column = names(loaded[[name]]),
               missing = vapply(loaded[[name]], function(x) sum(is.na(x)), integer(1)))
  }))
  fwrite(missing, file.path(args[2], "r_missing.csv"))
  fwrite(clean$results[, .(raceId, driverId, resultId, selection_method)], file.path(args[2], "r_selection.csv"))
  setorder(df, raceId, driverId)
  fwrite(df, file.path(args[2], "r_diagnostics.csv"), na = "")
  fwrite(q1_correlations(q1(clean)), file.path(args[2], "r_correlations.csv"), na = "")
}
