"""Filename-only classification. Suggestions never publish resources."""

import re
import unicodedata

from pydantic import BaseModel, Field

from app.core.enums import ResourceType


class ClassificationIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=50)


TYPE_RULES = (
    ("exam", r"试卷|真题|考试|期中|期末|(?i:\bexam\b)"),
    ("lab_guide", r"实验|(?i:\blab\b)"),
    ("slides", r"课件|幻灯|(?i:\bslides?\b)|\.(?i:pptx)$"),
    ("review", r"复习|总结|(?i:\breview\b)"),
    ("guide", r"攻略|指南|(?i:\bguide\b)"),
    ("note", r"笔记|讲义|(?i:\bnotes?\b)"),
)


def normalize(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def classify_name(name: str, courses: list[dict]) -> dict:
    text = normalize(name.rsplit(".", 1)[0])
    # Codes require a token boundary: CS1 must not match CS10.
    raw = unicodedata.normalize("NFKC", name).casefold()
    by_code = [c for c in courses if c["code"] and re.search(
        r"(?<![a-z0-9])" + re.escape(c["code"].casefold()) + r"(?![a-z0-9])", raw
    )]
    by_name = [c for c in courses if any(
        len(normalize(label)) >= 2 and normalize(label) in text
        for label in [c["name"], *c["aliases"]]
    )]
    matches = by_code or by_name
    types = [kind for kind, pattern in TYPE_RULES if re.search(pattern, name)]
    return {
        "name": name,
        "course_id": matches[0]["id"] if len(matches) == 1 else None,
        "candidates": [{k: c[k] for k in ("id", "name", "code")} for c in matches],
        "resource_type": types[0] if len(types) == 1 else ResourceType.OTHER.value,
        "reason": "课程代码" if by_code else "课程名称或别名" if by_name else "未匹配课程",
        "needs_confirmation": len(matches) != 1 or len(types) != 1,
        "type_matches": types,
    }


async def classify(db, names: list[str]) -> list[dict]:
    from app.core.errors import InvalidInput
    from app.modules.catalog.service import classification_catalog

    if any(not name.strip() or len(name) > 300 for name in names):
        raise InvalidInput("文件名不能为空且不能超过 300 字符")
    courses = await classification_catalog(db)
    return [classify_name(name, courses) for name in names]
