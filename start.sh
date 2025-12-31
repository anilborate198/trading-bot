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

try:
    import pytz
    IST = pytz.timezone('Asia/Kolkata')
    print("✅ pytz imported successfully")
except ImportError as e:
    print(f"❌ Error importing pytz: {e}")
    # Fallback to UTC if pytz not available
    import sys
    print("⚠️  Using UTC time as fallback")
    
    class UTC:
        @staticmethod
        def localize(dt):
            return dt
    IST = UTC()

app = FastAPI()

@app.get('/')
@app.get('/health')
async def health():
    try:
        ist_time = datetime.now(IST)
        return {
            'status': 'running', 
            'service': 'trading-bot', 
            'time_ist': ist_time.strftime('%Y-%m-%d %H:%M:%S IST'),
            'time_utc': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        }
    except Exception as e:
        return {
            'status': 'running',
            'service': 'trading-bot',
            'time_utc': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
            'error': str(e)
        }

def is_market_time():
    try:
        now = datetime.now(IST)
        current_time = now.hour * 60 + now.minute
        current_day = now.weekday()
        
        if current_day >= 5:
            return False
        
        market_open = 9 * 60 + 15
        market_close = 15 * 60 + 30
        
        return market_open <= current_time <= market_close
    except Exception as e:
        print(f"❌ Error in is_market_time: {e}")
        return False

def wait_until_trading_time():
    while True:
        try:
            now = datetime.now(IST)
            current_time = now.hour * 60 + now.minute
            current_day = now.weekday()
            
            if current_day >= 5:
                print(f"⏰ Weekend - Sleeping for 1 hour... (IST: {now.strftime('%Y-%m-%d %H:%M:%S')})")
                time.sleep(3600)
                continue
            
            target_time = 9 * 60 + 20
            market_close = 15 * 60 + 30
            
            if target_time <= current_time < market_close:
                print(f"✅ Trading time reached! Starting bot at IST: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                return True
            
            if current_time < target_time:
                wait_minutes = target_time - current_time
                print(f"⏰ Current IST time: {now.strftime('%H:%M')} - Waiting {wait_minutes} minutes until 9:20 AM IST...")
            else:
                wait_minutes = 1440 - current_time + target_time
                print(f"⏰ Market closed - Next run tomorrow at 9:20 AM IST (waiting {wait_minutes} minutes)...")
            
            time.sleep(60)
        except Exception as e:
            print(f"❌ Error in wait_until_trading_time: {e}")
            time.sleep(60)

def run_trading_bot():
    try:
        ist_now = datetime.now(IST)
        print("🤖 Trading Bot Background Service Started")
        print(f"📅 Current IST Time: {ist_now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📅 Current UTC Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
        
        while True:
            wait_until_trading_time()
            
            print("🚀 Launching trading bot...")
            try:
                result = subprocess.run(['python', 'b.py'], capture_output=True, text=True, timeout=300)
                print(f"Bot output: {result.stdout}")
                if result.stderr:
                    print(f"Bot errors: {result.stderr}")
            except subprocess.TimeoutExpired:
                print("⚠️  Bot execution timeout (5 minutes)")
            except Exception as e:
                print(f"❌ Error running bot: {e}")
            
            ist_now = datetime.now(IST)
            print(f"✅ Trading session completed at IST: {ist_now.strftime('%Y-%m-%d %H:%M:%S')}")
            print("⏰ Waiting for next trading session...")
            
            time.sleep(18000)
    except Exception as e:
        print(f"❌ Critical error in run_trading_bot: {e}")
        import traceback
        traceback.print_exc()

try:
    bot_thread = threading.Thread(target=run_trading_bot, daemon=True)
    bot_thread.start()
    print("✅ Trading bot thread started")
except Exception as e:
    print(f"❌ Error starting bot thread: {e}")

if __name__ == '__main__':
    try:
        port = int(os.getenv('PORT', 8000))
        print(f"🌐 Starting health check server on port {port}...")
        uvicorn.run(app, host='0.0.0.0', port=port, log_level="info")
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        import traceback
        traceback.print_exc()
        raise
EOF

# Run the combined server with error output
echo "🚀 Starting combined health check and trading bot service..."
python health_server.py 2>&1