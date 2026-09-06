from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bailian_api_key: str = ""
    bailian_region: str = "cn-beijing"
    bailian_asr_model: str = "qwen3-asr-flash"
    bailian_tts_model: str = "qwen3-tts-flash"
    bailian_tts_voice: str = "Cherry"
    bailian_asr_endpoint: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )
    bailian_asr_encoded_max_bytes: int = 10 * 1024 * 1024
    bailian_tts_endpoint: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_api_endpoint: str = "https://api.deepseek.com/v1/chat/completions"

    amap_api_key: str = ""
    amap_geocode_endpoint: str = "https://restapi.amap.com/v3/geocode/geo"
    amap_poi_search_endpoint: str = "https://restapi.amap.com/v3/place/around"

    backend_host: str = "127.0.0.1"
    backend_port: int = 8003
    frontend_origin: str = "http://localhost:5175"
    cors_allow_origins: list[str] = Field(
        default=["http://localhost:5175", "http://127.0.0.1:5175"]
    )

    max_audio_size_mb: int = 5
    min_audio_duration_sec: int = 1
    max_audio_duration_sec: int = 60
    max_poi_results: int = 3
    default_search_radius_m: int = 2000
    extended_search_radius_m: int = 5000

    timeout_asr: int = 20
    timeout_asr_vendor: int = 15
    timeout_extract: int = 15
    timeout_extract_vendor: int = 12
    deepseek_max_tokens: int = 1024
    timeout_search: int = 30
    timeout_search_geocode: int = 8
    timeout_search_poi: int = 10
    geocode_duplicate_max_m: int = 15
    timeout_finalize: int = 40

    temp_data_ttl_hours: int = 24
    storage_dir: Path = BACKEND_DIR / "storage"


settings = Settings()
