# f1-computing-infrastructure-for-data-science-project

## Python environment (local development)

The pandas scripts, the notebook and the Streamlit app run in a local virtual
environment. The loader and benchmark containers get their own copy of the same
pinned versions (see `loader/requirements.txt` and `bench/Dockerfile`).

**Requirements:** Python 3.11 or newer (developed on 3.13).

**Set up (once):**

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Every new terminal:** run `source .venv/bin/activate` again. The prompt shows
`(.venv)` when it is active. Leave the environment with `deactivate`.

**Use it in Jupyter / VS Code:** select the `.venv` interpreter (VS Code:
*Select Kernel → Python Environments → .venv*).

**Check that it works:**

```bash
python -c "import pandas, numpy, matplotlib, seaborn, psycopg, streamlit; print('ok')"
```

**Adding a library:** install it, then add it to `requirements.txt` with a pinned
version (`pip freeze | grep <name>`) in the same commit, so everyone stays in sync.
The `.venv/` folder is never committed.

## Database (Docker)

PostgreSQL 16 runs in a container defined in `docker-compose.yml`. Its data lives
in the named volume `dbdata`, so it survives restarts.

**Requirements:** Docker with Compose v2.

**Start (once per laptop):**

```bash
cp .env.example .env             # then edit .env and set your own password
docker compose up -d
docker compose ps                # wait until `db` shows (healthy)
```

`.env` holds the real password and is never committed; `.env.example` is the
template.

**Open a SQL prompt:**

```bash
docker compose exec db sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB'
```

From your laptop the database is on `localhost:5432`; from other containers in
the compose file it is on host `db`.

**Stop:**

```bash
docker compose down              # stops the container, keeps the data
docker compose down -v           # also deletes the data volume (fresh start)
```
