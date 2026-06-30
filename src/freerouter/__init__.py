"""freerouter — routing limited to OpenRouter's free models.

- As a server: `python -m freerouter` (OpenAI-compatible proxy)
- As a library: `from freerouter import FreeRouterClient`
"""

from .client import FreeRouterClient, FreeRouterError
from .models import FreeModel, ModelRegistry, is_chat_capable, is_free
from .router import FreeRouter

__version__ = "0.1.0"

__all__ = [
    "FreeRouterClient",
    "FreeRouterError",
    "FreeModel",
    "ModelRegistry",
    "FreeRouter",
    "is_free",
    "is_chat_capable",
    "__version__",
]
