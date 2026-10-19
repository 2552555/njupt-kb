"""搜索日志保留期清理命令行入口（设计文档 §22）。

只删除 `search_queries` 中超过 `SEARCH_LOG_RETENTION_DAYS` 的聚合日志，
不触碰课程、资源、账号或审计记录。运维定时任务或手动执行：

    python -m app.modules.search.retention

依赖 DATABASE_URL 指向的数据库（部署环境由 Alembic 先行迁移 0011）。
"""

from __future__ import annotations

import asyncio
import logging

from app.db import async_session_factory
from app.modules.search import service

logger = logging.getLogger("app.search.retention")


async def _run() -> int:
    async with async_session_factory() as db:
        try:
            count = await service.run_retention_cleanup(db)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("search_retention_cleanup_failed")
            raise
    logger.info("search_retention_cleanup_done", extra={"purged": count})
    return count


def main() -> int:
    count = asyncio.run(_run())
    print(f"search retention cleanup purged {count} row(s)")
    return count


if __name__ == "__main__":
    main()
