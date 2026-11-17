Q1_ERAS <- c("1994–2013", "2014–2024")

q1 <- function(clean) {
  rows <- merge(clean$results[, .(raceId, driverId, grid, podium)],
                clean$races[, .(raceId, year, circuitId)], by = "raceId", all.x = TRUE)
  rows <- merge(rows, clean$circuits[, .(circuitId, circuit = name)],
                by = "circuitId", all.x = TRUE)
  rows <- merge(rows, clean$qualifying[, .(raceId, driverId, quali_position = position)],
                by = c("raceId", "driverId"), all.x = TRUE)
  stopifnot(nrow(rows) == nrow(clean$results))
  rows <- rows[year >= 1994]
  rows[, era := fifelse(year >= 2014, Q1_ERAS[2], Q1_ERAS[1])]
  front <- rbindlist(list(
    rows[grid %in% 1:2, .(front_row_by = "grid", circuit, era, podium)],
    rows[quali_position %in% 1:2, .(front_row_by = "qualifying", circuit, era, podium)]))
  rates <- front[, .(starts = .N, podiums = sum(podium)), by = .(front_row_by, circuit, era)]
  eligible <- rates[, .(keep = .N == 2L && all(starts >= 10L)), by = .(front_row_by, circuit)][keep == TRUE]
  rates <- rates[eligible, on = .(front_row_by, circuit), nomatch = 0L]
  rates[, `:=`(rate = podiums / starts * 100, keep = NULL)]
  setcolorder(rates, c("front_row_by", "circuit", "era", "starts", "podiums", "rate"))
  setorder(rates, front_row_by, circuit, era)
  rates
}

q1_correlations <- function(rates) {
  rbindlist(lapply(c("grid", "qualifying"), function(definition) {
    wide <- dcast(rates[front_row_by == definition], circuit ~ era, value.var = "rate")
    value <- NA_real_
    if (nrow(wide) >= 2L && all(Q1_ERAS %in% names(wide))) {
      ranks_a <- rank(wide[[Q1_ERAS[1]]], ties.method = "average")
      ranks_b <- rank(wide[[Q1_ERAS[2]]], ties.method = "average")
      if (sd(ranks_a) > 0 && sd(ranks_b) > 0) value <- cor(ranks_a, ranks_b)
    }
    data.table(front_row_by = definition, correlation = value)
  }))
}
