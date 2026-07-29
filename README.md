# HotfolderMail (Straussdruck Mail Processing)

An automated email pipeline that fetches print orders from an Azure-hosted Microsoft 365 mailbox, processes their attachments, and dynamically imposes PDFs onto SRA3 print sheets based on page count — fully unattended, running as a background Docker service.

---

## How it works

1. **Poll** — The service logs into a shared M365 mailbox via the Microsoft Graph API (app-only credentials flow) and checks a specific folder (default: `DRUCKAUFTRÄGE`) for new mail.
2. **Filter** — Only **unread** messages with attachments are picked up. Once an email has been fully processed, it's marked as read so it's never fetched again — this is the primary duplicate-prevention mechanism, backed up by a byte-for-byte content check on disk.
3. **Impose** — Each attachment is analyzed with `py-impose`:
   - Page count is read from the PDF.
   - Binding type is chosen automatically: **1–2 pages → Normal**, **3+ pages → Flyer**.
   - Bleed, tiling, and imposition onto an SRA3 sheet are calculated and rendered.
4. **Save** — Both the untouched original and the imposed PDF are written side-by-side into a per-customer folder on a mounted network share.
5. **Repeat** — The service sleeps, then polls again on a fixed interval, running forever until stopped.

---

## Features

- **Automated Mailbox Polling** — connects to Microsoft 365 via the Graph API using the app-only (client credentials) auth flow; no user login or MFA involved, suitable for a headless background service.
- **Smart, Read-Based Filtering** — fetches only unread emails from the configured folder, then marks each one read only *after* it has been fully processed without error. If processing fails partway through, the email is deliberately left unread so it gets retried on the next poll instead of silently disappearing.
- **On-the-Fly Conversion** — incoming image attachments (`.jpg`, `.jpeg`, `.png`) are converted into standardized PDFs before imposition.
- **Dynamic Imposition Pipeline**
  - Reads page count from incoming PDFs.
  - Automatically assigns binding type based on page count (Normal vs. Flyer).
  - Uses `py-impose` to calculate bleed, tile the artwork, and impose it onto SRA3 sheets.
- **Correct File Naming, Regardless of Input Type** — the imposed output is always saved with a `.pdf` extension, even if the original attachment was a `.docx`, `.jpg`, or anything else py-impose accepts as input. The original file keeps its native extension.
- **Non-Destructive** — the original customer file and the generated imposed PDF are saved side-by-side in a customer-specific folder; nothing is overwritten. If a same-named file already exists for the day, a random 4-digit suffix is appended to both filenames instead.
- **Duplicate-Safe** — before writing anything, incoming attachments are compared byte-for-byte against existing files for that sender; exact duplicates are skipped rather than re-saved.
- **Resilient Token Handling** — automatically detects an expired/failed Azure token refresh and re-authenticates from scratch rather than crashing the service.
- **Robust, Separated Logging** — granular, module-level logs split into application logs (`main.log`, `pipeline.log`) and third-party library logs (`py_impose.log`), written to a mounted `logs/` directory so they survive container restarts.

---

## Architecture notes

- **No SMB library in Python.** The pipeline previously connected to the network share directly via `smbprotocol`, but older file servers requiring SMBv1 (NT1) aren't compatible with modern Python SMB libraries. The share is now mounted at the OS/Docker level instead, and the pipeline just does plain local file I/O against that mount — faster and far more reliable.
- **Per-sender folder structure.** Every sender gets a stable output subfolder derived from the local part of their email address (sanitized to safe filesystem characters).
- **Continuous polling loop.** After an initial catch-up run (wider time window, higher limit), the service switches to a tighter, more frequent polling loop for near-real-time processing of new orders.

---

## Prerequisites

- **Docker** and **Docker Compose** (recommended way to run this — see below)
- Alternatively, for running outside Docker: **Python 3.10+**
- An **Azure App Registration** with:
  - Application API permission `Mail.Read` (Microsoft Graph)
  - Admin consent granted for that permission
  - A client secret generated for the app
- Network/filesystem access from the Docker host to the target output share (mounted as a Docker volume — see `docker-compose.yml`)

---

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/ManuYT-dev/HotfolderMail.git
   cd HotfolderMail
   ```

2. **Configure environment variables:**

   Copy the example file and fill in your own values:
   ```bash
   cp .env.example .env
   ```

   | Variable | Description |
   |---|---|
   | `AZURE_CLIENT_ID` | Application (client) ID from your Azure App Registration |
   | `AZURE_CLIENT_SECRET` | Client secret generated for the app registration |
   | `AZURE_TENANT_ID` | Your Azure AD tenant ID |
   | `LOG_DIR` | Directory (inside the container) where log files are written — defaults to `logs` |
   | `OUTPUT_DIR` | Path **inside the container** where processed customer folders and PDFs are saved (e.g. `/app/daten`). This must match the container-side path used in the volume mount below — **not** a Windows UNC path or SMB credentials; the network connection is handled entirely by the host OS. |
   | `HOST_AUFTRAG_PATH` | The real path on the host machine that gets mounted into the container at `OUTPUT_DIR` |

3. **Check the volume mount in `docker-compose.yml`:**
   ```yaml
   services:
     hotfoldermail:
       build: .
       env_file:
         - .env
       volumes:
         - ./logs:/app/logs
         - ${HOST_AUFTRAG_PATH}:${OUTPUT_DIR}
       restart: unless-stopped
   ```
   `HOST_AUFTRAG_PATH` (host side) and `OUTPUT_DIR` (container side) come straight from your `.env` file, so make sure both are set correctly before starting — this is what actually connects the container to your network share/mounted drive.

4. **Build and start the service:**
   ```bash
   docker compose up -d --build
   ```

5. **Check the logs to confirm it's running:**
   ```bash
   docker compose logs -f
   ```
   You should see the initial mailbox poll happen, followed by the service settling into its regular polling interval.

---

## Updating

To pull the latest changes, rebuild the image from scratch, and restart the service, use the included update script:

```bash
./update_hotfoldermail.sh
```

This stops the running container, does a `git fetch && git pull`, rebuilds with `--no-cache` (so dependency versions are freshly resolved rather than reused from a cached layer), and starts the service back up.

If you'd rather do it manually:
```bash
docker compose down
git fetch
git pull
docker compose build --no-cache
docker compose up -d
```

---

## Logs

Logs are written to `./logs` on the host (mounted from `/app/logs` in the container) and split by concern:

| File | Contents |
|---|---|
| `main.log` | Top-level service/startup logs |
| `pipeline.log` | Per-email, per-attachment processing details — imposition decisions, save paths, skip/duplicate/collision handling, read-marking |
| `py_impose.log` | Logs from the third-party `py-impose` library itself |

---

## Troubleshooting

- **`Host is down` / SMB errors on port 445:** Usually a TCP/firewall issue between the Docker host and the file server, not a code problem — verify the host itself can reach the share on port 445 before checking the container.
- **O365 token errors mid-run:** The service automatically detects a failed token auto-refresh and re-authenticates from scratch on the next polling cycle; no manual restart should be needed.
- **A file didn't get processed:** Check `pipeline.log` for that sender/subject — if an exception was raised, the source email is deliberately left **unread** so it will be retried automatically on the next poll.

---

## License

No license specified yet — all rights reserved by default until one is added.