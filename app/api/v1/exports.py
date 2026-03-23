"""
# Data export routes (all require auth)
# GET /exports/calls.csv — export call history as CSV
#   Fields: call_id, direction, status, outcome, numbers, duration, latency, sentiment, cost
# GET /exports/calls/{id}/transcript.csv — export single call transcript as CSV
#   Fields: role, content, timestamp
"""

"""
Export API — CSV export of call data and transcripts.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.call_repo import CallRepository
from app.db.session import get_db
from app.middleware.auth import get_current_user, get_tenant_id

router = APIRouter(prefix="/exports", tags=["exports"])


@router.get("/calls.csv")
async def export_calls_csv(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 1000,
) -> StreamingResponse:
    """Export call history as CSV."""
    call_repo = CallRepository(db)
    calls, total = await call_repo.list_calls(
        tenant_id=uuid.UUID(tenant_id), limit=limit
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "call_id", "direction", "status", "outcome", "from_number", "to_number",
        "duration_seconds", "turn_count", "avg_latency_ms", "sentiment",
        "estimated_cost_usd", "created_at",
    ])

    for call in calls:
        writer.writerow([
            str(call.id), call.direction, call.status, call.outcome or "",
            call.from_number, call.to_number, call.duration_seconds or 0,
            call.turn_count, call.avg_response_latency_ms or 0,
            call.sentiment or "", call.estimated_cost_usd or 0,
            call.created_at.isoformat() if call.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=calls_export.csv"},
    )


@router.get("/calls/{call_id}/transcript.csv")
async def export_transcript_csv(
    call_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Export a single call's transcript as CSV."""
    call_repo = CallRepository(db)
    call = await call_repo.get_by_id(call_id, uuid.UUID(tenant_id))
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if not call.transcript:
        raise HTTPException(status_code=404, detail="No transcript available")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["role", "content", "timestamp"])

    for turn in call.transcript:
        writer.writerow([
            turn.get("role", ""),
            turn.get("content", ""),
            turn.get("timestamp", ""),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=transcript_{call_id}.csv"
        },
    )