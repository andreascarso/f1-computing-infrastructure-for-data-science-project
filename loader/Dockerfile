# Loader: fills the database from the CSVs, prints the timings, then exits.
# The CSVs and schema.sql are mounted at run time (see docker-compose.yml),
# so changing the data does not need a rebuild.
FROM python:3.13-slim

WORKDIR /app

# Install dependencies first: this layer stays cached while only the script changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY load_db.py .

CMD ["python", "load_db.py"]
