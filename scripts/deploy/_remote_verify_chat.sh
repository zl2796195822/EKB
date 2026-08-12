#!/usr/bin/env bash
# 在服务器上验证 chat 通过代理 + 配置中心链路
set +e
echo "--- (A) 容器 env 检查代理是否注入（仅输出变量名，不输出值）： ---"
docker exec ekb-api sh -c "env | sed 's/=.*//' | grep -iE 'proxy' | sort"
echo
echo "--- (B) provider egress probe（目标由 EKB_PROVIDER_HEALTH_URL 运行时提供；不打印地址）： ---"
if [ -z "${EKB_PROVIDER_HEALTH_URL:-}" ]; then
  echo "NOT RUN: EKB_PROVIDER_HEALTH_URL is not configured"
else
docker exec -u 0 ekb-api env EKB_PROVIDER_HEALTH_URL="${EKB_PROVIDER_HEALTH_URL}" python3 -c "
import os, urllib.request
try:
    r = urllib.request.urlopen(os.environ['EKB_PROVIDER_HEALTH_URL'], timeout=12)
    print('provider health probe completed; status=', r.status)
except Exception:
    print('provider health probe failed; details withheld')
" 2>&1 | tail -n 15
fi
echo
echo "--- (C) runtime chat providers（配置中心读 DB）： ---"
docker exec -u 0 ekb-api python3 <<'PYEOF'
import sys, os
sys.path.insert(0, "/app/ekb_api")
if os.path.exists("/app/.env"):
    for line in open("/app/.env"):
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); k=k.strip(); v=v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")): v=v[1:-1]
        os.environ.setdefault(k,v)
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.core.db import get_engine
from sqlalchemy import text
e = get_engine()
with e.connect() as c:
    uid, tid = c.execute(text("SELECT id, tenant_id FROM users LIMIT 1")).fetchone()
auth = AuthContext(actor_id=uid, tenant_id=tid, tenant_role=TenantRole.OWNER,
    platform_role="PLATFORM_ADMIN", capabilities=["*"], policy_version=1, trace_id="t1")
from ekb_api.core.config import get_runtime_chat_providers
ps = get_runtime_chat_providers(tenant_id=auth.tenant_id, user_id=auth.actor_id)
print(f"total runtime chat providers: {len(ps)}")
for _ in ps[:6]:
    print(" - provider configured (identity and endpoint withheld)")
PYEOF
echo
echo "--- (D) 实际调用 chat，超时 45 秒： ---"
timeout 55 docker exec -u 0 ekb-api python3 <<'PYEOF'
import sys, os
sys.path.insert(0, "/app/ekb_api")
if os.path.exists("/app/.env"):
    for line in open("/app/.env"):
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); k=k.strip(); v=v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")): v=v[1:-1]
        os.environ.setdefault(k,v)
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.core.db import get_engine
from sqlalchemy import text
e = get_engine()
with e.connect() as c:
    uid, tid = c.execute(text("SELECT id, tenant_id FROM users LIMIT 1")).fetchone()
auth = AuthContext(actor_id=uid, tenant_id=tid, tenant_role=TenantRole.OWNER,
    platform_role="PLATFORM_ADMIN", capabilities=["*"], policy_version=1, trace_id="t1")
from ekb_api.llm import chat, LlmError
try:
    out = chat(
        [{"role":"user","content":"你好，回复一个字"}],
        temperature=0,
        tenant_id=auth.tenant_id,
        user_id=auth.actor_id
    )
    print("[chat-SUCCESS]: response received (body withheld)")
except LlmError as e:
    print("[chat-LlmError]: provider call failed (details withheld)")
except Exception as ex:
    print("[chat-OTHER]: provider call failed (details withheld)")
PYEOF
