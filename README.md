# HotfolderMail (Straussdruck Mail Processing)

An automated email pipeline that fetches print orders from an Azure-hosted Microsoft 365 mailbox, processes attachments, and dynamically imposes PDFs onto SRA3 print sheets based on their page count.

## Features

* **Automated Mailbox Polling:** Connects to Microsoft 365 via Azure Graph API using the credentials flow (background service).
* **Smart Filtering:** Automatically fetches emails from the specific `DRUCKAUFTRÄGE` folder and filters out previously processed jobs.
* **On-the-Fly Conversion:** Automatically converts incoming image attachments (`.jpg`, `.jpeg`, `.png`) into standardized PDFs.
* **Dynamic Imposition Pipeline:** * Analyzes the page count of incoming PDFs using `PyMuPDF`.
  * Automatically assigns binding types (1-2 pages = Normal, 3+ pages = Flyer).
  * Uses `py-impose` to calculate bleed, tile, and impose the artwork onto SRA3 sheets.
* **Non-Destructive:** Saves both the original customer file and the generated `_imposed.pdf` side-by-side in a customer-specific folder.
* **Robust Logging:** Granular, module-level logging split into application logs (`main.log`, `pipeline.log`) and third-party library logs (`py_impose.log`).

---

## Prerequisites

* **Python 3.10+**
* An **Azure App Registration** with the following Application API Permissions:
  * `Mail.Read` (Microsoft Graph)
  * *Note: Admin consent must be granted for the permissions.*

---

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/ManuYT-dev/HotfolderMail.git
   cd HotfolderMail
