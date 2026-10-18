"""以真實公開來源／Luna 驗證已確認需求的研究流程；不讀寫產品 DB。"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4
from hashlib import sha256

from runtime import (
    research_workflow,
    research_documents,
    research_sources,
    research_reading,
    research_web,
    topics,
)
from runtime.provider_http import provider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--scope",
        type=Path,
        required=True,
        help="已檢視的範圍 JSON；不代替產品的使用者確認",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    scope = topics.proposal(json.loads(args.scope.read_text()))
    source_sha256 = {
        module.__name__: sha256(Path(module.__file__).read_bytes()).hexdigest()
        for module in (
            research_workflow,
            research_documents,
            research_sources,
            research_reading,
            research_web,
            topics,
        )
    }
    state = {
        "research_id": str(uuid4()),
        "query": args.request,
        "mode": "review",
        "material_title": scope["title"],
        "approved_scope": scope,
        "search_state": research_workflow.new_state(),
        "candidates": [],
    }
    started = time.monotonic()
    calls = []
    steps = []
    source_requests = []
    failure = None
    documents = args.output.parent / (args.output.stem + "-documents")
    documents.mkdir(mode=0o700, exist_ok=True)

    def read_document(receipt, digest):
        data = (documents / receipt["sha256"]).read_bytes()
        if (
            len(data) != receipt["size_bytes"]
            or sha256(data).hexdigest() != receipt["sha256"]
            or digest != receipt["sha256"]
        ):
            raise ValueError("RESEARCH_SNAPSHOT_UNAVAILABLE")
        metadata = deepcopy(receipt["metadata"])
        media, suffix = (
            ("text/plain", ".txt")
            if metadata.get("format") == "html"
            else ("application/pdf", ".pdf")
        )
        return data, media, suffix, metadata

    fetch_json = research_workflow.research_sources._json

    def observed_json(url, **kwargs):
        record = {"host": urlsplit(url).hostname}
        begin = time.monotonic()
        try:
            result = fetch_json(url, **kwargs)
            record["status"] = "succeeded"
            if record["host"] == "api.openalex.org":
                record["cost_usd"] = result.get("meta", {}).get("cost_usd")
            return result
        except Exception as error:
            record.update(
                status="failed",
                error=str(error),
                http_status=getattr(error, "status", None),
            )
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - begin, 3)
            source_requests.append(record)

    research_workflow.research_sources._json = observed_json

    def call(path, body):
        begin = time.monotonic()
        record = {"task": body["request"]["task"]}
        calls.append(record)
        try:
            result = provider(path, body)
            record["status"] = "succeeded"
            return result
        except Exception as error:
            record.update(status="failed", error=str(error))
            raise
        except KeyboardInterrupt:
            record["status"] = "interrupted"
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - begin, 3)

    try:
        for step in range(400):
            stage = state["search_state"]["stage"]
            waiting = state["search_state"].get("waiting_until", 0) or 0
            if waiting > time.time():
                failure = "DEFERRED_UNTIL:" + str(waiting)
                break
            before = len(calls)
            state.update(
                research_workflow.advance(state, call, read_document=read_document)
            )
            for candidate in state["candidates"]:
                acquired = candidate.pop("_acquired_document", None)
                if acquired is None:
                    continue
                data, metadata = acquired
                receipt = research_documents.snapshot(data, metadata)
                (documents / receipt["sha256"]).write_bytes(data)
                candidate["_document_snapshot"] = receipt
            steps.append({"stage": stage, "calls": len(calls) - before})
            if len(calls) > before or stage == "searching":
                print(
                    json.dumps(
                        {
                            "step": step,
                            "stage": stage,
                            "candidates": len(state["candidates"]),
                            "body_verified": sum(
                                c.get("review", {}).get("phase") == "body"
                                for c in state["candidates"]
                            ),
                            "calls": len(calls),
                            "elapsed_seconds": round(time.monotonic() - started),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                args.output.with_suffix(".progress.json").write_text(
                    json.dumps(
                        {
                            "search_state": state["search_state"],
                            "source_requests": source_requests,
                            "sources": [
                                {
                                    "title": c["title"],
                                    "eligible": c.get("eligible"),
                                    "access": c.get("access"),
                                    "screening": c.get("screening"),
                                    "review_phase": c.get("review", {}).get("phase"),
                                    "read_position": c.get("_read_plan_cursor"),
                                    "read_sections": len(c.get("_read_plan", [])),
                                }
                                for c in state["candidates"]
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            if state["status"] == "selecting":
                break
        else:
            failure = "TEST_STEP_BUDGET_REACHED"
    except KeyboardInterrupt:
        failure = "TEST_INTERRUPTED"
    except Exception as error:
        failure = str(error)
    finally:
        result = {
            "research_id": state["research_id"],
            "source_sha256": source_sha256,
            "request": args.request,
            "scope": scope,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "failure": failure,
            "calls": calls,
            "steps": steps,
            "source_requests": source_requests,
            "search_state": state["search_state"],
            "status": state.get("status"),
            "candidates": deepcopy(state["candidates"]),
        }
        result["documents"] = {
            c["id"]: c["_document_snapshot"]
            for c in state["candidates"]
            if c.get("_document_snapshot")
        }
        result["qualification"] = (
            "failed"
            if failure
            else "covered"
            if state["search_state"].get("finish_reason") == "covered"
            else "partial"
        )
        for candidate in result["candidates"]:
            for key in list(candidate):
                if key.startswith("_"):
                    candidate.pop(key)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "qualification": result["qualification"],
                    "failure": failure,
                    "coverage": result["search_state"].get("coverage"),
                    "calls": len(calls),
                    "elapsed_seconds": result["elapsed_seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return int(result["qualification"] != "covered")


if __name__ == "__main__":
    raise SystemExit(main())
