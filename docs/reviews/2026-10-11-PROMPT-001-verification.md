# PROMPT-001 独立验证记录

日期：2026-10-11。独立验证身份：`/root/prompt_verification`，未参与业务实现。依据原始 `tasks.yaml` 契约、AGENTS.md、task-card-delivery 技能、落盘计划和直接 CLI 入口构造反例；未用实现者 helper 自检作为必交付项证据。

## 验证边界

入口是 `python app/agents/version_registry.py` 的 `snapshot`、`check` 和 `export-config`。每次调用启动全新 Python 进程，工作目录放在隔离源树之外。测试源树包含从当前仓库复制的真实配置定义与合成 Prompt 源；登记和配置通过真实磁盘文件保存，后续由新进程重新读取。合成源、配置和端点不涉及真实用户、数据库、收费模型或生产环境。

通过 Python 审计钩子监测 `.env` 文件打开行为并留失败标记；临时源树含合成 `.env` 陷阱，子进程另有与登记不同的环境模型值。所有调用都未触发 `.env` 读取，默认值来自源码，环境覆盖未改变源码默认登记。

证据为 **E2 隔离集成**：真实 CLI、新进程、真实文件与合成配置；不属于真实生成质量、生产启用、生产秘密管理恢复或人工业务判断证据。

## 反例和结果

| 契约 / 失败边界 | 实际操作 | 可观察结果 |
|---|---|---|
| Prompt 来源及版本可追踪 | 新进程生成版本记录，写入登记，再从另一工作目录运行 check 两次 | 版本及指纹相同，登记字节未被改写 |
| 修改 / 新增 / 删除 Prompt 源 | 修改原文件、删除原文件、分别在 memory/evolution/rag 新增源文件；check 失败后恢复旧源 | 每次变更均失败；恢复后新进程校验通过，登记未变 |
| 跨平台换行 | LF 与 CRLF 等价替换 | 源指纹仍一致 |
| 模型配置版本与回滚 | 分别改变模型、provider、兜底路由、温度、端点；生成新版本，导出旧配置，重新读取旧配置 | 新版本模型指纹不同；变更校验失败；恢复旧快照后模型指纹回到原值 |
| 秘密端点回滚绑定 | 合成含账号、密码、查询秘密的端点登记；导出非敏感快照；改变模型，再把原端点与旧字段重组后恢复 | 改变时失败；恢复后通过；所有阶段 stdout/stderr 不含合成秘密 |
| 非法数值与配置 | NaN、Infinity、溢出数字、bool 代替整数、负 token、零 timeout、拼错字段、数组、重复 JSON 字段 | 退出码 1、无 traceback、无输出配置内容，登记未改写 |
| 损坏登记 | 坏 JSON、坏时间、仅日期 UTC 伪时间、重复版本、未知模型字段、路径穿越、current 缺失 | 退出码 1、无 traceback、登记未改写 |
| 幂等与失败后恢复 | 所有失败后重新读取原登记；恢复源和模型后再次 check | 只读操作无副作用，旧登记与旧版本可以继续校验 |

正式回归文件：`docs/ai-prompts/tests/test_prompt_version_registry_independent.py`，29 条独立参数化用例。没有跳过用例。

## 实际命令与结果

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest docs/ai-prompts/tests/test_prompt_version_registry_independent.py -q --basetemp=data/prompt001-independent-20261011-v6
# 29 passed in 16.11s；退出码 0；最终冻结代码重验
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m ruff check --no-cache docs/ai-prompts/tests/test_prompt_version_registry_independent.py
# All checks passed!；退出码 0
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' app/agents/version_registry.py check
# status=ok；version=prompt-model-v1；config_basis=source-defaults；退出码 0
```

当前仓库登记校验的 source_version：`32d7a3a3fa2ff976d6c23b5d23c46d4472c78d97c7ad1525ccf1474b38068192`；model_version：`eef69460f8a0996a9793ec2349e3f8ba66a4919d35b31161f45fe876735efaf8`。这里只证明源码默认登记，未读取部署配置。

初次独立运行 24 passed / 1 failed，原因为验证测试在 Windows 已为 CRLF 的文件上直接将 LF 替换成 CRLF，制造了 CRCRLF；修正为先规范化后转换后，25 passed，再补反例得到 29 passed。首次本文件 Ruff 因缓存目录权限失败；使用 `--no-cache` 暴露 6 条 E501，修正本文件格式后通过。没有降低断言或删除失败用例。

## 文件指纹

- `app/agents/version_registry.py` SHA-256：`BC70BFA04DCA6E7453FE30940ECD021EEDE9EDC293648F6E652D0ADA06CD081C`。
- `docs/ai-prompts/prompt-model-registry.json` SHA-256：`19FDE5CDB111935818F2DFA4D1D9C7608117F4482B1B0BFD352395577A9C261C`。
- 独立测试文件 SHA-256：`6662F88B5D6D9BBF8F71BA7AD4EAEA852CF4868884A34242048E30FB798C2D3B`。

最终源变化会使本记录失效，主 Agent 应以最终指纹复核整合。

## 结论与未验证项

以上入口级隔离验证通过，无未修复业务缺陷从本组反例暴露。独立审查、完整后端回归、全仓 Ruff/mypy 由主 Agent 分别整合，此报告不替代那些记录。未执行生产回滚、外网请求、真实 LLM 回答评估、真实秘密管理恢复；没有修改提示词生成行为或前端，因此页面验收不适用。人工业务判断没有被本次用户指令明确委托给 Agent，本报告不宣布整卡 done。未提交、推送、合并或部署。
