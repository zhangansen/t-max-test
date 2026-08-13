# Local bake of harbor's check-template image (python:3.13-slim) with the
# claude-code agent pre-installed: skips harbor's runtime apt/npm setup.
FROM python:3.13-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl procps nodejs npm git bash \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g --registry=https://registry.npmmirror.com @anthropic-ai/claude-code \
    && claude --version
