FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GUNICORN_CMD_ARGS="-b 0.0.0.0:8060" WEB_CONCURRENCY=2
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -r -u 10001 piebb && mkdir -p /data && chown piebb /data
USER piebb
EXPOSE 8060
CMD ["gunicorn", "--threads", "4", "-k", "gthread", "--timeout", "30", "--max-requests", "2000", "--max-requests-jitter", "200", "wsgi:app"]
