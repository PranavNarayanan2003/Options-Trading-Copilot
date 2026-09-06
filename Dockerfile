FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOST=0.0.0.0 PORT=8000 DATABASE_PATH=/data/alerts.sqlite3
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt
COPY . .
RUN mkdir -p /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import json,urllib.request; json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3))" || exit 1
CMD ["python","scripts/run_live.py"]
