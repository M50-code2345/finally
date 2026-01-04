# Orderbook Data Streamer

A standalone, production-ready Python module for streaming orderbook data from Binance using WebSocket and REST APIs.

## Features

✅ **Dual Data Sources**
- WebSocket streaming for real-time depth updates (100ms frequency)
- REST API polling for full orderbook snapshots
- Configurable to use either or both sources

✅ **Robust Connection Handling**
- Automatic reconnection with exponential backoff
- Jitter to prevent thundering herd problem
- Specific exception handling for different failure modes
- Connection state tracking

✅ **Data Quality**
- Automatic data validation and sorting
- Update ID tracking for synchronization
- Zero-quantity filtering (handles order removals)
- Calculated properties: best bid/ask, spread, mid price

✅ **Performance Optimized**
- Async/await for non-blocking I/O
- Configurable buffer management
- Minimal memory footprint
- Statistics tracking

✅ **Optional Retry Logic**
- Tenacity support for REST API calls (3 retries, exponential backoff)
- Graceful fallback if tenacity not installed

## Installation

### Required Dependencies
```bash
pip install aiohttp websockets
```

### Optional (Recommended for Production)
```bash
pip install tenacity  # For automatic REST API retries
```

## Usage

### Basic Usage (Python Script)

```python
import asyncio
from orderbook_streamer import OrderbookStreamer, OrderbookSnapshot

# Define your callback function
async def on_orderbook_update(snapshot: OrderbookSnapshot):
    print(f"Best Bid: ${snapshot.best_bid:.2f}")
    print(f"Best Ask: ${snapshot.best_ask:.2f}")
    print(f"Spread: ${snapshot.spread:.2f}")

async def main():
    # Create streamer
    streamer = OrderbookStreamer(
        symbol="BTCUSDT",
        on_orderbook=on_orderbook_update
    )
    
    # Start streaming
    await streamer.start()

# Run it
asyncio.run(main())
```

### Basic Usage (Jupyter/IPython)

```python
from orderbook_streamer import OrderbookStreamer, OrderbookSnapshot

# Define your callback function
async def on_orderbook_update(snapshot: OrderbookSnapshot):
    print(f"Best Bid: ${snapshot.best_bid:.2f}")
    print(f"Best Ask: ${snapshot.best_ask:.2f}")
    print(f"Spread: ${snapshot.spread:.2f}")

# Create streamer
streamer = OrderbookStreamer(
    symbol="BTCUSDT",
    on_orderbook=on_orderbook_update
)

# Start streaming (no asyncio.run needed in Jupyter)
await streamer.start()
```

**Note:** In Jupyter/IPython, you can use `await` directly since they have a built-in event loop. Do NOT use `asyncio.run()` in Jupyter - it will raise `RuntimeError: asyncio.run() cannot be called from a running event loop`.

### Advanced Configuration

```python
streamer = OrderbookStreamer(
    symbol="BTCUSDT",
    on_orderbook=on_orderbook_update,
    rest_interval=30,          # REST poll every 30 seconds
    depth_limit=100,           # Fetch 100 orderbook levels
    buffer_size=1000,          # Keep last 1000 snapshots
    enable_websocket=True,     # Enable WebSocket streaming
    enable_rest=True           # Enable REST polling
)
```

### WebSocket Only (Ultra-Low Latency)

```python
streamer = OrderbookStreamer(
    symbol="BTCUSDT",
    on_orderbook=on_orderbook_update,
    enable_websocket=True,
    enable_rest=False          # Disable REST polling
)
```

### REST Only (For Testing or Rate Limit Concerns)

```python
streamer = OrderbookStreamer(
    symbol="BTCUSDT",
    on_orderbook=on_orderbook_update,
    rest_interval=10,          # Poll every 10 seconds
    enable_websocket=False,    # Disable WebSocket
    enable_rest=True
)
```

## OrderbookSnapshot Object

The callback receives an `OrderbookSnapshot` object with:

### Properties
- `timestamp` (float): Unix timestamp
- `symbol` (str): Trading pair symbol
- `bids` (List[Tuple[float, float]]): Sorted bid levels [(price, quantity), ...]
- `asks` (List[Tuple[float, float]]): Sorted ask levels [(price, quantity), ...]
- `last_update_id` (Optional[int]): Update ID for synchronization
- `source` (str): "websocket" or "rest"

### Calculated Properties
- `best_bid` (Optional[float]): Highest bid price
- `best_ask` (Optional[float]): Lowest ask price
- `spread` (Optional[float]): Bid-ask spread
- `mid_price` (Optional[float]): (best_bid + best_ask) / 2

### Example
```python
async def on_orderbook_update(snapshot: OrderbookSnapshot):
    # Access raw data
    print(f"Symbol: {snapshot.symbol}")
    print(f"Timestamp: {snapshot.timestamp}")
    print(f"Source: {snapshot.source}")
    
    # Access calculated properties
    print(f"Best Bid: ${snapshot.best_bid:.2f}")
    print(f"Best Ask: ${snapshot.best_ask:.2f}")
    print(f"Spread: ${snapshot.spread:.4f}")
    print(f"Mid Price: ${snapshot.mid_price:.2f}")
    
    # Access orderbook levels
    print(f"Top 5 Bids:")
    for price, qty in snapshot.bids[:5]:
        print(f"  ${price:.2f} @ {qty:.4f} BTC")
    
    print(f"Top 5 Asks:")
    for price, qty in snapshot.asks[:5]:
        print(f"  ${price:.2f} @ {qty:.4f} BTC")
```

## Statistics

Get real-time statistics:

```python
stats = streamer.get_stats()
print(stats)
```

Returns:
```python
{
    "symbol": "BTCUSDT",
    "is_running": True,
    "ws_connected": True,
    "ws_messages_received": 1234,
    "rest_fetches": 45,
    "reconnection_count": 2,
    "error_count": 0,
    "buffer_size": 1000,
    "last_update_id": 123456789,
    "uptime": 3600.5
}
```

## Error Handling

The streamer handles errors gracefully:

- **Connection errors**: Automatic reconnection with exponential backoff
- **Invalid JSON**: Logs warning, continues processing
- **Callback errors**: Logs error, continues streaming
- **REST failures**: Retries with exponential backoff (if tenacity installed)

All errors are logged to stdout with clear formatting.

## Architecture

### WebSocket Listener (`_ws_listener`)
- Connects to Binance WebSocket stream
- Receives depth updates at 100ms intervals
- Automatic reconnection with backoff (1s → 2s → 4s → ... → 30s max)
- Jitter prevents simultaneous reconnections

### REST Poller (`_rest_poller`)
- Polls REST API at configured interval
- Fetches full orderbook snapshot
- Optional retry with tenacity (3 attempts, exponential backoff)
- Timeout: 10 seconds per request

### Data Flow
```
WebSocket Stream → _process_ws_depth → OrderbookSnapshot → Callback
REST API → _fetch_rest_snapshot → OrderbookSnapshot → Callback
```

## Performance Characteristics

- **Latency**: 10-50ms (WebSocket), 50-200ms (REST)
- **Memory**: ~1-2MB per symbol (with 1000-entry buffer)
- **CPU**: Minimal (async I/O, no heavy computation)
- **Network**: ~100 KB/s per symbol (WebSocket)

## Comparison with Current Implementation

| Feature | orderbook_streamer.py | codi (embedded) |
|---------|----------------------|-----------------|
| **Modularity** | ✅ Standalone file | ❌ Embedded in large file |
| **Reusability** | ✅ Import and use | ❌ Copy-paste required |
| **Documentation** | ✅ Comprehensive | ⚠️ Inline comments only |
| **Testing** | ✅ Easy to test | ❌ Requires full system |
| **Dependencies** | ✅ Minimal (aiohttp, websockets) | ⚠️ Many dependencies |
| **Configuration** | ✅ Simple parameters | ⚠️ Multiple config sections |
| **Error Handling** | ✅ Explicit and clear | ✅ Good |
| **Performance** | ✅ Optimized | ✅ Optimized |

## Example: Real-Time Spread Monitor

```python
import asyncio
from orderbook_streamer import OrderbookStreamer, OrderbookSnapshot

class SpreadMonitor:
    def __init__(self, alert_threshold=0.10):
        self.alert_threshold = alert_threshold
        self.spread_history = []
    
    async def on_orderbook(self, snapshot: OrderbookSnapshot):
        spread = snapshot.spread
        if spread and spread > self.alert_threshold:
            print(f"⚠️  ALERT: Wide spread detected: ${spread:.4f}")
        
        self.spread_history.append(spread)
        
        # Keep last 100 spreads
        if len(self.spread_history) > 100:
            self.spread_history.pop(0)
        
        # Calculate average spread
        avg_spread = sum(self.spread_history) / len(self.spread_history)
        print(f"Current: ${spread:.4f} | Average: ${avg_spread:.4f}")

async def main():
    monitor = SpreadMonitor(alert_threshold=0.10)
    
    streamer = OrderbookStreamer(
        symbol="BTCUSDT",
        on_orderbook=monitor.on_orderbook
    )
    
    await streamer.start()

asyncio.run(main())
```

## Example: Multi-Symbol Streaming

```python
import asyncio
from orderbook_streamer import OrderbookStreamer

async def btc_callback(snapshot):
    print(f"[BTC] Mid: ${snapshot.mid_price:.2f}")

async def eth_callback(snapshot):
    print(f"[ETH] Mid: ${snapshot.mid_price:.2f}")

async def main():
    btc_streamer = OrderbookStreamer("BTCUSDT", btc_callback)
    eth_streamer = OrderbookStreamer("ETHUSDT", eth_callback)
    
    await asyncio.gather(
        btc_streamer.start(),
        eth_streamer.start()
    )

asyncio.run(main())
```

## Testing

Run the example:
```bash
python orderbook_streamer.py
```

Stop with `Ctrl+C` to see final statistics.

## Troubleshooting

### "tenacity not available" warning
```bash
pip install tenacity
```

### WebSocket connection fails
- Check internet connection
- Verify symbol is correct (e.g., "BTCUSDT")
- Check Binance API status: https://www.binance.com/en/support/announcement

### REST requests timeout
- Increase timeout in code (default: 10s)
- Check Binance API rate limits
- Verify API endpoint is accessible

### RuntimeError: asyncio.run() cannot be called from a running event loop

This error occurs when trying to use `asyncio.run()` in Jupyter/IPython notebooks.

**Solution:**
```python
# ❌ WRONG - Don't use in Jupyter
import asyncio
asyncio.run(streamer.start())

# ✅ CORRECT - Use in Jupyter/IPython
await streamer.start()
```

**Or use the helper:**
```python
from orderbook_streamer import example_usage
await example_usage()
```

## License

Part of the Advanced Order Flow Analysis System
© 2026

## Support

For issues or questions, refer to the main repository documentation.
