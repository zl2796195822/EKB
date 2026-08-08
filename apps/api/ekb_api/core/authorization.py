from __future__ import annotations

from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext, KbRole

# ---- 能力常量（M2-2 RBAC）----
CAP_KB_READ = "kb:read"
CAP_KB_WRITE = "kb:write"
CAP_QA_ASK = "qa:ask"
CAP_AUDIT_READ = "audit:read"
CAP_TENANT_PROVISION = "tenant:provision"

# 平台管理员的额外能力（仅 dev_is_platform_admin 或真实平台角色时注入）。
PLATFORM_CAPABILITIES = [CAP_TENANT_PROVISION]

# 角色 → 能力映射。OWNER/ADMIN 等同全量；MEMBER 只读+问答+审计；CUSTOMER 仅问答。
ROLE_CAPABILITIES: dict[str, list[str]] = {
    "OWNER": [CAP_KB_READ, CAP_KB_WRITE, CAP_QA_ASK, CAP_AUDIT_READ],
    "ADMIN": [CAP_KB_READ, CAP_KB_WRITE, CAP_QA_ASK, CAP_AUDIT_READ],
    "MEMBER": [CAP_KB_READ, CAP_QA_ASK, CAP_AUDIT_READ],
    "CUSTOMER": [CAP_KB_READ, CAP_QA_ASK],
}

# KB 内可管理成员/可见性的角色。
KB_MANAGER_ROLES = {KbRole.OWNER.value, KbRole.ADMIN.value}


def capabilities_for_role(role: str, *, platform_admin: bool = False) -> list[str]:
    """返回某角色的能力列表；平台管理员追加 tenant:provision。"""
    caps = list(ROLE_CAPABILITIES.get(role, ROLE_CAPABILITIES["MEMBER"]))
    if platform_admin:
        caps = caps + [c for c in PLATFORM_CAPABILITIES if c not in caps]
    return caps


def assert_capability(auth: AuthContext, required: str) -> None:
    """统一授权函数（M2-5）：缺能力即统一拒绝并审计可由调用方补充。

    只抛出 PERMISSION_DENIED，不泄露资源是否存在。
    """
    if required not in auth.capabilities:
        raise ApiError(
            status_code=403,
            code="PERMISSION_DENIED",
            message="当前账号无权执行该操作",
        )


def assert_kb_manager(auth: AuthContext, kb_role: KbRole | None) -> None:
    """M2-2：仅 KB OWNER/ADMIN 成员可管理成员与可见性；否则统一拒绝。"""
    if kb_role is None or kb_role.value not in KB_MANAGER_ROLES:
        raise ApiError(
            status_code=403,
            code="PERMISSION_DENIED",
            message="仅知识库管理员可管理成员",
        )
