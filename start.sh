```bash
#!/bin/bash

# Function to check if market is open (9:15 AM - 3:30 PM on weekdays)
is_market_time() {
    current_hour=$(date +%H)
    current_minute=$(date +%M)
    current_day=$(date +%u)  # 1=Monday, 7=Sunday
    
    # Check if weekday (Monday-Friday)
    if [ $current_day -ge 6 ]; then
        return 1  # Weekend
    fi
    
    # Convert to minutes since midnight
    current_time=$((10#$current_hour * 60 + 10#$current_minute))
    market_open=$((9 * 60 + 15))   # 9:15 AM
    market_close=$((15 * 60 + 30)) # 3:30 PM
    
    if [ $current_time -ge $market_open ] && [ $current_time -le $market_close ]; then
        return 0  # Market is open
    else
        return 1  # Market is closed
    fi
}

# Function to wait until 9:33 AM
wait_until_trading_time() {
    while true; do
        current_hour=$(date +%H)
        current_minute=$(date +%M)
        current_day=$(date +%u)
        
        # If weekend, sleep for 1 hour and check again
        if [ $current_day -ge 6 ]; then
            echo "⏰ Weekend - Sleeping for 1 hour..."
            sleep 3600
            continue
        fi
        
        # Target: 9:33 AM
        target_hour=9
        target_minute=33
        
        current_time=$((10#$current_hour * 60 + 10#$current_minute))
        target_time=$((target_hour * 60 + target_minute))
        
        if [ $current_time -ge $target_time ] && [ $current_time -lt $((15 * 60 + 30)) ]; then
            echo "✅ Trading time reached! Starting bot at $(date)"
            return 0
        fi
        
        # Calculate wait time
        if [ $current_time -lt $target_time ]; then
            wait_minutes=$((target_time - current_time))
            echo "⏰ Current time: $(date +%H:%M) - Waiting $wait_minutes minutes until 9:33 AM..."
        else
            # After market hours, wait until next day 9:33 AM
            wait_minutes=$((1440 - current_time + target_time))
            echo "⏰ Market closed - Next run tomorrow at 9:33 AM (waiting $wait_minutes minutes)..."
        fi
        
        # Sleep for 1 minute and check again
        sleep 60
    done
}

# Main loop
echo "🤖 Trading Bot Service Started"
echo "📅 Current Time: $(date)"

while true; do
    # Wait until 9:33 AM on a weekday
    wait_until_trading_time
    
    # Run the trading bot
    echo "🚀 Launching trading bot..."
    python b.py
    
    # After bot finishes, wait for next trading day
    echo "✅ Trading session completed at $(date)"
    echo "⏰ Waiting for next trading session..."
    
    # Sleep until next day
    sleep 18000  # 5 hours
done
```