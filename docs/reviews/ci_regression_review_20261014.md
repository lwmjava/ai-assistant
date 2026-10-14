# CI 回归修复独立反例审查

审查者：`/root/ci_fix_review`；只读，未参与实现或修改测试数据。依据 code-review-quality 技能及当前 AGENTS.md，对本卡业务/测试Diff审查；此前 GOV-002治理改动单独保留。

## 初审发现与闭合

初审一项P2：默认后端测试硬编码覆盖native，可能在Settings声明默认值错误变更时仍通过，削弱原默认契约。实施者修复为先读取`type(settings).model_fields["RAG_BACKEND"].default`并断言native，再使用该声明值设置测试实例。独立进程的声明default变异为langchain被断言拒绝；仅实例环境覆盖langchain时仍通过。审查者复核确认P2闭合。

## 最终结论

无剩余有证据的P1/P2。

- persist_identity为显式成功fixture，非autouse；只创建缺失身份，不重启停用租户，不关闭权限复核；清理仅该fixture新建身份行，已有租户配额保持。原权限、拒绝、配额、落库断言保留。
- 缓存取消测试保留两次真实stream回答的约束，分别验证一次有界非流式技能选择及第三次缓存命中，没有只增加总调用次数或关闭技能。
- Linux Z/X判定符合“仍在执行”的语义；未知/不可读状态、权限不足保守视为存活。解析进程名括号，不改Windows Job Object路径。有限等待仅在测试用terminate_contained_tree，未扩大正式沙箱权限。
- 23项Linux独立标准库检查绑定实际proc源，只证明这条Linux路径，不代替最终SHA的Ubuntu Backend CI。

实施者重启最终全量到data/ci-fix-final2-20261014.log与独立测试库；修改前的final运行证据失效，不用于最终验收。最终全量及同最终提交Ubuntu Backend CI在审查结论时尚待完成；QA-005不能据此关闭，也不能宣称可发布。审查结果与最终命令/输入指纹由实现说明和ci_regression_20261014.json汇总。

## 最终证据独立复核

同一审查者只读核对最终ci_regression_20261014.json、XML及当前源树：422个输入归一化SHA256全部一致，XML哈希匹配，2292passed/0failed/0skipped；QA-005仍in_review。未发现验证后源码变化或把本机通过写成远程通过，无剩余有证据P1/P2。此次未改文件、未执行pytest。
