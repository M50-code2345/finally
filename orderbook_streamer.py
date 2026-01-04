"""
Orderbook Data Streamer
========================

Standalone module for streaming orderbook data from Binance using WebSocket and REST APIs.
Provides a clean, reusable interface for real-time market data ingestion.

Features:
- WebSocket streaming for real-time depth updates
- REST API polling for full orderbook snapshots
- Automatic reconnection with exponential backoff
- Data validation and quality checks
- Configurable buffer management
- Event-driven architecture

Author: Advanced Order Flow Analysis System
Date: 2026-01-04
"""

import asyncio
import aiohttp
import websockets
import json
import time
from datetime import datetime, timezone
from collections import deque
from typing import Dict, List, Tuple, Optional, Callable, Any
from dataclasses import dataclass, field

# Optional: Retry library for robust REST calls
try:
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type
    )
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    print("⚠️  tenacity not available - install with: pip install tenacity")
    
    # Fallback: no-op decorator
    def retry(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


@dataclass
class OrderbookSnapshot:
    """Represents a complete orderbook snapshot"""
    timestamp: float
    symbol: str
    bids: List[Tuple[float, float]]  # [(price, quantity), ...]
    asks: List[Tuple[float, float]]  # [(price, quantity), ...]
    last_update_id: Optional[int] = None
    source: str = "websocket"  # "websocket" or "rest"
    
    def __post_init__(self):
        """Validate and sort orderbook data"""
        # Sort bids descending (highest price first)
        self.bids = sorted(self.bids, key=lambda x: x[0], reverse=True)
        # Sort asks ascending (lowest price first)
        self.asks = sorted(self.asks, key=lambda x: x[0])
    
    @property
    def best_bid(self) -> Optional[float]:
        """Get best bid price"""
        return self.bids[0][0] if self.bids else None
    
    @property
    def best_ask(self) -> Optional[float]:
        """Get best ask price"""
        return self.asks[0][0] if self.asks else None
    
    @property
    def spread(self) -> Optional[float]:
        """Get bid-ask spread"""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None
    
    @property
    def mid_price(self) -> Optional[float]:
        """Get mid price"""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None


class OrderbookStreamer:
    """
    High-performance orderbook data streamer with WebSocket and REST support.
    
    Usage:
        async def on_orderbook(snapshot: OrderbookSnapshot):
            print(f"Received orderbook: {snapshot.symbol} @ {snapshot.mid_price}")
        
        streamer = OrderbookStreamer(
            symbol="BTCUSDT",
            on_orderbook=on_orderbook,
            rest_interval=30  # Poll REST every 30 seconds
        )
        
        await streamer.start()
    """
    
    def __init__(
        self,
        symbol: str,
        on_orderbook: Callable[[OrderbookSnapshot], None],
        rest_interval: int = 30,
        depth_limit: int = 100,
        buffer_size: int = 1000,
        enable_websocket: bool = True,
        enable_rest: bool = True
    ):
        """
        Initialize the orderbook streamer.
        
        Args:
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            on_orderbook: Callback function for orderbook updates
            rest_interval: REST polling interval in seconds (default: 30)
            depth_limit: Number of orderbook levels to fetch (5, 10, 20, 50, 100, 500, 1000)
            buffer_size: Size of internal data buffer
            enable_websocket: Enable WebSocket streaming
            enable_rest: Enable REST polling
        """
        self.symbol = symbol.upper()
        self.symbol_lower = symbol.lower()
        self.on_orderbook = on_orderbook
        self.rest_interval = rest_interval
        self.depth_limit = depth_limit
        self.buffer_size = buffer_size
        self.enable_websocket = enable_websocket
        self.enable_rest = enable_rest
        
        # WebSocket configuration
        self.ws_url = f"wss://fstream.binance.com/ws/{self.symbol_lower}@depth@100ms"
        
        # REST configuration
        self.rest_url = f"https://fapi.binance.com/fapi/v1/depth?symbol={self.symbol}&limit={self.depth_limit}"
        
        # State tracking
        self.is_running = False
        self.ws_connected = False
        self.ws_connection_time = None
        self.last_rest_fetch = 0
        self.last_update_id = None
        
        # Statistics
        self.ws_messages_received = 0
        self.rest_fetches = 0
        self.reconnection_count = 0
        self.error_count = 0
        
        # Buffer for orderbook snapshots
        self.snapshot_buffer = deque(maxlen=buffer_size)
        
        print(f"[OrderbookStreamer] Initialized for {self.symbol}")
        print(f"  WebSocket: {'✓' if enable_websocket else '✗'}")
        print(f"  REST: {'✓' if enable_rest else '✗'} (interval: {rest_interval}s)")
        print(f"  Depth: {depth_limit} levels")
    
    async def start(self):
        """Start the orderbook streamer"""
        self.is_running = True
        
        tasks = []
        
        if self.enable_websocket:
            tasks.append(self._ws_listener())
        
        if self.enable_rest:
            tasks.append(self._rest_poller())
        
        if not tasks:
            raise ValueError("At least one data source (WebSocket or REST) must be enabled")
        
        print(f"[OrderbookStreamer] Starting {len(tasks)} data source(s)...")
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def stop(self):
        """Stop the orderbook streamer"""
        print(f"[OrderbookStreamer] Stopping...")
        self.is_running = False
    
    async def _ws_listener(self):
        """
        WebSocket listener with robust reconnection logic.
        Streams real-time depth updates with 100ms frequency.
        """
        backoff = 1
        max_backoff = 30
        
        while self.is_running:
            try:
                self.reconnection_count += 1
                print(f"[OrderbookStreamer] 🔄 WebSocket connection attempt #{self.reconnection_count}")
                
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=40,
                    ping_timeout=30,
                    close_timeout=5,
                    max_size=10_000_000
                ) as ws:
                    print(f"[OrderbookStreamer] ✅ WebSocket connected")
                    self.ws_connected = True
                    self.ws_connection_time = time.time()
                    
                    # Reset backoff on successful connection
                    backoff = 1
                    
                    # Process messages
                    async for raw_message in ws:
                        try:
                            data = json.loads(raw_message)
                            await self._process_ws_depth(data)
                            self.ws_messages_received += 1
                        except json.JSONDecodeError as e:
                            print(f"[OrderbookStreamer] ⚠️ Invalid JSON: {e}")
                            self.error_count += 1
                        except Exception as e:
                            print(f"[OrderbookStreamer] ⚠️ Message processing error: {e}")
                            self.error_count += 1
            
            except websockets.exceptions.ConnectionClosed as e:
                print(f"[OrderbookStreamer] ⚠️ WebSocket closed: {e}")
            except asyncio.TimeoutError:
                print(f"[OrderbookStreamer] ⚠️ WebSocket timeout")
            except Exception as e:
                print(f"[OrderbookStreamer] ⚠️ WebSocket error: {e}")
                self.error_count += 1
            
            # Clear connection state
            self.ws_connected = False
            self.ws_connection_time = None
            
            # Reconnect with exponential backoff
            if self.is_running:
                print(f"[OrderbookStreamer] 🔄 Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                
                # Add jitter to prevent thundering herd
                if backoff > 4:
                    import random
                    jitter = random.uniform(0, min(backoff * 0.3, 5))
                    await asyncio.sleep(jitter)
    
    async def _rest_poller(self):
        """
        REST API poller for full orderbook snapshots.
        Polls at configured interval with retry logic.
        """
        async with aiohttp.ClientSession() as session:
            while self.is_running:
                try:
                    if TENACITY_AVAILABLE:
                        snapshot = await self._fetch_rest_with_retry(session)
                    else:
                        snapshot = await self._fetch_rest_snapshot(session)
                    
                    if snapshot:
                        await self._process_snapshot(snapshot)
                        self.rest_fetches += 1
                        self.last_rest_fetch = time.time()
                
                except Exception as e:
                    print(f"[OrderbookStreamer] ⚠️ REST fetch error: {e}")
                    self.error_count += 1
                
                await asyncio.sleep(self.rest_interval)
    
    async def _fetch_rest_snapshot(self, session: aiohttp.ClientSession) -> Optional[OrderbookSnapshot]:
        """Fetch orderbook snapshot from REST API"""
        try:
            async with session.get(self.rest_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return self._parse_rest_response(data)
        except Exception as e:
            print(f"[OrderbookStreamer] ⚠️ REST request failed: {e}")
            return None
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True
    )
    async def _fetch_rest_with_retry(self, session: aiohttp.ClientSession) -> Optional[OrderbookSnapshot]:
        """Fetch REST data with automatic retry"""
        return await self._fetch_rest_snapshot(session)
    
    def _parse_rest_response(self, data: Dict[str, Any]) -> OrderbookSnapshot:
        """Parse REST API response into OrderbookSnapshot"""
        bids = [(float(price), float(qty)) for price, qty in data.get("bids", [])]
        asks = [(float(price), float(qty)) for price, qty in data.get("asks", [])]
        
        return OrderbookSnapshot(
            timestamp=time.time(),
            symbol=self.symbol,
            bids=bids,
            asks=asks,
            last_update_id=data.get("lastUpdateId"),
            source="rest"
        )
    
    async def _process_ws_depth(self, data: Dict[str, Any]):
        """Process WebSocket depth update"""
        try:
            bids = [(float(price), float(qty)) for price, qty in data.get("b", [])]
            asks = [(float(price), float(qty)) for price, qty in data.get("a", [])]
            
            # Filter out zero quantities (removals)
            bids = [(p, q) for p, q in bids if q > 0]
            asks = [(p, q) for p, q in asks if q > 0]
            
            snapshot = OrderbookSnapshot(
                timestamp=data.get("E", time.time() * 1000) / 1000.0,
                symbol=self.symbol,
                bids=bids,
                asks=asks,
                last_update_id=data.get("u"),
                source="websocket"
            )
            
            await self._process_snapshot(snapshot)
            
        except Exception as e:
            print(f"[OrderbookStreamer] ⚠️ WebSocket processing error: {e}")
            self.error_count += 1
    
    async def _process_snapshot(self, snapshot: OrderbookSnapshot):
        """Process and deliver orderbook snapshot"""
        # Update last_update_id for synchronization
        if snapshot.last_update_id:
            self.last_update_id = snapshot.last_update_id
        
        # Store in buffer
        self.snapshot_buffer.append(snapshot)
        
        # Deliver to callback
        try:
            if asyncio.iscoroutinefunction(self.on_orderbook):
                await self.on_orderbook(snapshot)
            else:
                self.on_orderbook(snapshot)
        except Exception as e:
            print(f"[OrderbookStreamer] ⚠️ Callback error: {e}")
            self.error_count += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get streamer statistics"""
        return {
            "symbol": self.symbol,
            "is_running": self.is_running,
            "ws_connected": self.ws_connected,
            "ws_messages_received": self.ws_messages_received,
            "rest_fetches": self.rest_fetches,
            "reconnection_count": self.reconnection_count,
            "error_count": self.error_count,
            "buffer_size": len(self.snapshot_buffer),
            "last_update_id": self.last_update_id,
            "uptime": time.time() - self.ws_connection_time if self.ws_connection_time else 0
        }


# Example usage
async def example_usage():
    """Example of how to use the OrderbookStreamer"""
    
    # Define callback for orderbook updates
    async def on_orderbook_update(snapshot: OrderbookSnapshot):
        print(f"\n[{snapshot.symbol}] Orderbook Update:")
        print(f"  Source: {snapshot.source}")
        print(f"  Best Bid: ${snapshot.best_bid:.2f}")
        print(f"  Best Ask: ${snapshot.best_ask:.2f}")
        print(f"  Spread: ${snapshot.spread:.2f}")
        print(f"  Mid Price: ${snapshot.mid_price:.2f}")
        print(f"  Bid Depth: {len(snapshot.bids)} levels")
        print(f"  Ask Depth: {len(snapshot.asks)} levels")
    
    # Create streamer
    streamer = OrderbookStreamer(
        symbol="BTCUSDT",
        on_orderbook=on_orderbook_update,
        rest_interval=30,
        depth_limit=100,
        enable_websocket=True,
        enable_rest=True
    )
    
    # Start streaming (runs indefinitely)
    try:
        await streamer.start()
    except KeyboardInterrupt:
        print("\nStopping streamer...")
        await streamer.stop()
        
        # Print final statistics
        stats = streamer.get_stats()
        print("\nFinal Statistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    """
    Run the example when executed directly.
    
    Usage:
        python orderbook_streamer.py
    
    Stop with Ctrl+C
    """
    print("=" * 60)
    print("Orderbook Data Streamer - Example Usage")
    print("=" * 60)
    print()
    
    # Run the example
    asyncio.run(example_usage())
