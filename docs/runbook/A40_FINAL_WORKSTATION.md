# A40 final workstation

1. Check out the candidate feature branch without merging it. Confirm a clean tree and record `HEAD`.
2. Start the externally owned `Qwen/Qwen3.8-27B-FP8` vLLM service on `127.0.0.1:8000` with the
   runtime-lock versions, 32K context, one sequence, and bearer value supplied only through
   `VLLM_API_KEY`.
3. Export an absolute private `STUDYDY_LOCAL_RUNTIME_ROOT`; its OCR Python minor must be 3.12.
   Export `STUDYDY_ARTIFACT_ROOT` and database DSN only in the private shell. Never echo them.
4. Apply the sole final migration to a fresh pre-release database; a second invocation must return
   an empty tuple.
5. Run `runtime.local_runtime verify`, the complete local regression in `docs/testing.md`, and the A40
   `run` command on a representative 8-page input, the 45-page array material, another technical
   material, and a scanned material.
6. Through the real browser/API, verify upload, progress, Evidence, Concepts, Relations, Map, Path,
   StudySession, Assessment, answer, learner guidance, reload/reopen, exact revision, and PDF locator.
7. Complete the private review bound to the run SHA and run the `score` command. A summary with
   `"pass": true` is the only final A40 PASS evidence.
8. Stop only backend-owned processes and the explicit database. Do not stop the resident Qwen service.

Do not commit `.env`, private PDFs, review data, raw model output, `.studydy-runtime/`, `docs_local/`,
runtime/model paths, credentials, or DSNs.
