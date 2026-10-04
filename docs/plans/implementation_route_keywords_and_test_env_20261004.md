# 实现说明：测试环境去本机化 + 知识库路由关键词放宽

日期：2026-10-04　分支：`fix-ruff-and-route`　基线：`4083e75`

## 完成了什么

### 1. 静态检查自动清理

删除未使用的导入、统一 import 块排序。告警 153 项 → 58 项。

代码位置：44 个文件（集中在 `app/`、`alembic/`、`tests/` 的 import 区块）。

### 2. 测试不再依赖本机 `.env`

代码位置：`tests/conftest.py:26-30`

```python
os.environ.setdefault("AGENT_ORCHESTRATION", "self")
os.environ.setdefault("SECURITY_RATE_LIMIT", "false")
```

环境变量优先级高于 `.env` 文件，因此这两行会把本机为联调临时改的调试值压回去。
此前本机 `.env` 里遗留的 `AGENT_ORCHESTRATION=langgraph` 与
`SECURITY_RATE_LIMIT=true / CAPACITY=1 / RATE=0.01` 会让同一份代码在不同机器上
跑出不同的用例结果：三个依赖完整管线的对话用例失败，一个流式用例无限挂起。

### 3. 知识库路由关键词放宽

代码位置：`app/agents/route.py:16-40`

`_RAG_MARKS` 增加 `来源文件`、`文档里`、`文件里`、`写明`、`手册里`、`制度里`；
`_CODE_MARKS` 增加 `帮我运行`、`帮我执行`、`跑一下代码`、`写段代码`、`运行一下`、`执行一下`。

取舍写在文件头注释里：知识库放宽是因为漏判的代价（回答失去依据）远大于误判
（多一次检索，无果时按「没有可用的检索结果」降级）；工具路径保持收紧，避免
每次请求都多注入工具清单和一轮编排。

## 验证命令与结果

```powershell
python -m ruff check .          # 58 项剩余，均为需人工判断的类别
python -m pytest -q             # 7 failed, 442 passed, 2 skipped
python -m pytest tests/test_fast_route.py tests/test_llm_route.py  # 25 passed
```

关键验证：**全程未设置任何环境变量**，本机 `.env` 仍是 `langgraph` 调试值，
结果即 7 failed —— 测试已不受本机配置影响。

## 明确没做的事

- **没有改路由的兜底分支**。现有测试强制约定兜底为 `SIMPLE`（`test_fast_route.py`
  断言空串与「计算机有哪些部件」为 simple），改成完整管线是两个不同的设计取向，
  需要单独决策，不在本次范围内。
- **没有为了让某个用例通过而放宽到裸「运行」关键词**。「请运行后停住」这类省略
  说法在 langgraph 编排下仍会落回简单路径，这是子串匹配的固有局限，根治要靠
  语义路由，不能靠继续堆关键词掩盖。
- **没有处理剩余 58 项静态检查**（E501 31 / E702 9 / F821 7 等），每一项都需要
  人工判断。其中 `scripts/_gen_remaining_tasks.py` 独占 21 项，该文件是遗留的
  临时生成脚本，是否删除待定。
- **没有处理剩余 7 个真实失败**：`run_rag_baseline.py:712` 的 `args.rerank` 残留
  （2 项）、SSE 多事件循环绑定（4 项，单跑全绿）、删除文档未清理向量（1 项）。
