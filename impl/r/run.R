# Usage: Rscript impl/r/run.R data/ --out bench/outputs/r.csv --q1-out bench/outputs/r_q1.csv --repeat 3
script <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE)[1])
script_dir <- dirname(normalizePath(script))
for (file in c("load.R", "clean.R", "features.R", "questions.R")) source(file.path(script_dir, file))
args <- commandArgs(trailingOnly = TRUE)
data_dir <- if (length(args)) args[1] else "data"
options <- args[-1]
get_option <- function(flag, default = NULL) {
  pos <- match(flag, options)
  if (is.na(pos)) return(default)
  if (pos == length(options)) stop("Missing value for ", flag)
  options[pos + 1L]
}
allowed <- c("--out", "--q1-out", "--repeat", "--threads")
if (length(options)) {
  stopifnot(length(options) %% 2L == 0L, all(options[seq(1L, length(options), 2L)] %in% allowed))
}
repeat_n <- as.integer(get_option("--repeat", "1"))
threads <- as.integer(get_option("--threads", "1"))
stopifnot(!is.na(repeat_n), repeat_n >= 1L, !is.na(threads), threads >= 1L)
setDTthreads(threads)
cat(R.version.string, "; data.table ", as.character(packageVersion("data.table")),
    "; threads ", getDTthreads(), "\n", sep = "")
elapsed <- function() proc.time()[["elapsed"]]
start <- elapsed()
loaded <- load_tables(data_dir, verbose = FALSE)
load_time <- elapsed() - start
start <- elapsed()
clean <- clean_tables(loaded$tables)
clean_time <- elapsed() - start
start <- elapsed()
base <- base_table(clean)
base_time <- elapsed() - start
form_times <- query_times <- numeric(repeat_n)
for (i in seq_len(repeat_n)) {
  start <- elapsed()
  form <- add_form_features(base)
  form_times[i] <- elapsed() - start
  start <- elapsed()
  answers <- q1(clean)
  query_times[i] <- elapsed() - start
}
# Checks and output are deliberately outside calculation timings.
check_leakage(form)
print(clean$counts)
print(data.table(step = c("load 14 tables", "clean required tables", "base join + sort", "rolling form (median)", "Q1 (median)"),
                 seconds = c(load_time, clean_time, base_time, median(form_times), median(query_times))))
cat("Form rows:", nrow(form), "; Q1 rows:", nrow(answers), "; repetitions:", repeat_n, "\n")
print(q1_correlations(answers))
out <- get_option("--out")
if (!is.null(out)) write_form_output(form, out)
q1_out <- get_option("--q1-out")
if (!is.null(q1_out)) {
  dir.create(dirname(q1_out), recursive = TRUE, showWarnings = FALSE)
  fwrite(answers, q1_out, na = "", eol = "\n")
}
