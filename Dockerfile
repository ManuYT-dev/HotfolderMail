# ── HotfolderMail ──────────────────────────────────────────────────────────────
FROM python:3.12-slim

# System deps (py-impose may need these for PDF processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create log directory (output no longer lands on local disk — it goes
# straight to the SMB share, so no local output dir is needed)
RUN mkdir -p logs

# Azure credentials — override these at runtime via docker run -e or .env file
ENV AZURE_CLIENT_ID=""
ENV AZURE_CLIENT_SECRET=""
ENV AZURE_TENANT_ID=""
ENV LOG_DIR="logs"

# SMB share credentials — override these at runtime via docker run -e or .env file
ENV SMB_SERVER=""
ENV SMB_SHARE=""
ENV SMB_USER=""
ENV SMB_PASSWORD=""
# Main folder INSIDE the SMB share where all customer subfolders get created
ENV OUTPUT_DIR="Auftraege"

CMD ["python", "main.py"]