# Only the cleaning needed by Section 6.4 and Q1; raw tables stay unchanged.
assert_unique <- function(x, keys) {
  stopifnot(!anyDuplicated(x, by = keys))
}

clean_tables <- function(tables) {
  keys <- c("raceId", "driverId")
  races <- copy(tables$races)
  races[, date := as.IDate(date)]
  stopifnot(!anyNA(races$date), all(as.integer(format(races$date, "%Y")) == races$year))
  assert_unique(races, "raceId")
  qualifying <- copy(tables$qualifying)
  assert_unique(qualifying, keys)
  circuits <- copy(tables$circuits)
  assert_unique(circuits, "circuitId")
  # These names are the text fields consumed by this port. The supplied CSVs
  # have no HTML entities in them; refuse silent divergence on another dataset.
  stopifnot(!any(grepl("&(?:amp|quot|apos|lt|gt|#\\d+);", c(races$name, circuits$name), perl = TRUE)))
  work <- copy(tables$results)
  counts <- c(raw = nrow(work))
  work[, original_order := .I]
  work[, group_size := .N, by = keys]
  ref <- qualifying[, .(raceId, driverId, qual_number = number,
                         qual_constructor = constructorId)]
  work <- merge(work, ref, by = keys, all.x = TRUE, sort = FALSE)
  work[, car_match := !is.na(number) & !is.na(qual_number) &
         !is.na(constructorId) & !is.na(qual_constructor) &
         number == qual_number & constructorId == qual_constructor]
  work[, preferred := car_match & sum(car_match) == 1L, by = keys]
  setorderv(work, c(keys, "preferred", "original_order"), c(1L, 1L, -1L, 1L))
  work <- unique(work, by = keys)
  work[, selection_method := fcase(group_size == 1L, "single_record",
                                   preferred, "unique_qualifying_match",
                                   default = "original_entry_fallback")]
  counts <- c(counts, deduplicated = nrow(work))
  non_starters <- tables$status[status %in% c("Did not qualify", "Did not prequalify", "Withdrew"), statusId]
  work <- work[!statusId %in% non_starters]
  counts <- c(counts, starters = nrow(work))
  work <- work[!raceId %in% races[name == "Indianapolis 500", raceId]]
  counts <- c(counts, no_indy = nrow(work))
  work[, podium := as.integer(positionOrder <= 3L)]
  work[, c("original_order", "group_size", "qual_number", "qual_constructor", "car_match", "preferred") := NULL]
  setorderv(work, keys)
  assert_unique(work, keys)
  stopifnot(!anyNA(work$number), all(work$positionOrder >= 1),
            all(work$grid >= 0), all(work$points >= 0), all(work$laps >= 0),
            all(work$raceId %in% races$raceId), all(races$circuitId %in% circuits$circuitId),
            !anyNA(work$constructorId), !anyNA(work$driverId))
  list(results = work, races = races, qualifying = qualifying,
       circuits = circuits, counts = counts)
}
