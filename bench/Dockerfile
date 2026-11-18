# Bench container: runs the implementations for bench/benchmark.py.
# It idles (sleep infinity) and the harness starts each run with `docker exec`,
# so every run is a fresh process while `docker stats` keeps sampling the same
# container.
#
# Multi-stage build: the C and Rust ports are compiled in two builder stages
# and only their binaries are copied into the final image, so neither gcc nor
# the Rust toolchain (about 1 GB together) ends up in the image that runs.
# The build context is the repository root (docker-compose.yml);
# bench/Dockerfile.dockerignore lets in only the files copied below.
#
# pandas (bench/run_pandas.py, impl/python/) and R (impl/r/) are interpreted
# and read from the mounted impl/ at run time, so editing them needs no rebuild.
# A change to the C or Rust source does: benchmark.py builds the image each time
# it starts, and Docker's cache makes that a no-op when nothing changed.

# --- C port: make -C impl/c (gcc -O2, as in its Makefile) ---------------------
FROM debian:trixie-slim AS c-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev make \
    && rm -rf /var/lib/apt/lists/*
COPY impl/c /src/c
RUN make -C /src/c

# --- Rust port: cargo build --release -----------------------------------------
FROM rust:1.96-slim-trixie AS rust-build
WORKDIR /src/rust
# Dependencies first, with an empty main: this layer stays cached while only
# src/ changes, so a code change does not download and compile the csv crate again.
COPY impl/rust/Cargo.toml impl/rust/Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs \
    && cargo build --release --locked \
    && rm -rf src
COPY impl/rust/src ./src
# touch: the copied main.rs can be older than the empty one built above
RUN touch src/main.rs && cargo build --release --locked

# --- Final image: Python + pandas, R + data.table, the two binaries ------------
# python:3.13-slim is Debian trixie too, so the binaries find the same glibc.
FROM python:3.13-slim

# r-base-core and r-cran-data.table are Debian's packages: prebuilt, no
# compiler needed (installing data.table from CRAN would compile it).
# time: GNU time, which reports the max RSS of the C, Rust and R processes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends r-base-core r-cran-data.table time \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

COPY bench/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Outside /work: the impl/ mount at /work/impl would hide them there.
COPY --from=c-build /src/c/build/f1 /opt/ports/f1-c
COPY --from=rust-build /src/rust/target/release/f1 /opt/ports/f1-rust

CMD ["sleep", "infinity"]
