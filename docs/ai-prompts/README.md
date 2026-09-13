# ai-assistant AI 提示词索引

> 本目录中的提示词是项目任务模板，不是权限或业务规则的实现。使用时必须同时读取根目录 `AGENTS.md` 和相关代码。

## 1. 文件

- `ai-assistant项目实战提示词示例.md`
  - 项目事实审计。
  - 文档对账。
  - RAG Evaluation 设计与实现。
  - Agent、Skill、Workflow 候选数据。
  - 权限 ADR、RAG 单变量实验、ToolExecutor 和独立审查。
- `RAG测评数据集生成与校验提示词.md`
  - 资料盘点。
  - Silver/Adversarial 生成。
  - 独立校验。
  - 人工 Gold。
  - Baseline 和单变量比较。
- `Agent-Skill-Workflow测评数据集生成与校验提示词.md`
  - Agent Action/Tool/状态数据。
  - Skill Preconditions/Workflow/Escalation 数据。
  - Workflow 状态、幂等、恢复和业务结果数据。

## 2. 使用顺序

```text
选择 tasks.yaml 中一个任务
→ 复制对应提示词
→ 替换任务变量
→ AI 先读取 AGENTS.md 和证据
→ 只读输出计划
→ 人工确认
→ 生成 Silver/Adversarial
→ 独立 AI 校验
→ 人工确认 Gold
→ 运行 Evaluation
→ 保存版本和报告
```

不要把生成、校验和最终裁决交给同一个 AI 会话。

## 3. 通用前缀

`<repo-root>` 表示本仓库根目录；提示词中的文件路径均相对该根目录，不要写入本机盘符绝对路径。

任何项目提示词前可附加：

```text
仓库：<repo-root>

开始前必须读取：
1. AGENTS.md；
2. .cursor/rules 中与本任务相关的规则；
3. docs/AI辅助开发迭代指导.md；
4. docs/governance/agent-harness-engineering.md；
5. tasks.yaml 中当前任务；
6. 关联需求、设计、ADR、代码和测试。

先区分代码事实、文档声明、假设、决策和未知。
未经证据不得猜测。需要修改时先输出影响范围、计划、测试、
Evaluation、风险和回滚，得到确认后再实施。
```

## 4. 通用完成约束

```text
完成时必须报告：
- 实际修改文件；
- 实际执行命令和退出结果；
- 测试/Evaluation数据集和版本；
- 基线与候选结果；
- 权限、安全、失败和业务验证；
- 未执行项及原因；
- 残余风险和回滚方式。

不得使用“应该通过”“看起来正常”代替证据。
```

## 5. 数据标签

- Gold：有权负责人确认。
- Silver：AI 根据权威资料生成并独立校验。
- Adversarial：安全、权限、冲突和故障。
- Observed Regression：脱敏真实失败。
- Smoke：完全虚构，只验证代码链路。

AI 生成的数据默认不是 Gold。
