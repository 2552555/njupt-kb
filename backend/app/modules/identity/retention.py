"""保留期清理命令行入口（zhanghao.md §9「注销账号」）。

仅清理注销超过保留期且尚未清理的账号；不删除用户行与任何业务/审计/内容记录，
只抹除可识别 PII（邮箱置为墓碑值）。运维定时任务或手动执行：

    python -m app.modules.identity.retention

依赖 DATABASE_URL 指向的数据库（部署环境由 Alembic 先行迁移 0009）。
"""

from __future__ import annotations

import asyncio
import logging

from app.db import async_session_factory
from app.modules.identity import service

logger = logging.getLogger("app.identity.retention")


async def _run() -> int:
    async with async_session_factory() as db:
        try:
            count = await service.run_retention_cleanup(db)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("retention_cleanup_failed")
            raise
    logger.info("retention_cleanup_done", extra={"purged": count})
    return count


def main() -> None:
    count = asyncio.run(_run())
    print(f"retention cleanup purged {count} account(s)")


if __name__ == "__main__":
    main()
