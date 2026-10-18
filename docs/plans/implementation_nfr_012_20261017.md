# Compose 默认口令与跨域收紧实现说明

日期：2026-10-17。任务 NFR-012，计划见 plan_cli_compose_batch_20261017.md。前置 NFR-003 的既有隔离启动记录已核对；本卡为 in_review，最终门禁尚待收口。

## 实际完成

docker-compose.yml 的数据库服务与应用连接串不再回退固定数据库口令，CORS_ORIGINS 不再回退 *，均用 ${变量:?提示} 拒绝缺失或空值。JWT_SECRET_KEY 原有必填保留。docker-compose.prod.yml 已满足三项声明，本轮未修改。

.env.example 的 CORS_ORIGINS 改为 localhost:5173 与 localhost:8000，仅用于开发；生产填写实际HTTPS前端来源。生产运行手册、需求第六章已同步默认值要求与检查方法。README 旧 Docker 操作段尚需补数据库口令要求，但不在本卡原 allowed_paths，精确范围扩展已请求，未获批准前保留该文档缺口。

deploy/check_compose_defaults.py 为部署前只读检查入口：默认核对两份编排三项必填声明；--verify-compose 用临时合成env-file检查真实 Compose 展开，未读取工作区.env、未启动容器、不打印展开配置，临时文件在finally中移除。不是密钥强度检查，也不拒绝用户显式填写 *；原卡要求的是没有通配默认值，不能据此宣称已强制拒绝任意通配输入。

## 成功与失败

配置完整时 Compose config 可展开，数据库口令/JWT/来源与合成输入一致；缺失或空值时 Compose 返回非零。静态检查发现不满足必填声明时返回1。文件不可读、Docker不可用、展开失败或注入不一致均失败，不回显底层输出中的秘密。

开发连接串沿用原来的口令插值方式，因此特殊字符口令使用生产编排的独立已URL编码DATABASE_URL；没有借本卡重写数据库配置或修改其他MinIO默认值。

## 验证证据

开发前检查器有效红：docker-compose.yml 的数据库口令与来源两项失败，exit1。修改后 python deploy/check_compose_defaults.py、python scripts/check_production_compose.py 均exit0。

真实配置矩阵：python deploy/check_compose_defaults.py --verify-compose，exit0。两份文件各1个合法配置及3变量×缺失/空值，合计14项通过：合法展开exit0，12个拒绝案例各exit1。Docker28.5.1，无启动容器、无模型调用。Windows解释器与依赖同CLI实现说明；最终Ruff/mypy及输入指纹见批次证据。

独立审查：docs/reviews/2026-10-17-nfr-012-review.md。最终全量pytest退出0，2553 passed / 3 skipped / 3 warnings，547.51秒；Ruff无缓存和mypy均退出0；Linux只读镜像中的静态检查退出0。439个输入指纹未变，见 docs/reviews/2026-10-17-cli-compose-evidence.json；之前旧版本运行不当作新版本通过。未提交最终版本/Ubuntu CI仍未执行，保持in_review。

## 范围与回滚

本卡源码改动只在原allowed_paths；没有额外修改tests/或scripts/。前端及API契约不变，前端四门不适用。不修改真实.env、不启动真实服务、不改变向量库、不做数据迁移。回滚恢复本卡编排、示例与检查入口；不操作现有数据卷。已有数据卷的数据库口令不会因更换环境变量自动旋转，真实部署必须按原有凭据配置，不能当作凭据轮换完成。


2026-10-18 文档收尾：用户已明确批准将 README.md 加入 NFR-012 范围，已登记 allowed_paths，并补齐旧 Docker 操作段的数据库口令、JWT、LLM、CORS 要求。以上历史待授权项现已解除；业务源码、测试及运行配置未变，原439项输入指纹保持，原回归证据按文档补充规则沿用。最终提交Ubuntu CI仍未通过，不关闭两卡。用户本轮明确要求提交全部当前新增及修改文件，本次按保存待验收工作提交，不声明验收完成。
