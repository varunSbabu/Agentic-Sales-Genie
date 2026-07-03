"""Configuration endpoints — knowledge base + preferences + integrations.

Phase 3 wires up the KB endpoints. Integrations + preferences endpoints are
stubbed and will be expanded in Phase 6 (when connectors arrive).
"""

import json
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.factory import list_configured_connectors
from backend.connectors.notion_connector import NotionConnector
from backend.connectors.sheets_connector import SheetsConnector
from backend.connectors.supabase_connector import SupabaseConnector
from backend.db.models import KBDocument, UserIntegration
from backend.db.session import AsyncSessionLocal, get_db
from backend.rag.chunking import chunk_text
from backend.rag.ingestion import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE_BYTES,
    IngestionError,
    extract_text,
)
from backend.rag.vectorstore import add_chunks, collection_name_for, delete_document_chunks
from backend.utils.auth import CurrentUser, CurrentUserId
from backend.utils.logging import logger
from backend.utils.security import encrypt_secret

router = APIRouter(prefix="/config", tags=["configuration"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class KBDocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    file_type: str
    chunk_count: int
    status: str
    collection_name: str
    uploaded_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, doc: KBDocument) -> "KBDocumentOut":
        return cls(
            id=doc.id,
            filename=doc.filename,
            file_type=doc.file_type,
            chunk_count=doc.chunk_count,
            status=doc.status,
            collection_name=doc.collection_name,
            uploaded_at=doc.uploaded_at.isoformat() if doc.uploaded_at else "",
        )


class KBStatusOut(BaseModel):
    documents: list[KBDocumentOut]
    total_chunks: int


# ---------------------------------------------------------------------------
# Background ingestion task
# ---------------------------------------------------------------------------
async def _ingest_in_background(
    doc_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    content: bytes,
) -> None:
    """Run extraction + chunking + embedding outside the request lifecycle.

    Uses its own session because BackgroundTasks runs after the request's DB
    session has been closed.
    """
    async with AsyncSessionLocal() as session:
        doc = await session.get(KBDocument, doc_id)
        if doc is None:
            logger.error("doc {} vanished before ingestion ran", doc_id)
            return
        doc.status = "processing"
        await session.commit()

        try:
            text, kind = extract_text(filename, content)
            chunks = chunk_text(
                text,
                filename=filename,
                user_id=str(user_id),
                doc_id=str(doc_id),
            )
            added = add_chunks(user_id, chunks)
            doc.chunk_count = added
            doc.file_type = kind
            doc.status = "ready"
            await session.commit()
            logger.info("ingestion finished for doc {} ({} chunks)", doc_id, added)
        except IngestionError as exc:
            doc.status = "failed"
            await session.commit()
            logger.error("ingestion failed for doc {}: {}", doc_id, exc)
        except Exception as exc:
            doc.status = "failed"
            await session.commit()
            logger.exception("unexpected ingestion error for doc {}: {}", doc_id, exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post(
    "/kb/upload",
    response_model=KBDocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_kb_document(
    background_tasks: BackgroundTasks,
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
) -> KBDocumentOut:
    """Upload a PDF / DOCX / TXT framework. Returns immediately with status='pending';
    ingestion runs in the background and updates status to 'ready' or 'failed'.
    """
    filename = file.filename or "unknown"

    # Validate extension up-front (cheap check before reading the file)
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported file type {ext or '<none>'}; accepted: {sorted(ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file exceeds {MAX_FILE_SIZE_BYTES} bytes",
        )

    doc = KBDocument(
        user_id=current_user_id,
        filename=filename,
        file_type=ALLOWED_EXTENSIONS[ext],
        chunk_count=0,
        status="pending",
        collection_name=collection_name_for(current_user_id),
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    # Schedule ingestion to run after the response is returned.
    background_tasks.add_task(
        _ingest_in_background,
        doc.id,
        current_user_id,
        filename,
        content,
    )
    logger.info("queued ingestion for doc {} (user {})", doc.id, current_user_id)
    return KBDocumentOut.from_model(doc)


@router.get("/kb/status", response_model=KBStatusOut)
async def kb_status(
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> KBStatusOut:
    """Return all KB documents for the current user with processing status."""
    result = await db.execute(
        select(KBDocument)
        .where(KBDocument.user_id == current_user_id)
        .order_by(KBDocument.uploaded_at.desc())
    )
    docs = result.scalars().all()
    total = sum(d.chunk_count for d in docs)
    return KBStatusOut(
        documents=[KBDocumentOut.from_model(d) for d in docs],
        total_chunks=total,
    )


@router.delete("/kb/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb_document(
    doc_id: uuid.UUID,
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Remove a document and all its vectors. 404 if not owned by current user."""
    doc = await db.get(KBDocument, doc_id)
    if doc is None or doc.user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    deleted_chunks = delete_document_chunks(current_user_id, doc_id)
    await db.delete(doc)
    logger.info("deleted doc {} ({} chunks)", doc_id, deleted_chunks)


# ---------------------------------------------------------------------------
# Integrations — Notion / Sheets / Slack credentials (encrypted at rest)
# ---------------------------------------------------------------------------
class IntegrationsIn(BaseModel):
    """All fields optional — caller can update one integration at a time.
    Setting a field to empty string clears it; omitting it leaves it unchanged."""

    notion_token: Optional[str] = Field(default=None)
    notion_database_id: Optional[str] = Field(default=None)
    sheets_id: Optional[str] = Field(default=None)
    sheets_credentials: Optional[str] = Field(
        default=None,
        description="Full JSON content of a Google service-account key file (as a string).",
    )
    slack_token: Optional[str] = Field(default=None)
    slack_channel: Optional[str] = Field(default=None)
    slack_manager_dm: Optional[str] = Field(default=None)


class IntegrationsStatus(BaseModel):
    notion_configured: bool
    notion_database_id_set: bool
    sheets_configured: bool
    sheets_id_set: bool
    slack_configured: bool
    active_connectors: list[str]


def _is_set(v: Optional[str]) -> bool:
    return bool(v and v.strip())


@router.post("/integrations", response_model=IntegrationsStatus)
async def upsert_integrations(
    body: IntegrationsIn,
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> IntegrationsStatus:
    """Save / update CRM integrations. Tokens encrypted before storage.

    Pass `""` to clear a field, omit it (None) to leave it unchanged.
    """
    # Pre-validate the sheets_credentials JSON if provided (catches typos
    # before we encrypt + store).
    if body.sheets_credentials is not None and body.sheets_credentials.strip():
        try:
            parsed = json.loads(body.sheets_credentials)
            if not isinstance(parsed, dict) or "client_email" not in parsed:
                raise ValueError("missing required keys (client_email)")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"sheets_credentials is not valid Google service-account JSON: {exc}",
            )

    r = await db.execute(
        select(UserIntegration).where(UserIntegration.user_id == current_user_id)
    )
    row = r.scalar_one_or_none()
    if row is None:
        row = UserIntegration(user_id=current_user_id)
        db.add(row)
        await db.flush()

    # Apply each field if explicitly provided (None means "leave alone")
    def apply_encrypted(field_name: str, raw: Optional[str]) -> None:
        if raw is None:
            return
        if raw.strip() == "":
            setattr(row, field_name, None)
        else:
            setattr(row, field_name, encrypt_secret(raw))

    def apply_plain(field_name: str, raw: Optional[str]) -> None:
        if raw is None:
            return
        setattr(row, field_name, raw.strip() or None)

    apply_encrypted("notion_token", body.notion_token)
    apply_plain("notion_database_id", body.notion_database_id)
    apply_plain("sheets_id", body.sheets_id)
    apply_encrypted("sheets_credentials", body.sheets_credentials)
    apply_encrypted("slack_token", body.slack_token)
    apply_plain("slack_channel", body.slack_channel)
    apply_plain("slack_manager_dm", body.slack_manager_dm)

    await db.flush()
    await db.refresh(row)

    active = await list_configured_connectors(current_user_id)
    return IntegrationsStatus(
        notion_configured=_is_set(row.notion_token),
        notion_database_id_set=_is_set(row.notion_database_id),
        sheets_configured=_is_set(row.sheets_credentials),
        sheets_id_set=_is_set(row.sheets_id),
        slack_configured=_is_set(row.slack_token),
        active_connectors=active,
    )


@router.get("/integrations", response_model=IntegrationsStatus)
async def get_integrations(
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> IntegrationsStatus:
    """Return integration status flags (never the raw tokens)."""
    r = await db.execute(
        select(UserIntegration).where(UserIntegration.user_id == current_user_id)
    )
    row = r.scalar_one_or_none()
    active = await list_configured_connectors(current_user_id)
    if row is None:
        return IntegrationsStatus(
            notion_configured=False,
            notion_database_id_set=False,
            sheets_configured=False,
            sheets_id_set=False,
            slack_configured=False,
            active_connectors=active,
        )
    return IntegrationsStatus(
        notion_configured=_is_set(row.notion_token),
        notion_database_id_set=_is_set(row.notion_database_id),
        sheets_configured=_is_set(row.sheets_credentials),
        sheets_id_set=_is_set(row.sheets_id),
        slack_configured=_is_set(row.slack_token),
        active_connectors=active,
    )


# ---------------------------------------------------------------------------
# User preferences — alert thresholds + manager email + notification toggles
# ---------------------------------------------------------------------------
class PreferencesIn(BaseModel):
    alert_threshold_low: Optional[float] = Field(default=None, ge=0, le=5)
    alert_threshold_high: Optional[float] = Field(default=None, ge=0, le=5)
    notify_email: Optional[bool] = None
    notify_slack: Optional[bool] = None
    manager_email: Optional[str] = Field(default=None, max_length=255)


class PreferencesOut(BaseModel):
    alert_threshold_low: float
    alert_threshold_high: float
    notify_email: bool
    notify_slack: bool
    manager_email: Optional[str] = None


@router.put("/preferences", response_model=PreferencesOut)
async def update_preferences(
    body: PreferencesIn,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PreferencesOut:
    """Update alert thresholds + notification preferences. Any field left None
    is unchanged. Sets `manager_email=None` if the caller passes an empty string."""
    if body.alert_threshold_low is not None:
        current_user.alert_threshold_low = float(body.alert_threshold_low)
    if body.alert_threshold_high is not None:
        current_user.alert_threshold_high = float(body.alert_threshold_high)
    if body.notify_email is not None:
        current_user.notify_email = bool(body.notify_email)
    if body.notify_slack is not None:
        current_user.notify_slack = bool(body.notify_slack)
    if body.manager_email is not None:
        current_user.manager_email = body.manager_email.strip() or None

    if (
        current_user.alert_threshold_low is not None
        and current_user.alert_threshold_high is not None
        and current_user.alert_threshold_low >= current_user.alert_threshold_high
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="alert_threshold_low must be strictly less than alert_threshold_high",
        )

    await db.flush()
    return PreferencesOut(
        alert_threshold_low=current_user.alert_threshold_low,
        alert_threshold_high=current_user.alert_threshold_high,
        notify_email=current_user.notify_email,
        notify_slack=current_user.notify_slack,
        manager_email=current_user.manager_email,
    )


@router.get("/preferences", response_model=PreferencesOut)
async def get_preferences(current_user: CurrentUser) -> PreferencesOut:
    return PreferencesOut(
        alert_threshold_low=current_user.alert_threshold_low,
        alert_threshold_high=current_user.alert_threshold_high,
        notify_email=current_user.notify_email,
        notify_slack=current_user.notify_slack,
        manager_email=current_user.manager_email,
    )


@router.post("/integrations/test/{connector}")
async def test_integration(
    connector: str,
    current_user_id: CurrentUserId,
) -> dict:
    """Trigger a test_connection call against a single connector."""
    c_map = {
        "supabase": SupabaseConnector,
        "notion": NotionConnector,
        "sheets": SheetsConnector,
    }
    cls = c_map.get(connector)
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown connector {connector!r}; choose from {sorted(c_map)}",
        )
    result = await cls().test_connection(current_user_id)
    return result.as_dict()
