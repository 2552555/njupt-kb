"""全部 ORM 模型的汇总注册。

Alembic 自动生成与应用启动都依赖本模块：只要在这里导入，Base.metadata 就完整。
新增模块时必须在此追加一行，否则迁移会漏表。
"""

from app.core.idempotency import IdempotencyRecord  # noqa: F401
from app.modules.access.models import *  # noqa: F401,F403
from app.modules.ai.models import *  # noqa: F401,F403
from app.modules.catalog.models import *  # noqa: F401,F403
from app.modules.commerce.models import *  # noqa: F401,F403
from app.modules.content.models import *  # noqa: F401,F403
from app.modules.creator.models import *  # noqa: F401,F403
from app.modules.identity.models import *  # noqa: F401,F403
from app.modules.interaction.models import *  # noqa: F401,F403
from app.modules.jobs.models import *  # noqa: F401,F403
from app.modules.moderation.models import *  # noqa: F401,F403
from app.modules.notification.models import *  # noqa: F401,F403
from app.modules.topics.models import *  # noqa: F401,F403
