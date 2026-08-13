# visual fixtures boundary

该目录只允许放置经过脱敏、只读、用于同视口结构对照的视觉样本。它不能创建 session 或 token，不能参与生产业务提交，不能被当作 API 成功结果。

M0 不提供业务 fixtures；后续视觉检查如果需要样本，必须显式标记为 visual-only，并同时覆盖真实 API 不可用的状态。
