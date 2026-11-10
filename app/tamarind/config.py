from pydantic_settings import BaseSettings, SettingsConfigDict

class TamarindSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    tamarind_api_base: str = 'https://app.tamarind.bio/api/'
    tamarind_api_key: str = ''
    tamarind_request_timeout: int = 120
    tamarind_poll_interval: int = 15
    tamarind_max_upload_mb: int = 5120
    tamarind_job_prefix: str = 'molplatform'

settings = TamarindSettings()
