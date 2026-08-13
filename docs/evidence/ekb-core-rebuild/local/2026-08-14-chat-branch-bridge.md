# Chat branch bridge evidence

- `store.save_message` 在 v4 schema 下会自动创建 `root` / `active` branch，并写入 `branch_id`、`parent_message_id`、`content_hash`。
- 无 v4 列时走 legacy 路径。
- `bridge-check` 临时 SQLite：PASS。
- PH4：32 passed；API：28 passed；ruff/compileall：passed。
- `qa.py` history graph bridge：尚未完成；浏览器未认证。
- 生产、服务器、备份、部署、回滚：NOT RUN / BLOCKED。
- 本记录不含秘密。
