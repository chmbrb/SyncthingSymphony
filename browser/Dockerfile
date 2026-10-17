FROM docker.io/restic/restic:latest
RUN apk add --no-cache python3
COPY server.py /app/server.py
ENTRYPOINT ["python3", "/app/server.py"]
