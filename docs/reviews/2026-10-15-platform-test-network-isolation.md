# 测试外发隔离纠正记录

日期：2026-10-15。主 Agent 实际执行与报告；非产品能力验收。

## 已观察事实与影响

SSE 新增路由红测只覆盖了 Mock LLM，遗漏 Embedding override；应用 Settings 继承本机配置，日志实际出现两次 Embedding 批次 usage 与成功请求。随后存量 tests/test_chat_controls.py 的协议回归也观察到至少一次同类调用。输入是合成测试消息，不含真实业务文档；没有输出密钥或完整连接配置。没有获得这些真实调用的费用授权，这是测试隔离失误。

此前基线及 API 全量回归未安装统一外网阻断，且成功用例日志由 pytest 捕获，因此不能证明其全部离线，亦不能给出可信总调用数或费用。原有通过数量和退出码是实际结果，但不能据此声称“无真实外发”。不以 Mock 结果证明真实检索或生成质量。

发现后已告知用户并暂停受影响测试运行，未把意外调用计作两家真实供应商或质量验收。后续测试不继续使用宿主凭据。

## 已落实纠正

- tests/conftest.py 在导入应用前清空生成、意图、fallback、rerank 与 Embedding 密钥的环境覆盖，缺密钥沿既有开发 Mock 路径；不关闭 RAG 或能力、安全、权限检查。
- DotEnvSettingsSource 的仓库本机 .env 读取被阻断；配置契约测试仍可读取自己创建的隔离临时配置，不通过禁用全部配置读取掩盖行为。
- 每项测试自动阻断非 loopback DNS 和 socket 连接，允许本机事件循环和隔离 HTTP fixtures。新增配置不会自动取得外网授权。子进程仍需要各自的明确配置/隔离断言；父进程 fixture 不能冒充子进程网络防火墙。
- 新 SSE fixture 同时设置 Mock LLM 与 Mock Embedding 并恢复覆盖，另有独立网络阻断。CLI init 原有“不导入 Settings/不读工作区配置”的测试边界保留。
- tests/test_offline_network.py 实测三项：密钥未继承；外部 DNS/连接在 I/O 前被拒；loopback 仍可用。真实服务查询、模型费用、生产数据未用于纠正测试。

## 实际验证及证据边界

指定 Python 3.12.0，PYTHONUTF8=1/PYTHONIOENCODING=utf-8，新 basetemp，测试库 data/test_ai_assistant.db。新增隔离后三项护栏及 SSE/会话专项 13 passed，退出 0；进一步覆盖管线等 80 passed，退出 0；修复独立运行边界后 24 passed，退出 0；开发关闭认证兼容修复后运行/授权/隔离专项 20 passed，退出 0。详尽命令见各卡报告与宿主工具记录。这些是阶段性结果，源码/测试随后变化仍要最终重跑。

最终整合回归必须使用纠正后的隔离入口并重新绑定输入；不能沿用纠正前的全量结果关闭最终版本。真实提供商验收需另行明确端点、凭据注入方式、请求内容和费用授权，绝不以此次意外请求替代。

## 独立脚本入口补记（2026-10-16）

主 Agent 检查测试 PNG 的 CRC 时，用普通 Python 导入了 tests.test_chat_multimodal_llm 中的常量；该模块会间接导入应用 Settings，普通 Python 不执行 tests/conftest.py 的隔离，因此不能宣称该脚本没有加载本机 .env。实际输出仅 PNG 的 IHDR/IDAT/IEND 校验布尔值与库弃用警告，没有配置、凭据或业务正文；脚本仅作内存 CRC 计算，没有调用模型/HTTP/数据库初始化。该执行不算模型、视觉或网络隔离验收。

已向用户说明并通知协作 Agent：普通脚本不得为了取 fixture 常量导入应用/测试模块；需要应用导入的独立导出、工具或验收脚本必须先显式屏蔽工作区 dotenv 并清空凭据，或经已隔离的 pytest 入口执行。此前 pytest 护栏继续保留，不能将它冒充所有独立进程的防护。
