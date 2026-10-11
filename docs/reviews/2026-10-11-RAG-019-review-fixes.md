# 独立审查缺陷与修复计划

审查身份：/root/cache_plan_review，未参与业务实现；原始依据 tasks.yaml RAG-019。

## F1：flush 后未提交写入仍可能命中旧缓存（P2）

现状：快照只检查 Session.new/dirty/deleted。flush 后集合变空但事务未提交；新 Session 的已提交指纹仍是旧值。检索入口可能重放旧负缓存，掩盖本事务新发布文档。不是凭测试数量可关闭的缺口。

修复顺序：独立 Agent 在 RAGService.search 入口建立“先缓存空结果，新增/发布并 flush，不 commit，再检索”正式反例并执行失败；随后为 SQLModel Session 记录事务内写入（flush、显式 DML）。只有最外层事务结束才清理标记，嵌套事务结束不能丢失外层写入证据；有写入标记就绕过缓存。补回归后重跑缓存、生命周期及类型检查；独立 Agent 复验最终代码和文件指纹。连接级绕过 ORM 写入亦需核对，不能仅宣称 ORM 路径可用。

## 其他最终边界修补

缓存开关在流式重放中变化时停止并拒绝，不在已输出内容后重新请求从头重复输出；极大运行时整数字节预算在 math.isfinite 转换前拒绝；生成器每步绑定作用域，向调用方 yield 前解除绑定，finally 主动关闭内层生成器。验证 Agent 对受影响真实 HTTP 流式路径重验并更新指纹。

## F2：不透明后端及实际依赖与名义依赖不一致（P2）

独立入口反例（两项 exit 1）：自定义可变后端关闭 enabled 后仍命中旧内容；Native 后端实际使用另外的可变 Embedding，Service 名义 Embedding 不变，实际改为零向量后仍命中旧结果。实例 namespace 不能证明实例内部配置未变化；名义依赖键不能代表实际调用。

修复：只为登记的 Native/LangChain/LlamaIndex 后端缓存；实际后端的 Embedding、Store 和 Tokenizer 必须与 Service 提供的对象一致，否则绕过缓存走原后端。自定义后端保留可用性但不宣称缓存支持。独立 Agent 重跑两项红测及全套入口反例；主 Agent 重跑缓存验收与相关回归、完整门禁并更新最终指纹。

## F3：异步期间参数变化导致缓存键与实际 HTTP 请求不一致（P2）

最终检查发现 Provider 在 await 建立客户端之前构造缓存键，而在之后再次根据可变 messages/options/provider属性构造HTTP请求。输入在等待中被修改时，可能将实际新上下文的响应存到原上下文键下。此为真实上下文绑定缺口，需要修复，不以已有全绿关闭。

独立验证 Agent 构造 localhost HTTP 回显实际请求内容、AsyncClient连接等待时修改调用者参数的反例，先执行 RED。最小修复将有效 options复制并在首次await之前冻结实际payload、headers、url、timeout和期望模型，键与发送共用该快照，完成判定绑定快照模型。chat/stream均覆盖；重跑受影响HTTP验收、LLM/Chat回归和静态门禁，更新独立报告及最终指纹。

独立验证和代码审查最终均将F3定为P2：证明进程内可变对象引起实际上下文错配，未证明跨租户越权；仍须修复并独立复验后交付。有效RED为chat/stream两项失败，日志data/rag019-independent-request-red2.log。

最终结果：F1/F2/F3均修复并独立复验；F3完整36项E2通过，审查再次复验2项通过；最终65项缓存门禁、全仓Ruff与194源文件mypy通过（退出码均0），最终指纹及详细日志见实现说明和独立报告。
