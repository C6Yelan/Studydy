# Studydy V2 Canonical Workstation Runbook

This runbook reproduces the V2 release candidate on one Windows/WSL workstation. Commands run from
the repository root. Replace placeholders locally; never paste private paths, PDF names, passwords,
DSNs or model/cache locations into Git, screenshots, logs or review comments.

## 1. Preflight

```bash
git fetch --all --prune
git status --short
git rev-parse --show-toplevel
git rev-parse HEAD
git merge-base --is-ancestor 47156f624eefe51cc0bc37d3232868102e20c7a7 HEAD
```

`HEAD` 必須等於 Batch 1 handoff 的 exact candidate SHA，且 worktree 必須乾淨。
backend 與所有 local AI runtime 都使用 Python 3.12 minor：

```bash
UV_CACHE_DIR=/tmp/studydy-uv-cache uv sync --project backend --python 3.12 --extra test
backend/.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12)'
```

接著確認 Docker、GPU 與已安裝 runtime：

```bash
docker info --format '{{.ServerVersion}} {{.Driver}} {{.OSType}}'
nvidia-smi.exe --query-gpu=name,memory.total,driver_version --format=csv,noheader
PYTHONPATH=backend/src backend/.venv/bin/python -m runtime.local_runtime verify
```

Expected runtime verify: `22/22` files。此檢查確認必要 runtime layout、package 與既有
OCR/verifier assets；不以 executable、wheel/RECORD 或 Qwen shard byte hash 作為 Batch 1 gate。

## 2. PostgreSQL

Use a unique explicit container name and loopback-only port. Enter the password interactively so it
does not enter shell history:

```bash
export STUDYDY_V2_PG_CONTAINER=studydy-v2-postgres
read -rsp 'PostgreSQL password: ' STUDYDY_V2_PG_PASSWORD; echo
export POSTGRES_PASSWORD="$STUDYDY_V2_PG_PASSWORD"
docker run --detach --rm \
  --name "$STUDYDY_V2_PG_CONTAINER" \
  --publish 127.0.0.1:5432:5432 \
  --shm-size 256m \
  --env POSTGRES_PASSWORD \
  --env POSTGRES_DB=studydy_v2 \
  --env POSTGRES_USER=studydy_v2_owner \
  --env PGDATA=/var/lib/postgresql/18/docker \
  --env 'POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --data-checksums --encoding=UTF8 --locale=C' \
  postgres:18.4-bookworm@sha256:882236b897e39051d2368c5ccc6cda944904723506b2dfc97f2a8f5bc9afa382
export STUDYDY_DATABASE_DSN="host=127.0.0.1 port=5432 dbname=studydy_v2 user=studydy_v2_owner password=$STUDYDY_V2_PG_PASSWORD connect_timeout=5"
unset POSTGRES_PASSWORD STUDYDY_V2_PG_PASSWORD
```

Keep `STUDYDY_DATABASE_DSN` only in the current private shell. Do not echo it.

## 3. Migrations

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -c \
  'from runtime.storage.migrations import run_migrations; print(run_migrations())'
```

A fresh database must print versions 1 through 13. Running it again must print `()` and verify
ledger checksums without changing schema.

## 4. Backend and local AI

Choose an absolute ignored artifact directory. Omit `STUDYDY_LOCAL_RUNTIME_ROOT` to use the
installed default, or set it privately to the whole approved runtime root.

```bash
export STUDYDY_PROFILE=local
export STUDYDY_PUBLIC_ORIGIN=http://127.0.0.1:4173
export STUDYDY_SECURE_COOKIE=false
export STUDYDY_ARTIFACT_ROOT='<ABSOLUTE_IGNORED_ARTIFACT_ROOT>'
# export STUDYDY_LOCAL_RUNTIME_ROOT='<ABSOLUTE_APPROVED_RUNTIME_ROOT>'
PYTHONPATH=backend/src:local_ai/src backend/.venv/bin/python -c \
  'from runtime.local_app import run_local_app; run_local_app(port=8001)'
```

Backend 啟動時先載入唯一的 loopback `Qwen/Qwen3.8-27B-FP8` vLLM service，固定
`max_model_len=32768`、`max_num_seqs=1`，並持有到 app shutdown。Material、Knowledge Map
與 Assessment 的既有 OpenAI-compatible client 都只取得 non-owning lease；request 結束或
失敗不 unload Qwen。Assessment verifier 仍依既有 lifecycle 管理。

## 5. Frontend

In a second shell:

```bash
cd frontend
npm ci
npm test
npm run typecheck
npm run build
npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
```

Vite proxies `/v1` only to `127.0.0.1:8001`. Open `http://127.0.0.1:4173`.

## 6. Smoke

```bash
curl --fail --silent http://127.0.0.1:8001/v1/openapi.json >/dev/null
curl --fail --silent http://127.0.0.1:4173/ >/dev/null
```

In the browser, confirm upload ready, no account/search placeholder, and a server-created HttpOnly
`studydy_session` cookie. Browser network responses must not contain correct-option, private-answer,
private-scoring, generation-provenance, raw-model or local-path fields.

## 7. Cold Demo

1. Start PostgreSQL, backend and frontend from stopped state.
2. Upload `<PRIVATE_R1_PDF>` from the release frontend.
3. Wait for `succeeded` or truthful `partial`; do not refresh into a second run.
4. Open the Map. Inspect Concept, Evidence page action, the three-type legend and
   `教材建議學習順序`.
5. Start a StudySession from a Concept with an eligible safe Assessment.
6. Generate the first Assessment. On the canonical workstation, allow about 135 seconds cold.
7. Submit an intentionally wrong answer; show server Feedback, Learning State, Weakness and
   `目前為你調整`.
8. Apply the review/practice action.
9. Request a new Assessment and confirm different question identity or different Claim coverage.
10. Submit the reassessment and show event/state/next-step update.
11. Confirm Map revision and canonical initial path did not change.

Do not disclose how the controlled demo chooses a wrong/correct option. The product browser must
learn correctness only after server Feedback.

## 8. Warm Demo

Qwen 在同一 app uptime 內保持 resident：

1. Return to the Map and start a new StudySession on the qualified Concept.
2. Confirm watermark 0 and no inherited attempts/mastery/observed weakness.
3. Request a new Assessment and record warm latency.
4. Repeat one different-item reassessment and show stable model reuse without a second Qwen load.

Assessment verifier 的 idle 回收不會回收 resident Qwen；只有 app restart 才是下一次 Qwen cold load。

## 9. Recovery Demo

Use a bounded private recovery PDF and an otherwise disposable qualification database.

1. Upload and wait until the processing run is visibly `running`.
2. Stop only the backend process with `Ctrl-C`; leave PostgreSQL running.
3. Restart the exact backend command.
4. Confirm the interrupted run becomes terminal `RESTART_INTERRUPTED`, has no output binding and
   offers a safe return/retry path rather than remaining `running`.
5. Start a new idempotency run for the same uploaded material, or re-upload through the UI.
6. Confirm the retry reaches `succeeded`/`partial`, exactly one output/Map is published and no run is
   left `running`.

The measured P08 recovery baseline was 11.834 seconds to backend readiness and 127.800 seconds for a
three-page retry.

## 10. Focused release regression

```bash
PYTHONPATH=backend/src:local_ai/src:backend/tests backend/.venv/bin/pytest -q \
  backend/tests local_ai/tests
backend/.venv/bin/python backend/tests/runtime/material_review_e2e_runner.py
```

Record the actual backend/local-AI and Playwright totals. The Playwright runner creates and cleans
its own pinned PostgreSQL container.

## 11. Shutdown and evidence boundary

Stop frontend and backend with `Ctrl-C`, then stop only the explicit database container:

```bash
docker stop "$STUDYDY_V2_PG_CONTAINER"
unset STUDYDY_DATABASE_DSN STUDYDY_ARTIFACT_ROOT STUDYDY_LOCAL_RUNTIME_ROOT
```

Verify no owned process/container remains. Keep aggregate timings, counts, statuses and screenshots
only in ignored/local review evidence. Never commit private PDFs, source text, raw model output,
answers, DSNs, absolute paths, runtime/model/cache data or browser storage.
