# Multi-platform Dockerfile for ServiceHub
FROM python:3.11-slim

# Install procps (ps command), tini (init reaper), and curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY service_hub.py .

# Environment configuration
ENV PYTHONUNBUFFERED=1
ENV SERVICEHUB_CONFIG_DIR=/root/.config/service-hub

# Persistent volume for configuration and logs
VOLUME ["/root/.config/service-hub"]

# Expose Web Dashboard & REST API
EXPOSE 9099

# Use tini as PID 1 to reap zombie processes properly
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default launch command bound to all interfaces
CMD ["python", "service_hub.py", "--host", "0.0.0.0", "--port", "9099"]
