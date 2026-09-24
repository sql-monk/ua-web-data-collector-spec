"""Runtime handler that persists fetch evidence and raw artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts.enums import FetchOutcome, RouteState, SourceState
from collector.fetch.classify import BACKOFF, JITTER_FRACTION
from collector.fetch.client import FetchRequest, FetchResult, SafeFetcher
from collector.fetch.config import FetchConfig
from collector.fetch.permits import PgOriginPermits
from collector.fetch.urls import UrlError, normalize_url
from collector.persistence.postgres.repositories import artifacts, queue, sources
from collector.storage import (
    ClaimedUploader,
    S3ArtifactStore,
    StorageSettings,
    StoredObject,
    UploadBusyError,
    raw_object_key,
)
from collector.workers.handlers import (
    HANDLER_FACTORIES,
    HandlerContext,
    RetrySchedule,
    Task,
    TaskHandler,
    TaskResult,
)
from collector.workers.roles import WorkerRole

RAW_BUCKET_ENV: Final = "COLLECTOR_RAW_BUCKET"
DEFAULT_RAW_BUCKET: Final = "raw"
DEFER_DELAY: Final = timedelta(seconds=5)


def _required_text(args: Mapping[str, object], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"fetch.http args.{name} must be a non-empty string")
    return value


def _required_uuid(args: Mapping[str, object], name: str) -> UUID:
    try:
        return UUID(_required_text(args, name))
    except ValueError as exc:
        raise ValueError(f"fetch.http args.{name} must be a UUID") from exc


class FetchHandler(TaskHandler):
    """`fetch.http` handler; queue acknowledgement remains owned by worker runtime."""

    def __init__(
        self,
        context: HandlerContext,
        fetcher: SafeFetcher,
        uploader: ClaimedUploader,
        store: S3ArtifactStore,
        *,
        raw_bucket: str = DEFAULT_RAW_BUCKET,
    ) -> None:
        self._context = context
        self._fetcher = fetcher
        self._uploader = uploader
        self._store = store
        self._raw_bucket = raw_bucket

    @property
    def job_types(self) -> tuple[str, ...]:
        return ("fetch.http",)

    @property
    def retry_schedule(self) -> RetrySchedule:
        return RetrySchedule(BACKOFF, jitter_ratio=JITTER_FRACTION)

    async def check_ready(self) -> None:
        await self._store.check_ready(self._raw_bucket)
        async with self._context.sessions() as session:
            await session.execute(text("SELECT 1"))

    async def handle(self, task: Task) -> TaskResult:
        if task.source_id is None:
            return TaskResult.permanent("fetch_source_missing")
        try:
            route_id = _required_uuid(task.args, "route_id")
            normalized = normalize_url(_required_text(task.args, "url")).normalized
        except (ValueError, UrlError) as exc:
            return TaskResult.permanent("fetch_args_invalid", str(exc))

        async with self._context.sessions() as session:
            preflight = await sources.get_fetch_preflight(session, task.source_id, route_id)
            validators = (
                await artifacts.latest_validators(session, task.source_id, normalized)
                if preflight is not None
                else None
            )
        now = self._context.clock()
        if preflight is None:
            return TaskResult.permanent("fetch_preflight_missing")
        if preflight.source_state is not SourceState.ENABLED:
            return TaskResult.deferred(now + DEFER_DELAY, "source_not_enabled")
        if preflight.policy is None:
            return TaskResult.permanent("source_policy_missing")
        if preflight.route_state in {RouteState.CIRCUIT_OPEN, RouteState.UNSUPPORTED}:
            until = preflight.circuit_open_until or now + DEFER_DELAY
            return TaskResult.deferred(max(until, now + DEFER_DELAY), "route_not_available")

        fetched = await self._fetcher.fetch(
            FetchRequest(
                normalized,
                job_id=task.job_id,
                if_none_match=validators.etag if validators else None,
                if_modified_since=validators.last_modified if validators else None,
            )
        )
        if fetched.denied is not None:
            until = fetched.denied.retry_after or now + DEFER_DELAY
            return TaskResult.deferred(
                max(until, now + DEFER_DELAY), fetched.decision.error_code or "permit_denied"
            )
        if fetched.body is not None and fetched.decision.outcome is FetchOutcome.SUCCESS:
            try:
                await self._store_raw(task, route_id, normalized, fetched)
            except UploadBusyError:
                return TaskResult.deferred(now + DEFER_DELAY, "upload_claim_busy")
        else:
            await self._record_without_raw(
                task,
                preflight.route_revision,
                route_id,
                normalized,
                fetched,
                browser_allowed=preflight.policy.browser_allowed,
            )
        return self._task_result(fetched, now)

    async def _store_raw(
        self, task: Task, route_id: UUID, requested_url: str, fetched: FetchResult
    ) -> None:
        body = fetched.body
        if body is None:  # defensive: caller routes only successful responses with a body
            raise ValueError("raw upload requires response body")
        sha256 = hashlib.sha256(body).hexdigest()
        key = raw_object_key(sha256)

        async def commit(session: AsyncSession, stored: StoredObject) -> UUID:
            fetch = await artifacts.record_fetch(
                session,
                self._fetch_record(task, requested_url, fetched, raw_sha256=sha256),
                now=self._context.clock(),
            )
            await artifacts.record_raw_object(
                session,
                sha256=sha256,
                object_key=key,
                uri=stored.uri,
                size_bytes=stored.size,
                media_type=stored.media_type or fetched.media_type or "application/octet-stream",
                content_encoding=fetched.headers.get("content-encoding"),
                first_fetch_id=fetch.fetch_id,
                now=self._context.clock(),
            )
            await queue.enqueue(
                session,
                queue.NewJob(
                    job_type="parse.raw",
                    idempotency_key=f"parse:{fetch.fetch_id}",
                    args={
                        "fetch_id": str(fetch.fetch_id),
                        "raw_sha256": sha256,
                        "raw_uri": stored.uri,
                        "route_id": str(route_id),
                    },
                    max_attempts=4,
                    run_id=task.run_id,
                    source_id=task.source_id,
                ),
                now=self._context.clock(),
            )
            await sources.reset_route_failures(session, route_id, now=self._context.clock())
            return fetch.fetch_id

        await self._uploader.upload(
            bucket=self._raw_bucket,
            key=key,
            data=body,
            sha256=sha256,
            media_type=fetched.media_type or "application/octet-stream",
            commit=commit,
        )

    async def _record_without_raw(
        self,
        task: Task,
        route_revision: int,
        route_id: UUID,
        requested_url: str,
        fetched: FetchResult,
        *,
        browser_allowed: bool,
    ) -> None:
        async with self._context.sessions() as session, session.begin():
            await artifacts.record_fetch(
                session,
                self._fetch_record(task, requested_url, fetched),
                now=self._context.clock(),
            )
            if fetched.decision.route_incident:
                await sources.set_route_state(
                    session,
                    route_id,
                    RouteState.CIRCUIT_OPEN,
                    expected_revision=route_revision,
                    actor=self._context.owner,
                    reason=fetched.decision.error_code or "route_incident",
                    now=self._context.clock(),
                )
                if browser_allowed:
                    await queue.enqueue(
                        session,
                        queue.NewJob(
                            job_type="browser.render",
                            idempotency_key=f"browser:{task.job_id}",
                            args={**task.args, "request_variant": "browser"},
                            max_attempts=2,
                            run_id=task.run_id,
                            source_id=task.source_id,
                        ),
                        now=self._context.clock(),
                    )
            elif fetched.decision.outcome is FetchOutcome.SUCCESS:
                await sources.reset_route_failures(session, route_id, now=self._context.clock())

    def _fetch_record(
        self,
        task: Task,
        requested_url: str,
        fetched: FetchResult,
        *,
        raw_sha256: str | None = None,
    ) -> artifacts.FetchRecord:
        return artifacts.FetchRecord(
            requested_url=requested_url,
            outcome=fetched.decision.outcome,
            fetched_at=self._context.clock(),
            job_id=task.job_id,
            source_id=task.source_id,
            final_url=fetched.final_url,
            request_variant=str(task.args.get("request_variant", "http")),
            http_status=fetched.status,
            content_access=fetched.decision.content_access,
            content_type=fetched.headers.get("content-type"),
            content_encoding=fetched.headers.get("content-encoding"),
            etag=fetched.headers.get("etag"),
            last_modified_raw=fetched.headers.get("last-modified"),
            response_bytes=fetched.decoded_bytes,
            raw_sha256=raw_sha256,
            error_code=fetched.decision.error_code,
            error_message=fetched.decision.reason,
        )

    @staticmethod
    def _task_result(fetched: FetchResult, now: datetime) -> TaskResult:
        decision = fetched.decision
        if decision.outcome is FetchOutcome.SUCCESS:
            return TaskResult.success()
        if decision.outcome is FetchOutcome.RETRYABLE:
            not_before = now + decision.retry_after if decision.retry_after else None
            return TaskResult.retryable(decision.error_code or "fetch_retry", not_before=not_before)
        return TaskResult.permanent(decision.error_code or "fetch_permanent", decision.reason)


def create_fetch_handler(context: HandlerContext) -> FetchHandler:
    settings = StorageSettings.from_env(context.env)
    store = S3ArtifactStore(settings)
    permits = PgOriginPermits(context.sessions, owner_instance=context.owner)
    fetcher = SafeFetcher(config=FetchConfig.from_env(context.env), permits=permits)
    uploader = ClaimedUploader(
        context.sessions,
        store,
        owner=context.owner,
        clock=context.clock,
    )
    return FetchHandler(
        context,
        fetcher,
        uploader,
        store,
        raw_bucket=context.env.get(RAW_BUCKET_ENV, DEFAULT_RAW_BUCKET),
    )


HANDLER_FACTORIES.setdefault(WorkerRole.FETCH, create_fetch_handler)

__all__ = ["FetchHandler", "create_fetch_handler"]
