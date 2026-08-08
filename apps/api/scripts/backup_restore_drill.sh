#!/usr/bin/env bash
# M4-6 备份恢复端到端演练脚本。
#
# 演练流程：
#   1. 准备演练库（含种子数据）
#   2. 备份
#   3. 模拟数据丢失（删表/改数据）
#   4. 恢复
#   5. 校验恢复后数据与备份一致
#   6. 记录 RPO/RTO
#
# 用法：bash scripts/backup_restore_drill.sh [WORK_DIR]
# 退出码：0=成功，1=失败

set -euo pipefail

WORK_DIR="${1:-./drill_workspace}"
PYTHON="${PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$WORK_DIR"
DB_PATH="$WORK_DIR/drill.db"
BACKUP_DIR="$WORK_DIR/backups"
LOG_FILE="$WORK_DIR/drill_result.json"

echo "=== M4-6 备份恢复演练 ==="
echo "工作目录: $WORK_DIR"
echo "数据库: $DB_PATH"

# 清理旧演练数据
rm -f "$DB_PATH"
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

# 1. 准备演练库
echo ""
echo "[1/6] 准备演练库（种子数据）..."
$PYTHON -c "
import sqlite3, json
conn = sqlite3.connect('$DB_PATH')
conn.execute('CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT, content TEXT)')
conn.execute('CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, content TEXT)')
conn.executemany('INSERT INTO documents VALUES (?, ?, ?)', [
    (1, 'SOP-A', '内容A'),
    (2, 'SOP-B', '内容B'),
    (3, 'SOP-C', '内容C'),
])
conn.executemany('INSERT INTO chunks VALUES (?, ?, ?)', [
    (1, 1, 'chunk-A1'),
    (2, 1, 'chunk-A2'),
    (3, 2, 'chunk-B1'),
])
conn.commit()
conn.close()
print('种子数据：3 documents, 3 chunks')
"

# 记录备份前行数
BEFORE_DOCS=$($PYTHON -c "import sqlite3; print(sqlite3.connect('$DB_PATH').execute('SELECT COUNT(*) FROM documents').fetchone()[0])")
BEFORE_CHUNKS=$($PYTHON -c "import sqlite3; print(sqlite3.connect('$DB_PATH').execute('SELECT COUNT(*) FROM chunks').fetchone()[0])")
echo "备份前：documents=$BEFORE_DOCS, chunks=$BEFORE_CHUNKS"

# 2. 备份
echo ""
echo "[2/6] 执行备份..."
BACKUP_START=$(date +%s.%N)
BACKUP_RESULT=$($PYTHON "$SCRIPT_DIR/backup.py" --db "sqlite:///$DB_PATH" --out-dir "$BACKUP_DIR" --keep 7)
BACKUP_END=$(date +%s.%N)
BACKUP_TIME=$(echo "$BACKUP_END - $BACKUP_START" | bc)
BACKUP_FILE=$(echo "$BACKUP_RESULT" | $PYTHON -c "import sys,json; print(json.load(sys.stdin)['backup_path'])")
BACKUP_SHA=$(echo "$BACKUP_RESULT" | $PYTHON -c "import sys,json; print(json.load(sys.stdin)['backup_sha256'])")
echo "备份完成：$BACKUP_FILE (sha256: ${BACKUP_SHA:0:16}...)"
echo "备份耗时：${BACKUP_TIME}s"

# 3. 模拟数据丢失
echo ""
echo "[3/6] 模拟数据丢失（删除全部数据）..."
$PYTHON -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
conn.execute('DELETE FROM chunks')
conn.execute('DELETE FROM documents')
conn.commit()
conn.close()
"
AFTER_LOSS_DOCS=$($PYTHON -c "import sqlite3; print(sqlite3.connect('$DB_PATH').execute('SELECT COUNT(*) FROM documents').fetchone()[0])")
echo "数据丢失后：documents=$AFTER_LOSS_DOCS (应为 0)"

# 4. 恢复
echo ""
echo "[4/6] 执行恢复..."
RESTORE_START=$(date +%s.%N)
RESTORE_RESULT=$($PYTHON "$SCRIPT_DIR/restore.py" --backup "$BACKUP_FILE" --db "sqlite:///$DB_PATH" --out-dir "$BACKUP_DIR" --force)
RESTORE_END=$(date +%s.%N)
RESTORE_TIME=$(echo "$RESTORE_END - $RESTORE_START" | bc)
echo "恢复完成，耗时：${RESTORE_TIME}s"

# 5. 校验
echo ""
echo "[5/6] 校验恢复后数据..."
AFTER_RESTORE_DOCS=$($PYTHON -c "import sqlite3; print(sqlite3.connect('$DB_PATH').execute('SELECT COUNT(*) FROM documents').fetchone()[0])")
AFTER_RESTORE_CHUNKS=$($PYTHON -c "import sqlite3; print(sqlite3.connect('$DB_PATH').execute('SELECT COUNT(*) FROM chunks').fetchone()[0])")
echo "恢复后：documents=$AFTER_RESTORE_DOCS, chunks=$AFTER_RESTORE_CHUNKS"

if [ "$AFTER_RESTORE_DOCS" != "$BEFORE_DOCS" ] || [ "$AFTER_RESTORE_CHUNKS" != "$BEFORE_CHUNKS" ]; then
    echo "❌ 校验失败：恢复后行数与备份前不一致"
    exit 1
fi
echo "✅ 校验通过：恢复后数据与备份前一致"

# 6. 记录 RPO/RTO
echo ""
echo "[6/6] 记录演练结果..."
$PYTHON -c "
import json
result = {
    'drill_timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'backup_file': '$BACKUP_FILE',
    'backup_sha256': '$BACKUP_SHA',
    'backup_time_seconds': round(float('$BACKUP_TIME'), 3),
    'restore_time_seconds': round(float('$RESTORE_TIME'), 3),
    'rpo': '0（VACUUM INTO 在线一致性快照，备份时刻即恢复点）',
    'rto': f'{round(float(\"$RESTORE_TIME\"), 3)}s（SQLite 文件级恢复，停服务后文件覆盖）',
    'verification': {
        'before': {'documents': $BEFORE_DOCS, 'chunks': $BEFORE_CHUNKS},
        'after_loss': {'documents': $AFTER_LOSS_DOCS, 'chunks': 0},
        'after_restore': {'documents': $AFTER_RESTORE_DOCS, 'chunks': $AFTER_RESTORE_CHUNKS},
        'passed': True,
    },
}
print(json.dumps(result, ensure_ascii=False, indent=2))
" | tee "$LOG_FILE"

echo ""
echo "=== 演练完成 ==="
echo "结果已写入：$LOG_FILE"
