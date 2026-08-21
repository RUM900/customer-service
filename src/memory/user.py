"""
用户持久化 — 账号 CRUD 与默认管理员种子
"""
import logging
from datetime import datetime
from typing import Optional

import bcrypt
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base
from src.models.user import User, UserRole

logger = logging.getLogger(__name__)


class UserRow(Base):
    """用户表"""
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default=UserRole.CUSTOMER.value)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[str] = mapped_column(String(64))
    last_login_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


def hash_password(password: str) -> str:
    """bcrypt 哈希密码（bcrypt 是 bytes 操作）"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


class UserStore:
    """用户存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user: User) -> User:
        row = UserRow(
            user_id=user.user_id,
            username=user.username,
            password_hash=user.password_hash,
            role=user.role.value,
            display_name=user.display_name,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )
        self.db.add(row)
        await self.db.flush()
        return user

    async def get_by_username(self, username: str) -> Optional[User]:
        stmt = select(UserRow).where(UserRow.username == username)
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._row_to_model(row)

    async def update_last_login(self, user_id: str) -> None:
        row = await self.db.get(UserRow, user_id)
        if row:
            row.last_login_at = datetime.now().isoformat()
            await self.db.flush()

    async def has_admin(self) -> bool:
        stmt = select(UserRow).where(UserRow.role == UserRole.ADMIN.value).limit(1)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _row_to_model(row: UserRow) -> User:
        return User(
            user_id=row.user_id,
            username=row.username,
            password_hash=row.password_hash,
            role=UserRole(row.role),
            display_name=row.display_name,
            created_at=row.created_at,
            last_login_at=row.last_login_at,
        )


async def seed_admin() -> None:
    """幂等创建默认管理员账号（仅当数据库中没有任何管理员时）"""
    import config
    from src.memory.database import get_session_factory

    if not config.ADMIN_USERNAME or not config.ADMIN_PASSWORD:
        logger.warning("未配置 ADMIN_USERNAME/ADMIN_PASSWORD，跳过管理员种子")
        return

    factory = get_session_factory()
    async with factory() as db:
        try:
            store = UserStore(db)
            if await store.has_admin():
                return
            user = User(
                username=config.ADMIN_USERNAME,
                password_hash=hash_password(config.ADMIN_PASSWORD),
                role=UserRole.ADMIN,
                display_name="管理员",
            )
            await store.create(user)
            await db.commit()
            logger.info(f"已创建默认管理员账号: {config.ADMIN_USERNAME}")
        except Exception as e:
            await db.rollback()
            logger.error(f"管理员种子失败: {e}")
