from app.models.college import College
from app.models.major import Major
from app.models.user import User
from app.models.course import Course
from app.models.resource import Resource
from app.models.tag import Tag, ResourceTag
from app.models.favorite import Favorite
from app.models.comment import Comment
from app.models.download import Download
from app.models.points import PointsLog
from app.models.baoyan import BaoyanPolicy, BaoyanExperience
from app.models.competition import Competition, CompetitionWinner
from app.models.transfer import MajorTransferPolicy, MajorTransferCase
from app.models.kaoyan import KaoyanSchool, KaoyanMajor, KaoyanScoreLine
from app.models.scraper import ScrapedSource, ScrapedPage
from app.models.ai import AIChatHistory, AIStudyPlan
from app.models.contribution import ContributionDay
from app.models.notification import Notification

__all__ = [
    "College",
    "Major",
    "User",
    "Course",
    "Resource",
    "Tag",
    "ResourceTag",
    "Favorite",
    "Comment",
    "Download",
    "PointsLog",
    "BaoyanPolicy",
    "BaoyanExperience",
    "Competition",
    "CompetitionWinner",
    "MajorTransferPolicy",
    "MajorTransferCase",
    "KaoyanSchool",
    "KaoyanMajor",
    "KaoyanScoreLine",
    "ScrapedSource",
    "ScrapedPage",
    "AIChatHistory",
    "AIStudyPlan",
    "ContributionDay",
    "Notification",
]
