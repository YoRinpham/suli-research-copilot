"""Print bounded Qwen endpoint diagnostics without response bodies or credentials."""

from app.config.settings import get_settings
from app.providers.llm import DashScopeLLMProvider
from app.services.scientific_schemas import ClaimBatch


settings = get_settings()
provider = DashScopeLLMProvider(settings.qwen_api_key.get_secret_value() if settings.qwen_api_key else "",
                                "qwen3.8-max", settings.provider_timeout_seconds, 0,
                                base_url=settings.qwen_base_url)
try:
    provider.generate_structured("Return JSON containing one narrow Claim from the source.", ClaimBatch,
                                 {"evidence": [{"id": "synthetic-e1", "source_text": "Method A scored 70% accuracy on Dataset X."}]})
    print("QWEN_ENDPOINT_PROBE=OK")
except Exception as exc:
    cause = exc.__cause__
    response = getattr(cause, "response", None)
    print("QWEN_ENDPOINT_PROBE=" + type(exc).__name__)
    print("CAUSE=" + type(cause).__name__)
    print("HTTP_STATUS=" + str(getattr(response, "status_code", "none")))
    if response is not None:
        try:
            payload = response.json()
            print("PROVIDER_CODE=" + str(payload.get("code", "none"))[:80])
        except Exception:
            print("PROVIDER_CODE=non-json")
