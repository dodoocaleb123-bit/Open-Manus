FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir "pytest>=8.3,<9"

COPY runner-entrypoint.sh /usr/local/bin/runner-entrypoint
RUN chmod 0555 /usr/local/bin/runner-entrypoint

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/runner-entrypoint"]
CMD ["python", "-c", "import time; time.sleep(10**8)"]
