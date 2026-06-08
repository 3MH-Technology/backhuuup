FROM ubuntu:22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    DOCKER_HOST=unix:///var/run/docker.sock

RUN apt-get update -qq && apt-get upgrade -y -qq && \
    apt-get install -y -qq --no-install-recommends \
        ca-certificates curl gnupg lsb-release \
        python3.11 python3.11-venv python3-pip \
        supervisor \
        iptables iproute2 bridge-utils \
        tini \
    && rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3.11 /usr/bin/python

RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list && \
    apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel

ENV PATH=/venv/bin:$PATH

COPY backend/requirements.txt /app/requirements.txt
RUN /venv/bin/pip install --no-cache-dir -r /app/requirements.txt && \
    rm /app/requirements.txt

COPY backend/ /app/
COPY entrypoint.sh /entrypoint.sh
COPY supervisord.conf /etc/supervisor/conf.d/wolfhost.conf

RUN chmod +x /entrypoint.sh

WORKDIR /app
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fs http://127.0.0.1:7860/ || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
