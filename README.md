# Regulatory Monitor

FastAPI app for a daily regulatory monitoring workflow:

1. Read regulatory source links from a Google Sheet.
2. Use the sheet `Remarks` column as the site remark, Drive folder name, and email remark.
3. Visit configured regulatory sites.
4. Find articles published for the selected date.
5. Download article PDFs into a dated local folder structure.
6. Upload PDFs into the shared Google Drive folder.
7. Extract PDF headings and summaries.
8. Generate and save an email draft.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -B -m uvicorn app.main:app --reload
```

Open:

- Local website: http://127.0.0.1:8000
- Docs: http://127.0.0.1:8000/docs

The website has explicit switches for Drive upload, Hostinger draft, and email send. Leave Drive off for local-only tests.

## Step-by-Step CLI Test

Run the automation from CMD/PowerShell and see every step:

```powershell
python -B main.py --date 19-06-2026 --site "NOC under Regulation 37 Updates" --save-webmail-draft
```

Add `--send-email` only when you want to send the email:

```powershell
python -B main.py --date 19-06-2026 --site "NOC under Regulation 37 Updates" --save-webmail-draft --send-email
```

## Google Setup

Copy `.env.example` to `.env`, then set:

```text
GOOGLE_DRIVE_ROOT_FOLDER_URL="https://drive.google.com/drive/folders/15sBC2D-zFsQrN6f0oOXW0pvK0SUvlKLi"
GOOGLE_SHEET_URL="<your sheet link>"
GOOGLE_SERVICE_ACCOUNT_FILE="<path to service-account.json>"
SHEET_NAME="<optional tab name>"
```

Share both the Google Drive folder and the Google Sheet with the service account email from the JSON credentials file.

## Hostinger Email Setup

Set `config/credentials/email_draft_credentials.json` with your Hostinger mailbox:

```json
{
  "provider": "hostinger_smtp",
  "sender_email": "yourname@yourdomain.com",
  "to": ["recipient@example.com"],
  "cc": [],
  "bcc": [],
  "smtp_host": "smtp.hostinger.com",
  "smtp_port": 465,
  "smtp_username": "yourname@yourdomain.com",
  "smtp_password": "your-mailbox-password",
  "smtp_use_ssl": true,
  "smtp_use_starttls": false,
  "imap_host": "imap.hostinger.com",
  "imap_port": 993,
  "imap_drafts_folder": "Drafts"
}
```

The app saves an HTML `.eml` draft, saves a copy in Hostinger Drafts through IMAP, and sends it through SMTP after a daily run when these credentials are complete.

The sheet should include a source URL column named one of:

- `Link`
- `URL`
- `Site Link`
- `Website`

The app uses the `Remarks` column for the folder and email remark. Optional site-name columns are `Site`, `Site Name`, or `Name`.

## Shared Run History (Supabase)

Run history can be shared across computers by storing it in a Supabase (hosted Postgres)
table instead of only the local `data/run_history.json` file. Every run inserts one row,
so simultaneous runs from different machines never overwrite each other.

Setup (one time):

1. Create a free project at https://supabase.com.
2. In the project's **SQL Editor**, run:

   ```sql
   create table if not exists run_history (
     id text primary key,
     created_at timestamptz not null,
     run_date date not null,
     articles_found integer not null default 0,
     status text,
     data jsonb not null
   );
   create index if not exists idx_run_history_run_date on run_history (run_date);
   create index if not exists idx_run_history_created_at on run_history (created_at desc);
   ```

3. In **Project Settings → API**, copy the **Project URL** and the **`service_role`** key.
4. Put them in `.env` (keep the `service_role` key server-side only — never in frontend code):

   ```text
   SUPABASE_URL="https://<your-ref>.supabase.co"
   SUPABASE_KEY="<service_role key>"
   ```

Give the same two values to every computer that runs the app and they all share one history.
Leave both blank to keep history local-only. If Supabase is set but unreachable, the app falls
back to the local `data/run_history.json` backup, which is always written too.

## Review Queue (Accept / Deny)

Instead of auto-uploading everything, a run can discover articles into a **pending queue**.
You review each one on the dashboard and **Accept** or **Deny** it:

- **Accept** → checks Drive first. If the PDF is already in the dated folder it is skipped
  (no re-upload, excluded from the email). Otherwise it downloads the PDF and uploads it to Drive.
- **Build email** → creates ONE combined draft from all accepted articles, across dates
  (grouped by source, like a normal daily run). Accepting many articles does NOT create many
  emails — you build a single digest when ready.
- **Deny** → discards the item.

Endpoints add `POST /pending/build-email`.

The queue lives in a shared Supabase table, so pending items show up on any computer.

One-time table (run in Supabase → SQL Editor, same as `run_history`):

```sql
create table if not exists pending_articles (
  id text primary key,
  discovered_at timestamptz not null,
  run_date date not null,
  site_name text,
  site_remark text,
  title text,
  source_url text,
  pdf_url text,
  filename text,
  published_date date,
  status text not null default 'pending',
  drive_pdf_url text,
  drive_folder_url text,
  summary text,
  decided_at timestamptz
);
create index if not exists idx_pending_status on pending_articles (status);
```

Endpoints: `GET /pending`, `POST /pending/refresh`, `POST /pending/{id}/accept`, `POST /pending/{id}/deny`.

### New-arrivals detection (catches late/back-dated rows)

Some sources (e.g. BSE NOC) add a row to their page days after its printed date, so pure
date matching misses it. For those sources the app instead remembers a fingerprint of every
row it has ever seen and surfaces only rows it has **not** seen before — regardless of date
or list order. The first time a source is checked, all current rows are recorded silently as a
baseline (no flood); after that only genuinely new rows appear in the queue.

One-time table (Supabase → SQL Editor):

```sql
create table if not exists seen_articles (
  fingerprint text primary key,
  site_remark text,
  title text,
  published_date date,
  first_seen timestamptz not null
);
create index if not exists idx_seen_site on seen_articles (site_remark);
```

### Automatic daily checks (so weekends aren't missed)

Run the discovery unattended with Windows Task Scheduler. Create a **Daily** task whose action runs:

```text
Program:   C:\Users\nipra\OneDrive\Desktop\Manas\Regulatory Monitor\.venv\Scripts\python.exe
Arguments: -B -m app.tasks.pending_monitor
Start in:  C:\Users\nipra\OneDrive\Desktop\Manas\Regulatory Monitor
```

Set it to run every day (including Sat/Sun). It checks the current date, adds anything new to
the queue, and exits — no server or browser needed. When you open the dashboard, the bell badge
and the **Pending Review** panel show what was found. (The computer must be powered on at the
scheduled time.)

## Folder Flow

Local files are stored under:

```text
data/
  2026/
    July 2026/
      03-07-2026/
        Site Remark/
          article.pdf
          article.json
      email-drafts/
        03-07-2026.eml
```

Google Drive files are uploaded under the root folder you provided:

```text
Regulatory Monitor Drive Root/
  2026/
    July 2026/
      03-07-2026/
        Site Remark/
          article.pdf
```

## Main Endpoints

- `GET /health`
- `POST /tasks/daily-run`
- `POST /pdfs/extract`
