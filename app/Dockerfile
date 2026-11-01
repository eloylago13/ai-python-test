FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REPOSITORY_BACKEND=sqlite \
    SQLITE_PATH=/code/data/notifications.db

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=10001:10001 . .

RUN mkdir -p /code/data && chown 10001:10001 /code/data

EXPOSE 5000
USER 10001:10001

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health/live')"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
