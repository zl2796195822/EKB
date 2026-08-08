# EKB Skills

这些目录保存 EKB 研发使用的第三方参考资料，不属于产品运行时依赖。开始任务前先阅读 [开发 Skills 与使用规范](../docs/开发Skills与使用规范_v1.0_2026-08-07.md) 和 [文档治理与研发交付规范](../docs/文档治理与研发交付规范_v1.0_2026-08-07.md)，再按 Backlog 的 `Skill refs` 读取对应目录中的 `SKILL.md`。

## 当前纳入

- `react-best-practices`：React 性能与数据获取
- `composition-patterns`：可复用组件和共享状态
- `web-design-guidelines`：无障碍、表单、响应式和交互审查
- `writing-guidelines`：产品文案和项目文档审查
- `security-best-practices`：Python/FastAPI、TypeScript/React 安全实践
- `security-threat-model`：信任边界和攻击路径建模
- `playwright`：真实浏览器流程、截图和问题复现

项目原有的 `anthropics-skills`、`galaxy`、`gsap-skills-main`、`impeccable-main`、`mengto-skills`、`react-bits` 和 `taste-skill` 仍是历史参考。使用前必须单独核验来源、版本和许可证。

Skills 不能覆盖 Spec、安全设计、API 契约、数据模型、测试门禁或决策记录。任务完成时，在变更说明中记录实际使用的 skills、规则文件、测试证据和例外理由；不得把真实客户数据、生产密钥或内部地址提供给 skill 脚本或外部服务。
