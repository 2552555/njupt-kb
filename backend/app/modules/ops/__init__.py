"""ops 模块：运营任务工作台（设计文档 §27 后台概览）。

只做跨模块待办计数聚合，不拥有业务表：数据来源仍是 moderation / interaction /
notification / jobs 各自拥有的表。路由前缀 `/admin/workbench` 由 `app/main.py` 统一加。
"""

from __future__ import annotations
