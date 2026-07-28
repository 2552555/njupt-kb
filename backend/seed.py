"""
Seed script for NJUPT Knowledge Base.
Creates comprehensive test data including colleges, majors, courses,
competitions, baoyan experiences, transfer cases, kaoyan data, users and resources.

Usage: python seed.py
"""
import asyncio
import uuid
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings
from app.database import Base
from app.models.college import College
from app.models.major import Major
from app.models.course import Course
from app.models.user import User
from app.models.resource import Resource
from app.models.comment import Comment
from app.models.download import Download
from app.models.favorite import Favorite
from app.models.points import PointsLog
from app.models.tag import Tag, ResourceTag
from app.models.baoyan import BaoyanPolicy, BaoyanExperience
from app.models.competition import Competition, CompetitionWinner
from app.models.transfer import MajorTransferPolicy, MajorTransferCase
from app.models.kaoyan import KaoyanSchool, KaoyanMajor, KaoyanScoreLine
from app.models.scraper import ScrapedSource, ScrapedPage
from app.core.security import hash_password

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        # Check if data already exists
        from sqlalchemy import select, func
        count_result = await session.execute(select(func.count(College.id)))
        if count_result.scalar() > 0:
            print("Database already seeded. Skipping.")
            return

        print("Seeding database...")

        # =============================================
        # 12 NJUPT Colleges
        # =============================================
        colleges_data = [
            {"id": uuid.uuid4(), "name": "通信与信息工程学院", "short_name": "通信学院"},
            {"id": uuid.uuid4(), "name": "电子与光学工程学院/柔性电子（未来技术）学院", "short_name": "电光学院"},
            {"id": uuid.uuid4(), "name": "计算机学院/软件学院/网络空间安全学院", "short_name": "计算机学院"},
            {"id": uuid.uuid4(), "name": "自动化学院/人工智能学院", "short_name": "自动化学院"},
            {"id": uuid.uuid4(), "name": "材料科学与工程学院", "short_name": "材料学院"},
            {"id": uuid.uuid4(), "name": "物联网学院", "short_name": "物联网学院"},
            {"id": uuid.uuid4(), "name": "理学院", "short_name": "理学院"},
            {"id": uuid.uuid4(), "name": "管理学院", "short_name": "管理学院"},
            {"id": uuid.uuid4(), "name": "经济学院", "short_name": "经济学院"},
            {"id": uuid.uuid4(), "name": "传媒与艺术学院", "short_name": "传媒学院"},
            {"id": uuid.uuid4(), "name": "外国语学院", "short_name": "外国语学院"},
            {"id": uuid.uuid4(), "name": "贝尔英才学院", "short_name": "贝尔英才学院"},
        ]
        colleges = {}
        for c in colleges_data:
            college = College(id=c["id"], name=c["name"], short_name=c["short_name"])
            session.add(college)
            colleges[c["short_name"]] = college

        await session.flush()
        print(f"  Created {len(colleges_data)} colleges")

        # =============================================
        # 30+ Majors
        # =============================================
        majors_data = [
            # 通信学院
            {"college": "通信学院", "name": "通信工程", "category": "工学"},
            {"college": "通信学院", "name": "电子信息工程", "category": "工学"},
            {"college": "通信学院", "name": "广播电视工程", "category": "工学"},
            # 电光学院
            {"college": "电光学院", "name": "电子科学与技术", "category": "工学"},
            {"college": "电光学院", "name": "光电信息科学与工程", "category": "工学"},
            {"college": "电光学院", "name": "微电子科学与工程", "category": "工学"},
            # 计算机学院
            {"college": "计算机学院", "name": "计算机科学与技术", "category": "工学"},
            {"college": "计算机学院", "name": "软件工程", "category": "工学"},
            {"college": "计算机学院", "name": "网络空间安全", "category": "工学"},
            {"college": "计算机学院", "name": "数据科学与大数据技术", "category": "工学"},
            # 自动化学院
            {"college": "自动化学院", "name": "自动化", "category": "工学"},
            {"college": "自动化学院", "name": "人工智能", "category": "工学"},
            {"college": "自动化学院", "name": "智能科学与技术", "category": "工学"},
            {"college": "自动化学院", "name": "测控技术与仪器", "category": "工学"},
            # 材料学院
            {"college": "材料学院", "name": "材料物理", "category": "工学"},
            {"college": "材料学院", "name": "材料化学", "category": "工学"},
            {"college": "材料学院", "name": "高分子材料与工程", "category": "工学"},
            # 物联网学院
            {"college": "物联网学院", "name": "物联网工程", "category": "工学"},
            {"college": "物联网学院", "name": "网络工程", "category": "工学"},
            # 理学院
            {"college": "理学院", "name": "应用物理学", "category": "理学"},
            {"college": "理学院", "name": "信息与计算科学", "category": "理学"},
            {"college": "理学院", "name": "应用统计学", "category": "理学"},
            # 管理学院
            {"college": "管理学院", "name": "信息管理与信息系统", "category": "管理学"},
            {"college": "管理学院", "name": "工商管理", "category": "管理学"},
            {"college": "管理学院", "name": "市场营销", "category": "管理学"},
            # 经济学院
            {"college": "经济学院", "name": "经济学", "category": "经济学"},
            {"college": "经济学院", "name": "国际经济与贸易", "category": "经济学"},
            {"college": "经济学院", "name": "金融工程", "category": "经济学"},
            # 传媒学院
            {"college": "传媒学院", "name": "广告学", "category": "文学"},
            {"college": "传媒学院", "name": "数字媒体艺术", "category": "艺术学"},
            # 外国语学院
            {"college": "外国语学院", "name": "英语", "category": "文学"},
            {"college": "外国语学院", "name": "翻译", "category": "文学"},
            # 贝尔英才学院
            {"college": "贝尔英才学院", "name": "理工交叉创新班", "category": "工学"},
            {"college": "贝尔英才学院", "name": "信息科技英才班", "category": "工学"},
        ]
        majors = {}
        for m in majors_data:
            major = Major(
                id=uuid.uuid4(),
                college_id=colleges[m["college"]].id,
                name=m["name"],
                category=m["category"],
                degree="学士",
                duration=4,
            )
            session.add(major)
            majors[m["name"]] = major

        await session.flush()
        print(f"  Created {len(majors_data)} majors")

        # =============================================
        # 80+ Courses
        # =============================================
        courses_data = [
            # General courses (shared)
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
            # 通信学院 courses
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
            # 计算机学院 courses
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
            # 电光学院
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
            # 自动化学院
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
            # 物联网学院
            (["物联网工程"],
             "物联网导论", None, "大一上", "required", 2.0),
            (["物联网工程"],
             "嵌入式系统", None, "大二下", "required", 3.0),
            (["物联网工程"],
             "传感器网络", None, "大三上", "required", 3.0),
            (["物联网工程", "网络工程"],
             "无线通信技术", None, "大二下", "elective", 2.0),
            # 理学院
            (["应用物理学"],
             "量子力学", None, "大二下", "required", 4.0),
            (["应用物理学"],
             "热力学与统计物理", None, "大三上", "required", 3.5),
            (["信息与计算科学"],
             "数值分析", None, "大二下", "required", 3.0),
            (["应用统计学"],
             "多元统计分析", None, "大三上", "required", 3.0),
            # 更多课程 (补充)
            (["通信工程", "计算机科学与技术", "自动化"],
             "复变函数与积分变换", None, "大二上", "required", 2.0),
            (["通信工程", "计算机科学与技术", "自动化", "电子信息工程", "电子科学与技术"],
             "军事理论", None, "大一上", "required", 1.0),
            (["通信工程", "计算机科学与技术", "软件工程"],
             "工程制图", None, "大一上", "required", 2.0),
            # 80th course
            (["计算机科学与技术", "软件工程", "网络工程", "数据科学与大数据技术"],
             "程序设计综合实验", None, "大一上", "elective", 1.0),
            # Additional to reach 80+
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
        courses = {}
        teachers_pool = ["张教授", "李教授", "王教授", "刘教授", "陈教授", "赵老师", "周老师", "吴老师", "杨老师", "黄老师",
                         "朱老师", "马老师", "孙老师", "胡老师", "何老师", "高老师", "林老师", "郭老师", "罗老师", "郑老师"]
        for i, (major_names, name, code, semester, ctype, credits) in enumerate(courses_data):
            for mn in major_names:
                if mn in majors:
                    course = Course(
                        id=uuid.uuid4(),
                        major_id=majors[mn].id,
                        name=name,
                        course_code=code or f"B{10000 + i:05d}S",
                        semester=semester,
                        type=ctype,
                        credits=credits,
                        teacher=teachers_pool[i % len(teachers_pool)],
                    )
                    session.add(course)
                    courses[f"{mn}:{name}"] = course

        await session.flush()
        print(f"  Created {len(courses)} course-major assignments (from {len(courses_data)} unique patterns)")

        # =============================================
        # Test Users
        # =============================================
        test_user_id = uuid.uuid4()
        admin_user_id = uuid.uuid4()

        test_user = User(
            id=test_user_id,
            email="test@njupt.edu.cn",
            password_hash=hash_password("password123"),
            display_name="测试学生",
            student_id="B20010101",
            college_id=colleges["计算机学院"].id,
            major_id=majors["计算机科学与技术"].id,
            enrollment_year=2020,
            points=500,
            role="student",
            is_verified=True,
        )
        admin_user = User(
            id=admin_user_id,
            email="admin@njupt.edu.cn",
            password_hash=hash_password("admin123"),
            display_name="管理员",
            student_id="A20010001",
            college_id=colleges["通信学院"].id,
            major_id=majors["通信工程"].id,
            enrollment_year=2019,
            points=9999,
            role="admin",
            is_verified=True,
        )
        session.add(test_user)
        session.add(admin_user)

        # Sample users for experiences
        jj_id = uuid.uuid4()
        xx_id = uuid.uuid4()
        ww_id = uuid.uuid4()
        session.add(User(id=jj_id, email="jj@njupt.edu.cn", password_hash=hash_password("123456"), display_name="蒋同学", student_id="B20010201", college_id=colleges["通信学院"].id, major_id=majors["通信工程"].id, enrollment_year=2020, points=200, role="student"))
        session.add(User(id=xx_id, email="xx@njupt.edu.cn", password_hash=hash_password("123456"), display_name="许同学", student_id="B20010301", college_id=colleges["计算机学院"].id, major_id=majors["计算机科学与技术"].id, enrollment_year=2020, points=300, role="student"))
        session.add(User(id=ww_id, email="ww@njupt.edu.cn", password_hash=hash_password("123456"), display_name="王同学", student_id="B20010401", college_id=colleges["电光学院"].id, major_id=majors["电子科学与技术"].id, enrollment_year=2020, points=150, role="student"))

        await session.flush()
        print("  Created 5 users (2 test + 3 sample)")

        # =============================================
        # 8 Competitions
        # =============================================
        comps_data = [
            {"name": "全国大学生数学建模竞赛", "category": "学科竞赛", "difficulty": 4, "organizer": "教育部/中国工业与应用数学学会",
             "registration_period": "每年5月", "competition_period": "每年9月", "suitable_majors": ["全部专业"], "suitable_grades": ["大二及以上"],
             "team_size": "3人", "njupt_contact": "理学院竞赛指导中心", "njupt_winners_count": 120,
             "description": "全国大学生数学建模竞赛是中国规模最大的数学建模赛事，考察数学建模、编程和论文写作能力。"},
            {"name": "全国大学生电子设计竞赛", "category": "学科竞赛", "difficulty": 4, "organizer": "教育部/工信部",
             "registration_period": "每年5月", "competition_period": "每年8月", "suitable_majors": ["通信工程", "电子信息工程", "电子科学与技术", "自动化"],
             "suitable_grades": ["大二及以上"], "team_size": "3人", "njupt_contact": "电光学院创新中心", "njupt_winners_count": 80,
             "description": "全国电赛是电子信息类专业最具影响力的学科竞赛之一，考察电子系统设计与实践能力。"},
            {"name": "ACM-ICPC国际大学生程序设计竞赛", "category": "学科竞赛", "difficulty": 5, "organizer": "ACM",
             "registration_period": "每年9月", "competition_period": "次年5月", "suitable_majors": ["计算机科学与技术", "软件工程", "数据科学与大数据技术", "网络空间安全"],
             "suitable_grades": ["全部年级"], "team_size": "3人", "njupt_contact": "计算机学院ACM集训队", "njupt_winners_count": 50,
             "description": "ACM-ICPC是全球最具影响力的大学生程序设计竞赛，考察算法设计、编程和团队协作能力。"},
            {"name": "蓝桥杯全国软件和信息技术专业人才大赛", "category": "学科竞赛", "difficulty": 3, "organizer": "工信部人才交流中心",
             "registration_period": "每年10月", "competition_period": "次年4月", "suitable_majors": ["计算机科学与技术", "软件工程", "数据科学与大数据技术", "网络空间安全"],
             "suitable_grades": ["全部年级"], "team_size": "个人", "njupt_contact": "计算机学院学生工作办公室", "njupt_winners_count": 200,
             "description": "蓝桥杯是国内知名的IT类学科竞赛，覆盖C/C++、Java、Python等多个赛道。"},
            {"name": "挑战杯全国大学生课外学术科技作品竞赛", "category": "创新创业", "difficulty": 5, "organizer": "团中央/教育部/中国科协",
             "registration_period": "隔年举办", "competition_period": "隔年11月", "suitable_majors": ["全部专业"], "suitable_grades": ["全部年级"],
             "team_size": "1-8人", "njupt_contact": "校团委", "njupt_winners_count": 30,
             "description": "挑战杯是中国大学生科技创新创业的奥林匹克盛会，被誉为大学生科创领域的最高赛事。"},
            {"name": "中国互联网+大学生创新创业大赛", "category": "创新创业", "difficulty": 5, "organizer": "教育部",
             "registration_period": "每年4月", "competition_period": "每年10月", "suitable_majors": ["全部专业"], "suitable_grades": ["全部年级"],
             "team_size": "3-15人", "njupt_contact": "教务处/校团委", "njupt_winners_count": 25,
             "description": "互联网+大赛是中国最大规模的创新创业赛事，推动产学研用深度融合。"},
            {"name": "全国大学生集成电路创新创业大赛", "category": "学科竞赛", "difficulty": 4, "organizer": "工信部",
             "registration_period": "每年3月", "competition_period": "每年8月", "suitable_majors": ["微电子科学与工程", "电子科学与技术"],
             "suitable_grades": ["大二及以上"], "team_size": "1-3人", "njupt_contact": "电光学院", "njupt_winners_count": 15,
             "description": "集创赛聚焦集成电路设计领域，是国内芯片设计类最权威的学生竞赛。"},
            {"name": "全国大学生嵌入式芯片与系统设计竞赛", "category": "学科竞赛", "difficulty": 4, "organizer": "中国电子学会",
             "registration_period": "每年4月", "competition_period": "每年10月", "suitable_majors": ["通信工程", "电子信息工程", "自动化", "计算机科学与技术"],
             "suitable_grades": ["大二及以上"], "team_size": "1-3人", "njupt_contact": "通信学院创新基地", "njupt_winners_count": 20,
             "description": "嵌入式大赛考察嵌入式系统设计与开发能力，涵盖硬件设计与软件开发全流程。"},
        ]
        competitions = {}
        winners = []
        for c in comps_data:
            comp = Competition(
                id=uuid.uuid4(),
                name=c["name"],
                category=c["category"],
                difficulty=c["difficulty"],
                organizer=c["organizer"],
                description=c["description"],
                registration_period=c["registration_period"],
                competition_period=c["competition_period"],
                suitable_majors=c["suitable_majors"],
                suitable_grades=c["suitable_grades"],
                team_size=c["team_size"],
                njupt_contact=c["njupt_contact"],
                njupt_winners_count=c["njupt_winners_count"],
            )
            session.add(comp)
            competitions[c["name"]] = comp

            # Add 1-3 winners per competition
            winner_count = min(c.get("njupt_winners_count", 3) // 10, 3) or 1
            for w in range(winner_count):
                winners.append(CompetitionWinner(
                    id=uuid.uuid4(),
                    competition_id=comp.id,
                    year=2023 - w,
                    award_level=["一等奖", "二等奖", "三等奖"][w % 3],
                    team_members=["南邮学子"],
                    advisor="指导老师",
                    project_title=f"{c['name']} 获奖项目",
                ))
        for w in winners:
            session.add(w)

        await session.flush()
        print(f"  Created {len(comps_data)} competitions with {len(winners)} winners")

        # =============================================
        # 5 Baoyan Experiences
        # =============================================
        experiences_data = [
            {
                "author_id": jj_id,
                "college_id": colleges["通信学院"].id,
                "major_id": majors["通信工程"].id,
                "original_major": "通信工程",
                "gpa": 4.15,
                "gpa_rank": "3/120",
                "target_university": "东南大学",
                "target_college": "信息科学与工程学院",
                "target_major": "通信与信息系统",
                "offer_result": "拟录取",
                "year": 2024,
                "tags": ["通信", "985", "夏令营"],
                "advice": "建议大二下开始准备，绩点很重要，科研竞赛是加分项。东南大学通信方向夏令营竞争激烈，建议提前联系导师。",
                "summer_camp_experience": "参加了东南大学信息学院夏令营，为期3天。包含笔试和面试两个环节。笔试内容涵盖通信原理、信号与系统、数字信号处理等专业课。面试20分钟，先英文自我介绍，然后专业课问答。",
                "pre_interview_experience": "面试前复习了通信原理、信号与系统、数字信号处理三门核心课程。准备了英文简历和研究计划。",
                "interview_questions": {"英文自我介绍": "2分钟", "通信原理": "香农定理与信道容量", "信号与系统": "FFT的原理和应用", "科研经历": "大创项目介绍"},
            },
            {
                "author_id": xx_id,
                "college_id": colleges["计算机学院"].id,
                "major_id": majors["计算机科学与技术"].id,
                "original_major": "计算机科学与技术",
                "gpa": 4.22,
                "gpa_rank": "1/150",
                "target_university": "浙江大学",
                "target_college": "计算机科学与技术学院",
                "target_major": "计算机科学与技术",
                "offer_result": "拟录取",
                "year": 2024,
                "tags": ["计算机", "C9", "夏令营", "直博"],
                "advice": "GPA是第一位的，同时要有高质量的科研产出。浙大CS非常看重ACM或相关竞赛成绩。我大二参加了ACM-ICPC获得了银奖，这对简历加分很大。",
                "summer_camp_experience": "浙大夏令营：机试（4小时3题，难度较大）+ 面试（20分钟含英语考核+专业课+科研项目介绍）。机试重点考察算法、数据结构。面试很关注你的科研经历和学术兴趣。",
            },
            {
                "author_id": ww_id,
                "college_id": colleges["电光学院"].id,
                "major_id": majors["电子科学与技术"].id,
                "original_major": "电子科学与技术",
                "gpa": 4.05,
                "gpa_rank": "5/100",
                "target_university": "电子科技大学",
                "target_college": "电子科学与工程学院",
                "target_major": "电子科学与技术",
                "offer_result": "拟录取",
                "year": 2024,
                "tags": ["电子", "985", "两电一邮"],
                "advice": "电科大电科专业全国顶尖，竞争非常激烈。半导体物理和固体物理是核心课。集创赛获奖对申请帮助很大。",
            },
            {
                "author_id": uuid.uuid4(),
                "college_id": colleges["通信学院"].id,
                "major_id": majors["通信工程"].id,
                "original_major": "通信工程",
                "gpa": 3.85,
                "gpa_rank": "12/120",
                "target_university": "南京邮电大学",
                "target_college": "通信与信息工程学院",
                "target_major": "通信与信息系统",
                "offer_result": "拟录取",
                "year": 2024,
                "tags": ["南邮", "本校", "通信"],
                "advice": "如果你想继续留在南邮读研，保持不错的绩点（前30%），有过一些科研经历就可以。本校保研相对容易，但也要早做准备。导师选择很重要。",
            },
            {
                "author_id": uuid.uuid4(),
                "college_id": colleges["计算机学院"].id,
                "major_id": majors["软件工程"].id,
                "original_major": "软件工程",
                "gpa": 3.95,
                "gpa_rank": "8/100",
                "target_university": "南京大学",
                "target_college": "软件学院",
                "target_major": "软件工程",
                "offer_result": "拟录取",
                "year": 2023,
                "tags": ["软件", "C9", "预推免"],
                "advice": "南大软院看重编程能力和工程实践。建议多做一些有分量的项目放在GitHub上。预推免比夏令营竞争稍小，可以重点准备。",
            },
        ]
        for e in experiences_data:
            exp = BaoyanExperience(
                id=uuid.uuid4(),
                author_id=e["author_id"],
                college_id=e["college_id"],
                major_id=e["major_id"],
                original_major=e["original_major"],
                gpa=e["gpa"],
                gpa_rank=e["gpa_rank"],
                target_university=e["target_university"],
                target_college=e["target_college"],
                target_major=e["target_major"],
                offer_result=e["offer_result"],
                year=e["year"],
                tags=e.get("tags"),
                advice=e.get("advice"),
                summer_camp_experience=e.get("summer_camp_experience"),
                pre_interview_experience=e.get("pre_interview_experience"),
                interview_questions=e.get("interview_questions"),
                timeline={"夏令营报名": f"{e['year'] - 1}年5月", "夏令营": f"{e['year'] - 1}年7月", "预推免": f"{e['year'] - 1}年9月", "正式录取": f"{e['year']}年9月"},
                status="active",
                view_count=e.get("view_count", 0),
                helpful_count=0,
            )
            session.add(exp)

        # Also set correct author for last 2:
        # We need them persisted first
        await session.flush()

        # Baoyan policies
        policy = BaoyanPolicy(
            id=uuid.uuid4(),
            college_id=colleges["通信学院"].id,
            year=2024,
            gpa_min=3.5,
            gpa_rank_requirement="前30%",
            comprehensive_formula="综合成绩 = 绩点(70%) + 科研竞赛(20%) + 综合素质(10%)",
            total_quota=85,
            regular_quota=70,
            special_quota=15,
            competition_bonus={"国家级一等奖": 0.2, "国家级二等奖": 0.15, "省级一等奖": 0.1},
            research_bonus={"SCI论文": 0.3, "EI论文": 0.2, "核心期刊": 0.1, "发明专利": 0.15},
            source_type="官方公告",
        )
        session.add(policy)

        await session.flush()
        print("  Created 5 baoyan experiences + 1 policy")

        # =============================================
        # 3 Major Transfer Cases
        # =============================================
        transfer_cases = [
            MajorTransferCase(
                id=uuid.uuid4(), author_id=xx_id, year=2022,
                from_major="应用物理学", from_college="理学院",
                to_major="计算机科学与技术", to_college="计算机学院",
                gpa=3.9, result=True,
                exam_scores={"高等数学": 95, "C语言": 88},
                experience_text="转计算机专业竞争很大，建议大一开始准备。高数和C语言是笔试重点，面试会问一些计算机基础知识和转专业动机。我在大一下自学了Python和数据结构，面试时展示了自学的项目，老师们很认可。",
            ),
            MajorTransferCase(
                id=uuid.uuid4(), author_id=jj_id, year=2021,
                from_major="工商管理", from_college="管理学院",
                to_major="通信工程", to_college="通信学院",
                gpa=3.75, result=True,
                exam_scores={"高等数学": 90, "大学物理": 85},
                experience_text="从管理跨到通信是个挑战，但也是值得的选择。笔试难度中等，面试主要问为什么转专业。关键是要体现你对目标专业的兴趣和自学能力。",
            ),
            MajorTransferCase(
                id=uuid.uuid4(), author_id=ww_id, year=2023,
                from_major="市场营销", from_college="管理学院",
                to_major="电子科学与技术", to_college="电光学院",
                gpa=3.6, result=False,
                exam_scores={"高等数学": 72, "大学物理": 68},
                experience_text="这次转专业没有成功，分享一下踩过的坑。笔试分数不够高是主要原因。建议想转电子类的同学一定要把物理基础打牢。失败也是经验，现在通过考研途径也在读电子方向了。",
            ),
        ]
        for tc in transfer_cases:
            session.add(tc)

        policy_t = MajorTransferPolicy(
            id=uuid.uuid4(), college_id=colleges["计算机学院"].id, year=2024,
            quota=15, interview_required=True,
            requirements={"绩点要求": "≥3.0", "无挂科记录": True},
            exam_subjects={"高等数学A": "闭卷", "C语言程序设计": "闭卷"},
        )
        session.add(policy_t)

        await session.flush()
        print("  Created 3 transfer cases + 1 policy")

        # =============================================
        # 3 Kaoyan Schools with score lines
        # =============================================
        ks1 = KaoyanSchool(id=uuid.uuid4(), name="南京邮电大学", is_njupt=True, website_url="https://www.njupt.edu.cn")
        ks2 = KaoyanSchool(id=uuid.uuid4(), name="东南大学", is_njupt=False, website_url="https://www.seu.edu.cn")
        ks3 = KaoyanSchool(id=uuid.uuid4(), name="北京邮电大学", is_njupt=False, website_url="https://www.bupt.edu.cn")
        session.add_all([ks1, ks2, ks3])

        km1 = KaoyanMajor(id=uuid.uuid4(), school_id=ks1.id, name="通信与信息系统", college="通信与信息工程学院",
                          exam_subjects={"政治": 101, "英语": 201, "数学": 301, "专业课": "801通信原理"})
        km2 = KaoyanMajor(id=uuid.uuid4(), school_id=ks1.id, name="计算机科学与技术", college="计算机学院",
                          exam_subjects={"政治": 101, "英语": 201, "数学": 301, "专业课": "811数据结构"})
        km3 = KaoyanMajor(id=uuid.uuid4(), school_id=ks2.id, name="信息与通信工程", college="信息科学与工程学院",
                          exam_subjects={"政治": 101, "英语": 201, "数学": 301, "专业课": "920专业基础综合"})
        km4 = KaoyanMajor(id=uuid.uuid4(), school_id=ks3.id, name="通信与信息系统", college="信息与通信工程学院",
                          exam_subjects={"政治": 101, "英语": 201, "数学": 301, "专业课": "801通信原理"})
        session.add_all([km1, km2, km3, km4])

        # Score lines
        score_lines = [
            KaoyanScoreLine(kaoyan_major_id=km1.id, year=2024, total_score_line=320, political_line=45, english_line=45, business1_line=70, business2_line=70, applicants=450, admitted=85, lowest_score=322, highest_score=398, avg_score=345),
            KaoyanScoreLine(kaoyan_major_id=km1.id, year=2023, total_score_line=310, political_line=45, english_line=45, business1_line=68, business2_line=68, applicants=420, admitted=80, lowest_score=312, highest_score=390, avg_score=338),
            KaoyanScoreLine(kaoyan_major_id=km1.id, year=2022, total_score_line=300, political_line=40, english_line=40, business1_line=65, business2_line=65, applicants=400, admitted=78, lowest_score=302, highest_score=385, avg_score=330),
            KaoyanScoreLine(kaoyan_major_id=km2.id, year=2024, total_score_line=330, political_line=45, english_line=45, business1_line=75, business2_line=75, applicants=600, admitted=100, lowest_score=332, highest_score=410, avg_score=355),
            KaoyanScoreLine(kaoyan_major_id=km2.id, year=2023, total_score_line=315, political_line=45, english_line=45, business1_line=70, business2_line=70, applicants=550, admitted=95, lowest_score=318, highest_score=405, avg_score=345),
            KaoyanScoreLine(kaoyan_major_id=km3.id, year=2024, total_score_line=360, political_line=50, english_line=50, business1_line=80, business2_line=80, applicants=800, admitted=120, lowest_score=362, highest_score=430, avg_score=382),
        ]
        for sl in score_lines:
            session.add(sl)

        await session.flush()
        print("  Created 3 kaoyan schools, 4 majors, 6 score lines")

        # =============================================
        # Tags
        # =============================================
        tag_data = [
            ("期末重点", "#FF6B6B"), ("考研必备", "#4ECDC4"), ("保研参考", "#45B7D1"),
            ("竞赛资料", "#96CEB4"), ("实验报告", "#FFEAA7"), ("PPT课件", "#DDA0DD"),
            ("习题答案", "#98D8C8"), ("学霸笔记", "#F7DC6F"),
        ]
        tags = {}
        for name, color in tag_data:
            tag = Tag(id=uuid.uuid4(), name=name, color=color)
            session.add(tag)
            tags[name] = tag
        await session.flush()
        print("  Created 8 tags")

        # =============================================
        # 10 Sample Resources
        # =============================================
        sample_resources = []
        resource_data = [
            {"course_key": "通信工程:通信原理", "title": "通信原理期末复习笔记", "resource_type": "note",
             "description": "通信原理完整期末复习笔记，涵盖AM/FM/PM调制、PCM编码、数字基带传输等重点内容。手写整理，重点标注清晰。",
             "file_name": "通信原理期末笔记.pdf", "file_size": 5242880, "page_count": 45, "year": 2023, "semester_season": "秋",
             "view_count": 356, "download_count": 89, "avg_rating": 4.5, "rating_count": 12},
            {"course_key": "计算机科学与技术:数据结构", "title": "数据结构历年期末试卷合集（2019-2023）", "resource_type": "exam",
             "description": "数据结构近五年期末试卷整理，包含各类题型和详细答案解析。选择题+填空题+简答题+编程题全覆盖。",
             "file_name": "数据结构期末试卷合集.pdf", "file_size": 8388608, "page_count": 120, "year": 2023, "semester_season": "秋",
             "view_count": 892, "download_count": 234, "avg_rating": 4.8, "rating_count": 28},
            {"course_key": "计算机科学与技术:操作系统", "title": "操作系统实验报告完整版", "resource_type": "lab_report",
             "description": "操作系统课程设计全部实验报告，包括进程调度、内存管理、文件系统、设备管理四个模块。代码注释详细。",
             "file_name": "操作系统实验报告.zip", "file_size": 15728640, "page_count": 80, "year": 2024, "semester_season": "春"},
            {"course_key": "通信工程:模拟电子电路", "title": "模拟电子电路PPT课件全学期", "resource_type": "ppt",
             "description": "模电课程完整PPT课件，共16章。包含二极管、三极管、运算放大器、反馈放大电路等核心章节。",
             "file_name": "模电课件.pptx", "file_size": 20971520, "page_count": 320, "year": 2023, "semester_season": "秋"},
            {"course_key": "计算机科学与技术:计算机网络", "title": "计算机网络复习提纲（考研级）", "resource_type": "review",
             "description": "计算机网络考研级复习提纲，OSI七层模型、TCP/IP协议栈、路由协议、网络安全等内容全面梳理。适合考研和期末考试使用。",
             "file_name": "计算机网络复习提纲.pdf", "file_size": 3145728, "page_count": 60, "year": 2024, "semester_season": "春"},
            {"course_key": "通信工程:信号与系统", "title": "信号与系统课后习题全解", "resource_type": "exam",
             "description": "奥本海姆《信号与系统》课后习题完整解析，每道题都有详细推导过程。信号与系统课程必备参考资料。",
             "file_name": "信号系统习题解答.pdf", "file_size": 12582912, "page_count": 200, "year": 2023, "semester_season": "春"},
            {"course_key": "自动化:自动控制原理", "title": "自动控制原理思维导图全集", "resource_type": "note",
             "description": "自控原理思维导图，使用XMind制作。包含时域分析、根轨迹、频率响应、状态空间等所有核心内容。一目了然的复习利器。",
             "file_name": "自控原理思维导图.xmind", "file_size": 1048576},
            {"course_key": "计算机科学与技术:C语言程序设计", "title": "C语言程序设计大作业参考代码", "resource_type": "code",
             "description": "C语言课程设计大作业完整代码 - 学生成绩管理系统。包含文件读写、链表操作、排序查找等功能。",
             "file_name": "student_management.c", "file_size": 20480},
            {"course_key": "通信工程:数字信号处理", "title": "数字信号处理MATLAB实验代码", "resource_type": "code",
             "description": "DSP课程全部MATLAB实验代码，包括DFT、FFT、滤波器设计等内容。每段代码都有详细注释。",
             "file_name": "dsp_matlab.zip", "file_size": 5242880},
            {"course_key": "计算机科学与技术:人工智能导论", "title": "机器学习入门教程（自编讲义）", "resource_type": "textbook",
             "description": "面向本科生的机器学习入门讲义，涵盖线性回归、决策树、神经网络、SVM等经典算法。图文并茂，通俗易懂。",
             "file_name": "ML_intro.pdf", "file_size": 10485760, "page_count": 150},
        ]

        uploader_ids = [test_user_id, admin_user_id, xx_id, jj_id, ww_id]
        res_types = ["exam", "ppt", "note", "review", "lab_report", "textbook", "code", "other"]
        for i, rd in enumerate(resource_data):
            course = courses.get(rd["course_key"])
            if not course:
                continue
            res = Resource(
                id=uuid.uuid4(),
                course_id=course.id,
                uploader_id=uploader_ids[i % len(uploader_ids)],
                title=rd["title"],
                description=rd.get("description"),
                resource_type=rd["resource_type"],
                file_path=f"uploads/sample_{i}.pdf",
                file_name=rd.get("file_name"),
                file_size=rd.get("file_size"),
                page_count=rd.get("page_count"),
                view_count=rd.get("view_count", 0),
                download_count=rd.get("download_count", 0),
                avg_rating=rd.get("avg_rating", 0.0),
                rating_count=rd.get("rating_count", 0),
                year=rd.get("year", 2024),
                semester_season=rd.get("semester_season"),
                status="active",
            )
            session.add(res)
            sample_resources.append((res, i))

        await session.flush()
        print(f"  Created {len(sample_resources)} sample resources")

        # =============================================
        # Comments, Downloads, Favorites
        # =============================================
        comment_texts = [
            "非常有用的资料，感谢分享！", "整理得很详细，期末考试全靠它了",
            "质量很高，讲解清晰", "已下载，内容很全", "帮大忙了！",
            "有些地方还可以完善", "很棒的笔记！", "建议加上更多例题",
        ]
        users_list = [test_user_id, admin_user_id, xx_id, jj_id, ww_id]
        for res, idx in sample_resources[:5]:  # Comments on first 5
            for j in range(min(3, idx + 2)):
                comment = Comment(
                    id=uuid.uuid4(),
                    resource_id=res.id,
                    user_id=users_list[(idx + j) % len(users_list)],
                    content=comment_texts[(idx + j) % len(comment_texts)],
                    rating=min(5, max(4, 5 - (idx + j) % 2)),
                )
                session.add(comment)

        for res, idx in sample_resources[2:7]:
            d = Download(id=uuid.uuid4(), user_id=users_list[idx % len(users_list)], resource_id=res.id, cost_points=2)
            session.add(d)

        for res, idx in sample_resources[:4]:
            fav = Favorite(id=uuid.uuid4(), user_id=test_user_id, resource_id=res.id, folder_name="默认")
            session.add(fav)

        await session.flush()
        print("  Created comments, downloads, favorites")

        # =============================================
        # Points Logs for test user
        # =============================================
        points_events = [
            (50, "initial_bonus", "注册奖励"),
            (10, "upload_resource", "上传资源: 通信原理期末复习笔记"),
            (2, "daily_checkin", "每日签到"),
            (1, "comment", "评论资源"),
            (-2, "download_resource", "下载资源: 数据结构期末试卷"),
            (2, "daily_checkin", "每日签到"),
            (15, "share_baoyan_experience", "分享保研经验 - 东南大学"),
            (2, "daily_checkin", "每日签到"),
            (1, "comment", "评论资源"),
            (-2, "download_resource", "下载资源: 操作系统实验报告"),
        ]
        for amount, action, desc in points_events:
            log = PointsLog(user_id=test_user_id, amount=amount, action=action, description=desc)
            session.add(log)

        await session.flush()
        print("  Created points logs")

        # =============================================
        # Scraper source
        # =============================================
        src = ScrapedSource(
            id=uuid.uuid4(),
            name="南邮教务处公告",
            base_url="https://jwc.njupt.edu.cn",
            spider_name="njupt_jwc",
            schedule="daily",
            is_active=True,
        )
        session.add(src)
        await session.flush()

        sp = ScrapedPage(
            id=uuid.uuid4(),
            source_id=src.id,
            url="https://jwc.njupt.edu.cn/2024/notice1",
            title="关于2024年本科生转专业工作的通知",
            content_text="各学院：根据学校教学工作安排，2024年本科生转专业工作即将开始...",
            category="转专业",
        )
        session.add(sp)
        await session.flush()
        print("  Created scraper source + page")

        await session.commit()
        print("\nDatabase seeded successfully!")
        print(f"Summary:")
        print(f"  - {len(colleges_data)} colleges")
        print(f"  - {len(majors_data)} majors")
        print(f"  - 80+ courses")
        print(f"  - 5 users")
        print(f"  - {len(comps_data)} competitions with winners")
        print(f"  - 5 baoyan experiences + 1 policy")
        print(f"  - 3 transfer cases + 1 policy")
        print(f"  - 3 kaoyan schools, 4 majors, 6 score lines")
        print(f"  - {len(sample_resources)} sample resources with comments/downloads/favorites")
        print(f"\nTest accounts:")
        print(f"  Student: test@njupt.edu.cn / password123 (500 points)")
        print(f"  Admin:   admin@njupt.edu.cn / admin123")


if __name__ == "__main__":
    asyncio.run(seed())
