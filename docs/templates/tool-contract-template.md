# Tool Contract 模板

> 一个 Tool 表达一个原子、可审计的业务能力。Tool 经 Service/Repository 访问业务系统；Agent、Skill、Prompt 不得直接访问数据库或复制核心业务规则。

## 1. 身份与用途

- Name：`<填写全局唯一、稳定、动词开头的名称>`
- Version：`<填写语义版本及兼容策略>`
- Description：`<填写何时调用、完成什么，不写实现细节>`
- Owner：`<填写团队/角色>`
- 调用方：`<填写 Agent/Skill/系统>`
- 实现边界：`<填写 Tool → Service → Repository/外部适配器>`

## 2. Input Schema

```yaml
type: object
additionalProperties: false
required: [<填写必填字段>]
properties:
  <填写字段名>:
    type: <填写 string/integer/number/boolean/object/array>
    description: <填写业务含义、单位、格式、是否敏感>
    constraints: <填写枚举、范围、长度、正则或跨字段校验>
```

- 身份/租户上下文：`<填写由可信执行上下文注入、禁止模型伪造的字段>`
- 幂等键：`<填写 request_id/idempotency_key 的生成、作用域、有效期>`
- 校验顺序：`<结构 → 业务前置条件 → 权限 → 风险/审批>`

## 3. Output Schema

```yaml
type: object
required: [ok, data, error, metadata]
properties:
  ok: { type: boolean }
  data: { <填写成功结果结构；失败时为 null> }
  error:
    <填写 code/message/retryable/details 的稳定结构；成功时为 null>
  metadata:
    <填写 trace_id/request_id/tool_version/duration_ms/cache 等>
```

输出必须结构化、可解析、可追踪；不得返回内部堆栈、连接串、密钥或未脱敏数据。

## 4. 权限（Permission）

- 所需主体：`<填写用户、服务身份、Agent 身份>`
- 所需权限：`<填写动作与资源权限>`
- 租户/资源范围：`<填写过滤与防 IDOR 规则>`
- 授权数据源：`<填写 Policy Engine/ACL/RBAC/ABAC 等>`
- 拒绝行为：`<填写稳定错误码、审计内容、不可泄露信息>`

## 5. 风险与副作用（Side Effect）

- 风险等级：`<L0 只读/L1 低风险写/L2 中风险/L3 高风险>`
- Side Effect：`<none/create/update/delete/external-notification/financial 等>`
- 人工审批：`<填写触发条件、审批凭据、过期和撤销>`
- 幂等策略：`<填写重复请求返回、去重存储、并发锁/版本号>`
- 事务/补偿：`<填写原子性、补偿动作、部分成功表达>`
- Dry-run：`<填写是否支持及输出差异>`

## 6. Timeout、Retry 与限流

- 单次超时：`<填写毫秒/秒及依据>`
- 总预算：`<填写含重试的最大时长>`
- 可重试错误：`<填写错误码与前置条件>`
- 不可重试错误：`<填写>`
- 最大次数与退避：`<填写>`
- 熔断/限流：`<填写维度、阈值和恢复>`
- 取消语义：`<填写客户端取消后副作用如何确认>`

## 7. Error Model

| code | 含义 | retryable | HTTP/传输映射 | 用户可见信息 | 审计 |
|---|---|---|---|---|---|
| `VALIDATION_ERROR` | 输入不合法 | false | `<填写>` | `<填写安全信息>` | `<填写>` |
| `PERMISSION_DENIED` | 无权限 | false | `<填写>` | `<填写>` | `<填写>` |
| `NOT_FOUND` | 资源不存在或不可见 | false | `<填写>` | `<防枚举策略>` | `<填写>` |
| `TIMEOUT` | 执行超时 | true/`<条件>` | `<填写>` | `<填写>` | `<填写>` |
| `UPSTREAM_UNAVAILABLE` | 上游不可用 | true | `<填写>` | `<填写>` | `<填写>` |
| `CONFLICT` | 并发/状态冲突 | false/`<条件>` | `<填写>` | `<填写>` | `<填写>` |
| `UNKNOWN_ERROR` | 未分类错误 | false | `<填写>` | `<统一安全信息>` | `<填写>` |

## 8. Audit 与 Observability

- 审计字段：`<actor/tenant/action/resource/request_id/idempotency_key/approval_id/result/timestamp>`
- Trace/Metric/Log：`<填写关联键、时延、错误、重试、调用量>`
- 脱敏：`<填写禁止记录字段和掩码规则>`
- 告警：`<填写错误率、时延、副作用异常、权限拒绝激增阈值>`

## 9. 示例

### 成功

`<填写不含真实敏感数据的请求与响应示例>`

### 失败

`<填写校验、权限、冲突、超时和上游故障示例>`

## 10. 测试与 Evaluation

- Unit：`<Schema、边界、错误映射、幂等逻辑>`
- Integration：`<Service/Repository/MCP/上游契约>`
- Permission：`<跨租户、越权、资源枚举>`
- Side-effect：`<重复、并发、部分失败、补偿>`
- Agent Evaluation：`<工具选择准确率、参数准确率、不应调用率>`
- 兼容：`<Java/Go/Node/PHP/.NET 调用端契约测试及版本演进>`
