# Bench container: runs the implementations for bench/benchmark.py.
# It idles (sleep infinity) and the harness starts each run with `docker exec`,
# so every run is a fresh process while `docker stats` keeps sampling the same
# container. impl/, data/ and bench/ are mounted at run time (docker-compose.yml),
# so editing code or data does not need a rebuild.
FROM python:3.13-slim

WORKDIR /work

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

CMD ["sleep", "infinity"]
