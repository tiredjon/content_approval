from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import SqlApprovalRequestRepository
from app.db.session import get_db
from app.domain.service import ApprovalService


async def get_approval_service(session: AsyncSession = Depends(get_db)) -> ApprovalService:
    return ApprovalService(SqlApprovalRequestRepository(session))
