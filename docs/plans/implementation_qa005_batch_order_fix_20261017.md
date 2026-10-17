# 批次终态测试排序稳定性修复说明

任务 QA-005；计划 plan_qa005_batch_order_fix_20261017.md。修改 tests/test_rag_039_acceptance.py：保存 done/missing/last 三个ID，同一事务设置固定UTC起点与0/1/2秒偏移，确保首批包含指定缺失项。首批running/2完成/1成功/1失败、末项运行仍running、最终partial_success/3完成/2成功/1失败的原断言全部保留。不改调度器、业务或平台行为，不加sleep/skip；不改用户已有ENV变更。

## 环境与证据

HEAD 7cdfb29ae7aa42b103af7856211be89984e658c7，Windows，指定解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，Python3.12.0、pytest8.4.2、Ruff0.8.4、mypy1.14.1。586项输入指纹与版本在 data/qa005-order-inputs-20261017.json。下文python均指指定解释器。

- 同时间戳/乱序ID故障注入：pytest收集后包装该模块upload，固定created_at相同，ID顺序done=111…、last=222…、missing=333…。旧测试1 failed/退出1；修改后同输入1 passed/退出0。日志 data/qa005-order-{red,green}-20261017.log。只作用于隔离测试进程。
- `python -m pytest tests/test_rag_039_acceptance.py --basetemp=data/qa005-order-file-tmp-20261017 -q`：18 passed，11.22秒，退出0；DATABASE_URL=sqlite:///./data/qa005-order-file-20261017.db、PYTHONUTF8=1。日志 data/qa005-order-file-20261017.log。
- `python -m ruff check .`：退出0，RUFF_CACHE_DIR=data/qa005-order-ruff-20261017；日志 data/qa005-order-ruff-20261017.log。
- `python -m mypy app/`：退出0；日志 data/qa005-order-mypy-20261017.log。
- 全量：`python -m pytest --basetemp=data/qa005-order-final-tmp-20261017 -o cache_dir=data/qa005-order-final-cache-20261017`，DATABASE_URL=sqlite:///./data/qa005-order-final-20261017.db、PYTHONUTF8=1；2530 passed、3 skipped、3 warnings，691.93秒，退出0。日志 data/qa005-order-final-20261017.log。最终586项输入指纹复核无漂移；YAML/任务ID唯一及本任务diff空白检查通过。全工作区diff检查曾报告用户已有README/.env.example尾随空白，本任务未覆盖该改动；最终仅新增说明和登记不改变检查输入。

pytest conftest隔离真实密钥及工作区dotenv，未关闭安全检查。前端无影响，不适用。三个CLI平台用例Windows跳过不能视为通过，新提交Ubuntu CI尚未取得，旧CI不覆盖本次修改。QA-005保持in_review，独立审查及最终提交门禁待完成，不宣称整卡关闭或覆盖率达标。未提交、推送、部署。回滚只恢复该测试修改。
