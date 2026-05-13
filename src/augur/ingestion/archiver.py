"""
Payload archiver.

Writes normalized payload content to local filesystem and optionally
replicates to object storage (S3/S3-compatible).

Archive path: {archive_root}/{source_id}/{YYYY-MM}/{payload_id}.json
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


class PayloadArchiver:
    """
    Writes payloads to a local archive directory.

    S3 upload is done asynchronously by a background worker; the archiver
    only writes locally and signals that an upload is needed.
    """

    def __init__(
        self,
        archive_root: Path | str,
        *,
        s3_client=None,  # boto3 S3 client; None = local-only
        s3_bucket: str | None = None,
        s3_prefix: str = "augur/payloads",
    ) -> None:
        self._root = Path(archive_root)
        self._s3 = s3_client
        self._bucket = s3_bucket
        self._s3_prefix = s3_prefix

    def archive(
        self,
        payload: dict[str, Any],
        *,
        payload_id: UUID,
        content_timestamp: datetime,
        source_id: str,
    ) -> Path:
        """
        Write payload to local filesystem. Returns the path written.

        The payload dict is the normalized form from normalizer.normalize().
        """
        month_dir = self._root / source_id / content_timestamp.strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)

        out_path = month_dir / f"{payload_id}.json"
        out_path.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )

        log.debug(
            "archiver.written",
            path=str(out_path),
            source_id=source_id,
            payload_id=str(payload_id),
        )
        return out_path

    def upload_to_s3(
        self,
        local_path: Path,
        *,
        source_id: str,
        content_timestamp: datetime,
        payload_id: UUID,
    ) -> str | None:
        """
        Upload an already-archived payload to S3.

        Returns the S3 key on success, None if S3 is not configured.
        This is synchronous (boto3) — call from a thread if needed.
        """
        if self._s3 is None or self._bucket is None:
            return None

        key = (
            f"{self._s3_prefix}/{source_id}/"
            f"{content_timestamp.strftime('%Y-%m')}/"
            f"{payload_id}.json"
        )

        try:
            self._s3.upload_file(
                Filename=str(local_path),
                Bucket=self._bucket,
                Key=key,
                ExtraArgs={"ContentType": "application/json"},
            )
            log.debug("archiver.s3_uploaded", key=key)
            return key
        except Exception as exc:
            log.error("archiver.s3_failed", key=key, error=str(exc))
            return None
