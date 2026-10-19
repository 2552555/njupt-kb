"""licensing 模块：商业授权协议、双向定价与商品组成（用户权责体系 §6—§8、§15.3）。

本模块只记录合同与授权事实，不触碰资金；真实收费与结算受 §2.5 G1—G8 门槛约束，
`COMMERCE_ENABLED` 与 `COMMERCE_COMPLIANCE_GATE_CLOSED` 均放行后才可能启用。
"""

from app.modules.licensing.models import *  # noqa: F401,F403
