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

```bash
# 1. Fork 并克隆仓库
git clone https://github.com/<你的用户名>/ai-assistant.git
cd ai-assistant

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
2. 基于 `main` 分支创建特性分支：`git checkout -b feat/xxx` 或 `fix/xxx`；
3. 完成开发与自测；
4. 按规范提交代码并推送到你的 Fork；
5. 向 `lwmjava/ai-assistant` 的 `main` 分支发起 **Pull Request**。

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

示例：

```
feat(rag): 支持 Excel 文档混合检索
fix(auth): 修复多租户场景下 token 刷新失败的问题
```

## Pull Request 流程

1. PR 标题遵循提交信息规范，描述中说明 **动机、改动内容与测试方式**；
2. 保持 PR 聚焦单一改动，过大的改动请拆分；
3. 提交前同步官方仓库最新代码：`git fetch upstream && git rebase upstream/main`；
4. 确保 CI 检查（测试、lint）全部通过；
5. 维护者会在合理时间内进行 Review，请按评审意见迭代修改。

## 版本发布

项目遵循 [语义化版本（Semantic Versioning）](https://semver.org/lang/zh-CN/)：

- **MAJOR**：不兼容的 API 变更；
- **MINOR**：向后兼容的功能新增；
- **PATCH**：向后兼容的 Bug 修复。

---

再次感谢你的贡献！🎉 如有疑问，欢迎随时在 Issue 中讨论。
