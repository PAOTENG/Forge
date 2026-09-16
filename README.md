# Forge

Forge is a **local coding agent for the on-call desk**. You chat in a browser, inspect diffs, and approve writes. Agent bash runs inside a Docker sandbox. WeCom / DingTalk only pull a human into the page.

It is **not** a Cursor or Claude Code replacement. The loop is narrow on purpose:

**canary alert → inbox → diagnose in a session → patch → tests → commit → restart the monitored service → canary recheck.**

Version **0.6.0**. Personal demo, not a production on-call platform.

## What it does

- Chat-driven `read` / `grep` / `glob` / code search, plus human approval for writes, tests, git, and restarts
- Plan mode (read-only) and Build mode (mutating tools enabled)
- Sessions survive refresh: the agent runs on the API process; the page resubscribes over SSE
- Watches `targets/order-api` (one injected bug at a time); a failing canary opens an inbox item
- Pre-write file backups with per-file or per-session rollback
- First-run admin signup; roles `admin` / `engineer` / `viewer`
- Optional WeCom textcard / DingTalk actionCard / generic webhook for alerts and HITL cards

## Architecture

```
Browser (Vue 3, :5173)
    │  Vite proxies /api and WebSocket
    ▼
Forge API (FastAPI, :8787)
    │  OpenAI Agents SDK tool loop
    ├── PostgreSQL 17 + pgvector   sessions, messages, agent_states, alerts, backups
    ├── Docker sandbox             forge-runner, network=none, 2GB, 256 PIDs
    └── order-api (:8790)          watched demo service, /health + /canary
```

Design choices for a single duty PC:

- **PostgreSQL + Alembic**, no Redis / Celery
- Retrieval on session open: chunk the workspace into `file_chunks` (`pg_trgm` / ILIKE). Vector column is reserved; no separate embedding service yet
- HITL interrupt objects live in API process memory. After an API restart, pending cards can still open, but “Allow” may expire and the turn must be resent
- WeCom / DingTalk are outbound HTTP (`httpx`), not IDE plugins and not mini-programs

The agent is split on purpose:

| Layer | Who decides | What it may do |
|---|---|---|
| Agent | the model | locate the fault, propose a minimal patch |
| Program | tools + gates | write, test, git, restart, canary; refuse out-of-scope work |

## Safety model

Seventeen tools. Read-only diagnostics always run. **Eight mutating families require human approval** (and only in Build mode):

`write_file` · `delete_file` · `bash` · `git_checkout` · `git_commit` · `git_push` · `run_tests` · `restart_service`

Hard gates still run even when eval / admin auto-approve is on:

- Workspace jail; writes to `C:\` and paths outside the session root are rejected
- On the monitored hotfix repo, writes are limited to `bugs.py` / `app.py`
- Tests, `active_bug.txt`, and `git reset --hard` / force git are blocked (including bash bypass attempts)
- Ordinary commands go to the session sandbox (`network=none`). Git credentials and process restart use constrained host tools
- Commits never include `.env`. Forge does not store git passwords; push uses the duty machine’s existing credentials

## Demo target

`targets/order-api` is a small FastAPI order service with **10 mutually exclusive bugs**. `active_bug.txt` activates one at a time. Forge starts the process, polls `/canary`, and files an inbox alert on failure.

Headless task eval (real model, real tools, pytest + live `/canary`):

| Setting | Result |
|---|---|
| Model | `deepseek/deepseek-reasoner` |
| Trials | 1 per task (pass@1, not pass@3) |
| Scoreable bugs | 1, 5, 7, 8, 9, 10 (2/3/4 skipped: canary already green) |
| pass@1 | **6 / 7** |
| Test-file edits | 0 / 7 |
| `git_push` | disabled in eval; none invoked |

That number is one run, not a stable production rate. Backend deterministic tests: `pytest backend/tests`.

## Repository layout

```
backend/          FastAPI, Alembic, agent, tools, sandbox client
frontend/         Vue 3 duty console
infra/            Postgres compose + sandbox Dockerfile
targets/          monitored demo service (order-api)
docs/             Chinese runbooks and schema notes
项目测试/          eval scripts and write-ups (raw JSON traces are not published)
```

Runtime data (`data/`), `.env`, `node_modules/`, resume / job-search notes, and machine-local inventory files are **not** in git.

## Prerequisites

- Windows 11 with Docker Desktop (Linux containers)
- Python **3.12** in a dedicated conda env named `forge` (do not reuse a LangChain `agent` env: `openai` 2.x vs 3.x conflict)
- Node.js 22+ and **pnpm**
- At least one model key (`DEEPSEEK_API_KEY` is tried first)

## Quick start

1. Copy env and add a key:

```bat
copy .env.example .env
```

2. Start Postgres and build the sandbox image (first time):

```bat
docker compose -f infra\docker-compose.yml up -d
docker inspect --format "{{.State.Health.Status}}" forge-postgres
docker build -t forge-runner:latest -f infra\sandbox\Dockerfile infra\sandbox
```

3. Install and run the API (terminal A):

```bat
conda activate forge
python -m pip install -r backend\requirements.txt
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

Alembic migrations run on API startup. `order-api` on `:8790` also starts when `FORGE_TARGET_AUTOSTART=true`.

4. Install and run the console (terminal B):

```bat
cd frontend
pnpm install
pnpm dev
```

Open **http://127.0.0.1:5173/** — not `:8787`. First visit creates the admin user. On the same Wi-Fi, phones use the Vite Network URL.

Chinese step-by-step runbooks: [docs/启动说明.md](docs/启动说明.md), [docs/全部服务启动手册.md](docs/全部服务启动手册.md). Feature boundaries: [docs/功能说明.md](docs/功能说明.md).

## Configuration

All secrets live in root `.env` (see `.env.example`). Restart the API after changing keys.

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / … | Model providers |
| `FORGE_MODEL` | Fallback model until the UI picker writes a preference |
| `DATABASE_URL` | Postgres (default user/password/db: `forge`) |
| `FORGE_SANDBOX_*` | Image, memory, network |
| `FORGE_PUBLIC_URL` | URL phones can open (usually Vite `:5173`) |
| `FORGE_DUTY_TOKEN` | Alert webhook auth |
| `WECOM_*` / `DINGTALK_*` | Optional push channels |
| `FORGE_ADMIN_AUTO_APPROVE` | Default `false` |

## Duty console

The product is the desktop web app: chat, diff, approve, logs. A phone browser can open the same origin. WeCom / DingTalk are **entry points**, not an IDE.

Configure `FORGE_PUBLIC_URL`, optional `WECOM_*` or `DINGTALK_WEBHOOK`, restart the API, then use **Simulate alert** / **Investigate** on the landing page.

DingTalk custom robots must be created in the **desktop** client. Do not paste webhook secrets into chat.

Post a generic alert:

```bat
curl -X POST http://127.0.0.1:5173/api/duty/alerts -H "Content-Type: application/json" -d "{\"title\":\"order 500\",\"summary\":\"order-api\",\"severity\":\"critical\"}"
```

Health:

```bat
curl http://127.0.0.1:8787/api/health
```

## Tests

From `backend/`:

```bat
python -m pytest -q
```

Layered eval (no LLM for the first three; L5/L6 call a real model):

```bat
python 项目测试\run_forge_eval.py
python 项目测试\run_agent_task_eval.py --bugs 1,5,6,7,8,9,10 --timeout 600
python 项目测试\run_agent_safety_eval.py --timeout 240
```

## Limits

- Sandbox has no outbound network; do not expect `pip` / `npm` inside the container
- No SSO / LDAP / multi-tenant. Accounts are local to the duty PC
- Push channels are optional; the console works without them
- `file_chunks.embedding` is not auto-filled
- User terminal (Docker TTY) can be flaky on Windows
- Excel / Word viewers are not full Office; `.pptx` and old `.doc` are download-only
- Do not expose `:8787` to the public internet

## License

Source in this repository is provided as-is for study and local demonstration. Add a license file if you need a specific grant.
