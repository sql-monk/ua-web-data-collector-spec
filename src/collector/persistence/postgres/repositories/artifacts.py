"""Artifact store lineage і claimed/verified PUT protocol (§10 п.5, п.7; R-38, R-41).

Операції: `record_fetch`, `record_raw_object`, `acquire_upload_claim`, `commit_reference`,
`release_claim`, `expire_claims`, `list_orphan_candidates`, `get_claim`.

Протокол завантаження (§10 п.5) у трьох кроках producer-а:

1. `acquire_upload_claim(object_key, owner, lease)` — одна коротка транзакція, повертає
   `claim_generation` (fencing token);
2. producer пише bytes у S3/MinIO за `object_key`, робить HEAD/checksum/size verification —
   **поза** транзакцією PostgreSQL;
3. `commit_reference(object_key, generation, ...)` — друга коротка транзакція з предикатом
   `owner + generation + lease_expires_at > now`. Якщо за час запису хтось зробив reacquire
   (generation зросла) або lease сплив — `StaleClaimError`, і producer починає з кроку 1.

Transaction boundary усіх функцій — викликач; `acquire_upload_claim`/`commit_reference` мають
бути **окремими короткими** транзакціями, бо тримають row lock на ключі об'єкта.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, and_, exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import NormalizedArtifactRef, new_entity_id
from collector.contracts.enums import ContentAccess, FetchOutcome, UploadClaimStatus
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import NotFoundError, StaleClaimError
from collector.persistence.postgres.models import (
    ArtifactUploadClaim,
    Fetch,
    NormalizedArtifact,
    RawObject,
)


@dataclass(frozen=True, slots=True)
class FetchRecord:
    """Результат однієї HTTP-спроби (§9.1 `fetches`); `raw_sha256` заповнюється для 200/206."""

    requested_url: str
    outcome: FetchOutcome
    fetched_at: datetime
    job_id: UUID | None = None
    source_id: UUID | None = None
    final_url: str | None = None
    request_variant: str | None = None
    http_method: str = "GET"
    http_status: int | None = None
    content_access: ContentAccess = ContentAccess.UNKNOWN
    content_type: str | None = None
    content_encoding: str | None = None
    etag: str | None = None
    last_modified_raw: str | None = None
    response_bytes: int | None = None
    duration_ms: int | None = None
    raw_sha256: str | None = None
    error_code: str | None = None
    error_message: str | None = None


async def record_fetch(
    session: AsyncSession, record: FetchRecord, *, now: datetime | None = None
) -> Fetch:
    """Вставляє рядок `fetches`. Transaction boundary: викликач (зазвичай разом із
    `record_raw_object` і `queue.complete`)."""
    current = resolve_now(now)
    fetch = Fetch(
        fetch_id=new_entity_id(),
        fetched_at=record.fetched_at,
        job_id=record.job_id,
        source_id=record.source_id,
        requested_url=record.requested_url,
        final_url=record.final_url,
        request_variant=record.request_variant,
        http_method=record.http_method,
        http_status=record.http_status,
        outcome=record.outcome.value,
        content_access=record.content_access.value,
        content_type=record.content_type,
        content_encoding=record.content_encoding,
        etag=record.etag,
        last_modified_raw=record.last_modified_raw,
        response_bytes=record.response_bytes,
        duration_ms=record.duration_ms,
        raw_sha256=record.raw_sha256,
        error_code=record.error_code,
        error_message=record.error_message,
        created_at=current,
    )
    session.add(fetch)
    await session.flush()
    return fetch


async def record_raw_object(
    session: AsyncSession,
    *,
    sha256: str,
    object_key: str,
    uri: str,
    size_bytes: int,
    media_type: str,
    content_encoding: str | None = None,
    first_fetch_id: UUID | None = None,
    now: datetime | None = None,
) -> RawObject:
    """Content-addressed pointer (§9.3 п.4): ті самі bytes → той самий рядок, без дубля.

    Повторний виклик повертає **існуючий** рядок і не переписує `first_fetch_id`/`first_seen_at`
    — це lineage першої появи bytes, а не останньої.
    """
    current = resolve_now(now)
    stmt = (
        pg_insert(RawObject)
        .values(
            raw_object_id=new_entity_id(),
            sha256=sha256,
            object_key=object_key,
            uri=uri,
            size_bytes=size_bytes,
            media_type=media_type,
            content_encoding=content_encoding,
            first_fetch_id=first_fetch_id,
            first_seen_at=current,
            created_at=current,
        )
        .on_conflict_do_nothing(index_elements=[RawObject.sha256])
        .returning(RawObject)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is not None:
        return inserted
    existing = (
        await session.execute(
            select(RawObject)
            .where(RawObject.sha256 == sha256)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if existing is None:  # pragma: no cover — можливо лише поза READ COMMITTED
        msg = f"raw object {sha256} зник між INSERT і SELECT (потрібен READ COMMITTED)"
        raise NotFoundError(msg)
    return existing


async def acquire_upload_claim(
    session: AsyncSession,
    object_key: str,
    owner: str,
    *,
    lease_seconds: int,
    media_type: str | None = None,
    now: datetime | None = None,
) -> ArtifactUploadClaim:
    """Бере (або перебирає) claim на `object_key` (§10 п.5, R-41).

    - ключа ще немає → новий claim, `claim_generation = 1`;
    - ключ є, але claim не `leased` або lease прострочений → **reacquire**: атомарно
      `claim_generation + 1`, новий owner і новий lease;
    - ключ є з живим lease іншого owner → `StaleClaimError` (другий producer не бере ключ);
    - ключ є з живим lease того самого owner → **продовження** lease без інкременту generation
      (повтор власного виклику ідемпотентний і не інвалідує власний fencing token);
    - ключ уже `committed` → `StaleClaimError` (об'єкт незмінний: content-addressed ключ уже
      має DB reference; перезапис ламав би посилання, що вже роздані).

    Transaction boundary: викликач, **коротка окрема транзакція** — тримає row lock на ключі.
    """
    if lease_seconds < 1:
        msg = "lease_seconds має бути >= 1"
        raise ValueError(msg)
    current = resolve_now(now)
    expires = current + timedelta(seconds=lease_seconds)
    inserted = (
        await session.execute(
            pg_insert(ArtifactUploadClaim)
            .values(
                claim_id=new_entity_id(),
                object_key=object_key,
                owner=owner,
                status=UploadClaimStatus.LEASED.value,
                claim_generation=1,
                acquired_at=current,
                lease_expires_at=expires,
                media_type=media_type,
                created_at=current,
                updated_at=current,
            )
            .on_conflict_do_nothing(index_elements=[ArtifactUploadClaim.object_key])
            .returning(ArtifactUploadClaim)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted

    claim = await session.scalar(
        select(ArtifactUploadClaim)
        .where(ArtifactUploadClaim.object_key == object_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if claim is None:  # pragma: no cover — можливо лише поза READ COMMITTED
        msg = f"upload claim {object_key!r} зник між INSERT і SELECT"
        raise NotFoundError(msg)
    if claim.status == UploadClaimStatus.COMMITTED.value:
        msg = (
            f"upload claim {object_key!r}: об'єкт уже committed "
            f"(generation {claim.claim_generation})"
        )
        raise StaleClaimError(msg)
    alive = claim.status == UploadClaimStatus.LEASED.value and claim.lease_expires_at > current
    if alive and claim.owner != owner:
        msg = (
            f"upload claim {object_key!r}: живий lease належить {claim.owner!r} "
            f"до {claim.lease_expires_at.isoformat()}"
        )
        raise StaleClaimError(msg)
    if not alive:
        # Reacquire: старий generation стає недійсним — його commit більше не пройде.
        claim.claim_generation += 1
        claim.owner = owner
        claim.acquired_at = current
        claim.released_at = None
        claim.status = UploadClaimStatus.LEASED.value
    if media_type is not None:
        claim.media_type = media_type
    claim.lease_expires_at = expires
    claim.updated_at = current
    await session.flush()
    return claim


async def commit_reference(
    session: AsyncSession,
    object_key: str,
    generation: int,
    *,
    owner: str,
    sha256: str,
    size_bytes: int,
    uri: str,
    media_type: str | None = None,
    now: datetime | None = None,
) -> ArtifactUploadClaim:
    """Створює DB reference на завантажений об'єкт під fencing-предикатом (§10 п.5, R-41).

    Предикат — `object_key = :key AND owner = :owner AND claim_generation = :generation AND
    status = 'leased' AND lease_expires_at > now` — виконується **одним UPDATE**, тому між
    перевіркою і записом немає вікна: stale generation фізично не може створити reference.
    Невиконаний предикат → `StaleClaimError`.

    Повторний commit тим самим generation/owner із тим самим `sha256` ідемпотентний (повертає
    наявний committed claim); з **іншим** `sha256` — `StaleClaimError`, бо це вже інший об'єкт
    за тим самим ключем.

    Transaction boundary: викликач; зазвичай одна транзакція з `record_raw_object` або
    `record_parse_result`.
    """
    current = resolve_now(now)
    committed = await session.scalar(
        update(ArtifactUploadClaim)
        .where(
            ArtifactUploadClaim.object_key == object_key,
            ArtifactUploadClaim.owner == owner,
            ArtifactUploadClaim.claim_generation == generation,
            ArtifactUploadClaim.status == UploadClaimStatus.LEASED.value,
            ArtifactUploadClaim.lease_expires_at > current,
        )
        .values(
            status=UploadClaimStatus.COMMITTED.value,
            committed_at=current,
            sha256=sha256,
            size_bytes=size_bytes,
            uri=uri,
            media_type=media_type or ArtifactUploadClaim.media_type,
            updated_at=current,
        )
        .returning(ArtifactUploadClaim)
        .execution_options(populate_existing=True)
    )
    if committed is not None:
        return committed
    existing = await session.scalar(
        select(ArtifactUploadClaim)
        .where(ArtifactUploadClaim.object_key == object_key)
        .execution_options(populate_existing=True)
    )
    if existing is None:
        msg = f"upload claim {object_key!r} не знайдено — спершу acquire_upload_claim"
        raise NotFoundError(msg)
    if (
        existing.status == UploadClaimStatus.COMMITTED.value
        and existing.claim_generation == generation
        and existing.owner == owner
        and existing.sha256 == sha256
    ):
        return existing
    msg = (
        f"upload claim {object_key!r}: commit predicate не виконано "
        f"(owner={existing.owner!r} vs {owner!r}, generation={existing.claim_generation} vs "
        f"{generation}, status={existing.status!r}, lease_expires_at="
        f"{existing.lease_expires_at.isoformat()}, now={current.isoformat()})"
    )
    raise StaleClaimError(msg)


async def release_claim(
    session: AsyncSession,
    object_key: str,
    generation: int,
    *,
    owner: str,
    now: datetime | None = None,
) -> ArtifactUploadClaim | None:
    """Добровільне повернення claim (producer вирішив не писати об'єкт). Ідемпотентно.

    Committed claim **не** звільняється (DB reference уже існує) — повертається як є;
    невідповідність owner/generation → `None` (нічого не змінено), бо це вже чужий claim.
    """
    current = resolve_now(now)
    released = await session.scalar(
        update(ArtifactUploadClaim)
        .where(
            ArtifactUploadClaim.object_key == object_key,
            ArtifactUploadClaim.owner == owner,
            ArtifactUploadClaim.claim_generation == generation,
            ArtifactUploadClaim.status == UploadClaimStatus.LEASED.value,
        )
        .values(status=UploadClaimStatus.RELEASED.value, released_at=current, updated_at=current)
        .returning(ArtifactUploadClaim)
        .execution_options(populate_existing=True)
    )
    if released is not None:
        return released
    existing = await session.scalar(
        select(ArtifactUploadClaim).where(ArtifactUploadClaim.object_key == object_key)
    )
    if existing is not None and existing.status in {
        UploadClaimStatus.RELEASED.value,
        UploadClaimStatus.COMMITTED.value,
    }:
        return existing
    return None


async def expire_claims(
    session: AsyncSession, *, limit: int = 1000, now: datetime | None = None
) -> list[str]:
    """`leased` з простроченим lease → `expired`; повертає object keys (maintenance WP-12).

    Не обов'язкова для коректності — `acquire_upload_claim` і так перебирає прострочений
    claim, — але робить стан таблиці читабельним і дає sweeper-у чіткий перелік кандидатів.
    """
    current = resolve_now(now)
    stale = (
        select(ArtifactUploadClaim.claim_id)
        .where(
            ArtifactUploadClaim.status == UploadClaimStatus.LEASED.value,
            ArtifactUploadClaim.lease_expires_at <= current,
        )
        .order_by(ArtifactUploadClaim.lease_expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(
        update(ArtifactUploadClaim)
        .where(ArtifactUploadClaim.claim_id.in_(stale))
        .values(status=UploadClaimStatus.EXPIRED.value, updated_at=current)
        .returning(ArtifactUploadClaim.object_key)
    )
    return list(result.scalars().all())


def _orphan_candidates_query(deadline: datetime, limit: int) -> Select[tuple[str]]:
    referenced_normalized = exists().where(
        NormalizedArtifact.object_key == ArtifactUploadClaim.object_key
    )
    referenced_raw = exists().where(RawObject.object_key == ArtifactUploadClaim.object_key)
    return (
        select(ArtifactUploadClaim.object_key)
        .where(
            and_(
                ArtifactUploadClaim.status != UploadClaimStatus.COMMITTED.value,
                ArtifactUploadClaim.lease_expires_at <= deadline,
                ~referenced_normalized,
                ~referenced_raw,
            )
        )
        .order_by(ArtifactUploadClaim.lease_expires_at, ArtifactUploadClaim.object_key)
        .limit(limit)
    )


async def list_orphan_candidates(
    session: AsyncSession,
    *,
    grace: timedelta,
    limit: int = 1000,
    now: datetime | None = None,
) -> list[str]:
    """Object keys, які sweeper (WP-02/WP-12) має право видалити з artifact store (§10 п.5).

    Кандидат — ключ, для якого одночасно:

    - **немає DB reference**: claim не `committed` і на ключ не посилається ані
      `normalized_artifacts`, ані `raw_objects` (друга перевірка — захист на випадок, коли
      claim-рядок прибрали ретеншеном, а pointer лишився);
    - **немає живого claim**: lease сплив більш ніж `grace` тому.

    `grace` має перевищувати найдовше вікно «PUT + HEAD + commit», інакше sweeper змагається з
    producer-ом між HEAD і commit (R-38). Функція нічого не змінює і **сама по собі не дає
    права видаляти** — між SELECT і DELETE новий producer може взяти ключ.

    **Обов'язковий fencing-протокол sweeper-а (gate 3, CR-4)** — для кожного кандидата:

    1. `acquire_upload_claim(key, owner=<sweeper>, lease_seconds=…)` окремою короткою
       транзакцією. `StaleClaimError` (живий lease producer-а або ключ уже `committed`) →
       ключ пропускається: його хтось використовує;
    2. успішний claim робить sweeper єдиним власником ключа: producer, що прийде тепер,
       отримає `StaleClaimError` на acquire, а старий producer не закомітить свою
       `claim_generation` (вона вже менша за поточну);
    3. видалити об'єкт з artifact store, поки lease sweeper-а живий;
    4. `release_claim(key, generation, owner=<sweeper>)`. Наступний producer перебере ключ з
       `claim_generation + 1`, виконає PUT + HEAD заново і лише тоді commit-не reference.

    Transaction boundary: викликач; один SELECT без блокувань.
    """
    if grace < timedelta(0):
        msg = "grace має бути >= 0"
        raise ValueError(msg)
    deadline = resolve_now(now) - grace
    return list((await session.execute(_orphan_candidates_query(deadline, limit))).scalars().all())


async def get_claim(session: AsyncSession, object_key: str) -> ArtifactUploadClaim | None:
    """Claim за object key (unique). Transaction boundary: викликач; один SELECT."""
    stmt = select(ArtifactUploadClaim).where(ArtifactUploadClaim.object_key == object_key)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_normalized_artifact(
    session: AsyncSession, artifact_id: UUID
) -> NormalizedArtifact | None:
    """Pointer на normalized artifact за PK. Transaction boundary: викликач."""
    return await session.get(NormalizedArtifact, artifact_id)


def normalized_artifact_values(
    ref: NormalizedArtifactRef, *, object_key: str, now: datetime
) -> dict[str, object]:
    """Контракт `NormalizedArtifactRef` → колонки `normalized_artifacts`.

    Єдине місце мапінгу контракт↔таблиця: `record_parse_result` і тести користуються ним, тому
    нове поле контракту не «загубиться» у двох різних INSERT.
    """
    return {
        "artifact_id": new_entity_id(),
        "object_key": object_key,
        "entity_uuid": ref.entity_uuid,
        "domain": ref.domain.value,
        "uri": ref.uri,
        "sha256": ref.sha256,
        "size_bytes": ref.size_bytes,
        "media_type": ref.media_type,
        "schema_version": ref.schema_version,
        "parser_version": ref.parser_version,
        "fetch_id": ref.fetch_id,
        "raw_sha256": ref.raw_sha256,
        "raw_uri": ref.raw_uri,
        "produced_at": ref.produced_at,
        "created_at": now,
    }


__all__ = [
    "FetchRecord",
    "acquire_upload_claim",
    "commit_reference",
    "expire_claims",
    "get_claim",
    "get_normalized_artifact",
    "list_orphan_candidates",
    "normalized_artifact_values",
    "record_fetch",
    "record_raw_object",
    "release_claim",
]
