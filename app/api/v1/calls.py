"""
# Call management routes (all require auth)
# POST   /calls/outbound — initiate outbound AI call via Twilio
#   1. Checks concurrent call capacity
#   2. Creates Twilio call with webhook URL
#   3. When answered, Twilio hits /webhooks/twilio/outbound
# GET    /calls/active — list currently active calls (from session_manager)
# GET    /calls — list call history (paginated, filterable by direction/status)
# GET    /calls/{id} — get call details + transcript
# DELETE /calls/{sid} — end an active call by telephony SID
"""

"""
Call management API routes.

Endpoints:
- POST   /calls/outbound      — Initiate an outbound call
- GET    /calls                — List calls (paginated, filtered)
- GET    /calls/active         — List currently active calls
- GET    /calls/{call_id}      — Get call details
- GET    /calls/{call_id}/transcript — Get call transcript
- DELETE /calls/{call_id}      — End an active call
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import get_settings
from app.middleware.auth import get_current_user, get_tenant_id
from app.schemas import (
    ActiveCallResponse,
    CallListResponse,
    CallResponse,
    OutboundCallRequest,
)
from app.voice.session_manager import call_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])


@router.post("/outbound", status_code=status.HTTP_201_CREATED)
async def initiate_outbound_call(
    request: OutboundCallRequest,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, str]:
    """
    Initiate an outbound AI voice call.

    The system will:
    1. Validate the phone number and check DNC lists
    2. Verify tenant has available concurrent call capacity
    3. Initiate the call via Twilio
    4. When answered, connect to the AI voice pipeline
    """
    settings = get_settings()

    # Check concurrent call capacity
    active = call_manager.get_active_count(tenant_id)
    # TODO: Load max_concurrent from tenant DB record
    max_concurrent = 5
    if active >= max_concurrent:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Concurrent call limit reached ({max_concurrent})",
        )

    # TODO: DNC check
    # TODO: Calling hours check

    # Initiate call via Twilio
    from app.voice.providers.telephony.twilio_provider import TwilioTelephonyProvider

    telephony = TwilioTelephonyProvider(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        phone_number=settings.twilio_phone_number,
    )

    webhook_url = f"{settings.twilio_webhook_base_url}/api/v1/webhooks/twilio/outbound"

    try:
        call_sid = await telephony.initiate_call(
            to_number=request.to_number,
            from_number=request.from_number or settings.twilio_phone_number,
            webhook_url=webhook_url,
            metadata={
                "tenant_id": tenant_id,
                "agent_id": str(request.agent_id),
                **(request.metadata or {}),
            },
        )
    except Exception as e:
        logger.error("Failed to initiate outbound call: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to initiate call: {e}",
        ) from e

    return {
        "call_sid": call_sid,
        "status": "initiated",
        "to_number": request.to_number,
    }


@router.get("/active", response_model=list[ActiveCallResponse])
async def list_active_calls(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
) -> list[dict[str, Any]]:
    """List all currently active calls for this tenant."""
    return call_manager.get_active_sessions(tenant_id)


@router.get("")
async def list_calls(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    direction: str | None = Query(None, pattern="^(inbound|outbound)$"),
    status_filter: str | None = Query(None, alias="status"),
) -> dict[str, Any]:
    """List calls with pagination and filtering."""
    # TODO: Query from database with filters
    return {
        "calls": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{call_id}")
async def get_call(
    call_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
) -> dict[str, Any]:
    """Get detailed call information."""
    # TODO: Query from database
    raise HTTPException(status_code=404, detail="Call not found")


@router.get("/{call_id}/transcript")
async def get_call_transcript(
    call_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
) -> dict[str, Any]:
    """Get the full transcript for a call."""
    # TODO: Query from database
    raise HTTPException(status_code=404, detail="Call not found")


@router.delete("/{call_sid}")
async def end_active_call(
    call_sid: str,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
) -> dict[str, str]:
    """End an active call by its telephony SID."""
    session = call_manager.get_session(call_sid)
    if not session:
        raise HTTPException(status_code=404, detail="Active call not found")

    if session.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Call belongs to another tenant")

    # End via telephony provider
    settings = get_settings()
    from app.voice.providers.telephony.twilio_provider import TwilioTelephonyProvider

    telephony = TwilioTelephonyProvider(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
    )
    await telephony.end_call(call_sid)

    return {"call_sid": call_sid, "status": "ending"}