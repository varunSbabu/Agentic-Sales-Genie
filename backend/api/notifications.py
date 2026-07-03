"""Notification test endpoints — smoke-check SendGrid + Slack from the dev console."""

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from backend.notifications.email import EmailDeliveryError, send_test_email
from backend.utils.auth import CurrentUser
from backend.utils.logging import logger

router = APIRouter(prefix="/notifications", tags=["notifications"])


class TestEmailRequest(BaseModel):
    to_email: Optional[EmailStr] = None  # falls back to current user's email


class TestEmailResponse(BaseModel):
    ok: bool
    recipient: str
    subject: str = ""
    detail: str = ""
    error: Optional[str] = None


@router.post("/test/email", response_model=TestEmailResponse)
async def post_test_email(
    body: TestEmailRequest,
    current_user: CurrentUser,
) -> TestEmailResponse:
    """Send a sample INTERVENTION email so the user can confirm SendGrid works."""
    recipient = body.to_email or current_user.email
    try:
        result = await send_test_email(str(recipient), rep_name=current_user.full_name or "Test Rep")
        return TestEmailResponse(
            ok=True,
            recipient=str(recipient),
            subject=result.get("subject", ""),
            detail=f"SendGrid returned status {result.get('status_code')}",
        )
    except EmailDeliveryError as exc:
        logger.warning("test email failed: {}", exc)
        # 200 with ok=false — makes the UI easier to render vs handling HTTPException
        return TestEmailResponse(
            ok=False, recipient=str(recipient), error=str(exc)
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("unexpected test-email error: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        )


class TestSlackResponse(BaseModel):
    ok: bool
    detail: str = ""
    error: Optional[str] = None


@router.post("/test/slack", response_model=TestSlackResponse)
async def post_test_slack(current_user: CurrentUser) -> TestSlackResponse:
    """Placeholder — full Slack test wired in later once user has a token stored."""
    return TestSlackResponse(
        ok=False,
        error=(
            "Slack test not wired yet — save a token in /config/integrations first, "
            "then this will send a sample intervention block."
        ),
    )
