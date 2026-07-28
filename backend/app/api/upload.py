import os
import uuid
import aiofiles
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    # Validate file size
    contents = await file.read()
    if len(contents) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max size is {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB",
        )

    # Generate unique filename
    ext = os.path.splitext(file.filename)[1] if file.filename else ""
    unique_name = f"{uuid.uuid4()}{ext}"

    # Ensure upload directory exists
    upload_dir = settings.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)

    # Save file
    file_path = os.path.join(upload_dir, unique_name)
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(contents)

    return {
        "file_name": unique_name,
        "original_name": file.filename,
        "file_path": file_path,
        "file_size": len(contents),
        "file_type": file.content_type,
    }
