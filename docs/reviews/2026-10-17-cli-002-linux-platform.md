# CLI-002 三项平台保护的 Linux 证据

日期：2026-10-17。未改 `app/core/bootstrap_config.py` 与两条测试，未读取工作区 `.env`，未提交。

Windows 上这三项会跳过，跳过原因保持不变。本次在 Linux 容器里实跑同一断言。

## 命令与结果

镜像 `python:3.12-slim`，容器内 CPython 3.12.15、pytest 8.3.4。内核 `Linux-6.18.40.1-microsoft-standard-WSL2-x86_64-with-glibc2.41`。只安装 pytest，使用 `--noconftest`，不加载应用、不打开工作区配置。

```text
python -m pytest --noconftest -p no:cacheprovider -p no:asyncio \
  tests/test_cli_init.py::test_init_refuses_symlink_without_touching_target \
  tests/test_cli_init.py::test_init_file_permissions_are_private \
  tests/test_cli_init_review.py::test_write_failure_does_not_delete_replacement_file \
  -v --tb=short
```

3 passed，2 warnings，1.18 秒，退出码 0。两条 warning 是精简环境未安装 pytest-asyncio，因而把 `pyproject.toml` 里的 asyncio 配置项标成未知；没有失败，也没有跳过。

| 用例 | 结果 |
|---|---|
| `test_init_refuses_symlink_without_touching_target` | PASSED。符号链接指向已有文件时拒绝创建，原字节不变，链接仍在 |
| `test_init_file_permissions_are_private` | PASSED。新建文件权限为 `0600` |
| `test_write_failure_does_not_delete_replacement_file` | PASSED。同步失败前路径已被换成另一个 inode 时，替换后的内容保留 |

## 与已有 Ubuntu CI 的同一版本

当前工作区这三个文件的 Git blob 与 `ea7b288cefa44895b3825e4fd20bd5bbe8772546` 相同：

| 文件 | blob |
|---|---|
| `app/core/bootstrap_config.py` | `0c9e56627b4ab53644ed77510d862baed6970192` |
| `tests/test_cli_init.py` | `5c26d2c445687bc1116275e621425b8341a0a966` |
| `tests/test_cli_init_review.py` | `af23cac1d515d6414be499f73281ede428467147` |

该提交的 [Backend](https://github.com/lwmjava/ai-assistant/actions/runs/38994748750/job/117284715271) 已记录 `test_cli_init.py` 16 条全部通过、`test_cli_init_review.py` 3 条全部通过。其后到 `5171e509a39731868c4f67a961c22aabf3c46092` 的 [Backend](https://github.com/lwmjava/ai-assistant/actions/runs/39009767645/job/117329600156) 仍为 success，这三个文件没有新的提交。本次容器结果与那份 Ubuntu 日志核对的是同一实现。

容器内 SHA256：

```text
7e3b01f8fcab744b87f9f44216745443624aa3c10eedb5d932c0086c1c266344  app/core/bootstrap_config.py
847490c0a562a0bce3d7cfca9a798c9fb4bd9090351f88d61f66fec0897a6467  tests/test_cli_init.py
91f5ef51aa634b6cb3c4879bcb72d06e5613bd0d2951ab20d030a0f54f0f3369  tests/test_cli_init_review.py
```

非开发环境拒绝默认密钥、重复 init 拒绝，仍以既有专项和上述全量 pytest 为准。本轮没有重跑全量，因为业务代码和测试输入没有变化。
