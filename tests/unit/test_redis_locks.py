from unittest.mock import AsyncMock, patch

import pytest

from shared.utils import acquire_distributed_lock


class TestRedisLocks:
    @pytest.mark.asyncio
    @patch('shared.utils.get_redis_client')
    async def test_acquire_lock_success(self, mock_get_redis: AsyncMock) -> None:
        mock_redis = AsyncMock()
        # Mock redis.set to return True (lock acquired)
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        result = await acquire_distributed_lock("lock_key", ttl_seconds=60)

        assert result is True
        mock_redis.set.assert_called_once_with("lock_key", "locked", ex=60, nx=True)
        mock_redis.aclose.assert_called_once()

    @pytest.mark.asyncio
    @patch('shared.utils.get_redis_client')
    async def test_acquire_lock_failure(self, mock_get_redis: AsyncMock) -> None:
        mock_redis = AsyncMock()
        # Mock redis.set to return None/False (lock already exists)
        mock_redis.set.return_value = None
        mock_get_redis.return_value = mock_redis

        result = await acquire_distributed_lock("lock_key", ttl_seconds=60)

        assert result is False
        mock_redis.aclose.assert_called_once()

    @pytest.mark.asyncio
    @patch('shared.utils.get_redis_client')
    async def test_acquire_lock_exception_fail_open(self, mock_get_redis: AsyncMock) -> None:
        mock_redis = AsyncMock()
        # Mock redis to raise ConnectionError
        mock_redis.set.side_effect = Exception("Redis connection failed")
        mock_get_redis.return_value = mock_redis

        # The lock should "fail open" and allow processing to continue
        # (graceful degradation against Redis partition)
        result = await acquire_distributed_lock("lock_key", ttl_seconds=60)

        assert result is True
        mock_redis.aclose.assert_called_once()
