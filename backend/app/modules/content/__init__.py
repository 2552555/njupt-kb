"""content：资源、版本、文件与上传会话（设计文档 §12、§13.1、§16、§17.1、§18）。

本模块拥有资源发布相关的关键列（`publication_status`、`freshness_status`、
`published_version_id`、`revision`）；其他模块只能通过 service 中的
`mark_published` / `mark_withdrawn` / `set_freshness` 改变它们（见 docs/INTERFACES.md）。
"""
