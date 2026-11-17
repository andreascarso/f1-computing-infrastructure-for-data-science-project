FORM_COLUMNS <- c("drv_prev_races", "drv_podium_rate_last5", "drv_avg_finish_last5",
                  "drv_career_podium_rate", "drv_circuit_podium_rate",
                  "cons_podiums_last5", "cons_avg_finish_last5")

base_table <- function(clean) {
  out <- merge(clean$results[, .(resultId, raceId, driverId, constructorId,
                                grid, positionOrder, podium)],
               clean$races[, .(raceId, year, round, date, circuitId)],
               by = "raceId", all.x = TRUE, sort = FALSE)
  setorder(out, date, raceId, driverId)
  stopifnot(nrow(out) == nrow(clean$results), !anyNA(out$date))
  out
}

past_rolling_mean <- function(x) {
  # Adaptive windows reproduce pandas min_periods=1 even for groups shorter
  # than five. Shift happens first, so the current result cannot contribute.
  previous <- shift(as.numeric(x))
  ans <- frollmean(previous, n = pmin(seq_along(previous), 5L),
                   adaptive = TRUE, na.rm = TRUE)
  ans[is.nan(ans)] <- NA_real_
  ans
}

past_expanding_mean <- function(x) {
  previous <- shift(as.numeric(x))
  n <- cumsum(!is.na(previous))
  total <- cumsum(fifelse(is.na(previous), 0, previous))
  fifelse(n > 0, total / n, NA_real_)
}

add_form_features <- function(base) {
  df <- copy(base)
  setorder(df, date, raceId, driverId)
  df[, `:=`(drv_prev_races = seq_len(.N) - 1L,
            drv_podium_rate_last5 = past_rolling_mean(podium),
            drv_avg_finish_last5 = past_rolling_mean(positionOrder),
            drv_career_podium_rate = past_expanding_mean(podium),
            drv_form_source_date = shift(date)), by = driverId]
  df[, `:=`(drv_circuit_podium_rate = past_expanding_mean(podium),
            drv_circuit_source_date = shift(date)), by = .(driverId, circuitId)]
  team <- df[, .(date = first(date), team_podiums = sum(podium),
                 team_avg_finish = mean(positionOrder)), by = .(constructorId, raceId)]
  setorder(team, date, raceId)
  team[, `:=`(cons_podiums_last5 = past_rolling_mean(team_podiums),
              cons_avg_finish_last5 = past_rolling_mean(team_avg_finish),
              cons_form_source_date = shift(date)), by = constructorId]
  df <- merge(df, team[, .(constructorId, raceId, cons_podiums_last5,
                           cons_avg_finish_last5, cons_form_source_date)],
              by = c("constructorId", "raceId"), all.x = TRUE, sort = FALSE)
  setorder(df, date, raceId, driverId)
  stopifnot(nrow(df) == nrow(base))
  assert_unique(df, c("raceId", "driverId"))
  df
}

check_leakage <- function(df) {
  for (column in c("drv_form_source_date", "drv_circuit_source_date", "cons_form_source_date")) {
    stopifnot(all(is.na(df[[column]]) | df[[column]] < df$date))
  }
  invisible(TRUE)
}

write_form_output <- function(df, path) {
  out <- copy(df[, c("raceId", "driverId", FORM_COLUMNS), with = FALSE])
  setorder(out, raceId, driverId)
  for (column in FORM_COLUMNS) {
    values <- out[[column]]
    set(out, j = column, value = fifelse(is.na(values), "", sprintf("%.6f", values)))
  }
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  fwrite(out, path, quote = FALSE, na = "", eol = "\n")
}
