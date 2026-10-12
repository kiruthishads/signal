"""
FastAPI dependencies shared across routers.
"""
from __future__ import annotations

import uuid
import structlog

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenPayload, verify_supabase_jwt
from app.database import get_db
from app.models.user import User

log = structlog.get_logger()
_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    token_data: TokenPayload = verify_supabase_jwt(
        credentials.credentials
    )

    supabase_uuid = uuid.UUID(token_data.sub)

    result = await db.execute(
    select(User).where(or_(*conditions))
)
    user = result.scalar_one_or_none()

    if user is None:
        if not token_data.github_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub username not found in token. Re-authenticate.",
            )

        # Check whether this GitHub account already exists.
        conditions = [
            User.github_username == token_data.github_username
        ]

        if token_data.github_id is not None:
            conditions.append(
                User.github_id == token_data.github_id
            )

        result = await db.execute(
            select(User).where(
                __import__("sqlalchemy").or_(*conditions)
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            if not token_data.github_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="GitHub ID missing from token. Sign in again.",
                )

            user = User(
                id=supabase_uuid,
                github_id=token_data.github_id,
                github_username=token_data.github_username,
                github_email=token_data.email,
                github_avatar=token_data.github_avatar,
                full_name=token_data.full_name,
            )
            db.add(user)
            await db.flush()
            log.info(
                "user.created",
                username=user.github_username,
                id=str(user.id),
            )
        else:
            # Preserve the existing database ID and related records.
            log.info(
                "user.existing_account_found",
                username=user.github_username,
            )

    # Refresh profile details from the verified Supabase JWT.
    if token_data.github_avatar:
        user.github_avatar = token_data.github_avatar

    if token_data.full_name:
        user.full_name = token_data.full_name

    if token_data.email:
        user.github_email = token_data.email

    await db.flush()
    return user