FROM python:3.12-slim

# CLIs Cirdan can drive if the container is given access (docker socket, kubeconfig).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates docker.io \
    && curl -fsSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/v1.30.0/bin/linux/$(dpkg --print-architecture)/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/cirdan
COPY . .
RUN pip install --no-cache-dir ".[all]"

WORKDIR /workspace
VOLUME ["/workspace"]
EXPOSE 8090

ENTRYPOINT ["cirdand"]
CMD ["serve", "/workspace", "--http", "--mcp", "--host", "0.0.0.0", "--port", "8090"]
