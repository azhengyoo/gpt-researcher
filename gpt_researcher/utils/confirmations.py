"""Shared confirmation mechanism for frontend-backend communication.

Provides a way for the backend researcher to ask the frontend user for consent
before performing certain actions (e.g., falling back to web search when
local documents are not available).
"""

import asyncio
import uuid
from typing import Dict


# {confirmation_id: asyncio.Future}
pending_confirmations: Dict[str, asyncio.Future] = {}


async def request_user_confirmation(websocket, message: str, question: str, timeout: float = 120.0) -> bool:
    """Send a confirmation request to the frontend and wait for the user's response.

    Args:
        websocket: The WebSocket connection to the frontend.
        message: Detailed message explaining the situation.
        question: Short question for the user (e.g., "Fall back to web search?").
        timeout: Maximum seconds to wait for user response (default 120).

    Returns:
        True if user approved, False if rejected or timed out.
    """
    conf_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    pending_confirmations[conf_id] = future

    try:
        await websocket.send_json({
            "type": "confirmation_required",
            "confirmation_id": conf_id,
            "message": message,
            "question": question,
        })
        approved = await asyncio.wait_for(future, timeout=timeout)
        return approved
    except asyncio.TimeoutError:
        return False
    finally:
        pending_confirmations.pop(conf_id, None)
