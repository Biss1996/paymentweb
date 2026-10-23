FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m gateway && mkdir /data && chown gateway /data
COPY . .
USER gateway
ENV DATABASE_PATH=/data/payments.db
EXPOSE 3001
CMD ["gunicorn", "--bind", "0.0.0.0:3001", "--workers", "2", "--threads", "2", "wsgi:app"]
