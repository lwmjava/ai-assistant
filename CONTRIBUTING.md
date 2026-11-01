# 贡献指南（Contributing Guide）

感谢你关注 **ai-assistant**！这是一个基于深度文档理解与 Agent 编排的企业级开源 AI 助手（RAG + MCP + 工作流引擎）。我们非常欢迎任何形式的贡献：提交 Bug 报告、提出功能建议、完善文档或贡献代码。

本项目采用 [Apache License 2.0](LICENSE) 开源协议。提交贡献即表示你同意你的贡献以相同协议发布。

## 目录

- [行为准则](#行为准则)
- [如何提问与报告问题](#如何提问与报告问题)
- [开发环境搭建](#开发环境搭建)
- [贡献流程](#贡献流程)
- [代码规范](#代码规范)
- [提交信息规范](#提交信息规范)
- [Pull Request 流程](#pull-request-流程)
- [版本发布](#版本发布)

## 行为准则

请保持友善、包容与尊重。我们期望所有参与者遵守以下原则：

- 对事不对人，就技术方案进行讨论；
- 尊重不同背景、经验水平的贡献者；
- 不允许任何形式的骚扰、歧视或人身攻击。

## 如何提问与报告问题

### 提问

- 先搜索已有的 [Issues](https://github.com/lwmjava/ai-assistant/issues)，避免重复提问；
- 如未找到答案，新建 Issue 并选择相应模板，提供尽可能完整的上下文。

### 报告 Bug

请在 Issue 中包含以下信息：

1. **环境信息**：操作系统、Docker / Docker Compose 版本、Python / Node.js 版本；
2. **复现步骤**：最小可复现的操作序列；
3. **期望行为** 与 **实际行为**；
4. **相关日志**：后端日志、浏览器控制台输出（请脱敏，不要包含 API Key 等敏感信息）。

### 功能建议

- 先在 Issue 中描述你的想法，说明使用场景与预期收益；
- 获得维护者认可后再开始编码，避免方向偏差导致的返工。

## 开发环境搭建

以下为外部贡献者的通用环境示例。本项目当前 Windows 工作区的 AI 验证须使用 `AGENTS.md` 第10节指定的 conda `ai-assistant` 环境，不使用 PATH 上的 base 解释器。依赖与启动配置以 README 和实际依赖声明为准。


```bash
# 1. Fork 并克隆仓库
git clone https://github.com/<你的用户名>/ai-assistant.git
cd ai-assistant

# 1.5 添加上游仓库（用于同步最新代码）
git remote add upstream https://github.com/lwmjava/ai-assistant.git

# 2. 创建 Python 虚拟环境（后端）
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 3. 安装后端依赖
pip install -r requirements.txt

# 4. 复制并填写环境变量
cp .env.example .env

# 5. 启动前端（如需修改 UI）
cd frontend
npm install
npm run dev
```

## 贡献流程

1. **Fork** 本仓库并克隆到本地；
2. 基于更新后的 `develop` 分支创建特性分支，默认命名 `codex/<简短描述>`；
3. 完成开发与自测；
4. 按规范提交代码并推送到你的 Fork；
5. 向 `lwmjava/ai-assistant` 的 `develop` 分支发起 **Pull Request**。

单任务先集成到 `develop`；项目全部任务完成且经用户最终验收后才将 `develop` 合入 `main`。分支、授权与合并要求以 [AGENTS.md 第5节](AGENTS.md#5-修改范围) 为准。

## 代码规范

- **Python**：遵循 [PEP 8](https://peps.python.org/pep-0008/)，建议使用 `ruff` 进行格式化与静态检查；公共函数需补充类型注解与文档字符串；
- **前端**：遵循现有 TypeScript / React 代码风格，提交前确保 `npm run build` 类型检查通过；
- **测试**：新增功能请附带相应测试；修复 Bug 请尽量补充可复现该 Bug 的回归测试；
- **安全**：严禁提交 API Key、密码、令牌等敏感信息；环境变量通过 `.env.example` 契约化管理，真实密钥只放在本地 `.env`（已被 `.gitignore` 忽略）。

## 提交信息规范

采用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 格式：

```
<type>(<scope>): <subject>
```

常用类型：

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `refactor` | 重构（不改变外部行为） |
| `test` | 测试相关 |
| `chore` | 构建、依赖、CI 等杂项 |
| `perf` | 性能优化 |

提交说明只写实现了什么功能，或修复了什么问题。不要写入需求编号、任务编号、ADR 编号，也不要写依据哪份需求、设计或计划文档。

示例：

```
feat(rag): 支持 Excel 文档混合检索
fix(auth): 修复多租户场景下 token 刷新失败的问题
```

## Pull Request 流程

1. PR 标题遵循提交信息规范，描述中说明 **动机、改动内容与测试方式**；
2. 保持 PR 聚焦单一改动，过大的改动请拆分；
3. 提交前同步官方仓库最新代码：
   ```bash
   git fetch upstream
   git rebase upstream/develop
   ```
   如提示 `upstream` 不存在，请先执行 `git remote add upstream https://github.com/lwmjava/ai-assistant.git`；
4. 验证与合并门禁统一执行 [AGENTS.md 第10节](AGENTS.md#10-测试与-evaluation)，检查结果须绑定最终候选；
5. 维护者会在合理时间内进行 Review，请按评审意见迭代修改。

## 版本发布

项目遵循 [语义化版本（Semantic Versioning）](https://semver.org/lang/zh-CN/)：

- **MAJOR**：不兼容的 API 变更；
- **MINOR**：向后兼容的功能新增；
- **PATCH**：向后兼容的 Bug 修复。

---

再次感谢你的贡献！🎉 如有疑问，欢迎随时在 Issue 中讨论。

## 平台与数据库验证

Ubuntu Backend 配置在非 root 的真实 Linux runner 上执行完整 pytest，并创建仅绑定本机动态端口的 PostgreSQL 16 临时容器。CI 专用数据库适配器在工作流内安装，不改变应用运行依赖；临时口令运行时生成并掩码，连接配置仅进入 runner 环境。容器按本次运行的名称及标签核对后清理，不使用业务数据库。

`POSTGRES_VALIDATION_REQUIRED=1` 使缺少数据库目标成为失败。`tests/platform_validation_cases.json` 记录需要补齐的原 31 项平台场景；`scripts/check_platform_validation.py` 核对 JUnit，任一目标缺失、跳过、失败、错误或重复都拒绝通过。PostgreSQL 就绪检查使用 TCP，避免把镜像初始化期间的 Unix socket 临时服务误认为已就绪。

Windows 的条件跳过不能作为对应 Linux/PostgreSQL 场景的通过证据。以同一最终提交的 Ubuntu job 实际结果为准；既有真实 RAG/Agent 官方门禁仍保留，其费用授权按项目规则执行。

费用授权缺失时，官方评测授权步骤会在调用 API 前明确失败。只有项目负责人已有明确授权与预算记录后，才应配置 `OFFICIAL_EVALUATION_AUTHORIZED=true` 、`OFFICIAL_EVALUATION_BUDGET_REF` 与精确绑定提交的 `OFFICIAL_EVALUATION_AUTHORIZED_SHA`；本批没有设置它们。平台测试仍先执行，平台结果通过不代表官方门禁或合并条件已通过。

前端开发使用 Node.js 22.13+（22.x）、24.x 或 26+。ESLint 工具链使用 minimatch 11.0.3 的安全匹配实现；在上游依赖声明全面支持安全版本前保留 package.json 中的 override，更新时运行 lint 兼容回归与 npm 审计。React Hooks 检查保持规则调用错误和依赖警告的既有级别。
