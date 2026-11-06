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
