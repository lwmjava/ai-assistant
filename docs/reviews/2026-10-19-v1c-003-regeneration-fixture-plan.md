# 全量回归的存量时间戳夹具隔离

当前状态：用户已批准最小夹具修正；该文件仅按下文补丁修改，第三轮全量2797 passed/3 skipped退出0。以下失败与待批准表述保留为调查历史。V1C-003第二轮全量：2796 passed /1 failed /3 skipped /3 warnings，599.23秒，退出1。失败为 tests/test_chat_retrieval_lifecycle.py::test_regenerate_direct_reply_stays_independent_of_unused_rag。

## 事实与根因

失败临时库（仅合成数据）两消息created_at均为2026-10-19 01:39:14.679349；assistant的随机ID按升序排在user之前。regenerate按created_at,id排序并要求目标为最后一条，因而在进入任何新模型/检索步骤前409拒绝。该方法AST与d886560基线完全相同。

[隔离基线复现脚本](2026-10-19-v1c-003-timestamp-repro.py)复制失败的临时SQLite库，用git show读取d886560的完整ChatService源码并执行其regenerate；导入采用当前依赖，但失败发生在未修改的排序/目标检查分支，未进入任何本卡变更的chat/模型路径。复现仍409、消息仍2条，退出0表示成功复现缺陷，**不是业务通过**。原测试首轮全量通过、第二轮失败，与等时间戳加随机ID的非确定先后吻合。

## 推荐的最小补丁（待批准）

只追加 allowed_paths：`tests/test_chat_retrieval_lifecycle.py`。

```diff
+from datetime import timedelta
 ...
     with Session(engine) as fresh:
         original = fresh.exec(select(Message).where(col(Message.role) == "assistant")).one()
+        question = fresh.exec(select(Message).where(col(Message.role) == "user")).one()
+        original.created_at = question.created_at + timedelta(microseconds=1)
+        fresh.add(original)
+        fresh.commit()
+        assert question.created_at < original.created_at
         original_id = original.id
```

该用例验证重新生成不依赖未使用的RAG工厂，正常前提是先有问题再有尾部回复。修复只稳定这一前提，不改成功/失败预期，不跳过用例，不改原回复、4消息/2助手、空来源或工厂零调用断言。

## 不做的事与残余风险

不在本卡修改业务时间排序、增加消息序号、数据迁移或放宽尾部身份校验。等时间戳排序缺陷仍未修复，保留复现和失败历史，不能声称重新生成功能无此缺口；应另行确认修复范围。若用户不批准路径扩展，保持本卡in_review/待验收，不能把该失败写为通过。

批准后重跑该用例/相邻生命周期、370专项与新输入绑定的无筛选全量；Ruff全库重新执行，mypy按未变更app输入沿用并注明。新复现脚本也纳入证据摘要，避免修改后旧报告冒用。

2026-10-19追加批准：用户明确选择“允许最小修正测试夹具（推荐）”。允许路径增加 `tests/test_chat_retrieval_lifecycle.py`，仅按上述补丁明确消息先后并保留业务断言；业务排序缺陷不变，等待重新验证。
