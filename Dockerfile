FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DB_PATH=/data/gutscheine.db \
    IOBROKER_URL=http://192.168.1.8:8087

COPY server.py ./server.py
COPY public ./public
RUN mkdir -p /data

EXPOSE 8080
VOLUME ["/data"]
CMD ["python", "server.py"]
