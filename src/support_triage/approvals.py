"""Approval storage: the audit trail must outlive the task.

The SQLite table is the local default. In the deployed shape the container's filesystem dies
with the task, so approvals are written to S3 as one immutable JSON object per approval when
``APPROVALS_BUCKET`` is set. The interface is deliberately small: record one approval, list the
trail for a case.

The S3 key sorts by time — ``approvals/<case>/<timestamp>-<uuid>.json`` — so a prefix listing is
the trail in order. No object is ever overwritten, which is what an audit trail wants.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Protocol

from . import backend

BUCKET_ENV = "APPROVALS_BUCKET"
ENDPOINT_ENV = "S3_ENDPOINT_URL"  # local S3-compatible endpoint (MinIO) for smoke runs


class ApprovalStore(Protocol):
    def record(self, case_id: str, action: str, payload: str, rationale: str,
               approved_by: str, executed_at: str) -> None: ...

    def list(self, case_id: str | None = None) -> list[dict]: ...


class SqliteApprovalStore:
    """The local default: the approvals table in the same SQLite file."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def record(self, case_id: str, action: str, payload: str, rationale: str,
               approved_by: str, executed_at: str) -> None:
        backend.record_approval(self.conn, case_id, action, payload, rationale, approved_by,
                                executed_at)

    def list(self, case_id: str | None = None) -> list[dict]:
        return backend.approvals(self.conn, case_id)


class S3ApprovalStore:
    """One immutable object per approval, listed by case prefix."""

    def __init__(self, bucket: str) -> None:
        import boto3  # lazy: a local run should not need AWS credentials to import the app

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=os.getenv(ENDPOINT_ENV) or None)

    def record(self, case_id: str, action: str, payload: str, rationale: str,
               approved_by: str, executed_at: str) -> None:
        key = f"approvals/{case_id}/{executed_at}-{uuid.uuid4().hex}.json"
        body = json.dumps({
            "case_id": case_id,
            "action": action,
            "payload": payload,
            "rationale": rationale,
            "approved_by": approved_by,
            "executed_at": executed_at,
        })
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body.encode(), ContentType="application/json"
        )

    def list(self, case_id: str | None = None) -> list[dict]:
        prefix = f"approvals/{case_id}/" if case_id else "approvals/"
        rows: list[dict] = []
        token = None
        while True:
            kwargs: dict = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            for item in page.get("Contents", []):
                body = self.client.get_object(Bucket=self.bucket, Key=item["Key"])["Body"].read()
                rows.append(json.loads(body))
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return rows


def store_for(conn) -> ApprovalStore:
    """S3 when the deploy supplies a bucket; SQLite otherwise."""
    bucket = os.getenv(BUCKET_ENV, "").strip()
    return S3ApprovalStore(bucket) if bucket else SqliteApprovalStore(conn)
