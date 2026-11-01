# 密钥存储交付记录

用户已批准 Fernet、独立环境主密钥、依赖/专用迁移脚本/使用说明扩展，以及独立 worktree 从 develop 开工。分支 codex/nfr-007，基线 3b0e322b0775bcc4e18f03eeb005570e251ebeef；开工干净，原工作区未动。

范围：十个供应商 API Key 的持久配置认证加密与读取解密；数据库目前无 API Key 字段，不新增空凭据表。JWT、Milvus Token、用户口令不在本卡覆盖声明。主密钥 APP_SECRET_ENCRYPTION_KEY 仅从进程环境读取，不得同存 dotenv。环境明文注入兼容，dotenv 非空明文拒绝，环境覆盖不能掩盖文件残留。密文绑定字段名。配置 repr/model_dump 排除 API Key，错误不携带值。

基线：声明 conda Python3.12，隔离 validator targeted：test_llm_profiles_review/test_embeddings/test_security_smoke，退出0，data/validation/20261031T050220-757f04c739a1。认证加密4例退出0；配置入口红灯2例缺失行为后修复，记录050417-d0a20e0c1fd3。配置与调用方专项050604-ad6faea564e9退出0。初始化循环导入曾导致两次收集失败，已将安全模块配置读取延迟至实际调用，不能将收集失败当业务红灯。

实现进行中：迁移命令初稿和回归已加入，尚需完整失败反例、Windows/Linux、静态、独立实现审查和最终CI。未提交推送，不可关闭。回滚仅还原本分支代码；不迁移真实环境，原件保留。

使用：安装锁定依赖 cryptography==50.0.2；通过受控进程环境注入随机Fernet主密钥，不将密钥放命令行、日志或dotenv。执行 python scripts/migrate_api_secrets.py --source <配置副本> --target <不存在的新路径>。验证新配置后由负责人处理旧明文及备份，生成副本不代表真实迁移完成。丢失主密钥无法恢复，保留受控离线备份；旧程序无法直接读取密文，回退前需负责人提供受控原配置。不得自动退回明文。


## 当前候选验证

2026-10-31 Windows，声明 conda Python3.12，cryptography50.0.2/cffi2.1.1/pycparser3.12。最终专项171 passed，1条既有 ast.NameConstant 警告，退出0。记录 data/validation/20261031T050952-0c242159c7ab/result.json、pytest.log、pytest.xml，完整实际命令和非敏感配置保存在result；包含13个模块：secret_storage、llm_profiles_review、embeddings、security_smoke、llm、response_cache、pipeline_budget、auth_revoke、switch_tenant、rag_038_deadline_retry、rate_limit_retry、llm_task_profiles、llm_route。最终 ruff check . --no-cache 退出0；mypy app/ --cache-dir data/validation/nfr007-mypy 退出0（257文件）；git diff --check退出0；pip check退出0。

独立审查：/root/nfr007_design_review，记录 docs/reviews/review_nfr007_20261031.md。未知enc格式被再加密、文件密钥源明文绕过两项P2已修复并复核；原子完整文件非覆盖发布已验证。无未关闭P1/P2。真实凭据/供应商调用未做，不据此声称真实迁移或生产验收。前端无契约或界面变化，不适用前端检查。

Linux未验证：docker images探测失败，Docker Desktop Linux命名管道不存在，未擅自启动宿主服务；需要可用Linux环境验证文件权限/硬链接/失败清理。同最终提交必要Ubuntu CI未运行；未获提交推送授权，当前无最终提交SHA。保持in_review，不宣称完成/可发布。Windows文件测试证明非覆盖发布和加载，不声称已验证所有Windows ACL策略。依赖在线漏洞审计尚未补齐。

主密钥只在运行环境读取，凭据内存中的str兼容保留；不声称防止宿主管理员或进程内存读取。固定前缀及payload版本与字段名阻止误替换。非密钥配置保持原内容；拒绝重复键/插值/多行/畸形语法/主密钥同存/未知enc版本，失败不发布目标。原件保留，需操作者安全处置；迁移工具本身无真实环境操作。

2026-10-31 用户批准将pypdf补丁升级6.21.3→6.21.4及PDF解析回归纳入本次交付，以修复提交hook发现的三条漏洞；不忽略审计。合成凭据扫描误报改为运行时等价构造，受影响回归25passed（055045-34f52b8d0b60），独立增量复核无新增P1/P2。

批准补丁后的验证：PDF/密钥专项51 passed，退出0（data/validation/20261031T083426-75e0f1c581ef）；pip check退出0；在线python审计退出0、0漏洞（data/validation/python-audit.json）。审计期间修复新增secret_storage文件CRLF行尾，pre-commit检测到工作区变化而拒绝提交，未将审计通过误报为提交成功；规范换行后重新完整执行hook。
