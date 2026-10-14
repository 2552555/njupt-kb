"""课程目录种子数据：迁移原仓库中的真实南邮目录（幂等）。

来源：仓库根目录 `backend/seed.py`（原型种子）中的学院/专业/课程目录。
迁移口径与"不编造"约束（见 AGENT 作业要求）：
- 学院（12）与专业（34，含学科门类）按原文迁移；短名如"计算机学院"一并保留。
- 课程（73 门）按 (名称, 课程代码) 识别，同名不同代码分开；原文为无课号课程生成的
  伪造代码（B10000S…）**不迁移**，这些课程 `code` 留空。
- 学分：原文中的学分值来源不可核实，按"不得编造学分"要求一律留空。
- 教师：原文中的"张教授/李老师"等为示例姓名，**不迁移**，`teachers` 表保持为空。
- 开课实例：原文没有真实开课记录，**不生成** `course_offerings`/`offering_teachers`。
- 培养方案：原文未给出入学年级，为每个专业建立一个占位培养方案（entry_year 为空，
  名称标注"待核实"），课程通过 `curriculum_courses` 挂到对应专业的培养方案下。
- 学期：原文未提供校历，仅写入学年 + 春秋学期，起止日期留空。
- 别名：仅补充少量真实常用简称（如"模电"→模拟电子电路），不新增课号或学分。

入口：`python -m scripts.seed_catalog`。使用应用配置的 async_session_factory，
不自行建 engine，也不调用 create_all（建表由 Alembic 负责，§31.2）。
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CourseStatus
from app.db import async_session_factory
from app.modules.catalog.models import (
    College,
    Course,
    CourseAlias,
    Curriculum,
    CurriculumCourse,
    Major,
    Semester,
    University,
)

UNIVERSITY_NAME = "南京邮电大学"
UNIVERSITY_SLUG = "njupt"

# (学院全称, 学院简称)
COLLEGES: list[tuple[str, str]] = [
    ("通信与信息工程学院", "通信学院"),
    ("电子与光学工程学院/柔性电子（未来技术）学院", "电光学院"),
    ("计算机学院/软件学院/网络空间安全学院", "计算机学院"),
    ("自动化学院/人工智能学院", "自动化学院"),
    ("材料科学与工程学院", "材料学院"),
    ("物联网学院", "物联网学院"),
    ("理学院", "理学院"),
    ("管理学院", "管理学院"),
    ("经济学院", "经济学院"),
    ("传媒与艺术学院", "传媒学院"),
    ("外国语学院", "外国语学院"),
    ("贝尔英才学院", "贝尔英才学院"),
]

# (学院简称, 专业名称, 学科门类)
MAJORS: list[tuple[str, str, str]] = [
    ("通信学院", "通信工程", "工学"),
    ("通信学院", "电子信息工程", "工学"),
    ("通信学院", "广播电视工程", "工学"),
    ("电光学院", "电子科学与技术", "工学"),
    ("电光学院", "光电信息科学与工程", "工学"),
    ("电光学院", "微电子科学与工程", "工学"),
    ("计算机学院", "计算机科学与技术", "工学"),
    ("计算机学院", "软件工程", "工学"),
    ("计算机学院", "网络空间安全", "工学"),
    ("计算机学院", "数据科学与大数据技术", "工学"),
    ("自动化学院", "自动化", "工学"),
    ("自动化学院", "人工智能", "工学"),
    ("自动化学院", "智能科学与技术", "工学"),
    ("自动化学院", "测控技术与仪器", "工学"),
    ("材料学院", "材料物理", "工学"),
    ("材料学院", "材料化学", "工学"),
    ("材料学院", "高分子材料与工程", "工学"),
    ("物联网学院", "物联网工程", "工学"),
    ("物联网学院", "网络工程", "工学"),
    ("理学院", "应用物理学", "理学"),
    ("理学院", "信息与计算科学", "理学"),
    ("理学院", "应用统计学", "理学"),
    ("管理学院", "信息管理与信息系统", "管理学"),
    ("管理学院", "工商管理", "管理学"),
    ("管理学院", "市场营销", "管理学"),
    ("经济学院", "经济学", "经济学"),
    ("经济学院", "国际经济与贸易", "经济学"),
    ("经济学院", "金融工程", "经济学"),
    ("传媒学院", "广告学", "文学"),
    ("传媒学院", "数字媒体艺术", "艺术学"),
    ("外国语学院", "英语", "文学"),
    ("外国语学院", "翻译", "文学"),
    ("贝尔英才学院", "理工交叉创新班", "工学"),
    ("贝尔英才学院", "信息科技英才班", "工学"),
]

# (适用专业列表, 课程名称, 课程代码, 建议修读学期, 要求类型, 学分-未采用)
COURSES: list[tuple[list[str], str, str | None, str, str, float]] = [
    (["通信工程", "电子信息工程", "自动化", "人工智能", "计算机科学与技术", "软件工程", "电子科学与技术"],
     "高等数学A（上）", None, "大一上", "required", 5.0),
    (["通信工程", "电子信息工程", "自动化", "人工智能", "计算机科学与技术", "软件工程", "电子科学与技术"],
     "高等数学A（下）", None, "大一下", "required", 5.0),
    (["通信工程", "电子信息工程", "自动化", "人工智能", "计算机科学与技术", "软件工程", "电子科学与技术", "光电信息科学与工程"],
     "大学物理B", None, "大一下", "required", 3.5),
    (["通信工程", "电子信息工程", "自动化", "人工智能", "计算机科学与技术", "软件工程"],
     "线性代数", None, "大一上", "required", 2.5),
    (["通信工程", "电子信息工程", "自动化", "人工智能", "计算机科学与技术", "软件工程"],
     "概率论与数理统计", None, "大二上", "required", 3.0),
    (["计算机科学与技术", "软件工程", "自动化", "人工智能", "数据科学与大数据技术", "网络空间安全"],
     "C语言程序设计", "B0400011S", "大一上", "required", 3.0),
    (["计算机科学与技术", "软件工程", "数据科学与大数据技术", "网络空间安全"],
     "C++程序设计", "B0400021S", "大一下", "required", 3.0),
    (["计算机科学与技术", "软件工程", "数据科学与大数据技术"],
     "离散数学", None, "大二上", "required", 3.0),
    (["计算机科学与技术", "软件工程", "数据科学与大数据技术", "网络空间安全", "物联网工程"],
     "数据结构", "B0400031S", "大二上", "required", 4.0),
    (["通信工程", "电子信息工程"],
     "模拟电子电路", None, "大二上", "required", 3.5),
    (["通信工程", "电子信息工程"],
     "数字电路与逻辑设计", None, "大二下", "required", 3.0),
    (["通信工程", "电子信息工程"],
     "信号与系统", None, "大二下", "required", 4.0),
    (["通信工程"],
     "通信原理", None, "大三上", "required", 4.0),
    (["通信工程"],
     "电磁场与电磁波", None, "大二下", "required", 3.0),
    (["通信工程", "电子信息工程"],
     "数字信号处理", None, "大三上", "required", 3.0),
    (["通信工程"],
     "移动通信", None, "大三下", "elective", 2.0),
    (["计算机科学与技术", "软件工程", "网络空间安全", "数据科学与大数据技术"],
     "操作系统", "B0400041S", "大二下", "required", 3.5),
    (["计算机科学与技术"],
     "计算机组成原理", "B0400051S", "大二下", "required", 3.5),
    (["计算机科学与技术", "软件工程", "数据科学与大数据技术", "物联网工程"],
     "计算机网络", "B0400061S", "大二下", "required", 3.5),
    (["计算机科学与技术", "软件工程"],
     "数据库系统原理", "B0400071S", "大三上", "required", 3.0),
    (["计算机科学与技术", "软件工程"],
     "编译原理", "B0400081S", "大三上", "required", 3.0),
    (["计算机科学与技术"],
     "计算机体系结构", "B0400091S", "大三下", "elective", 2.0),
    (["软件工程"],
     "软件工程导论", "B0400101S", "大二上", "required", 2.0),
    (["软件工程"],
     "软件测试", "B0400111S", "大三下", "required", 2.0),
    (["网络空间安全"],
     "密码学", "B0400121S", "大二下", "required", 3.0),
    (["网络空间安全"],
     "网络安全", "B0400131S", "大三上", "required", 3.0),
    (["数据科学与大数据技术"],
     "大数据技术基础", "B0400141S", "大三上", "required", 3.0),
    (["计算机科学与技术", "软件工程"],
     "人工智能导论", "B0400151S", "大三上", "elective", 2.0),
    (["电子科学与技术"],
     "半导体物理", None, "大二下", "required", 3.5),
    (["电子科学与技术"],
     "固体物理", None, "大三上", "required", 3.0),
    (["光电信息科学与工程"],
     "光学", None, "大二上", "required", 3.5),
    (["光电信息科学与工程"],
     "激光原理与技术", None, "大三上", "required", 3.0),
    (["微电子科学与工程"],
     "集成电路设计", None, "大三上", "required", 3.5),
    (["电子科学与技术", "微电子科学与工程"],
     "微电子学概论", None, "大一上", "required", 2.0),
    (["自动化", "人工智能", "智能科学与技术"],
     "自动控制原理", None, "大二下", "required", 3.5),
    (["自动化"],
     "现代控制理论", None, "大三上", "required", 3.0),
    (["自动化"],
     "传感器与检测技术", None, "大三上", "elective", 2.0),
    (["自动化", "测控技术与仪器"],
     "电子测量技术", None, "大二下", "elective", 2.0),
    (["人工智能", "智能科学与技术"],
     "机器学习", None, "大二下", "required", 3.5),
    (["人工智能", "智能科学与技术"],
     "深度学习", None, "大三上", "required", 3.0),
    (["人工智能"],
     "自然语言处理", None, "大三下", "elective", 2.5),
    (["物联网工程"],
     "物联网导论", None, "大一上", "required", 2.0),
    (["物联网工程"],
     "嵌入式系统", None, "大二下", "required", 3.0),
    (["物联网工程"],
     "传感器网络", None, "大三上", "required", 3.0),
    (["物联网工程", "网络工程"],
     "无线通信技术", None, "大二下", "elective", 2.0),
    (["应用物理学"],
     "量子力学", None, "大二下", "required", 4.0),
    (["应用物理学"],
     "热力学与统计物理", None, "大三上", "required", 3.5),
    (["信息与计算科学"],
     "数值分析", None, "大二下", "required", 3.0),
    (["应用统计学"],
     "多元统计分析", None, "大三上", "required", 3.0),
    (["通信工程", "计算机科学与技术", "自动化"],
     "复变函数与积分变换", None, "大二上", "required", 2.0),
    (["通信工程", "计算机科学与技术", "自动化", "电子信息工程", "电子科学与技术"],
     "军事理论", None, "大一上", "required", 1.0),
    (["通信工程", "计算机科学与技术", "软件工程"],
     "工程制图", None, "大一上", "required", 2.0),
    (["计算机科学与技术", "软件工程", "网络工程", "数据科学与大数据技术"],
     "程序设计综合实验", None, "大一上", "elective", 1.0),
    (["通信工程"],
     "光纤通信", None, "大三下", "elective", 2.5),
    (["通信工程", "电子信息工程"],
     "微波技术与天线", None, "大三上", "required", 3.0),
    (["计算机科学与技术", "软件工程"],
     "分布式系统", None, "大四上", "elective", 2.0),
    (["计算机科学与技术"],
     "计算机图形学", None, "大三下", "elective", 2.5),
    (["软件工程"],
     "人机交互", None, "大三下", "elective", 2.0),
    (["网络空间安全"],
     "恶意代码分析", None, "大三下", "elective", 2.0),
    (["数据科学与大数据技术"],
     "数据可视化", None, "大三下", "elective", 2.0),
    (["自动化"],
     "机器人学", None, "大三下", "elective", 2.5),
    (["人工智能"],
     "计算机视觉", None, "大三下", "elective", 2.5),
    (["物联网工程"],
     "RFID技术", None, "大三下", "elective", 2.0),
    (["电子信息工程"],
     "信息论基础", None, "大三上", "required", 2.5),
    (["通信工程", "电子信息工程"],
     "随机信号分析", None, "大三上", "required", 3.0),
    (["计算机科学与技术", "软件工程", "网络空间安全"],
     "算法设计与分析", None, "大二下", "required", 3.0),
    (["自动化", "测控技术与仪器"],
     "过程控制", None, "大三下", "elective", 2.5),
    (["电子科学与技术"],
     "电子材料", None, "大三上", "elective", 2.0),
    (["光电信息科学与工程"],
     "光电子学", None, "大三下", "required", 3.0),
    (["微电子科学与工程"],
     "VLSI设计", None, "大三下", "required", 3.0),
    (["信息与计算科学"],
     "优化方法", None, "大三上", "required", 3.0),
    (["应用统计学"],
     "时间序列分析", None, "大三下", "elective", 2.5),
    (["物联网工程", "网络工程"],
     "云计算概论", None, "大三下", "elective", 2.0),
]

# 学年 + 学期（校历日期未提供，留空）
SEMESTERS: list[tuple[str, str]] = [
    ("2023-2024", "fall"),
    ("2023-2024", "spring"),
    ("2024-2025", "fall"),
    ("2024-2025", "spring"),
]

# 真实常用简称/检索别名（不新增课号与学分）
ALIASES: list[tuple[str, str]] = [
    ("模拟电子电路", "模电"),
    ("数字电路与逻辑设计", "数电"),
    ("高等数学A（上）", "高数上"),
    ("高等数学A（下）", "高数下"),
    ("数据结构", "数构"),
    ("计算机网络", "计网"),
    ("计算机组成原理", "计组"),
    ("操作系统", "OS"),
    ("概率论与数理统计", "概率论"),
    ("线性代数", "线代"),
    ("自动控制原理", "自控"),
    ("数字信号处理", "DSP"),
    ("嵌入式系统", "嵌入式"),
    ("软件工程导论", "软工导论"),
]

T = TypeVar("T")


async def _get_or_create(
    session: AsyncSession, model: type[T], defaults: dict[str, Any] | None = None, **keys: Any
) -> T:
    """按自然键 get-or-create，保证重复执行不产生重复数据。"""
    obj = (await session.execute(select(model).filter_by(**keys))).scalar_one_or_none()
    if obj is not None:
        return obj
    obj = model(**keys, **(defaults or {}))
    session.add(obj)
    await session.flush()
    return obj


async def seed_catalog() -> None:
    async with async_session_factory() as session:
        university = await _get_or_create(
            session, University, slug=UNIVERSITY_SLUG, defaults={"name": UNIVERSITY_NAME}
        )

        colleges: dict[str, College] = {}
        for name, short_name in COLLEGES:
            colleges[short_name] = await _get_or_create(
                session,
                College,
                university_id=university.id,
                name=name,
                defaults={"short_name": short_name},
            )

        majors: dict[str, Major] = {}
        for college_short, name, category in MAJORS:
            majors[name] = await _get_or_create(
                session,
                Major,
                university_id=university.id,
                name=name,
                defaults={"college_id": colleges[college_short].id, "category": category},
            )

        # 一个专业一个占位培养方案；入学年级未知，名称标注待核实。
        curricula: dict[str, Curriculum] = {}
        for name, major in majors.items():
            curricula[name] = await _get_or_create(
                session,
                Curriculum,
                major_id=major.id,
                name=f"{name}培养方案（待核实）",
            )

        courses: dict[str, Course] = {}
        for major_names, name, code, term, requirement, _credits in COURSES:
            course = await _get_or_create(
                session,
                Course,
                university_id=university.id,
                name=name,
                code=code,
                defaults={"status": CourseStatus.ACTIVE},
            )
            courses[name] = course
            for major_name in major_names:
                await _get_or_create(
                    session,
                    CurriculumCourse,
                    curriculum_id=curricula[major_name].id,
                    course_id=course.id,
                    defaults={"requirement_type": requirement, "recommended_term": term},
                )

        for academic_year, season in SEMESTERS:
            await _get_or_create(
                session,
                Semester,
                university_id=university.id,
                academic_year=academic_year,
                season=season,
            )

        for course_name, alias in ALIASES:
            await _get_or_create(
                session,
                CourseAlias,
                course_id=courses[course_name].id,
                alias=alias,
                defaults={"source": "abbreviation"},
            )

        await session.commit()

    print(
        "seed_catalog 完成："
        f"{len(COLLEGES)} 个学院、{len(MAJORS)} 个专业、"
        f"{len({name for _, name, *_ in COURSES})} 门课程、"
        f"{sum(len(majors) for majors, *_ in COURSES)} 条培养方案-课程关联、"
        f"{len(SEMESTERS)} 个学期、{len(ALIASES)} 条别名。"
    )


if __name__ == "__main__":
    asyncio.run(seed_catalog())
