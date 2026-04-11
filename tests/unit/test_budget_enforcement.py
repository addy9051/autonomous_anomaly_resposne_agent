import pytest
from unittest.mock import patch, AsyncMock
from shared.utils import LLMCostTracker, BudgetExceededError

class TestBudgetEnforcement:
    def test_cost_tracker_within_budget(self) -> None:
        tracker = LLMCostTracker(incident_id="INC-100", max_tokens=1000)
        
        # Track 500 tokens
        tracker.track(model="gpt-4o", input_tokens=400, output_tokens=100)
        assert tracker.total_tokens == 500
        assert tracker.budget_remaining == 500
        assert tracker.budget_exceeded is False

    def test_cost_tracker_exceeds_budget(self) -> None:
        tracker = LLMCostTracker(incident_id="INC-100", max_tokens=1000)
        
        # Track 800 tokens
        tracker.track(model="gpt-4o", input_tokens=600, output_tokens=200)
        
        # The next call pushes it over the 1000 limit
        with pytest.raises(BudgetExceededError) as exc_info:
            tracker.track(model="gpt-4o", input_tokens=300, output_tokens=100)
            
        assert "exceeded token budget" in str(exc_info.value)
        assert tracker.total_tokens == 1200
        assert tracker.budget_remaining == 0
        assert tracker.budget_exceeded is True
