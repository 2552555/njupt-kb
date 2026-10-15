"""离线生成 Alembic 初始迁移。

本环境无法启动 PostgreSQL（无 Docker、无本地 PG、二进制源不可达），
因此无法运行 `alembic revision --autogenerate`（autogenerate 需要连接数据库
做差异比对）。本脚本改用 SQLAlchemy 自带的 PostgreSQL DDL 编译器，
对 `Base.metadata` 中全部表逐一编译出与 autogenerate 同源的建表语句，
写入 `alembic/versions/0001_initial.py`。

要点：
- 外键约束（含 access_policies<->resources 的循环引用）不内联进 CREATE TABLE，
  而是在所有表建好之后用 ALTER TABLE ADD CONSTRAINT 统一追加，避免建表顺序死锁。
- CHECK / UNIQUE / PK 仍内联在建表语句中。
- 这样生成的基线在类型、列名、约束名上与元数据完全一致，后续在真实数据库上
  运行 `alembic revision --autogenerate` 不会产生虚假差异。
"""

from __future__ import annotations

import app.models_registry  # noqa: F401 注册全部模块表
from app.db import Base
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable, ForeignKeyConstraint

PG = postgresql.dialect()

HEADER = '''"""初始基线：建全部表（设计文档 §8、§10）。

离线生成（本环境无可达 PostgreSQL，无法 autogenerate）。
DDL 由 SQLAlchemy PostgreSQL 编译器产出，与 autogenerate 同源。
外键约束在所有表建好后再追加，以容纳 access_policies<->resources 的循环引用。
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None
'''

FOOTER = '''

def upgrade() -> None:
    for ddl in _TABLE_DDL:
        op.execute(sa.text(ddl))
    for ddl in _FK_DDL:
        op.execute(sa.text(ddl))
    for ddl in _INDEX_DDL:
        op.execute(sa.text(ddl))


def downgrade() -> None:
    for name in reversed(_DROP_TABLES):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {name} CASCADE"))
'''


def _strip(ddl: str) -> str:
    ddl = ddl.strip()
    if ddl.endswith(";"):
        ddl = ddl[:-1]
    return ddl


def main() -> None:
    tables = Base.metadata.sorted_tables
    table_ddl: list[str] = []
    fk_ddl: list[str] = []
    index_ddl: list[str] = []
    drop_tables: list[str] = []

    for table in tables:
        # 建表时排除外键，避免循环引用导致建表顺序死锁。
        compiled = CreateTable(
            table, include_foreign_key_constraints=()
        ).compile(dialect=PG)
        table_ddl.append(_strip(str(compiled)))

        for con in table.constraints:
            if isinstance(con, ForeignKeyConstraint):
                add = AddConstraint(con).compile(dialect=PG)
                fk_ddl.append(_strip(str(add)))

        for idx in table.indexes:
            compiled_idx = CreateIndex(idx).compile(dialect=PG)
            index_ddl.append(_strip(str(compiled_idx)))

        drop_tables.append(table.name)

    body = HEADER
    body += "\n_TABLE_DDL = [\n"
    for d in table_ddl:
        body += "    " + repr(d) + ",\n"
    body += "]\n\n_FK_DDL = [\n"
    for d in fk_ddl:
        body += "    " + repr(d) + ",\n"
    body += "]\n\n_INDEX_DDL = [\n"
    for d in index_ddl:
        body += "    " + repr(d) + ",\n"
    body += "]\n\n_DROP_TABLES = [\n"
    for n in drop_tables:
        body += "    " + repr(n) + ",\n"
    body += "]\n"
    body += FOOTER

    out = "alembic/versions/0001_initial.py"
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)

    print(f"WROTE {out}")
    print(f"TABLES={len(tables)} FKS={len(fk_ddl)} INDEXES={len(index_ddl)}")


if __name__ == "__main__":
    main()
