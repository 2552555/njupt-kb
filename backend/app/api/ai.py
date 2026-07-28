from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ai import AIChatHistory, AIStudyPlan
from app.models.user import User
from app.schemas.ai import AIChatRequest, AIChatResponse, AIStudyPlanCreate, AIStudyPlanOut
from app.core.security import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/chat", response_model=AIChatResponse)
async def chat(
    data: AIChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Stub AI response for now
    response_text = f"您好，{current_user.display_name or '同学'}！我是南邮知识库AI助手。您的问题已收到：{data.message}。AI功能正在完善中，敬请期待完整版本上线。"

    # Save chat history
    chat_record = AIChatHistory(
        user_id=current_user.id,
        session_type=data.session_type or "general",
        user_message=data.message,
        ai_response=response_text,
        model_used="stub",
        tokens_used=0,
    )
    db.add(chat_record)
    await db.flush()

    return AIChatResponse(
        response=response_text,
        model_used="stub",
        tokens_used=0,
    )


@router.get("/plans")
async def list_plans(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AIStudyPlan)
        .where(AIStudyPlan.user_id == current_user.id)
        .order_by(AIStudyPlan.created_at.desc())
    )
    plans = result.scalars().all()
    return [AIStudyPlanOut.model_validate(p) for p in plans]


@router.post("/plans", status_code=status.HTTP_201_CREATED)
async def save_plan(
    data: AIStudyPlanCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = AIStudyPlan(
        user_id=current_user.id,
        plan_type=data.plan_type,
        input_params=data.input_params,
        plan_content=data.plan_content,
    )
    db.add(plan)
    await db.flush()
    await db.refresh(plan)

    return AIStudyPlanOut.model_validate(plan)
