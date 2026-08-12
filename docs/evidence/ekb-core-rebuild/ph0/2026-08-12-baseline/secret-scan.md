# CR-PH0 secret and target scan

- Date: 2026-08-12
- Evidence timestamp: 2026-08-12T10:44:57+0800.
- Source: scripts/deploy/** static pattern scan executed from the current work tree.
- Result: PASS for the bounded deploy-script scan.
- Output policy: file path, line number, and pattern class only; no matched value is emitted.

## Scan command

```sh
python3 - <<'PY'
from pathlib import Path
import re

root = Path("scripts/deploy")
rules = {
    "fixed-production-target-assignment": re.compile(r'^\s*(?:export\s+)?(?:PUBLIC_URL|SERVER_HOST|DEPLOY_HOST|BASE_URL)\s*=\s*(?!.*\$\{DEPLOY_HOST\})(?!.*(?:\[production host\]|deploy\.example\.invalid|example\.invalid|example\.com))[^#\n]+', re.I),
    "fixed-remote-target": re.compile(r'(?:ssh|scp|rsync)[^#\n]*@[A-Za-z0-9_.-]+(?::|\s)', re.I),
    "literal-secret-assignment": re.compile(r'^\s*(?:export\s+)?(?:EKB_[A-Z0-9_]*(?:PASSWORD|PASS|SECRET|API_KEY)[A-Z0-9_]*|SSHPASS)\s*=\s*(?!$|["\']?\$\{|["\']?\[REDACTED\])[^#\n]+', re.I),
    "default-admin-assignment": re.compile(r"EKB_DEV_USER_(?:EMAIL|NAME)\s*=\s*(?:admin|管理员|admin@example\.com)", re.I),
    "raw-value-output": re.compile(r"原始响应|body\[:|model=|key_set=|\*\*HIDDEN\*\*", re.I),
}
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for name, rule in rules.items():
            if rule.search(line):
                print(f"{path}:{number}: {name}")
PY
```

## Results

- Fixed production marker: no matches.
- Literal secret assignment: no value-bearing matches; assignment-shaped runtime references are classified separately and are confined to the SSH helper or controlled remote checks.
- Fixed/default administrator assignment: no matches; missing or prohibited defaults fail closed.
- Raw provider/credential/response output: no matches.
- Secret values, target values, account names, and private connection details were not printed or recorded.

## Limit

This is a static pattern scan of the bounded deploy directory. It does not prove that secrets have never existed in history, in external runtime stores, or in unrelated pre-existing files outside this task scope.
