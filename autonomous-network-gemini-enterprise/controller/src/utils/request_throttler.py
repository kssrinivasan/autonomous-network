# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import logging
import time
import os
from typing import Dict, Any, Callable, Optional
from functools import wraps
from collections import deque

logger = logging.getLogger(__name__)

class RequestThrottler:
    """
    Robust request throttler that prevents API rate limit issues.
    Uses token bucket algorithm for rate limiting and semaphore for concurrency control.
    """
    
    def __init__(self):
        # Configuration from environment variables
        self.rate_limit = float(os.getenv("THROTTLE_RATE_LIMIT", "20.0"))  # requests per second
        self.max_concurrent = int(os.getenv("THROTTLE_MAX_CONCURRENT", "20"))
        self.initial_delay = float(os.getenv("THROTTLE_INITIAL_DELAY", "0.5"))
        self.max_delay = float(os.getenv("THROTTLE_MAX_DELAY", "5.0"))
        self.backoff_factor = float(os.getenv("THROTTLE_BACKOFF_FACTOR", "1.5"))
        self.max_retries = int(os.getenv("THROTTLE_MAX_RETRIES", "3"))
        
        # Token bucket for rate limiting
        self.bucket_size = max(1, int(self.rate_limit))  # Allow burst up to 1 second worth
        self.tokens = self.bucket_size
        self.last_refill = time.time()
        self.token_lock = asyncio.Lock()
        
        # Concurrency control
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        
        # Adaptive backoff state
        self.consecutive_failures = 0
        self.last_failure_time = 0
        self.adaptive_delay = 0.0
        
        # Statistics
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.throttled_requests = 0
        
        logger.info(f"RequestThrottler initialized: rate_limit={self.rate_limit} RPS, "
                   f"max_concurrent={self.max_concurrent}, bucket_size={self.bucket_size}")
    
    async def _acquire_token(self) -> None:
        """Acquire a token from the bucket, waiting if necessary."""
        async with self.token_lock:
            now = time.time()
            
            # Refill tokens based on elapsed time
            elapsed = now - self.last_refill
            tokens_to_add = elapsed * self.rate_limit
            self.tokens = min(self.bucket_size, self.tokens + tokens_to_add)
            self.last_refill = now
            
            # If no tokens available, wait for next token
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate_limit
                logger.debug(f"Rate limit reached, waiting {wait_time:.2f}s for token")
                await asyncio.sleep(wait_time)
                
                # Refill after waiting
                now = time.time()
                elapsed = now - self.last_refill
                tokens_to_add = elapsed * self.rate_limit
                self.tokens = min(self.bucket_size, self.tokens + tokens_to_add)
                self.last_refill = now
            
            # Consume one token
            self.tokens -= 1
    
    def _calculate_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        base_delay = self.initial_delay
        delay = base_delay * (self.backoff_factor ** attempt)
        return min(delay, self.max_delay)
    
    def _update_adaptive_delay(self, success: bool) -> None:
        """Update adaptive delay based on recent success/failure patterns."""
        now = time.time()
        
        if not success:
            self.consecutive_failures += 1
            self.last_failure_time = now
            
            # Increase adaptive delay for consecutive failures
            if self.consecutive_failures > 2:
                self.adaptive_delay = min(
                    self.adaptive_delay + (0.5 * self.consecutive_failures),
                    self.max_delay / 4
                )
                logger.warning(f"Consecutive failures: {self.consecutive_failures}, "
                             f"adaptive delay: {self.adaptive_delay:.2f}s")
        else:
            # Success - gradually reduce adaptive delay
            if self.consecutive_failures > 0:
                self.consecutive_failures = max(0, self.consecutive_failures - 1)
                
            # Reduce adaptive delay over time
            if self.adaptive_delay > 0 and now - self.last_failure_time > 10:
                self.adaptive_delay = max(0, self.adaptive_delay - 0.1)
    
    def _is_retryable_error(self, exception: Exception) -> bool:
        """Determine if an exception is retryable."""
        # Handle Kubernetes API exceptions
        if hasattr(exception, 'status'):
            status = exception.status
            # Retry on rate limits and server errors
            return status == 429 or (500 <= status < 600)
        
        # Handle other common retryable exceptions
        error_str = str(exception).lower()
        retryable_patterns = [
            'timeout', 'connection', 'network', 'temporary', 
            'rate limit', 'too many requests', 'service unavailable'
        ]
        
        return any(pattern in error_str for pattern in retryable_patterns)
    
    async def execute_with_throttling(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with comprehensive throttling and retry logic.
        
        Args:
            func: The function to execute
            *args, **kwargs: Arguments to pass to the function
            
        Returns:
            The result of the function call
            
        Raises:
            The last exception if all retries are exhausted
        """
        self.total_requests += 1
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            # Acquire semaphore for each attempt to ensure proper release
            async with self.semaphore:
                try:
                    # Apply rate limiting
                    await self._acquire_token()
                    
                    # Apply adaptive delay if needed
                    if self.adaptive_delay > 0:
                        logger.debug(f"Applying adaptive delay: {self.adaptive_delay:.2f}s")
                        await asyncio.sleep(self.adaptive_delay)
                    
                    # Execute the function
                    start_time = time.time()
                    
                    if asyncio.iscoroutinefunction(func):
                        result = await func(*args, **kwargs)
                    else:
                        result = func(*args, **kwargs)
                    
                    # Success - semaphore will be released automatically
                    execution_time = time.time() - start_time
                    self.successful_requests += 1
                    self._update_adaptive_delay(success=True)
                    
                    logger.debug(f"Request successful in {execution_time:.2f}s "
                               f"(attempt {attempt + 1}/{self.max_retries + 1})")
                    
                    return result
                    
                except Exception as e:
                    # Semaphore will be released automatically here
                    last_exception = e
                    self.failed_requests += 1
                    self._update_adaptive_delay(success=False)
                    
                    # Check if we should retry
                    if attempt < self.max_retries and self._is_retryable_error(e):
                        backoff_delay = self._calculate_backoff_delay(attempt)
                        
                        logger.warning(f"Request failed (attempt {attempt + 1}/{self.max_retries + 1}): {e}. "
                                     f"Retrying in {backoff_delay:.2f}s")
                        
                        # Sleep outside the semaphore context to release it during backoff
                        pass  # Will sleep after semaphore is released
                    else:
                        # Non-retryable error or max retries exceeded
                        if attempt >= self.max_retries:
                            logger.error(f"Max retries ({self.max_retries}) exceeded. Last error: {e}")
                        else:
                            logger.error(f"Non-retryable error: {e}")
                        
                        raise e
            
            # Sleep for backoff outside semaphore context (if we're retrying)
            if attempt < self.max_retries and last_exception and self._is_retryable_error(last_exception):
                backoff_delay = self._calculate_backoff_delay(attempt)
                await asyncio.sleep(backoff_delay)
        
        # All retries exhausted
        if last_exception:
            raise last_exception
        else:
            raise RuntimeError("Unexpected error in throttling logic")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current throttler statistics."""
        success_rate = (self.successful_requests / max(1, self.total_requests)) * 100
        active_requests = self.max_concurrent - self.semaphore._value
        
        # Log warning if semaphore is getting full
        if active_requests >= self.max_concurrent * 0.8:
            logger.warning(f"High semaphore usage: {active_requests}/{self.max_concurrent} active requests")
        
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": f"{success_rate:.1f}%",
            "current_tokens": f"{self.tokens:.2f}",
            "adaptive_delay": f"{self.adaptive_delay:.2f}s",
            "consecutive_failures": self.consecutive_failures,
            "active_requests": active_requests,
            "semaphore_available": self.semaphore._value,
            "semaphore_max": self.max_concurrent,
        }
    
    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.throttled_requests = 0
        logger.info("Throttler statistics reset")

# Global throttler instance
_throttler = RequestThrottler()

def throttled(func: Callable) -> Callable:
    """
    Decorator to add throttling to async functions.
    
    Usage:
        @throttled
        async def my_api_call():
            # This will be throttled
            pass
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await _throttler.execute_with_throttling(func, *args, **kwargs)
    
    return wrapper

def throttled_sync(func: Callable) -> Callable:
    """
    Decorator to add throttling to synchronous functions.
    
    Usage:
        @throttled_sync
        def my_sync_api_call():
            # This will be throttled
            pass
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await _throttler.execute_with_throttling(func, *args, **kwargs)
    
    return wrapper

async def throttled_call(func: Callable, *args, **kwargs) -> Any:
    """
    Manually throttle a function call.
    
    Usage:
        result = await throttled_call(my_function, arg1, arg2, kwarg1=value1)
    """
    return await _throttler.execute_with_throttling(func, *args, **kwargs)

def get_throttler_stats() -> Dict[str, Any]:
    """Get current throttler statistics."""
    return _throttler.get_stats()

def reset_throttler_stats() -> None:
    """Reset throttler statistics."""
    _throttler.reset_stats()

# Backward compatibility
request_throttler = _throttler
