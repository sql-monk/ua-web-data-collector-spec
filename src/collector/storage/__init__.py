"""Shared artifact/object storage API (owner WP-02)."""

from collector.storage.keys import normalized_object_key, put_normalized, raw_object_key
from collector.storage.store import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStore,
    ArtifactTooLargeError,
    LazyS3ArtifactStore,
    ObjectHead,
    S3ArtifactStore,
    S3Credentials,
    StorageConfigError,
    StorageError,
    StorageSettings,
    StoredObject,
    load_credentials,
)
from collector.storage.sweeper import SweepReport, sweep_orphans
from collector.storage.upload import (
    ClaimedUploader,
    ClaimedUploadResult,
    UploadBusyError,
    UploadRetriesExhaustedError,
)

__all__ = [
    "ArtifactIntegrityError",
    "ArtifactNotFoundError",
    "ArtifactStore",
    "ArtifactTooLargeError",
    "ClaimedUploadResult",
    "ClaimedUploader",
    "LazyS3ArtifactStore",
    "ObjectHead",
    "S3ArtifactStore",
    "S3Credentials",
    "StorageConfigError",
    "StorageError",
    "StorageSettings",
    "StoredObject",
    "SweepReport",
    "UploadBusyError",
    "UploadRetriesExhaustedError",
    "load_credentials",
    "normalized_object_key",
    "put_normalized",
    "raw_object_key",
    "sweep_orphans",
]
