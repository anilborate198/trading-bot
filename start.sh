#!/bin/bash

# Create a simple health check server file
cat > health_server.py << 'EOF'
from fastapi import FastAPI
import uvicorn
import os
import subprocess
import threading
import time
from datetime import datetime

app = FastAPI()

@app.get('/')
@app.get('/health')
async def health():
    return {'status': 'running', 'service': 'trading-bot', 'time': str(datetime.now())}

def is_market_time():
    now = datetime.now()
    current_time = now.hour * 60 + now.minute
    current_day = now.weekday()  # 0=Monday, 6=Sunday
    
    # Check if weekday (Monday-Friday)
    if current_day >= 5:
        return False
    
    market_open = 9 * 60 + 15   # 9:15 AM
    market_close = 15 * 60 + 30  # 3:30 PM
    
    return market_open <= current_time <= market_close

def wait_until_trading_time():
    while True:
        now = datetime.now()
        current_time = now.hour * 60 + now.minute
        current_day = now.weekday()
        
        # If weekend, sleep for 1 hour
        if current_day >= 5:
            print(f"⏰ Weekend - Sleeping for 1 hour... ({now})")
            time.sleep(3600)
            continue
        
        # Target: 11:00 AM
        target_time = 11 * 60 + 00
        market_close = 15 * 60 + 30
        
        if target_time <= current_time < market_close:
            print(f"✅ Trading time reached! Starting bot at {now}")
            return True
        
        # Calculate wait time
        if current_time < target_time:
            wait_minutes = target_time - current_time
            print(f"⏰ Current time: {now.strftime('%H:%M')} - Waiting {wait_minutes} minutes until 9:33 AM...")
        else:
            # After market hours, wait until next day
            wait_minutes = 1440 - current_time + target_time
            print(f"⏰ Market closed - Next run tomorrow at 9:33 AM (waiting {wait_minutes} minutes)...")
        
        # Sleep for 1 minute
        time.sleep(60)

def run_trading_bot():
    print("🤖 Trading Bot Background Service Started")
    print(f"📅 Current Time: {datetime.now()}")
    
    while True:
        # Wait until 9:33 AM on a weekday
        wait_until_trading_time()
        
        # Run the trading bot
        print("🚀 Launching trading bot...")
        try:
            result = subprocess.run(['python', 'b.py'], capture_output=True, text=True)
            print(f"Bot output: {result.stdout}")
            if result.stderr:
                print(f"Bot errors: {result.stderr}")
        except Exception as e:
            print(f"❌ Error running bot: {e}")
        
        print(f"✅ Trading session completed at {datetime.now()}")
        print("⏰ Waiting for next trading session...")
        
        # Sleep for 5 hours before checking again
        time.sleep(18000)

# Start trading bot in background thread
bot_thread = threading.Thread(target=run_trading_bot, daemon=True)
bot_thread.start()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    print(f"🌐 Starting health check server on port {port}...")
    uvicorn.run(app, host='0.0.0.0', port=port)
EOF

# Run the combined server
echo "🚀 Starting combined health check and trading bot service..."
python health_server.py