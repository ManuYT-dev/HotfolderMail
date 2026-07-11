# ── HotfolderMail ──────────────────────────────────────────────────────────────
FROM python:3.12-slim

# System deps (py-impose may need these for PDF processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create output and log directories
RUN mkdir -p data/output logs

# Azure credentials — override these at runtime via docker run -e or .env file
ENV AZURE_CLIENT_ID=""
ENV AZURE_CLIENT_SECRET=""
ENV AZURE_TENANT_ID=""
ENV LOG_DIR="logs"
ENV OUTPUT_DIR="data/output"

CMD ["python", "main.py"]