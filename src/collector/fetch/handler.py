"""Runtime handler that persists fetch evidence and raw artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts.enums import FetchOutcome, RouteState, SourceState
from collector.fetch.classify import BACKOFF, JITTER_FRACTION, classify_error
from collector.fetch.client import FetchRequest, FetchResult, RequestKind, SafeFetcher
from collector.fetch.config import FetchConfig
from collector.fetch.metrics import FETCH_METRICS, FetchMetrics
from collector.fetch.permits import PgOriginPermits
from collector.fetch.robots import (
    DEFAULT_ROBOTS_TTL,
    MAX_ROBOTS_BYTES,
    ROBOTS_POLICIES,
    latest_robots_snapshot,
    robots_allows,
    robots_url,
)
from collector.fetch.urls import UrlError, normalize_url
from collector.persistence.postgres.repositories import artifacts, queue, sources
from collector.storage import (
    ArtifactStore,
    ClaimedUploader,
    LazyS3ArtifactStore,
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
BROWSER_ROUTE_KINDS: Final = frozenset({"category", "detail"})
REQUEST_KINDS: Final = frozenset({"page", "sitemap", "robots", "api"})


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


def _request_kind(args: Mapping[str, object]) -> RequestKind:
    value = args.get("request_kind", "page")
    if not isinstance(value, str) or value not in REQUEST_KINDS:
        raise ValueError(f"fetch.http args.request_kind must be one of {sorted(REQUEST_KINDS)}")
    return cast(RequestKind, value)


class FetchHandler(TaskHandler):
    """`fetch.http` handler; queue acknowledgement remains owned by worker runtime."""

    def __init__(
        self,
        context: HandlerContext,
        fetcher: SafeFetcher,
        uploader: ClaimedUploader,
        store: ArtifactStore,
        *,
        raw_bucket: str = DEFAULT_RAW_BUCKET,
        robots_ttl: timedelta = DEFAULT_ROBOTS_TTL,
        user_agent: str = "UAWebDataCollector",
        metrics: FetchMetrics = FETCH_METRICS,
    ) -> None:
        self._context = context
        self._fetcher = fetcher
        self._uploader = uploader
        self._store = store
        self._raw_bucket = raw_bucket
        self._robots_ttl = robots_ttl
        self._user_agent = user_agent
        self._metrics = metrics

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
            request_kind = _request_kind(task.args)
            if request_kind == "robots":
                normalized = robots_url(normalized)
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
        if preflight.policy.robots_policy not in ROBOTS_POLICIES:
            return TaskResult.permanent("robots_policy_invalid")
        if preflight.route_state in {RouteState.CIRCUIT_OPEN, RouteState.UNSUPPORTED}:
            until = preflight.circuit_open_until or now + DEFER_DELAY
            return TaskResult.deferred(max(until, now + DEFER_DELAY), "route_not_available")

        if request_kind == "robots":
            async with self._context.sessions() as session:
                snapshot = await latest_robots_snapshot(session, task.source_id, normalized)
            if snapshot is not None and snapshot.fresh(now, self._robots_ttl):
                return TaskResult.success()
        elif request_kind == "page" and preflight.policy.robots_policy == "respect":
            robots_result = await self._enforce_robots(
                task,
                route_id,
                normalized,
                preflight.source_id,
                preflight.policy.robots_policy,
            )
            if robots_result is not None:
                return robots_result

        fetched = await self._fetcher.fetch(
            FetchRequest(
                normalized,
                request_kind=request_kind,
                job_id=task.job_id,
                if_none_match=validators.etag if validators else None,
                if_modified_since=validators.last_modified if validators else None,
            )
        )
        self._observe_fetch(preflight.source_id, fetched)
        if fetched.denied is not None:
            until = fetched.denied.retry_after or now + DEFER_DELAY
            return TaskResult.deferred(
                max(until, now + DEFER_DELAY), fetched.decision.error_code or "permit_denied"
            )
        if fetched.body is not None and fetched.decision.outcome is FetchOutcome.SUCCESS:
            try:
                await self._store_raw(
                    task,
                    route_id,
                    normalized,
                    fetched,
                    source_name=preflight.source_id,
                    enqueue_parse=request_kind != "robots",
                    request_variant="robots" if request_kind == "robots" else None,
                )
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
                browser_route_allowed=preflight.route_kind in BROWSER_ROUTE_KINDS,
                request_variant="robots" if request_kind == "robots" else None,
            )
        return self._task_result(fetched, now)

    async def _store_raw(
        self,
        task: Task,
        route_id: UUID,
        requested_url: str,
        fetched: FetchResult,
        *,
        source_name: str,
        enqueue_parse: bool = True,
        request_variant: str | None = None,
    ) -> None:
        body = fetched.body
        if body is None:  # defensive: caller routes only successful responses with a body
            raise ValueError("raw upload requires response body")
        sha256 = hashlib.sha256(body).hexdigest()
        key = raw_object_key(sha256)

        async def commit(session: AsyncSession, stored: StoredObject) -> UUID:
            fetch = await artifacts.record_fetch(
                session,
                self._fetch_record(
                    task,
                    requested_url,
                    fetched,
                    raw_sha256=sha256,
                    request_variant=request_variant,
                ),
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
            if enqueue_parse:
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

        result = await self._uploader.upload(
            bucket=self._raw_bucket,
            key=key,
            data=body,
            sha256=sha256,
            media_type=fetched.media_type or "application/octet-stream",
            commit=commit,
        )
        self._metrics.observe_raw(source_name, result.stored.size, deduplicated=result.deduplicated)

    async def _record_without_raw(
        self,
        task: Task,
        route_revision: int,
        route_id: UUID,
        requested_url: str,
        fetched: FetchResult,
        *,
        browser_allowed: bool,
        browser_route_allowed: bool = False,
        request_variant: str | None = None,
        apply_route_effects: bool = True,
    ) -> None:
        async with self._context.sessions() as session, session.begin():
            await artifacts.record_fetch(
                session,
                self._fetch_record(task, requested_url, fetched, request_variant=request_variant),
                now=self._context.clock(),
            )
            if fetched.decision.route_incident and apply_route_effects:
                await sources.set_route_state(
                    session,
                    route_id,
                    RouteState.CIRCUIT_OPEN,
                    expected_revision=route_revision,
                    actor=self._context.owner,
                    reason=fetched.decision.error_code or "route_incident",
                    now=self._context.clock(),
                )
                if browser_allowed and browser_route_allowed:
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
        request_variant: str | None = None,
    ) -> artifacts.FetchRecord:
        return artifacts.FetchRecord(
            requested_url=requested_url,
            outcome=fetched.decision.outcome,
            fetched_at=self._context.clock(),
            job_id=task.job_id,
            source_id=task.source_id,
            final_url=fetched.final_url,
            request_variant=request_variant or str(task.args.get("request_variant", "http")),
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

    def _observe_fetch(self, source: str, fetched: FetchResult) -> None:
        if fetched.status is not None:
            self._metrics.observe_http(source, fetched.status)
        if fetched.decision.error_code == "policy_blocked":
            self._metrics.observe_policy_block(source)

    async def _enforce_robots(
        self,
        task: Task,
        route_id: UUID,
        requested_url: str,
        source_name: str,
        policy: str,
    ) -> TaskResult | None:
        """Refresh stale robots evidence, then apply the configured policy to a page."""
        now = self._context.clock()
        async with self._context.sessions() as session:
            snapshot = await latest_robots_snapshot(session, task.source_id, requested_url)  # type: ignore[arg-type]
        previous_snapshot = snapshot
        if snapshot is None or not snapshot.fresh(now, self._robots_ttl):
            target = robots_url(requested_url)
            fetched = await self._fetcher.fetch(
                FetchRequest(target, request_kind="robots", job_id=task.job_id)
            )
            self._observe_fetch(source_name, fetched)
            if fetched.denied is not None:
                until = fetched.denied.retry_after or now + DEFER_DELAY
                return TaskResult.deferred(max(until, now + DEFER_DELAY), "robots_permit_denied")
            if fetched.body is not None and fetched.decision.outcome is FetchOutcome.SUCCESS:
                try:
                    await self._store_raw(
                        task,
                        route_id,
                        target,
                        fetched,
                        source_name=source_name,
                        enqueue_parse=False,
                        request_variant="robots",
                    )
                except UploadBusyError:
                    return TaskResult.deferred(now + DEFER_DELAY, "robots_upload_claim_busy")
            else:
                await self._record_without_raw(
                    task,
                    0,
                    route_id,
                    target,
                    fetched,
                    browser_allowed=False,
                    request_variant="robots",
                    apply_route_effects=False,
                )
                if (
                    fetched.status != 404
                    and fetched.decision.outcome is not FetchOutcome.SUCCESS
                    and previous_snapshot is None
                ):
                    return self._task_result(fetched, now)
            async with self._context.sessions() as session:
                snapshot = await latest_robots_snapshot(session, task.source_id, requested_url)  # type: ignore[arg-type]

        body: bytes | None = None
        if snapshot is not None and snapshot.sha256 is not None and snapshot.object_key is not None:
            body = await self._store.get(
                self._raw_bucket,
                snapshot.object_key,
                expected_sha256=snapshot.sha256,
                max_bytes=MAX_ROBOTS_BYTES,
            )
        if robots_allows(policy, body, requested_url, self._user_agent):
            return None

        snapshot_ref = snapshot.sha256 if snapshot is not None and snapshot.sha256 else "absent"
        blocked = FetchResult(
            classify_error(f"robots_disallowed:{snapshot_ref}", policy=True),
            requested_url,
            final_url=requested_url,
        )
        self._observe_fetch(source_name, blocked)
        await self._record_without_raw(
            task,
            0,
            route_id,
            requested_url,
            blocked,
            browser_allowed=False,
        )
        return self._task_result(blocked, now)

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
    store = LazyS3ArtifactStore(context.env)
    permits = PgOriginPermits(context.sessions, owner_instance=context.owner)
    config = FetchConfig.from_env(context.env)
    fetcher = SafeFetcher(config=config, permits=permits)
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
        robots_ttl=timedelta(seconds=int(context.env.get("COLLECTOR_ROBOTS_TTL_SECONDS", "86400"))),
        user_agent=config.user_agent,
    )


HANDLER_FACTORIES.setdefault(WorkerRole.FETCH, create_fetch_handler)

__all__ = ["FetchHandler", "create_fetch_handler"]
