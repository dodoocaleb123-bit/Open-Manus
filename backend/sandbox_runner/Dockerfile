FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm util-linux tar \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir "pytest>=8.3,<9"

WORKDIR /workspace
CMD ["python", "-c", "import time; time.sleep(10**8)"]
