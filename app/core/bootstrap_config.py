"""独立生成首次启动配置，不加载应用配置或访问数据库。"""

import os
import secrets
from pathlib import Path


def initialize_config(path: str | Path) -> None:
    """独占创建仅含随机 JWT 密钥的配置，写入失败清理本次文件。"""
    destination = Path(path)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    created = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = -1
            output.write("JWT_SECRET_KEY=" + secrets.token_urlsafe(48) + "\n")
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor != -1:
            os.close(descriptor)
        try:
            current = destination.lstat()
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                destination.unlink()
        except OSError:
            pass
        raise
