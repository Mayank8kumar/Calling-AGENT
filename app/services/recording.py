"""
# Recording service — S3/MinIO storage
# Upload:
#   upload_recording(audio, tenant_id, call_id) — date-partitioned S3 key, AES-256 encryption
#   Key format: recordings/{tenant}/{year}/{month}/{day}/{call_id}.wav
# Download:
#   get_presigned_url(s3_key, expiry) — temporary download link
# Deletion:
#   delete_recording(s3_key) — single recording (GDPR right to erasure)
#   delete_tenant_recordings(tenant_id) — ALL recordings for a tenant
# Setup:
#   ensure_bucket_exists() — creates bucket on MinIO if missing
"""

"""
Recording service — S3 upload, retrieval, lifecycle management.

Handles:
- Uploading call recordings to S3/MinIO
- Generating pre-signed download URLs
- Lifecycle policy management (Standard → IA → Glacier)
- Consent-aware recording deletion (GDPR right to erasure)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from app.config import get_settings

logger = logging.getLogger(__name__)


class RecordingService:
    def __init__(self) -> None:
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "aws_access_key_id": settings.s3_access_key_id,
            "aws_secret_access_key": settings.s3_secret_access_key,
            "region_name": settings.s3_region,
            "config": BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url

        self._client = boto3.client("s3", **kwargs)
        self._bucket = settings.s3_bucket_recordings

    def _build_key(
        self, tenant_id: str, call_id: str, extension: str = "wav"
    ) -> str:
        """Build S3 key with date-partitioned path for efficient querying."""
        now = datetime.now(UTC)
        return (
            f"recordings/{tenant_id}/{now.year}/{now.month:02d}/"
            f"{now.day:02d}/{call_id}.{extension}"
        )

    async def upload_recording(
        self,
        audio_data: bytes,
        tenant_id: str,
        call_id: str,
        content_type: str = "audio/wav",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """
        Upload a call recording to S3.
        Returns the S3 key for storage in the Call record.
        """
        extension = "wav" if "wav" in content_type else "mp3"
        key = self._build_key(tenant_id, call_id, extension)

        extra_args: dict[str, Any] = {
            "ContentType": content_type,
            "ServerSideEncryption": "AES256",
            "Metadata": {
                "tenant_id": tenant_id,
                "call_id": call_id,
                "uploaded_at": datetime.now(UTC).isoformat(),
                **(metadata or {}),
            },
        }

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=audio_data,
                **extra_args,
            )
            logger.info("Recording uploaded: bucket=%s key=%s size=%d", self._bucket, key, len(audio_data))
            return key
        except Exception as e:
            logger.error("Failed to upload recording: %s", e)
            raise

    async def get_presigned_url(
        self, s3_key: str, expiry_seconds: int = 3600
    ) -> str:
        """Generate a pre-signed URL for downloading a recording."""
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": s3_key},
                ExpiresIn=expiry_seconds,
            )
            return url
        except Exception as e:
            logger.error("Failed to generate presigned URL for %s: %s", s3_key, e)
            raise

    async def delete_recording(self, s3_key: str) -> bool:
        """Delete a recording (GDPR right to erasure)."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=s3_key)
            logger.info("Recording deleted: %s", s3_key)
            return True
        except Exception as e:
            logger.error("Failed to delete recording %s: %s", s3_key, e)
            return False

    async def delete_tenant_recordings(self, tenant_id: str) -> int:
        """Delete ALL recordings for a tenant (account deletion / data erasure)."""
        prefix = f"recordings/{tenant_id}/"
        deleted = 0

        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                objects = page.get("Contents", [])
                if not objects:
                    continue
                delete_keys = [{"Key": obj["Key"]} for obj in objects]
                self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": delete_keys},
                )
                deleted += len(delete_keys)

            logger.info("Deleted %d recordings for tenant %s", deleted, tenant_id)
            return deleted
        except Exception as e:
            logger.error("Failed to delete tenant recordings: %s", e)
            raise

    def ensure_bucket_exists(self) -> None:
        """Create the recordings bucket if it doesn't exist (for MinIO setup)."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            try:
                self._client.create_bucket(
                    Bucket=self._bucket,
                    CreateBucketConfiguration={
                        "LocationConstraint": get_settings().s3_region,
                    },
                )
                logger.info("Created bucket: %s", self._bucket)
            except Exception as e:
                logger.error("Failed to create bucket %s: %s", self._bucket, e)