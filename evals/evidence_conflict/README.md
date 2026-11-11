# 证据冲突验证与有限纠错离线实验

只运行 mock 对照。不要加 `--mode live` 或任何付费参数。

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe evals/evidence_conflict/run.py --mode mock --write
```

报告写到 `evals/reports/evidence-conflict-correction-v0.1.json` 和 `docs/evaluations/evidence-conflict-correction-v0.1.md`。
生产回答链默认保持关闭。
