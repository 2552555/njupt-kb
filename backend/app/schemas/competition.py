import uuid
from datetime import datetime
from typing import Optional, List, Union
from pydantic import BaseModel


class CompetitionListOut(BaseModel):
    id: uuid.UUID
    name: str
    category: Optional[str] = None
    difficulty: Optional[int] = None
    organizer: Optional[str] = None
    official_url: Optional[str] = None
    description: Optional[str] = None
    registration_period: Optional[str] = None
    competition_period: Optional[str] = None
    suitable_majors: Union[list, dict, None] = None
    suitable_grades: Union[list, dict, None] = None
    team_size: Optional[str] = None
    njupt_contact: Optional[str] = None
    njupt_winners_count: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CompetitionOut(CompetitionListOut):
    winners: List["CompetitionWinnerOut"] = []


class CompetitionWinnerOut(BaseModel):
    id: uuid.UUID
    competition_id: uuid.UUID
    year: int
    award_level: str
    team_members: Union[list, dict, None] = None
    advisor: Optional[str] = None
    project_title: Optional[str] = None
    experience_text: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
