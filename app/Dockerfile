# App: the Streamlit dashboard (app.py) on port 8501.
# It reads the question views and benchmark_results from the db service.
FROM python:3.13-slim

WORKDIR /app

# Dependencies first: this layer stays cached while only app.py changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8501
# Streamlit's own health endpoint; the image has no curl, so Python asks it
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

# 0.0.0.0: listen on the container's network, not only inside it, so the
# published port reaches it. headless: no browser to open, no e-mail prompt.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
