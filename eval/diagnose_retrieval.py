"""诊断 G069 的 query 改写 + 多路召回候选。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "apps/api")

os.environ.setdefault("EKB_ENV", "development")

from ekb_api.core.config import get_settings
from ekb_api.core.db import init_db
from ekb_api.core.auth import AuthContext
from ekb_api.domain import TenantRole
from ekb_api.store import SqlStore
from ekb_api.llm import rewrite_query
from ekb_api.retrieval import retrieve

init_db()
store = SqlStore()
settings = get_settings()

# 构造 dev auth context
user = store.user
tenant = store.tenant
auth = AuthContext(
    actor_id=user.id,
    tenant_id=tenant.id,
    tenant_role=tenant.role,
    platform_role="NONE",
    capabilities=[],
    policy_version=tenant.policy_version,
    trace_id="diag",
)

question = "网络延迟恢复步骤的顺序是什么？"

# 1. 看 query 改写
print("=== Query 改写 ===")
queries = rewrite_query(question)
for i, q in enumerate(queries):
    print(f"  [{i}] {q}")

# 2. 看多路召回
print("\n=== 多路召回候选 ===")
chunks = retrieve(store, auth, question, [], 5)
for i, c in enumerate(chunks, 1):
    print(f"  #{i} score={c.score:.2f} | {c.title} | {' / '.join(c.section_path)}")
    print(f"      content[:100]: {c.content[:100]}")

# 3. 逐 query 看召回
print("\n=== 逐 query 召回 ===")
for q_idx, q in enumerate(queries):
    results = store.search(auth, q, [], 20)
    print(f"\n  query[{q_idx}]: {q}")
    for i, c in enumerate(results, 1):
        marker = " ← 恢复步骤" if "恢复步骤" in " / ".join(c.section_path) else ""
        print(f"    #{i} score={c.score:.2f} | {c.title} | {' / '.join(c.section_path)}{marker}")
