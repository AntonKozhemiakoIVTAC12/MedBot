#!/usr/bin/env bash
set -euo pipefail

echo "==> Pulling latest code..."
git pull origin main

echo "==> Building containers..."
docker compose build --no-cache

echo "==> Restarting services..."
docker compose up -d

echo "==> Cleaning up old images..."
docker image prune -f

echo "==> Current status:"
docker compose ps

echo "==> Deploy complete!"
