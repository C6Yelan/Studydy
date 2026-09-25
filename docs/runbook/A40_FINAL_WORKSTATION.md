# A40 final workstation

> 歷史 A40 驗收紀錄，不是現行環境的啟動或模型驗收指令。現行操作見
> [本機環境](../local-environment.md)與[測試及品質驗收](../testing.md)；當時的專用 scorer 已退役。

1. Check out the candidate feature branch without merging it. Confirm a clean tree and record `HEAD`.
2. Start the externally owned `google/gemma-4-31B-it-qat-w4a16-ct` vLLM service with the command below; backend target is `http://127.0.0.1:18000`, with the
   runtime-lock versions, 32K context, one sequence, and bearer value supplied only through
   `VLLM_API_KEY`.
3. Export an absolute private `STUDYDY_LOCAL_RUNTIME_ROOT`; its OCR Python minor must be 3.12.
   Export `STUDYDY_ARTIFACT_ROOT` and database DSN only in the private shell. Never echo them.
4. Apply the migrations to a fresh database, or apply the pending additive migrations (credentials and
   material names) to the accepted schema. A second invocation must return an empty tuple. Before upgrading a database
   containing needed data, stop product writes and privately preserve the database and source PDF
   store together. Do not replace their configured locations. See [account setup](../accounts.md).
5. Complete the static regression in `docs/testing.md` first. If the Gemma endpoint is unavailable,
   stop with `STATIC_CUTOVER_READY_FOR_GEMMA_RUNTIME`. Otherwise check service identity/authentication,
   run one small structured material request, then one Assessment generation and checker.
   Only after both smokes pass, run the approved 45-page input through the product pipeline.
   Use freshly processed material; do not rewrite old development provenance or replace the product DB.
   For the local OCR plus remote Gemma deployment, upload through the running product API/UI
   and collect its published artifact. The CLI `run` diagnostic assumes a same-host A40;
   do not use its local GPU/process probe to identify the remote Gemma service.
6. Through the real browser/API, verify upload, progress, Evidence, Concepts, Relations, Map, Path,
   StudySession, Assessment, answer, learner guidance, reload/reopen, exact revision, and PDF locator.
7. Complete the private review bound to the run SHA and run the `score` command. A summary with
   `"pass": true` is the only final A40 PASS evidence.
8. Stop only backend-owned processes and the explicit database. Do not stop the resident Gemma 4 service.

Do not commit `.env`, private PDFs, review data, raw model output, `.studydy-runtime/`, `docs_local/`,
runtime/model paths, credentials, or DSNs.

## Qualified semantic service

The competition final lock pins vLLM `0.28.0`, Python `3.12`, torch `2.13.0+cu130`,
CUDA `13.0`, transformers `5.15.1` (container `vllm/vllm-openai:v0.28.0`).
Python is recorded to minor-version precision in the qualification lock.
Keep the existing private `VLLM_API_KEY` in the server environment. Do not print it or
place it in command arguments. vLLM reads this environment variable for bearer authentication.

```bash
test -n "${VLLM_API_KEY:?VLLM_API_KEY must be supplied privately}"
vllm serve google/gemma-4-31B-it-qat-w4a16-ct \
  --revision 52f3f65bc7a02d555763bc923bd1d9094898219d \
  --host 0.0.0.0 --port 18000 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 32768 --max-num-seqs 1 \
  --reasoning-parser gemma4 \
  --default-chat-template-kwargs '{"enable_thinking": true}' \
  --structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true}'
```

The server binds `0.0.0.0:18000` inside the Pod; do not expose a public port.
The product targets `127.0.0.1:18000` through the existing private SSH transport.
Confirm an unauthenticated `/v1/models` call on the server is rejected and the existing
bearer succeeds. Verify `/version`, exactly one `/v1/models` entry with 32768 context,
and `/tokenize`. These endpoints do not attest the model revision or all installed packages:
also inspect the actual launch revision and package versions privately on the server.

The backend owns no semantic lifecycle. API startup validates local configuration without
contacting AI; login and saved read paths remain available when Gemma is offline.
Only processing and Assessment operations require the service.
Unlimited-OCR and its local Python integration remain unchanged.
See [cutover audit and qualification limits](../gemma-cutover.md).

The qualification input is the competition-final 45-page source, SHA-256
`07b1c1c1352934f75cc5182aa15db8a702138861f7557f470f9200ac33b06d13`.
The scorer uses this exact identity, the existing 85% reviewed-usability threshold,
complete canonical Path, source/revision binding, runtime and closed-loop checks.
For a split deployment, runtime metadata comes from the remote semantic service;
OCR counts and processing durations come from the actual local product run.
