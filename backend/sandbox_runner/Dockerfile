FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir "pytest>=8.3,<9"

RUN useradd --system --uid 65534 --create-home --home-dir /tmp/runner runner
WORKDIR /workspace
USER 65534:65534
ENV HOME=/tmp
CMD ["python", "-c", "import time; time.sleep(10**8)"]
