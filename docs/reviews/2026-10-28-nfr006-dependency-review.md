# 依赖安全升级与扫描入口独立审查

- 审查者：独立 Agent `/root/nfr006_dependency_review`；未参与实现。
- 日期：2026-10-28；基线：`codex/sand-003` / `5aaa8b63f7fa006d9bd006b93724fb9bfa3d3680` 加未提交候选。
- 范围：前端清单/锁文件、Knowledge 表单类型；Python 清单；共享安全脚本、正式安全测试、本地 hook 与 Security CI。按缺口查看认证解码/refresh、PDF 解析、Supervisor 图调用及相关回归入口。未审其他任务的工作区改动。
- 方式：分块静态审查及隔离 Git 索引反例；未运行全量测试、在线审计、真实模型、生产数据或 Git 发布。实现者正在验证新依赖，旧环境的通过记录不能替代最终候选证据。

## 发现

### P2：文件类型变化可绕过新增密钥扫描（已修复并独立复核）

位置：`scripts/security_checks.py:80`，`--diff-filter=ACMR` 未包含 Git 的 `T` 文件类型变化。

可复现反例：隔离临时仓库以 `git hash-object`、`git update-index --cacheinfo` 建立安全内容的 mode `120000` 链接并提交；随后将同一路径索引替换成 mode `100644` 的合成 AWS Access Key 源文件。普通 `git diff --cached --name-status` 返回 `T credential.py`；当前 `scan_secrets(..., staged=True)` 却返回 `status=passed, files_checked=0, findings=[]`。此反例不依赖 Windows 创建真实符号链接的权限，也未输出合成值。共享过滤条件同时用于提交前和 CI 范围，存在漏检。

修复将过滤条件改为 `ACMRT`，新增 `tests/test_security_type_changes.py` 使用真实 Git 对象/索引覆盖暂存及 CI 范围。实施者红测 `data/validation/20261028T105559-0865074c5da0` 两项失败、退出 1；修复后 `20261028T105707-d661a9c25c10` 两项通过、退出 0。审查者读取正式测试与原始结果，并独立重建同类隔离仓库复核：暂存和已提交范围均返回 `failed`、检查 1 个文件、定位 AWS Access Key，输出不含合成值，复核命令退出 0。未改动或放宽原始断言。

## 其余覆盖与缺口

- JWT 入口继续显式固定算法；API 身份与 refresh 对解码失败安全返回 401，用户/租户/撤销判定未在本次源码中放宽。新 PyJWT 与框架环境的真实专项仍待实现者完成。
- PDF 仍将损坏/加密及解析异常转为固定错误；Supervisor 使用 StateGraph、END、ainvoke 入口。升级后的真实文件、取消和异步回归不能由静态审查替代。
- Zod 数字转换表单区分输入与输出类型合理；已查路由未使用跨多段 splat 相对跳转；清单/锁文件无漏洞忽略或 overrides。运行证据由实现者绑定最终版本。
- Python 审计合并当前运行清单与 dev/langgraph，严格解析传递依赖；网络、输出缺失、skip_reason 和非零审计结果不按通过处理。其他未启用 extras 未纳入，文档已明确。
- hook 读取暂存密钥 blob；CI 逐提交检查并使 npm 审计在 Python 失败后继续执行，未见吞失败配置。远程 required checks/保护及最终同 SHA CI 尚未验证，不能声称已强制保护或可发布。

## 输入指纹（SHA256）

| 文件 | 指纹 |
| --- | --- |
| scripts/security_checks.py | ed1606479edbb4f5cb0de8c0efe8f97c0b45d1250d75b3a0615a0c04cfdd3570 |
| tests/test_security_checks.py | 09c3a82c5c2bff716e6f140620294f0989f304cdd49ee32b5b5456f4d88b6ee6 |
| pyproject.toml | 75fa22f44831b41a0515161d76e31d1925abd35374ac77f4e541b3371048d1fd |
| requirements.txt | bd2903b51f3a2fda7993a5dfd0bb2d27b61eaf8f2a6dff0afb80f4500d1c0cee |
| frontend/package.json | 1e0b6b3120c9dff2e6db060ee2d3ea543c5265cd60491c97a2b97391b0e148d8 |
| frontend/package-lock.json | f5de9385c69df67d6815e635bd74973cfb0e4aca0b1286762eacaa2b05ff5159 |
| frontend/src/pages/Knowledge.tsx | 708da46f43d3d6d0cfdc7b8455b5cb8f6247868b0d9c0a3c787d586ead2894bb |
| .github/workflows/security.yml | 21dbabc6f6bdeb6d6d6fd74f736a42c847ed0d197b5c20a7d6e9e07bde7def35 |
| .pre-commit-config.yaml | e3035735fb9908ca954ff2e5b0d46e49bbce4c22676c6308bb25d31e3664e48d |

上述表格记录首次审查输入。修复复核输入：`scripts/security_checks.py` 为 `8a43921feaf3a5115a1000bac859c8393b6f0c42974d3c33248d6b4ad8e5c2bb`；新增 `tests/test_security_type_changes.py` 为 `8f8013698c412c0f331c2a442d66020c09e5096939b3a61ba670a0cc90335896`；新增 `tests/test_jwt_malformed_claims.py` 最终为 `1acab524f1db3640acf8429948cc8fcf735aab066f85e3e0d67fc21ae6f6d9f8`。Python/前端清单指纹未变。

复核还查看新增 JWT 正式回归：针对 exp/nbf/iat 各自的 null/list/dict 输入，同时要求真实解码抛 PyJWTError、真实 API 返回 401 且不泄露 token；没有关闭签名或时间校验。初次测试误请求未版本化 `/api/auth/me`，实际返回 404，不能作为认证拒绝的有效证据；已更正为 `/api/v1/auth/me`。审查者核对 `app/main.py` 的 `/api/v1` 挂载、auth router 的 `/auth` 前缀与 `/me` 路由，并读取 `data/validation/20261028T110155-92dbff0dc09c` 原始 result/pytest 日志：JWT 与类型变更共 11 项通过，退出 0、status=passed。此前 JWT 文件指纹与失败结果仅属历史，不替代此最终专项。审查者未重跑测试或修改正在全量验证的输入。

读取实施者生成的 `data/validation/nfr006-python-remediation.json`：113 个已解析依赖、0 条漏洞、0 个 skip_reason。此处是对既有真实审计报告的核对，不声称审查者重复运行在线审计，也不替代最终同 SHA CI。

提交前合成 fixture 调整复核：`tests/test_security_checks.py` 5 处、`tests/test_security_type_changes.py` 1 处将分段字符串的 `+` 改为 `"".join((...))`，避免源码中的赋值形式被真实 Secret Keyword hook 识别。审查者用 AST 比较全部 6 个运行时值，并把这 6 处表达式反向替换后重算 SHA256，与前述已审原文件指纹逐一一致：运行时值等价，其他字节（包括断言）未变，未调整扫描配置/检测规则。此次复核仅运行独立字节/表达式核对，未运行或改动全量检查。最终两个测试指纹分别为 `c4e2004651f8802dbb7c7e5a2b38cd862cc048d7130d4102d0746199ca8c5927`、`3060aab931ce4094e2c744e9b5c0a71e63013f4dd7337504bf3a2a305eecc54b`；之前测试指纹及结果属历史，最终完整回归/Linux 必须绑定本次字节版本。

结论：发现的 1 项 P2 已修复并独立复核，当前已审范围无未修复 P1/P2。新依赖完整回归、Linux 与最终 CI 尚待，不作完成接受结论；后续相关源码变化需复核受影响部分。
