# n8n: when research runs happen

n8n decides **when** a research run starts. What a run does lives in the
backend (`backend/app/research/`). These workflows make one HTTP call, and the
weekly one also polls until the run finishes. There is no research logic in n8n.

| Workflow | Trigger | Calls |
|---|---|---|
| `workflows/weekly_research.json` | Schedule, cron from `RESEARCH_WEEKLY_CRON` (default `0 8 * * 0`, Sunday 08:00) | `POST /api/automation/research/runs {"mode": "weekly"}`, then `GET …/runs/{id}` every minute until the run finishes (at most 45 checks) |
| `workflows/manual_research.json` | `POST http://localhost:5678/webhook/evidwell-research` | `POST /api/automation/research/runs {"mode": "manual", …}`, which answers with the run |

The desk's **Find trending topics** button does not go through n8n. It calls
`POST /api/console/research/runs`, which calls the same
`research/service.py::start_research_run`. So the button still works when n8n
is not running.

## Setup

1. Put a shared secret in the repo-root `.env`. The backend and n8n both read it from there:

   ```bash
   RESEARCH_TRIGGER_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
   ```

   While it is unset, the backend's `/api/automation/*` routes answer 404 and
   the workflows cannot start anything. After setting it, restart the API: a
   host venv picks the value up on restart, and a container needs
   `docker compose up -d --force-recreate backend worker`.

2. Start n8n and import the workflows:

   ```bash
   docker compose --profile automation up -d n8n
   docker compose exec n8n n8n import:workflow --separate --input=/workflows
   ```

   If the API runs in a host venv rather than in compose, point n8n at the
   host: `EVIDWELL_API_URL=http://host.docker.internal:8000 docker compose --profile automation up -d n8n`.

3. Open http://localhost:5678, create the owner account, then open each
   workflow and **publish/activate** it. The schedule and the production
   webhook only run while their workflow is active.

## Try it

```bash
curl -X POST http://localhost:5678/webhook/evidwell-research \
  -H 'Content-Type: application/json' \
  -d '{"targetArticleCount": 5, "categories": ["sleep", "supplements"]}'
```

The optional fields are `targetArticleCount` (4-6), `categories`,
`trendWindowDays`, `geo` and `language`. Anything you leave out uses the
backend's settings.

A second trigger while a run is queued or running gets **409**: runs are
single-flight. The weekly workflow treats that as finished rather than failed.

## Security

- n8n is published on `127.0.0.1:5678` only. The manual webhook starts a
  research run for anyone who can reach it. If you expose n8n beyond this
  machine, add Header Auth to the Webhook node.
- The token only lets a caller start and read research runs. It gives no
  access to articles, reviewers or readers.
- The n8n container gets `RESEARCH_TRIGGER_TOKEN`, not the whole `.env`. It has
  no reason to hold model or image-provider keys.
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` lets the workflows read `$env`. They
  read only `EVIDWELL_API_URL`, `RESEARCH_TRIGGER_TOKEN` and
  `RESEARCH_WEEKLY_CRON`.
