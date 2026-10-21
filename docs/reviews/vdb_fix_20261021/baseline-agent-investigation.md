# 全量pytest慢速与编码失败调查

角色：独立验证子Agent `/root/baseline_verify`，未修改业务源码、测试或配置。
基线HEAD：`c2fc1bf3e1f02327a6972539a0905371d5f4acef`；分支 `milvus-default-vector-store`；存在主Agent记录的未提交VDB实现。本报告是修复前/过程调查，不是最终候选验收。

## 已确认

1. 先前`-q`长时间显示2%不足以证明死锁。本轮无筛选`-vv`全量收集3259条，在2%位置实际执行Agent gate CLI子进程探针，随后正常推进。`faulthandler_timeout=60`未在该处触发。
2. 首批门禁失败属于Windows字符编码不一致。命令只设置`PYTHONIOENCODING=utf-8`，子Python输出UTF-8；父Python的`subprocess.run(text=True)`仍按GBK解码，reader线程抛`UnicodeDecodeError`，`result.stdout`为None，导致业务断言读取输出失败。不是RAG门禁实际将正常报告拒绝。
3. 单条反例`tests/eval/test_rag_gate.py::test_gate_passes_on_real_report`：子进程returncode=0，但stdout读取断言失败，1 failed / exit1，见`baseline-agent-rag-probe.log`。
4. 仅增加非敏感环境`PYTHONUTF8=1`，同条和`test_independent_runner_missing_real_identity_creates_no_report`都通过，2 passed / exit0 / 6.31s，见`baseline-agent-utf8-probe.log`。
5. 同环境重跑完整`tests/eval/test_rag_gate.py`，80 passed / exit0 / 37.64s，见`baseline-agent-utf8-rag.log`。未跳过、改写或关闭任何断言。

## 命令与隔离

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`；Python3.12.0 / pytest8.4.2 / Windows11。开始时只有既有Python PID15348，未处理不属于本轮的进程。

```powershell
$env:DATABASE_URL='sqlite:///./data/vdb-baseline-agent-20261021.db'
$env:PYTHONIOENCODING='utf-8'
& D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest -vv -o faulthandler_timeout=60 --basetemp data/t/vdb_baseline_agent_20261021
```

完整输出：`baseline-agent-full.log`。按主Agent要求在28%正常推进时中断本轮过程基线，工具会话退出1，没有pytest最终汇总；未完成，不能用本调查关闭任务。原2%慢速与编码失败已取得足够调查证据，不把中断后的会话退出码当作完整断言统计。

编码复核使用各自独立库和basetemp，第三项额外`PYTHONUTF8=1`：

```text
python -m pytest tests/eval/test_rag_gate.py::test_gate_passes_on_real_report -vv --basetemp data/t/vdb_baseline_probe_20261021
python -m pytest tests/eval/test_rag_gate.py::test_gate_passes_on_real_report tests/eval/test_agent_gate_entry.py::test_independent_runner_missing_real_identity_creates_no_report -vv --basetemp data/t/vdb_baseline_utf8_probe_20261021
python -m pytest tests/eval/test_rag_gate.py -q --basetemp data/t/vdb_baseline_utf8_rag_20261021
```

独立DATABASE_URL分别为`sqlite:///./data/vdb-baseline-probe-20261021.db`、`sqlite:///./data/vdb-baseline-utf8-probe-20261021.db`、`sqlite:///./data/vdb-baseline-utf8-rag-20261021.db`。沿用conftest的`.env`/模型凭据隔离及外发禁止，没有付费调用或重要数据写入。

## 最终回归建议

最终全量Windows回归显式设置`PYTHONUTF8=1`和`PYTHONIOENCODING=utf-8`，记录环境；无需为编码失败降低门禁或修改业务。主Agent并行修复VDB模块，当前过程运行不绑定最终源码，最终仍须重新完整执行。

曾按主Agent通知启动候选无筛选全量，见`final-agent-full.log`，独立库`sqlite:///./data/vdb-final-agent-20261021.db`、basetemp`data/t/vdb_final_agent_20261021`，命令仍为`python -m pytest -vv -o faulthandler_timeout=60`，同时设置上述两个UTF-8环境变量。启动时740项输入指纹见`final-agent-inputs.sha256.json`。运行期间真实服务验证与独立审查产生源码修复，使输入漂移；按主Agent要求停止此过程运行，工具会话退出1，无pytest最终统计。它只能证明已执行前缀没有重现编码失败，不能当作最终全量通过。等待独立审查修复后再次冻结再执行。

第二次冻结候选运行使用`final-frozen-pytest.log`与`final-frozen-inputs.sha256.json`（740项），命令`python -m pytest -q -o faulthandler_timeout=60 --basetemp data/t/vdb_final_frozen_20261021`，独立库`sqlite:///./data/vdb-final-frozen-20261021.db`，同时两个UTF-8变量。运行到后半段仍没有F/E或超时栈；主Agent随后通知独立审核发现未知backend冒充local的P2并将修改输入，要求中断。工具会话退出1，无pytest最终汇总，仍不是完整全量通过。最终检查必须等该审核修复冻结后重新执行。
