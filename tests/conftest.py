import sys
import pytest
from unittest.mock import MagicMock, AsyncMock

# Mock out langfuse for testing to avoid module resolution errors natively
sys.modules['langfuse'] = MagicMock()
sys.modules['langfuse.callback'] = MagicMock()
sys.modules['langfuse.decorators'] = MagicMock()

# Re-provide the specific decorators that agents import
mock_decorators = MagicMock()
mock_decorators.observe = lambda *args, **kwargs: (lambda func: func)
mock_decorators.langfuse_context = MagicMock()
sys.modules['langfuse.decorators'] = mock_decorators

# Mock CallbackHandler to be a class that can be instantiated
class MockCallbackHandler:
    def __init__(self, *args, **kwargs):
        pass
    def __getattr__(self, name):
        return MagicMock()

mock_callback = MagicMock()
mock_callback.CallbackHandler = MockCallbackHandler
sys.modules['langfuse.callback'] = mock_callback
