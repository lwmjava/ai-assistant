# 单实例生产编排实现说明

日期：2026-10-15。NFR-003 保持 in_review，不是部署或生产就绪结论。

新增 docker-compose.prod.yml：默认仅 app/db，单 worker、命名卷、loopback 应用端口、数据库健康依赖、资源限制和重启策略。应用继承 Dockerfile 唯一健康探针，检查 HTTP 200 且 status=ok。新增 deploy/compose.milvus.prod.yml 显式覆盖：Milvus 2.5.11 与外部 etcd/MinIO、健康依赖、独立命名卷与一致凭据；应用合并后等待 db/milvus。默认 local 不变，不将可选服务计入默认启动路径。

数据库随机口令与 URL 编码后的 DATABASE_URL 分别显式提供；JWT、CORS、模型与 Embedding 配置必填，不提供生产口令或通配跨域回退。Dockerfile 可选 INSTALL_MILVUS 默认 false，开启时安装已有 milvus extra；未声称镜像构建或 extra 安装已验证。.dockerignore 排除环境文件、数据、Git、缓存及本地工具状态，包括执行期间另有来源新增的 .trae（未读取或修改该目录内容）。部署说明位于 docs/workflows/production-compose.md，静态检查脚本 scripts/check_production_compose.py 不访问 Docker 或 .env。

实际检查（Windows 11，指定 Python 3.12.0、Compose v2.40.0）：

- 初始静态红验固定报告两个必交付生产文件缺失，退出 1，无堆栈。
- 文件实现与健康探针统一后，静态契约检查退出 0；独立审查者也实际执行静态检查退出 0。
- 使用全新临时空 env 文件、隔离 Docker 配置目录和运行时合成环境值，执行 base/overlay 的 config --quiet 两项及 config --format json 两项（只内部解析，不输出完整配置），并分别移除 JWT、数据库口令、CORS 验证三项拒绝。七次命令及五输入 SHA256 记录在 ../reviews/production_compose_config_20261015.json；所有断言通过。报告不包含口令、完整连接串或展开配置。
- JSON 断言覆盖 local 仅 app/db、不等 Milvus、overlay 保留 db 并追加 Milvus、extra 开启、带特殊字符口令的编码连接串原样保留、缺必填项拒绝。
- 独立源码审查记录：../reviews/2026-10-15-nfr-003-review.md。后续 API 前缀变更会改变探针输入，最终批次须更新相关检查及指纹，不能拿旧报告关闭新版本。

实际构建、安装、启动、健康、运行时资源限制、数据持久化、Linux 和最终 Ubuntu CI 未验证。已尝试启动本机已安装 Docker Desktop，但 Linux Engine pipe 不存在，未构建或启动任何项目容器。配置展开不代替原卡运行验收；本卡不得 done，NFR-012 不以本卡为已完成前置。原开发 docker-compose.yml 没有在本卡修改，其默认值仍交 NFR-012 处理。

没有进行 PostgreSQL 生产演练、默认向量库切换、多实例外部化、反向代理部署、真实索引迁移、重要数据或卷删除、Git 提交/推送/合并。回滚只换回已验证配置/镜像，不自动回退 schema/索引，不使用 down -v。
