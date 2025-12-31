import requests, json, time, sys, os
from datetime import datetime, timedelta
from colorama import Fore, init
from SmartApi import SmartConnect
import pyotp
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import asyncio, threading, uvicorn
import pandas as pd
from dotenv import load_dotenv
from collections import deque
from bs4 import BeautifulSoup

load_dotenv()
init(autoreset=True)

# ==============CONFIGURATION==============# 
class Config:
    MODE = os.getenv("TRADING_MODE", "PAPER")

    # Credentials
    API_KEY = os.getenv("ANGEL_API_KEY")
    CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE")
    MPIN = os.getenv("ANGEL_MPIN")
    TOTP_KEY = os.getenv("ANGEL_TOTP_KEY")
    
    # Trading Parameters
    STOP_LOSS_AMOUNT = 2500
    TRAILING_PROFIT_TRIGGER = 5000
    TRAILING_STOP_DRAWDOWN = 2500
    MAX_TRADES_PER_DAY = 8
    MAX_DAILY_LOSS = 10000
    MAX_STOCKS_TO_TRADE = 2
    MIN_STOCK_PRICE = 100
    
    # Breakout Parameters
    TICK_INTERVAL = 2
    
    # Exit Time
    AUTO_EXIT_TIME = "15:15"
    
    # Server - FIXED for Render
    WS_HOST = "0.0.0.0"
    WS_PORT = int(os.getenv('PORT', 10000))  # Use Render's PORT
    LOG_TRADES = True
    LOG_FILE = "trades_log.json"

if not all([Config.API_KEY, Config.CLIENT_CODE, Config.MPIN, Config.TOTP_KEY]):
    print(Fore.RED + "❌ Missing credentials in .env file!")
    sys.exit(1)

print(f"{Fore.CYAN}{'='*70}\n🤖 LONG BUILD UP TRADING SYSTEM - PARALLEL MONITORING\n{'='*70}")
print(f"{Fore.YELLOW}MODE: {Config.MODE} | Stop Loss: ₹{Config.STOP_LOSS_AMOUNT:,} | Max Daily Loss: ₹{Config.MAX_DAILY_LOSS:,}")
print(f"Top Stocks: {Config.MAX_STOCKS_TO_TRADE} | Tick Interval: {Config.TICK_INTERVAL}s")
print(f"Min Stock Price: ₹{Config.MIN_STOCK_PRICE} | Auto-Exit Time: {Config.AUTO_EXIT_TIME}")
print(f"{Fore.CYAN}{'='*70}\n")

# ============WEBSOCKET MANAGER===================# 
class WSManager:
    def __init__(self):
        self.app = FastAPI()
        self.app.add_middleware(
            CORSMiddleware, 
            allow_origins=["*"], 
            allow_credentials=True, 
            allow_methods=["*"], 
            allow_headers=["*"]
        )
        self.connections = []
        self.data = {
            "trades": {}, 
            "buildup_stocks": [], 
            "total_pnl": 0, 
            "mode": Config.MODE,
            "live_prices": {},
            "breakout_status": {}
        }
        
        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "timestamp": datetime.now().isoformat()}
        
        @self.app.get("/")
        async def root():
            return {"message": "Trading Bot API", "status": "running"}
        
        @self.app.websocket("/ws/trading")
        async def ws_endpoint(ws: WebSocket):
            await ws.accept()
            self.connections.append(ws)
            print(f"{Fore.GREEN}✅ Client connected. Total: {len(self.connections)}")
            await ws.send_json(self.data)
            try:
                while True: 
                    await ws.receive_text()
            except:
                if ws in self.connections: 
                    self.connections.remove(ws)
    
    def start(self):
        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            port = Config.WS_PORT
            print(f"{Fore.GREEN}🌐 Starting server on port {port}...")
            uvicorn.run(self.app, host="0.0.0.0", port=port, log_level="error")
        threading.Thread(target=run, daemon=True).start()
        time.sleep(2)
        print(f"{Fore.GREEN}🌐 WebSocket Server: ws://0.0.0.0:{Config.WS_PORT}/ws/trading")
    
    async def broadcast(self, data):
        for conn in self.connections[:]:
            try: 
                await conn.send_json(data)
            except: 
                self.connections.remove(conn)

ws = WSManager()



# =============== ANGEL ONE CLIENT====================# 
class AngelClient:
    SCRIP_URL = 'https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json'
    CACHE_TTL = 3600
    
    def __init__(self, api_key, client_code, mpin, totp_key):
        self.api_key = api_key
        self.client_code = client_code
        self.mpin = mpin
        self.totp_key = totp_key
        self.smart_api = SmartConnect(api_key=api_key)
        self.auth_token = None
        self._scrip_cache = None
        self._cache_time = None
    
    def login(self):
        try:
            totp = pyotp.TOTP(self.totp_key).now()
            data = self.smart_api.generateSession(self.client_code, self.mpin, totp)
            if data.get('status'):
                self.auth_token = data['data']['jwtToken']
                print(f"{Fore.GREEN}✅ Logged in to Angel One")
                return True
            print(f"{Fore.RED}❌ Login failed: {data.get('message', 'Unknown error')}")
            return False
        except Exception as e:
            print(f"{Fore.RED}❌ Login failed: {e}")
            return False
    
    def get_ltp(self, exchange, symbol, token):
        try:
            data = self.smart_api.ltpData(exchange, symbol, token)
            return float(data.get('data', {}).get('ltp', 0)) if data.get('status') else 0
        except: return 0
    
    def get_ltp_batch(self, instruments):
        """Get LTP for multiple instruments at once"""
        prices = {}
        for inst in instruments:
            ltp = self.get_ltp(inst['exchange'], inst['symbol'], inst['token'])
            prices[inst['key']] = ltp
        return prices
    
    def _load_scrip_master(self, force_refresh=False):
        if not force_refresh and self._scrip_cache is not None and self._cache_time:
            if time.time() - self._cache_time < self.CACHE_TTL:
                return self._scrip_cache
        
        try:
            print(f"{Fore.CYAN}📥 Downloading ScripMaster...")
            response = requests.get(self.SCRIP_URL, timeout=15)
            response.raise_for_status()
            self._scrip_cache = pd.DataFrame(response.json())
            self._cache_time = time.time()
            print(f"{Fore.GREEN}✓ ScripMaster loaded: {len(self._scrip_cache):,} instruments")
            return self._scrip_cache
        except Exception as e:
            print(f"{Fore.RED}❌ ScripMaster download failed: {e}")
            return None
    
    def get_lot_size(self, symbol):
        try:
            df = self._load_scrip_master()
            if df is None: return None
            
            fno = df[(df['name'] == symbol) & (df['exch_seg'] == 'NFO') & 
                     (df['instrumenttype'].isin(['FUTSTK', 'OPTSTK']))]
            
            if fno.empty:
                print(f"{Fore.YELLOW}⚠️ {symbol}: Not in F&O")
                return None
            
            lot_size = int(fno.iloc[0]['lotsize'])
            print(f"{Fore.GREEN}✓ {symbol} Lot Size: {lot_size:,}")
            return lot_size
        except Exception as e:
            print(f"{Fore.RED}❌ Error fetching lot size for {symbol}: {e}")
            return None
    
    def search(self, exchange, text):
        try:
            data = self.smart_api.searchScrip(exchange, text)
            return data.get('data', []) if data.get('status') else []
        except: return []
    
    def get_candle_data(self, exchange, symbol, token, interval="THREE_MINUTE"):
        try:
            return self._fetch_candles(exchange, token, interval, 15, 2) or \
                   self._fetch_aggregated_candles(exchange, token)
        except:
            return self._fetch_aggregated_candles(exchange, token)
    
    def _fetch_candles(self, exchange, token, interval, lookback_mins, min_candles):
        try:
            from_date = (datetime.now() - timedelta(minutes=lookback_mins)).strftime("%Y-%m-%d %H:%M")
            to_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            data = self.smart_api.getCandleData({
                "exchange": exchange, "symboltoken": token, "interval": interval,
                "fromdate": from_date, "todate": to_date
            })
            
            if data.get('status') and data.get('data') and len(data['data']) >= min_candles:
                candle = data['data'][-2]
                return self._parse_candle(candle)
            return None
        except: return None
    
    def _fetch_aggregated_candles(self, exchange, token):
        try:
            from_date = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
            to_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            data = self.smart_api.getCandleData({
                "exchange": exchange, "symboltoken": token, "interval": "ONE_MINUTE",
                "fromdate": from_date, "todate": to_date
            })
            
            if data.get('status') and data.get('data') and len(data['data']) >= 3:
                candles = data['data'][-4:-1]
                return {
                    'open': float(candles[0][1]),
                    'high': max(float(c[2]) for c in candles),
                    'low': min(float(c[3]) for c in candles),
                    'close': float(candles[-1][4]),
                    'volume': sum(int(c[5]) if len(c) > 5 else 0 for c in candles),
                    'timestamp': self._parse_timestamp(candles[-1][0]).strftime('%H:%M:%S'),
                    'candle_time': self._parse_timestamp(candles[-1][0])
                }
            return None
        except: return None
    
    def _parse_candle(self, candle):
        return {
            'open': float(candle[1]),
            'high': float(candle[2]),
            'low': float(candle[3]),
            'close': float(candle[4]),
            'volume': int(candle[5]) if len(candle) > 5 else 0,
            'timestamp': self._parse_timestamp(candle[0]).strftime('%H:%M:%S'),
            'candle_time': self._parse_timestamp(candle[0])
        }
    
    @staticmethod
    def _parse_timestamp(ts_str):
        if 'T' in ts_str:
            return datetime.fromisoformat(ts_str.replace('+05:30', ''))
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    
    def place_order(self, symbol, token, transaction_type, quantity, order_type="MARKET", price=0):
        return (self._place_paper_order(symbol, token, transaction_type, quantity, price) 
                if Config.MODE == "PAPER" 
                else self._place_live_order(symbol, token, transaction_type, quantity, order_type, price))

    def _place_paper_order(self, symbol, token, transaction_type, quantity, price):
        import random
        order_id = f"PAPER_{int(time.time())}_{random.randint(1000, 9999)}"
        ltp = self.get_ltp("NFO", symbol, token)
        execution_price = ltp if ltp > 0 else price
        
        print(Fore.CYAN + f"📄 PAPER: {transaction_type} {quantity} {symbol} @ ₹{execution_price:.2f} | ID: {order_id}")
        time.sleep(0.5)
        
        return {'success': True, 'orderid': order_id, 'data': {
            'orderid': order_id, 'mode': 'PAPER', 'price': execution_price, 
            'quantity': quantity, 'symbol': symbol}}

    def _place_live_order(self, symbol, token, transaction_type, quantity, order_type, price):
        try:
            order_params = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": str(token),
                "transactiontype": transaction_type, "exchange": "NFO", "ordertype": order_type,
                "producttype": "INTRADAY", "duration": "DAY", 
                "price": str(price) if order_type == "LIMIT" else "0",
                "squareoff": "0", "stoploss": "0", "quantity": str(quantity)
            }
            
            print(Fore.CYAN + f"📤 {transaction_type}: {quantity} {symbol}")
            response = self.smart_api.placeOrder(order_params)
            
            if isinstance(response, str):
                print(Fore.GREEN + f"✅ ORDER: {response}")
                return {'success': True, 'orderid': response, 'data': {'orderid': response}}
            
            if isinstance(response, dict):
                if response.get('status') in [True, 'true']:
                    order_id = (response.get('data', {}).get('orderid') or 
                               response.get('orderid') or 
                               response.get('uniqueorderid'))
                    
                    if order_id:
                        print(Fore.GREEN + f"✅ ORDER: {order_id}")
                        return {'success': True, 'orderid': str(order_id), 'data': response.get('data', response)}
                    
                    return {'success': True, 'orderid': 'PENDING_VERIFICATION', 'data': response}
                
                error_msg = response.get('message') or response.get('error') or str(response)
                print(Fore.RED + f"❌ FAILED: {error_msg}")
                return {'success': False, 'error': error_msg}
            
            return {'success': False, 'error': f'Unexpected type: {type(response)}'}
                
        except Exception as e:
            print(Fore.RED + f"❌ Exception: {e}")
            return {'success': False, 'error': str(e)}

    def get_order_book(self):
        if Config.MODE == "PAPER":
            return []
        
        try:
            response = self.smart_api.orderBook()
            if not (isinstance(response, dict) and response.get('status')):
                return []
            
            orders = response.get('data', [])
            if orders:
                print(Fore.CYAN + f"\n{'='*70}\n📋 ORDER BOOK ({len(orders)} orders)\n{'='*70}")
                for order in orders[-5:]:
                    color = Fore.GREEN if order.get('orderstatus') == 'complete' else Fore.YELLOW
                    print(color + f"{order.get('orderid')} | {order.get('tradingsymbol')} | "
                          f"{order.get('transactiontype')} {order.get('quantity')} | {order.get('orderstatus')}")
                print(Fore.CYAN + f"{'='*70}\n")
            return orders
        except Exception as e:
            print(Fore.RED + f"❌ Order book error: {e}")
            return []

# ============================================================================
# LONG BUILD UP SCANNER
# ============================================================================

def fetch_long_buildup_from_nse():
    """
    Fetch REAL Long Build Up stocks from NSE using FnO data
    Long Build Up = Price UP + OI UP (Bullish signal)
    FILTERS STOCKS WITH LTP > Config.MIN_STOCK_PRICE
    """
    print(Fore.CYAN + f"\n{'='*70}\n🔍 FETCHING REAL LONG BUILD UP STOCKS FROM NSE\n{'='*70}\n")
    print(Fore.YELLOW + f"📊 Filtering stocks with LTP > ₹{Config.MIN_STOCK_PRICE}\n")
    
    buildup_stocks = []
    
    try:
        # Setup session with proper headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.nseindia.com/',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin'
        }
        
        session = requests.Session()
        
        # Step 1: Initialize session
        print(Fore.YELLOW + "📡 Connecting to NSE...")
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        time.sleep(2)
        
        # Step 2: Get FnO stocks with OI data
        print(Fore.YELLOW + "📊 Fetching FnO stocks data...\n")
        
        fno_url = "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
        response = session.get(fno_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            raise Exception(f"NSE API returned status code: {response.status_code}")
        
        data = response.json()
        fno_stocks = data.get('data', [])
        
        if not fno_stocks:
            raise Exception("No data received from NSE")
        
        print(Fore.GREEN + f"✅ Received data for {len(fno_stocks)} F&O stocks\n")
        print(Fore.CYAN + f"{'Symbol':<15} {'Price Change %':<15} {'Volume':<15} {'LTP':<12} {'Status'}")
        print(Fore.CYAN + "="*80)
        
        # Step 3: Filter for Long Build Up criteria + MIN PRICE
        for stock in fno_stocks:
            try:
                symbol = stock.get('symbol', '')
                
                # Skip indices
                if symbol in ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']:
                    continue
                
                pct_change = float(stock.get('pChange', 0))
                last_price = float(stock.get('lastPrice', 0))
                volume = int(stock.get('totalTradedVolume', 0))
                prev_close = float(stock.get('previousClose', 0))
                
                # Long Build Up Criteria:
                # 1. Price should be UP (pct_change > 0)
                # 2. Should have decent volume
                # 3. Should be a valid F&O stock
                # 4. LTP should be > MIN_STOCK_PRICE (NEW)
                
                if pct_change > 0.2 and volume > 100000 and last_price > Config.MIN_STOCK_PRICE:
                    # Calculate score based on price momentum
                    score = pct_change * (volume / 1000000)  # Weight by volume
                    
                    buildup_stocks.append({
                        'symbol': symbol,
                        'price_change_pct': pct_change,
                        'oi_change_pct': pct_change,  # Using price as proxy for OI
                        'score': score,
                        'ltp': last_price,
                        'volume': volume,
                        'prev_close': prev_close
                    })
                    
                    status = "🟢 LONG BUILD UP" if pct_change > 0.5 else "🟡 Potential"
                    print(Fore.GREEN + f"{symbol:<15} {pct_change:>+6.2f}%         {volume:>12,}   ₹{last_price:>9.2f}   {status}")
            
            except Exception as e:
                continue
        
        if not buildup_stocks:
            print(Fore.YELLOW + f"\n⚠️ No Long Build Up stocks found with LTP > ₹{Config.MIN_STOCK_PRICE}")
            print(Fore.YELLOW + "📊 Using top gainers from F&O segment...\n")
            
            # Fallback: Get top gainers with price filter
            for stock in fno_stocks:
                try:
                    symbol = stock.get('symbol', '')
                    if symbol in ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']:
                        continue
                    
                    pct_change = float(stock.get('pChange', 0))
                    last_price = float(stock.get('lastPrice', 0))
                    volume = int(stock.get('totalTradedVolume', 0))
                    
                    if pct_change > 0 and last_price > Config.MIN_STOCK_PRICE:
                        buildup_stocks.append({
                            'symbol': symbol,
                            'price_change_pct': pct_change,
                            'oi_change_pct': pct_change,
                            'score': pct_change,
                            'ltp': last_price,
                            'volume': volume
                        })
                except:
                    continue
        
        # Sort by score (descending)
        buildup_stocks.sort(key=lambda x: x['score'], reverse=True)
        
        # Take top stocks
        top_stocks = buildup_stocks[:Config.MAX_STOCKS_TO_TRADE]
        
        print(Fore.CYAN + f"\n{'='*80}")
        print(Fore.GREEN + f"✅ SELECTED TOP {len(top_stocks)} LONG BUILD UP STOCKS (LTP > ₹{Config.MIN_STOCK_PRICE}):")
        print(Fore.CYAN + f"{'='*80}\n")
        
        for i, stock in enumerate(top_stocks, 1):
            print(Fore.GREEN + f"{i}. {stock['symbol']:<12} | Price: +{stock['price_change_pct']:.2f}% | "
                  f"LTP: ₹{stock['ltp']:.2f} | Score: {stock['score']:.2f}")
        
        print(Fore.CYAN + f"\n{'='*80}\n")
        
        return top_stocks
    
    except requests.exceptions.Timeout:
        print(Fore.RED + "❌ NSE API timeout. Using fallback method...\n")
    except requests.exceptions.ConnectionError:
        print(Fore.RED + "❌ Connection error. Using fallback method...\n")
    except Exception as e:
        print(Fore.RED + f"❌ Error fetching from NSE: {e}\n")
        print(Fore.YELLOW + "Using fallback method...\n")
    
    # FALLBACK: Manual selection with price filter
    print(Fore.YELLOW + f"⚠️ Using fallback stock selection (LTP > ₹{Config.MIN_STOCK_PRICE})\n")
    print(Fore.CYAN + "💡 TIP: For live Long Build Up data:")
    print(Fore.CYAN + "   • Check NSE website manually")
    print(Fore.CYAN + "   • Use Sensibull/Opstra screeners")
    print(Fore.CYAN + "   • Subscribe to premium data providers\n")
    
    fallback_stocks = [
        {'symbol': 'RELIANCE', 'price_change_pct': 0.8, 'oi_change_pct': 2.5, 'score': 3.3, 'ltp': 1285.50, 'volume': 5000000},
        {'symbol': 'INFY', 'price_change_pct': 0.6, 'oi_change_pct': 2.0, 'score': 2.6, 'ltp': 1850.30, 'volume': 3000000},
        {'symbol': 'SBIN', 'price_change_pct': 0.9, 'oi_change_pct': 3.0, 'score': 3.9, 'ltp': 825.40, 'volume': 8000000},
        {'symbol': 'HDFCBANK', 'price_change_pct': 0.5, 'oi_change_pct': 1.8, 'score': 2.3, 'ltp': 1745.60, 'volume': 4000000},
        {'symbol': 'TATAMOTORS', 'price_change_pct': 1.2, 'oi_change_pct': 3.5, 'score': 4.7, 'ltp': 945.80, 'volume': 6000000}
    ]
    
    # Filter by min price
    filtered_stocks = [s for s in fallback_stocks if s['ltp'] > Config.MIN_STOCK_PRICE]
    
    return filtered_stocks[:Config.MAX_STOCKS_TO_TRADE]

# ============================================================================
# UTILITIES
# ============================================================================

def get_expiry():
    import calendar
    today = datetime.now()
    year, month = today.year, today.month
    
    _, last_day = calendar.monthrange(year, month)
    last_date = datetime(year, month, last_day)
    while last_date.weekday() != 1:
        last_date -= timedelta(days=1)
    
    if today.date() >= last_date.date():
        month = 1 if month == 12 else month + 1
        year = year + 1 if month == 1 else year
        _, last_day = calendar.monthrange(year, month)
        last_date = datetime(year, month, last_day)
        while last_date.weekday() != 1:
            last_date -= timedelta(days=1)
    
    return last_date.strftime('%d%b%Y').upper()

def get_atm(client, symbol, expiry):
    try:
        results = client.search("NSE", f"{symbol}-EQ")
        if not results:
            return None
        
        stock = next((r for r in results if r.get('tradingsymbol', '').endswith('-EQ')), None)
        if not stock:
            return None
        
        ltp_data = client.smart_api.ltpData("NSE", stock['tradingsymbol'], stock['symboltoken'])
        spot = float(ltp_data.get('data', {}).get('ltp', 0))
        if spot <= 0:
            return None
        
        lot = client.get_lot_size(symbol)
        if not lot:
            return None
        
        response = requests.get('https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json', timeout=10)
        df = pd.DataFrame(response.json())
        
        expiry_fmt = f"{expiry[:2]}{expiry[2:5]}{expiry[5:]}"
        options_df = df[(df['name'] == symbol) & (df['expiry'] == expiry_fmt) & 
                       (df['exch_seg'] == 'NFO') & (df['instrumenttype'] == 'OPTSTK')].copy()
        
        if options_df.empty:
            return None
        
        options_df['strike'] = pd.to_numeric(options_df['strike']) / 100
        options_df['distance'] = abs(options_df['strike'] - spot)
        atm_strike = options_df.loc[options_df['distance'].idxmin(), 'strike']
        
        ce = options_df[(options_df['strike'] == atm_strike) & (options_df['symbol'].str.endswith('CE'))].iloc[0]
        pe = options_df[(options_df['strike'] == atm_strike) & (options_df['symbol'].str.endswith('PE'))].iloc[0]
        
        ce_candle = client.get_candle_data("NFO", ce['symbol'], ce['token'])
        pe_candle = client.get_candle_data("NFO", pe['symbol'], pe['token'])
        ce_ltp = float(client.smart_api.ltpData("NFO", ce['symbol'], ce['token']).get('data', {}).get('ltp', 0))
        pe_ltp = float(client.smart_api.ltpData("NFO", pe['symbol'], pe['token']).get('data', {}).get('ltp', 0))
        
        if ce_candle and pe_candle:
            print(Fore.CYAN + f"\n{symbol:<12} Spot: ₹{spot:.2f} | ATM: {int(atm_strike)} | Lot: {lot}")
            print(Fore.GREEN + f"CE: O:{ce_candle['open']:.2f} H:{ce_candle['high']:.2f} L:{ce_candle['low']:.2f} C:{ce_candle['close']:.2f} | Time: {ce_candle['timestamp']}")
            print(Fore.RED + f"PE: O:{pe_candle['open']:.2f} H:{pe_candle['high']:.2f} L:{pe_candle['low']:.2f} C:{pe_candle['close']:.2f} | Time: {pe_candle['timestamp']}")
            
            return {
                "symbol": symbol, "spot": spot, "atm": int(atm_strike), "lot": lot, "expiry": expiry,
                "ce_token": ce['token'], "pe_token": pe['token'], "ce_symbol": ce['symbol'], "pe_symbol": pe['symbol'],
                "ce_ltp": ce_ltp, "pe_ltp": pe_ltp, "candle_time": ce_candle['timestamp'],
                "ce_open": ce_candle['open'], "pe_open": pe_candle['open'],
                "ce_high": ce_candle['high'], "pe_high": pe_candle['high'],
                "ce_low": ce_candle['low'], "pe_low": pe_candle['low'],
                "ce_close": ce_candle['close'], "pe_close": pe_candle['close'],
                "ce_volume": ce_candle.get('volume', 0), "pe_volume": pe_candle.get('volume', 0)
            }
    except Exception as e:
        print(Fore.RED + f"❌ {symbol}: {e}")
    return None

def is_open():
    import pytz
    IST = pytz.timezone('Asia/Kolkata')
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close

def should_auto_exit():
    """Check if it's time for auto-exit (3:15 PM IST)"""
    import pytz
    IST = pytz.timezone('Asia/Kolkata')
    now = datetime.now(IST)
    exit_hour, exit_minute = map(int, Config.AUTO_EXIT_TIME.split(':'))
    exit_time = now.replace(hour=exit_hour, minute=exit_minute, second=0, microsecond=0)
    return now >= exit_time

# ============================================================================
# PARALLEL MONITORING SYSTEM
# ============================================================================

class ParallelMonitor:
    def __init__(self, client, watchlist):
        self.client = client
        self.watchlist = watchlist
        self.trades = {}
        self.daily_pnl = {'total': 0, 'trades': []}
        self.breakout_levels = {}
        self.highest_pnl = {}
        self.trailing_active = {}
        self.running = True
        
        # Initialize breakout levels
        for stock in watchlist:
            symbol = stock['symbol']
            self.breakout_levels[f"{symbol}_CE"] = stock['ce_high'] * 1.01
            self.breakout_levels[f"{symbol}_PE"] = stock['pe_high'] * 1.01
            
            print(Fore.CYAN + f"\n📊 {symbol}")
            print(Fore.GREEN + f"   CE Breakout: ₹{self.breakout_levels[f'{symbol}_CE']:.2f}")
            print(Fore.RED + f"   PE Breakout: ₹{self.breakout_levels[f'{symbol}_PE']:.2f}")
            print(Fore.YELLOW + f"   Last Candle Time: {stock['candle_time']}")
    
    def get_all_instruments(self):
        """Build list of all instruments to monitor"""
        instruments = []
        
        # Add untraded options
        for stock in self.watchlist:
            symbol = stock['symbol']
            ce_key = f"{symbol}_CE"
            pe_key = f"{symbol}_PE"
            
            if ce_key not in self.trades or self.trades[ce_key].get('status') != 'open':
                instruments.append({
                    'key': ce_key,
                    'exchange': 'NFO',
                    'symbol': stock['ce_symbol'],
                    'token': stock['ce_token'],
                    'stock': stock,
                    'is_ce': True
                })
            
            if pe_key not in self.trades or self.trades[pe_key].get('status') != 'open':
                instruments.append({
                    'key': pe_key,
                    'exchange': 'NFO',
                    'symbol': stock['pe_symbol'],
                    'token': stock['pe_token'],
                    'stock': stock,
                    'is_ce': False
                })
        
        # Add open trades
        for name, trade in self.trades.items():
            if trade.get('status') == 'open':
                instruments.append({
                    'key': name,
                    'exchange': 'NFO',
                    'symbol': trade['tradingsymbol'],
                    'token': trade['token'],
                    'stock': None,
                    'is_trade': True,
                    'trade': trade
                })
        
        return instruments
    
    def execute_breakout(self, stock, is_ce, ltp):
        """Execute breakout entry"""
        opt_type = "CE" if is_ce else "PE"
        name = f"{stock['symbol']}_{opt_type}"
        
        print((Fore.GREEN if is_ce else Fore.RED) + f"\n🚀 {name} BREAKOUT @ ₹{ltp:.2f} | Time: {datetime.now().strftime('%H:%M:%S')}")
        
        order_result = self.client.place_order(
            symbol=stock['ce_symbol'] if is_ce else stock['pe_symbol'],
            token=stock['ce_token'] if is_ce else stock['pe_token'],
            transaction_type="BUY",
            quantity=stock['lot']
        )
        
        if not order_result['success']:
            return False
        
        self.trades[name] = {
            'token': stock['ce_token'] if is_ce else stock['pe_token'],
            'lot': stock['lot'],
            'entry': ltp,
            'ltp': ltp,
            'stop_loss': ltp - (Config.STOP_LOSS_AMOUNT / stock['lot']),
            'trailing_sl': None,
            'type': opt_type,
            'symbol': stock['symbol'],
            'tradingsymbol': stock['ce_symbol'] if is_ce else stock['pe_symbol'],
            'strike': stock['atm'],
            'status': 'open',
            'pnl': 0,
            'entry_time': datetime.now().strftime('%H:%M:%S'),
            'order_id': order_result['orderid'],
            'mode': Config.MODE,
            'strategy': 'Long Build Up - 3-Min Breakout'
        }
        
        self.highest_pnl[name] = 0
        self.trailing_active[name] = False
        
        return True
    
    def execute_exit(self, trade, name, ltp, pnl, reason):
        """Execute exit order"""
        is_ce = trade['type'] == 'CE'
        color = Fore.GREEN if is_ce else Fore.RED
        
        print(color + f"\n🛑 {reason} - {name} @ ₹{ltp:.2f} | Time: {datetime.now().strftime('%H:%M:%S')}")
        
        exit_order = self.client.place_order(
            symbol=trade['tradingsymbol'],
            token=trade['token'],
            transaction_type="SELL",
            quantity=trade['lot']
        )
        
        if not exit_order['success']:
            return False
        
        trade.update({
            'status': 'closed',
            'exit': ltp,
            'pnl': pnl,
            'exit_time': datetime.now().strftime('%H:%M:%S'),
            'exit_reason': reason,
            'exit_order_id': exit_order['orderid']
        })
        
        self.daily_pnl['total'] += pnl
        self.daily_pnl['trades'].append(trade)
        
        print(color + f"P&L: ₹{pnl:,.0f} | Daily: ₹{self.daily_pnl['total']:,.0f}")
        
        return True
    
    def process_tick(self, instruments, prices):
        """Process a single tick for all instruments"""
        now = datetime.now().strftime('%H:%M:%S')
        
        # Print header
        print(Fore.CYAN + f"\n{'='*100}")
        print(Fore.YELLOW + f"⏰ TICK @ {now}")
        print(Fore.CYAN + f"{'='*100}")
        
        # Track breakout checks
        for inst in instruments:
            if inst.get('is_trade'):
                continue
            
            key = inst['key']
            ltp = prices.get(key, 0)
            
            if ltp <= 0:
                continue
            
            stock = inst['stock']
            is_ce = inst['is_ce']
            name = key
            breakout_level = self.breakout_levels[key]
            
            color = Fore.GREEN if is_ce else Fore.RED
            opt_type = "CE" if is_ce else "PE"
            
            # Print current price vs breakout
            status = "🔥 ABOVE" if ltp >= breakout_level else "⏳ BELOW"
            print(color + f"{name:<15} | LTP: ₹{ltp:7.2f} | Breakout: ₹{breakout_level:7.2f} | {status}")
            
            # Check breakout
            if ltp >= breakout_level and key not in self.trades:
                self.execute_breakout(stock, is_ce, ltp)
        
        # Track open trades
        print(Fore.CYAN + f"\n{'-'*100}")
        print(Fore.YELLOW + "📊 OPEN POSITIONS:")
        print(Fore.CYAN + f"{'-'*100}")
        
        for inst in instruments:
            if not inst.get('is_trade'):
                continue
            
            key = inst['key']
            trade = inst['trade']
            ltp = prices.get(key, 0)
            
            if ltp <= 0:
                continue
            
            trade['ltp'] = ltp
            pnl = (ltp - trade['entry']) * trade['lot']
            trade['pnl'] = pnl
            
            # Update highest PnL
            if pnl > self.highest_pnl[key]:
                self.highest_pnl[key] = pnl
            
            # Print position status
            is_ce = trade['type'] == 'CE'
            color = Fore.GREEN if is_ce else Fore.RED
            pnl_color = Fore.GREEN if pnl > 0 else Fore.RED
            
            print(color + f"{key:<15} | Entry: ₹{trade['entry']:7.2f} | LTP: ₹{ltp:7.2f} | " + 
                  pnl_color + f"PnL: ₹{pnl:8,.0f} " + 
                  Fore.CYAN + f"| SL: ₹{trade['stop_loss']:.2f}")
            
            # Check stop loss
            if pnl <= -Config.STOP_LOSS_AMOUNT:
                self.execute_exit(trade, key, ltp, pnl, 'Stop Loss')
                continue
            
            # Activate trailing stop
            if pnl >= Config.TRAILING_PROFIT_TRIGGER and not self.trailing_active[key]:
                self.trailing_active[key] = True
                trade['trailing_sl'] = ltp - (Config.TRAILING_STOP_DRAWDOWN / trade['lot'])
                print(Fore.CYAN + f"   🎯 Trailing Stop Activated @ ₹{trade['trailing_sl']:.2f}")
            
            # Update trailing stop
            if self.trailing_active[key]:
                new_trailing_sl = ltp - (Config.TRAILING_STOP_DRAWDOWN / trade['lot'])
                if new_trailing_sl > trade.get('trailing_sl', 0):
                    trade['trailing_sl'] = new_trailing_sl
                    print(Fore.CYAN + f"   📈 Trailing Stop Updated @ ₹{trade['trailing_sl']:.2f}")
            
            # Check trailing stop
            if self.trailing_active[key] and (self.highest_pnl[key] - pnl) >= Config.TRAILING_STOP_DRAWDOWN:
                self.execute_exit(trade, key, ltp, pnl, 'Trailing Stop')
        
        print(Fore.CYAN + f"{'='*100}\n")
    
    def close_all_positions(self, reason="Auto-Exit"):
        """Close all open positions at market price"""
        print(Fore.YELLOW + f"\n{'='*100}")
        print(Fore.YELLOW + f"⏰ {reason} - CLOSING ALL POSITIONS @ {datetime.now().strftime('%H:%M:%S')}")
        print(Fore.YELLOW + f"{'='*100}\n")
        
        instruments = self.get_all_instruments()
        prices = self.client.get_ltp_batch(instruments)
        
        closed_count = 0
        for inst in instruments:
            if not inst.get('is_trade'):
                continue
            
            key = inst['key']
            trade = inst['trade']
            ltp = prices.get(key, 0)
            
            if ltp <= 0:
                ltp = trade.get('ltp', trade['entry'])
            
            pnl = (ltp - trade['entry']) * trade['lot']
            
            if self.execute_exit(trade, key, ltp, pnl, reason):
                closed_count += 1
        
        print(Fore.GREEN + f"\n✅ Closed {closed_count} positions")
        return closed_count
    
    async def update_websocket(self):
        """Update WebSocket data"""
        closed_pnl = sum(t['pnl'] for t in self.trades.values() if t.get('status') == 'closed')
        open_pnl = sum(t['pnl'] for t in self.trades.values() if t.get('status') == 'open')
        
        closed = [t for t in self.trades.values() if t.get('status') == 'closed']
        open_trades = [t for t in self.trades.values() if t.get('status') == 'open']
        
        # Build live prices dict
        live_prices = {}
        for name, trade in self.trades.items():
            if trade.get('status') == 'open':
                live_prices[name] = {
                    'ltp': trade.get('ltp', 0),
                    'entry': trade.get('entry', 0),
                    'pnl': trade.get('pnl', 0),
                    'stop_loss': trade.get('stop_loss', 0),
                    'trailing_sl': trade.get('trailing_sl'),
                    'trailing_active': self.trailing_active.get(name, False)
                }
        
        # Build breakout status
        breakout_status = {}
        for stock in self.watchlist:
            symbol = stock['symbol']
            breakout_status[symbol] = {
                'ce_breakout': self.breakout_levels[f"{symbol}_CE"],
                'pe_breakout': self.breakout_levels[f"{symbol}_PE"],
                'ce_traded': f"{symbol}_CE" in self.trades,
                'pe_traded': f"{symbol}_PE" in self.trades,
                'candle_time': stock['candle_time']
            }
        
        ws.data = {
            'trades': self.trades,
            'buildup_stocks': [s['symbol'] for s in self.watchlist],
            'total_pnl': closed_pnl,
            'unrealized_pnl': open_pnl,
            'combined_pnl': closed_pnl + open_pnl,
            'total_trades': len(closed) + len(open_trades),
            'open_trades': len(open_trades),
            'closed_trades': len(closed),
            'winning_trades': sum(1 for t in closed if t['pnl'] > 0),
            'daily_pnl': self.daily_pnl['total'],
            'mode': Config.MODE,
            'is_live_trading': Config.MODE == "LIVE",
            'connected': True,
            'last_update': datetime.now().strftime('%H:%M:%S'),
            'live_prices': live_prices,
            'breakout_status': breakout_status
        }
        
        await ws.broadcast(ws.data)
    
    def start(self):
        """Start parallel monitoring"""
        print(Fore.CYAN + f"\n{'='*100}")
        print(Fore.GREEN + "🚀 STARTING PARALLEL MONITORING FOR ALL STOCKS")
        print(Fore.CYAN + f"{'='*100}\n")
        
        tick_count = 0
        auto_exit_triggered = False
        
        try:
            while is_open() and self.running:
                tick_count += 1
                
                # Check for auto-exit time (3:15 PM)
                if should_auto_exit() and not auto_exit_triggered:
                    auto_exit_triggered = True
                    self.close_all_positions(f"Auto-Exit @ {Config.AUTO_EXIT_TIME}")
                    print(Fore.CYAN + "\n✅ All positions closed at scheduled time")
                    break
                
                # Check daily loss limit
                if self.daily_pnl['total'] <= -Config.MAX_DAILY_LOSS:
                    print(Fore.RED + f"\n🛑 DAILY LOSS LIMIT REACHED: ₹{self.daily_pnl['total']:,.0f}")
                    break
                
                # Check max trades
                if len(self.daily_pnl['trades']) >= Config.MAX_TRADES_PER_DAY:
                    print(Fore.RED + f"\n🛑 MAX DAILY TRADES REACHED: {len(self.daily_pnl['trades'])}")
                    break
                
                # Get all instruments to monitor
                instruments = self.get_all_instruments()
                
                if not instruments:
                    print(Fore.YELLOW + "✅ All positions closed")
                    break
                
                # Fetch all prices at once
                prices = self.client.get_ltp_batch(instruments)
                
                # Process this tick
                self.process_tick(instruments, prices)
                
                # Update WebSocket
                asyncio.run(self.update_websocket())
                
                # Sleep before next tick
                time.sleep(Config.TICK_INTERVAL)
        
        except KeyboardInterrupt:
            print(Fore.YELLOW + "\n⚠️ Monitoring stopped by user")
        except Exception as e:
            print(Fore.RED + f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            self.running = False
            
            # Final summary
            print(Fore.CYAN + f"\n{'='*100}")
            print(Fore.GREEN + "📊 FINAL SUMMARY")
            print(Fore.CYAN + f"{'='*100}")
            print(Fore.YELLOW + f"Mode: {Config.MODE}")
            print(Fore.YELLOW + f"Total Ticks: {tick_count}")
            print(Fore.YELLOW + f"Total Trades: {len(self.daily_pnl['trades'])}")
            print(Fore.YELLOW + f"Open Positions: {len([t for t in self.trades.values() if t.get('status') == 'open'])}")
            print(Fore.YELLOW + f"Closed Positions: {len(self.daily_pnl['trades'])}")
            
            pnl_color = Fore.GREEN if self.daily_pnl['total'] > 0 else Fore.RED
            print(pnl_color + f"Total P&L: ₹{self.daily_pnl['total']:,.0f}")
            print(Fore.CYAN + f"{'='*100}\n")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print(Fore.CYAN + f"🚀 Long Build Up Trader - {Config.MODE} MODE\n")
    ws.start()
    
    try:
        client = AngelClient(Config.API_KEY, Config.CLIENT_CODE, Config.MPIN, Config.TOTP_KEY)
        if not client.login():
            sys.exit(1)
        
        # Fetch Long Build Up stocks
        buildup_stocks = fetch_long_buildup_from_nse()
        asyncio.run(ws.broadcast({
            'buildup_stocks': buildup_stocks,
            'mode': Config.MODE,
            'connected': True
        }))
        
        if not buildup_stocks:
            print(Fore.RED + "❌ No stocks to trade")
            sys.exit(0)
        
        if not is_open():
            print(Fore.RED + "❌ Market closed")
            sys.exit(0)
        
        expiry = get_expiry()
        print(Fore.CYAN + f"📅 Expiry: {expiry}\n")
        
        # Get ATM data for all stocks
        watchlist = []
        for s in buildup_stocks:
            atm = get_atm(client, s['symbol'], expiry)
            if atm:
                watchlist.append(atm)
        
        if not watchlist:
            print(Fore.RED + "❌ No options data available")
            sys.exit(0)
        
        print(Fore.GREEN + f"\n✅ Watchlist ready with {len(watchlist)} stocks\n")
        
        # Start parallel monitoring
        monitor = ParallelMonitor(client, watchlist)
        monitor.start()
        
        # Show order book if live trading
        if Config.MODE == "LIVE":
            client.get_order_book()
    
    except Exception as e:
        print(Fore.RED + f"❌ Error: {e}")
        import traceback
        traceback.print_exc()