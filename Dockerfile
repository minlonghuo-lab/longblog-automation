FROM node:22-alpine

RUN apk add --no-cache \
    python3 py3-requests git openssh-client ca-certificates tzdata curl

WORKDIR /app
COPY service/ /app/service/
RUN chmod +x /app/service/*.py

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    LONGBLOG_RUNTIME_DIR=/data/runtime \
    LONGBLOG_REPO_DIR=/data/workspace/current \
    LONGBLOG_GIT_SSH_KEY=/data/ssh/id_ed25519

VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python3 /app/service/healthcheck.py

CMD ["python3", "/app/service/daemon.py"]
