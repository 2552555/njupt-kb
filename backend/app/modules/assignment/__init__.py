"""assignment 模块：按职责授予的系统管理员权限与资料管理员的资源级分配。

《课程资料平台用户权责、内容治理与授权收益体系设计文档》§3、§15.2：
- `admin_permission_assignments`：系统管理员内部权限集合，按职责最小授予、可到期、可撤销；
- `resource_assignments`：资料管理员的作用范围（本人或明确分配的资源／课程）。

RBAC 决定「能做哪类动作」，本模块决定「可对哪一个对象做」；单纯拥有角色不能绕过对象归属。
"""

from app.modules.assignment.models import AdminPermissionAssignment, ResourceAssignment

__all__ = ["AdminPermissionAssignment", "ResourceAssignment"]
