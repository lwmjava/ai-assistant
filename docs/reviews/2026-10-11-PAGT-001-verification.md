# PAGT-001 独立入口验证

执行身份：`/root/pagt001_verify`，未参与主要实现；2026-10-11。
依据：原 tasks.yaml 契约、用户仅国内搜索服务/模型指令、已批准实施计划；使用 task-card-delivery 1.2.0 的 E2 与失败边界门禁。

## 范围与证据性质

正式测试在 `tests/test_pagt001_integration.py`。四个国内服务通过 httpx MockTransport 合成协议响应；真实 default_tools、ToolRegistry、AgentPipeline 和 `/api/chat`/ChatService 均未替换。对话模型为本地确定性脚本，仅作为提出动作与引用观测的驱动器。实际 HTTP API、测试 SQLite、新请求消息回读、实际磁盘文件均运行。本次属于 E2 隔离集成；不是收费模型 E1，不证明真实搜索质量、账户资格或服务可用性。未读取 `.env` 或真实凭据，未发收费请求。

## 执行记录

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。共享测试数据库经主 Agent 协调串行窗口；每次使用独立 basetemp。

1. 初始主要实现缺行为的 Red 已由实现 Agent 留证；验证开始时 builtin 已引用尚未落地模块，因此未将中间态 importerror 当业务 Red。
2. `python -m pytest tests/test_pagt001_integration.py -q --basetemp=data/pagt001-verify-20261011-a`：27 passed，2.47s，退出码0。
3. 追加安全反例后 `python -m pytest tests/test_pagt001_integration.py -q -k registry_does_not_echo --basetemp=data/pagt001-verify-20261011-leak-red`：1 failed、27 deselected，2.08s，退出码1。属于有效业务失败，见下方缺陷。
4. Ruff 首次创建 `.ruff_cache` 临时文件遇 Windows拒绝访问，环境失败；使用 `--no-cache` 检查，不降低检查范围。末轮结果见第6条。
5. 修复后 `python -m pytest tests/test_pagt001_integration.py -q --basetemp=data/pagt001-verify-20261011-b`：30 passed，2.18s，退出码0。包括原27项、入口参数脱敏回归，以及两个新工具的意外异常脱敏回归。
6. `python -m ruff check --no-cache tests/test_pagt001_integration.py`：All checks passed，退出码0。
7. 后续 F03 深层 JSON 安全拒绝修复、百度可选参数删除、health启动校验日志与base原换行恢复后，独立执行 `python -m pytest tests/test_pagt001_integration.py -q --basetemp=data/pagt001-verify-20261011-final`：30 passed、0 skipped，2.02s，退出码0；本测试 Ruff --no-cache 再次退出码0。主 Agent另执行扩大200项回归通过；该200项结论归属主 Agent证据，不计为本验证 Agent执行。

## 原始验收对账

| 要求/边界 | 实际链与反例 | 初轮结果 |
|---|---|---|
| 四国内 provider 可插拔、有来源 | 分别配置合成 key，实际工具循环发单次固定国内主机POST；真实 `/api/chat` 回复带来源，重新GET会话验证持久化 | E2通过 |
| 无 key 启动与health可见 | TestClient实际lifespan启动，health HTTP200，搜索和文件 unavailable，旧四工具仍注册 | E2通过 |
| file_ops受共享根限制 | 实际临时目录读公开文本，越界/未授权root/`.env`/伪造tenant/写动作被拒绝且文件不变 | E2通过 |
| 不存在写接口、兼容/回滚 | 未注册写能力，额外action无效；关闭配置恢复四工具，calculator仍42 | E2通过 |
| 数值 | NaN max_bytes被拒绝，不产生文件变更 | E2通过 |
| 失败 | timeout、429、500、非法JSON、响应超限安全返回，旧工具仍可用；取消传播且单次请求无重试 | E2通过 |
| 身份绑定 | 国外provider/model不注册；file/localIP/含凭据/javascript来源不返回 | E2通过 |
| 入口脱敏 | Registry隐藏拒绝输入中的合成凭据及主机绝对路径；两个新工具意外异常中的凭据/路径不进入观测或日志 | Red失败后已修复，E2回归通过 |

## 发现与修复计划

P2：ToolRegistry.run 在安全 helper 返回拒绝结果后仍原样回显调用参数，导致新增 file_ops 的被拒绝未知 `api_key` 和主机绝对路径进入 Agent 观测。正式测试 `test_tool_registry_does_not_echo_rejected_sensitive_arguments` 实际复现；合成值仅用于测试，不是真实泄漏事件。该缺陷阻断本卡关闭。

修复由主 Agent负责并已完成：对新增 web_search/file_ops 的注册表观测隐藏原始调用参数，意外异常仅记录类型、返回固定错误，维持既有四工具接口及行为。正式反例与集成30项通过；全量既有回归由主 Agent最后串行执行。本次没有修改原工具错误治理或引入新架构。

Red指纹：base.py `086AA417B502229A108698772240E0804ACE45AFE3AABB94EAD395B0B3B2539C`；web_search.py `458085F49B6B851D0401C92DDB6A3DB1E3D2934641D8C64CD006363D096FE70B`；file_ops.py `9E4FD1838F2B17B4DCFB17A46E1A814FB4CD306DBE49217FEA3D11667575BFA6`；本测试 `0049B2F9DA12C796143AE2E8AADEA50E0F4BE8B728E404A45A15CCBB835F1468`。

首次修复复验历史指纹：base.py `4C0844A03AC52DE77E701C7AC93E260FA7712A68A5F8F7A61E642DF462620E3E`；web_search.py `81DAF641D5CACD8FCD9E245986555ED8F6F7FD45D43347FE0BD1F148F499D086`（包含Accept-Encoding identity修复）；file_ops.py `9E4FD1838F2B17B4DCFB17A46E1A814FB4CD306DBE49217FEA3D11667575BFA6`；本测试 `1C25EC5B7FAF9EF333DCB729D097665B6F2DDAF7D42375714BDBC978CBCE797C`。

最终第7条复验源码指纹：base.py `D295DAB0948135AC4BAF6FF4B57EC1ED370FC79BEFEC5058E0BD640CC1F93B5B`；web_search.py `488C36ECA6833CFAF63787AF5D82C17FBFD7F5E041A6E587181756C7EAB849A2`；file_ops.py `39FE09963324F8D6BD35F8CC7AC9A0784AB773BF6172E14DB0C6EA1750BA301D`；health.py `597A4A70B7EB115BE60BD351E32D19987C0CE309ED41C295F3F571D224DC78BF`；测试仍为 `1C25EC5B7FAF9EF333DCB729D097665B6F2DDAF7D42375714BDBC978CBCE797C`。

## 当前结论

主要原始功能及发现的入口脱敏 P2 已有最终代码 E2 通过证据。本报告范围未发现仍未修复缺陷；其他独立审查结论与全后端回归由主 Agent汇总。本报告不授权 done，不替代人工业务验收。不适用前端页面截图：本卡没有前端交互修改。
