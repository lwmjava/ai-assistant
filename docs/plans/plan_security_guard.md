# 计划：安全治理（骨架阶段）

## 目标
引入 6 层安全防护，覆盖输入过滤、输出过滤、Prompt 注入检测、日志脱敏、速率限制，满足 PRD §5.8.1 的安全要求。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/security-guard`**
- 遵循 Conventional Commits：`feat(security): 新增安全治理 6 层防护骨架`

## 与 PRD 的对应关系

| 层 | PRD 要求 | 实现状态 |
|----|----------|:--:|
| 输入过滤 | PII 检测 + 敏感词过滤（P0） | ✅ 6 种 PII 模式 + 3 类敏感词 |
| 输出过滤 | 有害内容检测 + 敏感信息遮蔽（P0） | ✅ 4 类有害内容检测 |
| Prompt Injection | 检测恶意注入 + 可选阻断（P1） | ✅ 6 种注入模式 + 置信度评分 |
| 文件安全（校验） | 文件类型/大小校验（P0） | 🔮 RAG 模块上传时校验 |
| 文件安全（病毒扫描） | ClamAV（P1，默认关闭） | 🔮 docker-compose 可选 |
| 日志脱敏 | SensitiveFilter 过滤敏感字段（P0） | ✅ 13 种敏感字段 + 值模式兜底 |
| 速率限制 | Token Bucket 算法（P1） | ✅ 内存级 Token Bucket |

## 1. 架构设计

```
app/security/
├── __init__.py          # 公开 API 导出
├── types.py             # SecurityContext 安全上下文
├── input_filter.py      # 输入过滤：PII 检测 + 敏感词
├── output_filter.py     # 输出过滤：有害内容检测
├── prompt_injection.py  # 注入检测：6 种注入模式
├── log_sanitizer.py     # 日志脱敏：敏感字段遮蔽
└── rate_limiter.py      # 速率限制：Token Bucket

app/services/
└── chat_service.py      # 集成：chat() / chat_stream() 前后安全过滤
```

### 1.1 安全上下文（SecurityContext）

贯穿一次请求的安全状态，各过滤器协同填充：

```
SecurityContext
├── input_flagged / input_reasons / input_pii_detected
├── injection_detected / injection_confidence / injection_reasons
├── output_flagged / output_reasons
├── rate_limited / rate_limit_remaining
└── blocked (计算属性：任一标记为 True 即阻断)
```

### 1.2 输入过滤（InputFilter）

| 检测类型 | 正则模式 | 示例 |
|---------|---------|------|
| 身份证号 | 18 位含校验位 | 110101199001011234 |
| 手机号 | 1[3-9] + 9 位 | 13800138000 |
| 银行卡号 | 16-19 位 | 6222021234567890 |
| 邮箱 | RFC 5322 简化 | test@example.com |
| IP 地址 | IPv4 | 192.168.1.1 |
| API Key | sk-/api-/key- 前缀 | sk-abc123... |
| SQL 注入 | SELECT...FROM, INSERT...INTO 等 | — |
| 命令注入 | rm -rf, /etc/shadow 等 | — |
| XSS | script 标签, onerror 等 | — |

### 1.3 输出过滤（OutputFilter）

| 检测类型 | 说明 |
|---------|------|
| violence | 暴力/自残/武器制作 |
| illegal | 黑客攻击/破解/入侵 |
| pii_leak | 模型输出中包含 PII |
| jailbreak | 越狱/提示词泄露 |

### 1.4 Prompt 注入检测（PromptInjectionDetector）

| 注入类型 | 置信度 | 说明 |
|---------|:--:|------|
| role_hijack | 0.9 | "从现在开始你是..." / "DAN" |
| prompt_theft | 0.85 | "输出你的系统提示词" |
| ignore_instructions | 0.8 | "忽略之前的指令" |
| jailbreak | 0.7 | "不要遵守规则" |
| delimiter_injection | 0.3 | 特殊分隔符破坏 Prompt 结构 |
| multilingual_bypass | 0.2 | 英文 bypass 关键词 |

### 1.5 日志脱敏（LogSanitizer）

**按字段名脱敏**（13 种）：password, passwd, secret, token, api_key, apikey, authorization, auth, credential, private_key, access_key, jwt, bearer

**按值模式脱敏**（兜底）：JWT token 格式、API Key 格式

### 1.6 速率限制（RateLimiter）

Token Bucket 算法，可配置 rate 和 capacity。内存级实现，适合单机部署。

## 2. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 检测方式 | 纯正则匹配 | 无外部依赖，零延迟，5 分钟部署目标 |
| 阻断策略 | 默认仅告警不阻断 | 安全过滤不应影响用户体验，误报成本高 |
| 中文 `\b` 问题 | 移除 `\b` 边界 | Python 中 `\b` 对中文无效 |
| 速率限制存储 | 内存级 | 骨架阶段简化，生产环境可用 Redis |
| 与管线集成 | 在 chat() 前后挂钩 | 最小侵入，一处覆盖所有路径 |

## 3. 集成点

| 模块 | 集成方式 | 状态 |
|------|----------|:--:|
| `app/services/chat_service.py` | chat() 前输入过滤 + 注入检测，后输出过滤 | ✅ |
| `app/core/config.py` | 新增 SECURITY_* 配置项（11 项） | ✅ |
| 审计日志 | 安全告警写入审计日志 | 🔮 内核打磨 |
| RAG 上传 | 文件类型/大小校验 | 🔮 内核打磨 |

## 4. 测试覆盖

| 测试 | 说明 |
|------|------|
| Test 1 | SecurityContext 阻塞逻辑 |
| Test 2 | InputFilter PII 检测（手机号/身份证/邮箱/纯净文本） |
| Test 3 | InputFilter 敏感词检测（SQL 注入/命令注入/XSS） |
| Test 4 | InputFilter + SecurityContext 联动 |
| Test 5 | OutputFilter 有害内容检测（暴力/违法/PII 泄露/纯净） |
| Test 6 | PromptInjectionDetector（角色劫持/忽略指令/纯净/低置信度） |
| Test 7 | PromptInjectionDetector + SecurityContext 联动 |
| Test 8 | LogSanitizer（JSON key-value / key=value / JWT token） |
| Test 9 | RateLimiter（允许/拒绝/禁用模式） |
| Test 10 | InputFilter 禁用选项 |