"""search 模块：统一搜索、搜索建议与热词（设计文档 §22）。

搜索是独立能力，不依赖 Elasticsearch 等外部服务：本版只用 PostgreSQL / SQLite
都可编译的 SQL 实现召回与排序。查询日志只保留归一化词与结果计数，用于热词与
零结果观测，不保存用户标识（见 `models.SearchQuery`）。

路由前缀 `/api/v1/search` 由 `app/main.py` 统一添加，本模块不自行决定前缀。
"""

from __future__ import annotations
