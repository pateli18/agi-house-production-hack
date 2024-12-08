import logging

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    botmailroom_webhook_secret: str
    botmailroom_api_key: str
    openai_api_key: str
    exa_api_key: str
    max_response_cycles: int = 10
    database_url: str = "sqlite+aiosqlite:///./sql_app.db"


settings = Settings()  # type: ignore


def setup_logging():
    logging.basicConfig(
        format="%(asctime)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger().setLevel(
        level=logging.INFO,
    )
