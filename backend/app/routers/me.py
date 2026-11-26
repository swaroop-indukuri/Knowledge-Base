from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.models import MeResponse

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=MeResponse)
def me(user_id: str = Depends(get_current_user)) -> MeResponse:
    return MeResponse(user_id=user_id)
