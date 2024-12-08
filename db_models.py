from typing import Optional, cast

from sqlalchemy import JSON, VARCHAR, Column, select
from sqlalchemy.ext.asyncio import async_scoped_session

from db_base import Base


class Chat(Base):
    __tablename__ = "chats"

    id = Column(VARCHAR, primary_key=True, index=True)
    chat = Column(JSON)


async def get_chat(chat_id: str, db: async_scoped_session) -> Optional[Chat]:
    results_raw = await db.execute(select(Chat).filter(Chat.id == chat_id))
    return results_raw.scalars().one_or_none()


async def store_chat(
    chat_id: str, chat: list[dict], db: async_scoped_session
) -> None:
    existing_chat = await get_chat(chat_id, db)
    if existing_chat:
        existing_chat.chat = cast(Column, chat)
    else:
        new_chat = Chat(id=chat_id, chat=chat)
        db.add(new_chat)
    await db.commit()
