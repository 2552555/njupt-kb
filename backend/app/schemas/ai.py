import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class AIChatRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    message: str
    session_type: Optional[str] = "general"


class AIChatResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    response: str
    model_used: Optional[str] = None
    tokens_used: Optional[int] = None


class AIStudyPlanCreate(BaseModel):
    plan_type: str
    input_params: Optional[dict] = None
    plan_content: Optional[dict] = None


class AIStudyPlanOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    plan_type: Optional[str] = None
    input_params: Optional[dict] = None
    plan_content: Optional[dict] = None
    is_favorite: bool
    created_at: datetime

    class Config:
        from_attributes = True
