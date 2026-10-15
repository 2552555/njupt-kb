"""topics 应用服务：业务规则的唯一落点（设计文档 §3、§10、§25.1、§33）。

- 列表接口统一走游标分页（`CursorPage`：items / next_cursor / has_more）。
- 写操作只用会话依赖注入的当前用户；不写积分、不写账本（原 `user.points += 15`
  与 `PointsLog` 逻辑已随积分体系移除，见 docs/DECISIONS.md）。
- 数据来源未经核实：本模块不编造政策、分数线或获奖名单，也不改写 `is_verified`。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidInput, NotFound
from app.core.pagination import CursorPage, decode_cursor, encode_cursor, normalize_page_size
from app.modules.topics.models import (
    BaoyanExperience,
    BaoyanPolicy,
    Competition,
    CompetitionWinner,
    KaoyanMajor,
    KaoyanSchool,
    KaoyanScoreLine,
    MajorTransferCase,
    MajorTransferPolicy,
    ScrapedPage,
)
from app.modules.topics.schemas import (
    BaoyanExperienceCreate,
    BaoyanExperienceOut,
    BaoyanPolicyOut,
    CompetitionListOut,
    CompetitionOut,
    CompetitionWinnerOut,
    KaoyanMajorOut,
    KaoyanSchoolOut,
    KaoyanScoreLineOut,
    ScrapedPageOut,
    TransferCaseCreate,
    TransferCaseOut,
    TransferPolicyOut,
)

_CURSOR_ERROR = "cursor 无效"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 保研
# --------------------------------------------------------------------------- #
async def list_policies(
    db: AsyncSession,
    *,
    college_id: uuid.UUID | None,
    year: int | None,
    limit: int | None,
    cursor: str | None,
) -> CursorPage[BaoyanPolicyOut]:
    size = normalize_page_size(limit)
    stmt = select(BaoyanPolicy)
    if college_id is not None:
        stmt = stmt.where(BaoyanPolicy.college_id == college_id)
    if year is not None:
        stmt = stmt.where(BaoyanPolicy.year == year)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_year = int(payload["y"])
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                BaoyanPolicy.year < last_year,
                and_(BaoyanPolicy.year == last_year, BaoyanPolicy.id < last_id),
            )
        )
    stmt = stmt.order_by(BaoyanPolicy.year.desc(), BaoyanPolicy.id.desc()).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"y": last.year, "i": str(last.id)})
    return CursorPage(
        items=[BaoyanPolicyOut.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_experiences(
    db: AsyncSession,
    *,
    college_id: uuid.UUID | None,
    target_university: str | None,
    year: int | None,
    limit: int | None,
    cursor: str | None,
) -> CursorPage[BaoyanExperienceOut]:
    size = normalize_page_size(limit)
    stmt = select(BaoyanExperience).where(BaoyanExperience.status == "active")
    if college_id is not None:
        stmt = stmt.where(BaoyanExperience.college_id == college_id)
    if target_university:
        stmt = stmt.where(BaoyanExperience.target_university.contains(target_university))
    if year is not None:
        stmt = stmt.where(BaoyanExperience.year == year)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_year = int(payload["y"])
            last_ts = _aware(datetime.fromisoformat(payload["t"]))
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                BaoyanExperience.year < last_year,
                and_(
                    BaoyanExperience.year == last_year,
                    BaoyanExperience.created_at < last_ts,
                ),
                and_(
                    BaoyanExperience.year == last_year,
                    BaoyanExperience.created_at == last_ts,
                    BaoyanExperience.id < last_id,
                ),
            )
        )
    stmt = stmt.order_by(
        BaoyanExperience.year.desc(),
        BaoyanExperience.created_at.desc(),
        BaoyanExperience.id.desc(),
    ).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            {"y": last.year, "t": last.created_at.isoformat(), "i": str(last.id)}
        )
    return CursorPage(
        items=[BaoyanExperienceOut.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def create_experience(
    db: AsyncSession, *, user, payload: BaoyanExperienceCreate
) -> BaoyanExperienceOut:
    experience = BaoyanExperience(
        author_id=user.id,
        college_id=payload.college_id,
        major_id=payload.major_id,
        original_major=payload.original_major,
        gpa=payload.gpa,
        gpa_rank=payload.gpa_rank,
        target_university=payload.target_university,
        target_college=payload.target_college,
        target_major=payload.target_major,
        offer_result=payload.offer_result,
        timeline=payload.timeline,
        summer_camp_experience=payload.summer_camp_experience,
        pre_interview_experience=payload.pre_interview_experience,
        interview_questions=payload.interview_questions,
        advice=payload.advice,
        year=payload.year,
        tags=payload.tags,
        status="active",
    )
    db.add(experience)
    await db.flush()
    await db.refresh(experience)
    return BaoyanExperienceOut.model_validate(experience)


async def get_experience(db: AsyncSession, *, experience_id: uuid.UUID) -> BaoyanExperienceOut:
    experience = (
        await db.execute(select(BaoyanExperience).where(BaoyanExperience.id == experience_id))
    ).scalar_one_or_none()
    if experience is None:
        raise NotFound("保研经验不存在")
    experience.view_count += 1
    await db.flush()
    return BaoyanExperienceOut.model_validate(experience)


# --------------------------------------------------------------------------- #
# 竞赛
# --------------------------------------------------------------------------- #
async def list_competitions(
    db: AsyncSession, *, category: str | None, limit: int | None, cursor: str | None
) -> CursorPage[CompetitionListOut]:
    size = normalize_page_size(limit)
    stmt = select(Competition)
    if category:
        stmt = stmt.where(Competition.category == category)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_name = str(payload["n"])
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                Competition.name > last_name,
                and_(Competition.name == last_name, Competition.id > last_id),
            )
        )
    stmt = stmt.order_by(Competition.name, Competition.id).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"n": last.name, "i": str(last.id)})
    return CursorPage(
        items=[CompetitionListOut.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_competition(db: AsyncSession, *, competition_id: uuid.UUID) -> CompetitionOut:
    competition = await db.get(Competition, competition_id)
    if competition is None:
        raise NotFound("竞赛不存在")
    winners = (
        await db.execute(
            select(CompetitionWinner)
            .where(CompetitionWinner.competition_id == competition.id)
            .order_by(CompetitionWinner.year.desc(), CompetitionWinner.id.desc())
        )
    ).scalars().all()
    detail = CompetitionOut.model_validate(competition)
    detail.winners = [CompetitionWinnerOut.model_validate(w) for w in winners]
    return detail


# --------------------------------------------------------------------------- #
# 考研
# --------------------------------------------------------------------------- #
async def list_schools(
    db: AsyncSession, *, limit: int | None, cursor: str | None
) -> CursorPage[KaoyanSchoolOut]:
    size = normalize_page_size(limit)
    stmt = select(KaoyanSchool)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_name = str(payload["n"])
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                KaoyanSchool.name > last_name,
                and_(KaoyanSchool.name == last_name, KaoyanSchool.id > last_id),
            )
        )
    stmt = stmt.order_by(KaoyanSchool.name, KaoyanSchool.id).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"n": last.name, "i": str(last.id)})
    return CursorPage(
        items=[KaoyanSchoolOut.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_majors(
    db: AsyncSession, *, school_id: uuid.UUID, limit: int | None, cursor: str | None
) -> CursorPage[KaoyanMajorOut]:
    if await db.get(KaoyanSchool, school_id) is None:
        raise NotFound("考研院校不存在")
    size = normalize_page_size(limit)
    stmt = select(KaoyanMajor).where(KaoyanMajor.school_id == school_id)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_name = str(payload["n"])
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                KaoyanMajor.name > last_name,
                and_(KaoyanMajor.name == last_name, KaoyanMajor.id > last_id),
            )
        )
    stmt = stmt.order_by(KaoyanMajor.name, KaoyanMajor.id).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"n": last.name, "i": str(last.id)})
    return CursorPage(
        items=[KaoyanMajorOut.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_score_lines(
    db: AsyncSession,
    *,
    school_id: uuid.UUID | None,
    major_id: uuid.UUID | None,
    year: int | None,
    limit: int | None,
    cursor: str | None,
) -> CursorPage[KaoyanScoreLineOut]:
    size = normalize_page_size(limit)
    stmt = select(KaoyanScoreLine)
    if school_id is not None:
        stmt = stmt.where(
            KaoyanScoreLine.kaoyan_major_id.in_(
                select(KaoyanMajor.id).where(KaoyanMajor.school_id == school_id)
            )
        )
    if major_id is not None:
        stmt = stmt.where(KaoyanScoreLine.kaoyan_major_id == major_id)
    if year is not None:
        stmt = stmt.where(KaoyanScoreLine.year == year)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_year = int(payload["y"])
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                KaoyanScoreLine.year < last_year,
                and_(KaoyanScoreLine.year == last_year, KaoyanScoreLine.id < last_id),
            )
        )
    stmt = stmt.order_by(KaoyanScoreLine.year.desc(), KaoyanScoreLine.id.desc()).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"y": last.year, "i": str(last.id)})
    return CursorPage(
        items=[KaoyanScoreLineOut.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# --------------------------------------------------------------------------- #
# 转专业
# --------------------------------------------------------------------------- #
async def list_transfer_policies(
    db: AsyncSession,
    *,
    year: int | None,
    college_id: uuid.UUID | None,
    limit: int | None,
    cursor: str | None,
) -> CursorPage[TransferPolicyOut]:
    size = normalize_page_size(limit)
    stmt = select(MajorTransferPolicy)
    if year is not None:
        stmt = stmt.where(MajorTransferPolicy.year == year)
    if college_id is not None:
        stmt = stmt.where(MajorTransferPolicy.college_id == college_id)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_year = int(payload["y"])
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                MajorTransferPolicy.year < last_year,
                and_(MajorTransferPolicy.year == last_year, MajorTransferPolicy.id < last_id),
            )
        )
    stmt = stmt.order_by(MajorTransferPolicy.year.desc(), MajorTransferPolicy.id.desc()).limit(
        size + 1
    )
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"y": last.year, "i": str(last.id)})
    return CursorPage(
        items=[TransferPolicyOut.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_transfer_cases(
    db: AsyncSession, *, limit: int | None, cursor: str | None
) -> CursorPage[TransferCaseOut]:
    size = normalize_page_size(limit)
    stmt = select(MajorTransferCase)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_year = int(payload["y"])
            last_ts = _aware(datetime.fromisoformat(payload["t"]))
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                MajorTransferCase.year < last_year,
                and_(MajorTransferCase.year == last_year, MajorTransferCase.created_at < last_ts),
                and_(
                    MajorTransferCase.year == last_year,
                    MajorTransferCase.created_at == last_ts,
                    MajorTransferCase.id < last_id,
                ),
            )
        )
    stmt = stmt.order_by(
        MajorTransferCase.year.desc(),
        MajorTransferCase.created_at.desc(),
        MajorTransferCase.id.desc(),
    ).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            {"y": last.year, "t": last.created_at.isoformat(), "i": str(last.id)}
        )
    return CursorPage(
        items=[TransferCaseOut.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def create_transfer_case(
    db: AsyncSession, *, user, payload: TransferCaseCreate
) -> TransferCaseOut:
    case = MajorTransferCase(
        author_id=user.id,
        year=payload.year,
        from_major=payload.from_major,
        from_college=payload.from_college,
        to_major=payload.to_major,
        to_college=payload.to_college,
        gpa=payload.gpa,
        exam_scores=payload.exam_scores,
        experience_text=payload.experience_text,
        result=payload.result,
    )
    db.add(case)
    await db.flush()
    await db.refresh(case)
    return TransferCaseOut.model_validate(case)


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #
async def list_latest_pages(
    db: AsyncSession, *, category: str | None, limit: int | None, cursor: str | None
) -> CursorPage[ScrapedPageOut]:
    size = normalize_page_size(limit)
    stmt = select(ScrapedPage)
    if category:
        stmt = stmt.where(ScrapedPage.category == category)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_ts = _aware(datetime.fromisoformat(payload["t"]))
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput(_CURSOR_ERROR, details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                ScrapedPage.scraped_at < last_ts,
                and_(ScrapedPage.scraped_at == last_ts, ScrapedPage.id < last_id),
            )
        )
    stmt = stmt.order_by(ScrapedPage.scraped_at.desc(), ScrapedPage.id.desc()).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"t": last.scraped_at.isoformat(), "i": str(last.id)})
    # 正文只回传前 500 字，避免列表接口返回整页抓取内容（沿用原实现）。
    out_items = []
    for row in items:
        item = ScrapedPageOut.model_validate(row)
        item.content_text = row.content_text[:500] if row.content_text else None
        out_items.append(item)
    return CursorPage(items=out_items, next_cursor=next_cursor, has_more=has_more)
