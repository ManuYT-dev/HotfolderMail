#!/bin/bash
set -e

# Path to the HotfolderMail repo - adjust if needed
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

echo ">>> Stopping containers..."
docker compose down

echo ">>> Fetching and pulling latest changes..."
git fetch
git pull

echo ">>> Building with no cache..."
docker compose build --no-cache

echo ">>> Starting containers..."
docker compose up -d

echo ">>> Done."
docker compose ps
