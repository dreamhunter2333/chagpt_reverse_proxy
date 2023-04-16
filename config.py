import logging
from pydantic import BaseSettings

_logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s: %(levelname)s: %(name)s: %(message)s",
    level=logging.INFO
)


class Settings(BaseSettings):
    base_url: str = "https://chat.openai.com/"
    user_data_dir: str = "tmp/.playwright"
    browser_server: str = "http://localhost:9222"
    headless: bool = True
    heart_beat: bool = True
    timeout: int = 10000
    navigation_timeout: int = 10000

    class Config:
        env_file = ".env"


settings = Settings()
_logger.info(settings)