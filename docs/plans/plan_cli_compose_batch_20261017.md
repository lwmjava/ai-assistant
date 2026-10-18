# CLI 与 Compose 默认值批次计划

日期：2026-10-17。授权来源：用户要求新建分支，调用 module-batch-delivery，按 CLI-003 → NFR-012 实现。技能及 references/acceptance-gates.md 已读取。

## 基线与授权边界

- 基线 HEAD：6eacc586f7b2b8581095538aadbc78a7cc9e35cc。
- 原分支：codex/platform-batch-20261015；新分支：codex/cli-lifecycle-compose-hardening。
- 已有改动：docs/product/项目产品需求方案.md、docs/workflows/production-compose.md、tasks.yaml，以及未跟踪 docs/reviews/2026-10-17-nfr-003-runtime.md。属于已有 NFR-003 验收成果，保留，不计作本批实现。
- NFR-003 当前卡面 done；隔离 Linux 容器验收记录包含 app/db 启动 13.5 秒、健康 HTTP 200、数据库停止后 unhealthy。仅核验前置，不扩大实施范围。
- 本批授权不包含 push、merge、部署、真实配置/生产数据操作、新模型费用或已有卷删除。
- 各卡分别遵守 allowed_paths，不合并成任意写入权限。

## 调度与终点

CLI-001（已有前置）→ CLI-003 → NFR-012；NFR-003 为 NFR-012 的集合外前置。一次实施一张卡。独立 Agent 先做工程准入审查，最终由非实现者审查源码及契约证据。

流程台账：CLI-003 in_review（本地功能及 Linux smoke 通过，最终 Ubuntu CI 待验收）；NFR-012 in_review（配置矩阵和独立审查通过，README范围及最终CI待齐）。用户已明确批准增加 tests/test_cli_lifecycle.py，后续授权记录以此为准。CLI 的 CI 缺口不作为已完成前置，但 NFR-012 不依赖 CLI-003，按技能局部阻塞规则完成了独立卡的本地实施。最终全量2553通过/3跳过，exit0，439个验证输入指纹未变；批次交付记录见 docs/reviews/2026-10-17-cli-compose-delivery.md。

全部必交付及适用检查通过才可关闭；Linux/最终 Ubuntu CI 或其他必要证据缺失时保留 in_review/blocked，不宣称批次完成。

## CLI-003 实现计划

目标：增加可调用的 start/stop/status/logs，真实启停、读状态和日志，失败原因可读，与 README 一致。

现状：app.cli 仅有 init/migrate/admin；项目已有 Uvicorn 和 Docker Compose，尚无生命周期管理契约。实现前独立工程审查比较复用 Compose 与本机 Uvicorn 进程管理；不引入新框架或依赖，不以 PID 文件加任意 kill 作为安全停止依据。

方案：复用 Docker Compose，不新增后台进程管理器。四命令均强制 --project-name 和 --env-file；--compose-file 默认当前目录 docker-compose.prod.yml，解析为绝对路径，以其父目录作为 project-directory。start 为 up --detach --wait，stop 为 stop，status 为 ps --all，logs 为有限 tail 且无 follow。正整数 timeout/tail，项目名严格校验；无 shell。清除 COMPOSE_* 隐式选择，其他部署环境变量保留 Compose 的常规优先级。操作失败不回显子进程输出/异常正文，输出固定解法；超时/中断不表示 daemon 操作回滚。成功日志按显式配置及当前进程的敏感值和敏感行保守遮蔽，不初始化应用 Settings。

允许范围：沿用原卡 app/、scripts/、README.md、pyproject.toml、docs/、tasks.yaml，追加用户批准的 tests/test_cli_lifecycle.py。

非目标：不重做 migrate/admin，不实现 Kubernetes，不修改真实 .env、生产数据或已有服务。

验收：有效红绿用例；真实隔离生命周期 start→status→logs→stop→status；重复启动、未启动、启动失败、停止失败、超时与错误脱敏；相关原命令回归。执行 conda ai-assistant 的 Ruff/mypy、任务回归；按最终影响补全量 pytest。进程/文件系统行为必须有 Linux 及同一最终提交 Ubuntu Backend CI，缺失不关闭。

回滚：仅回退本卡代码与文档，不清理其他工作或数据。

## NFR-012 实现计划

目标：编排不使用可预测数据库口令，不默认通配跨域，JWT_SECRET_KEY 继续必填。

现状：docker-compose.prod.yml 已要求显式数据库口令、数据库 URL、JWT、CORS；docker-compose.yml 仍有数据库默认口令与 CORS 通配默认值。最终范围由原卡及生产语义约束核对，不顺带修改 Milvus 等其他默认凭据。

方案：对卡范围内编排默认值做最小收紧，更新示例与部署说明；显式缺失、空值和合成合法值分别验证，不读取工作区 .env、不展开输出秘密、不启动真实部署。

具体文件：docker-compose.yml 去掉数据库口令和跨域默认回退；.env.example 用本地显式来源；deploy/check_compose_defaults.py 提供运维只读声明检查，先运行反例再修正配置。docker-compose.prod.yml 已符合必填契约，保持原样。该运维检查器在原卡 deploy/ 范围，不修改 tests/ 或 scripts/。需同步需求第六章及运行手册，说明显式填 * 不是该默认值检查所阻断的行为。

允许范围：docker-compose.yml、docker-compose.prod.yml、deploy/、.env.example、docs/、tasks.yaml。若需修改 tests/ 或 scripts/ 校验程序，先申请精确范围扩展。

独立审查发现 README 旧 Docker 操作段未列数据库口令，需将 README.md 精确追加到本卡范围后同步；已通过确认卡请求用户授权，尚未获答复。不借 CLI-003 的 README 权限合并两卡范围，未批准前保留文档缺口。

非目标：不放宽 JWT 必填、不更换向量库、不迁移数据库、不修改生产凭据。

验收：隔离临时 env-file 下 Compose config --quiet，缺必填值拒绝；合法合成配置通过；非通配 CORS 和数据库口令注入一致；独立审查实际展开结构但不输出秘密。检查输入摘要绑定最终版本。

回滚：仅回退本卡编排与说明，不操作真实数据。

## 证据交付

开发前记录命令、环境、退出码及失败隔离；最终记录源码/测试/配置输入 SHA256、实际命令与结果、独立审查、逐项契约对账、实现说明及任务实际状态。纯前端无改动且无契约影响时说明前端检查不适用，不用文档检查替代业务门禁。


2026-10-18 文档收尾：用户已明确批准将 README.md 加入 NFR-012 范围，已登记 allowed_paths，并补齐旧 Docker 操作段的数据库口令、JWT、LLM、CORS 要求。以上历史待授权项现已解除；业务源码、测试及运行配置未变，原439项输入指纹保持，原回归证据按文档补充规则沿用。最终提交Ubuntu CI仍未通过，不关闭两卡。用户本轮明确要求提交全部当前新增及修改文件，本次按保存待验收工作提交，不声明验收完成。
