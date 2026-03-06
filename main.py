import os
import sys
import asyncio
import json
import math
import aiohttp
import time
import re
import threading

# ==========================================
# 📦 УМНЫЙ ИМПОРТ
# ==========================================
try:
    from rustPlusPushReceiver import PushReceiver
except ImportError:
    try:
        from push_receiver import PushReceiver
    except ImportError:
        print("❌ ОШИБКА: Библиотека уведомлений не найдена!")
        sys.exit(1)

import discord
from discord.ext import commands, tasks
from rustplus import RustSocket, ServerDetails

# ==========================================
# ⚙️ ИНИЦИАЛИЗАЦИЯ
# ==========================================

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

rust_socket = RustSocket(server_details=ServerDetails(
    config["rust_server"]["ip"], config["rust_server"]["port"],
    int(config["rust_server"]["player_id"]), int(config["rust_server"]["player_token"])
))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Переменные состояния
MAP_SIZE_VAL = 4500
active_map_events = {} 
pending_heli_alerts = {} 
last_known_online = {} 

FILES = {"watchlist": "data_watchlist.json"}

def load_watchlist():
    if not os.path.exists(FILES["watchlist"]): return {}
    try:
        with open(FILES["watchlist"], "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list): return {str(i): "Target" for i in data}
            return {str(k): str(v) for k, v in data.items()}
    except: return {}

data_store = {"watchlist": load_watchlist()}

# ==========================================
# 🛰️ HELPERS
# ==========================================

async def fetch_bm(url):
    headers = {"Authorization": f"Bearer {config['bm_token']}"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200: return await resp.json()
                return None
        except: return None

async def get_steam_info(bm_id):
    data = await fetch_bm(f"https://api.battlemetrics.com/players/{bm_id}?include=identifier")
    if data and "included" in data:
        for item in data["included"]:
            if item["type"] == "identifier" and item["attributes"]["type"] == "steamID":
                return f"https://steamcommunity.com/profiles/{item['attributes']['identifier']}"
    return "Скрыт"

# ==========================================
# 🕵️ TASKS
# ==========================================

@tasks.loop(minutes=2)
async def watchlist_task():
    global last_known_online
    channel = bot.get_channel(int(config["channel_id"]))
    data = await fetch_bm(f"https://api.battlemetrics.com/servers/{config['bm_server_id']}?include=player")
    if not data or not channel: return

    online_now = {str(item["id"]): item["attributes"]["name"] for item in data.get("included", []) if item["type"] == "player"}
    
    if last_known_online:
        for pid, name in online_now.items():
            if pid in data_store["watchlist"] and pid not in last_known_online:
                tag = data_store["watchlist"].get(pid)
                display = name if tag == "Target" else f"{tag} ({name})"
                steam = await get_steam_info(pid)
                await channel.send(f"🚨 **ВРАГ ЗАШЕЛ:** {display}\n🎮 Steam: <{steam}>")
        
        for pid, name in last_known_online.items():
            if pid in data_store["watchlist"] and pid not in online_now:
                tag = data_store["watchlist"].get(pid)
                display = name if tag == "Target" else f"{tag} ({name})"
                await channel.send(f"👋 **ВРАГ ВЫШЕЛ:** {display}")

    last_known_online = online_now

@tasks.loop(seconds=15)
async def map_task():
    global MAP_SIZE_VAL
    channel = bot.get_channel(int(config["channel_id"]))
    if not rust_socket.ws or not channel: return
    try:
        markers = await rust_socket.get_markers()
        m_ids = [m.id for m in markers]
        for m in markers:
            if m.type not in [5, 6] or m.id in active_map_events: continue
            if m.type == 5:
                if m.id not in pending_heli_alerts:
                    pending_heli_alerts[m.id] = {"x": m.x, "y": m.y}; continue
                prev = pending_heli_alerts[m.id]
                if math.sqrt((prev["x"]-m.x)**2 + (prev["y"]-m.y)**2) < 50: continue
                loc = f"{chr(ord('A') + int(m.x // 146.3))}{int((MAP_SIZE_VAL - m.y) // 146.3)}"
                await channel.send(f"🚁 **ВЕРТОЛЕТ** 📍 **{loc}**")
                active_map_events[m.id] = True
            elif m.type == 6:
                dist = math.sqrt((m.x - MAP_SIZE_VAL/2)**2 + (m.y - MAP_SIZE_VAL/2)**2)
                if dist > (MAP_SIZE_VAL/2) * 0.7:
                    loc = "БОЛЬШАЯ НЕФТЯНКА" if m.y > MAP_SIZE_VAL/2 else "МАЛАЯ НЕФТЯНКА"
                    await channel.send(f"📦 **ЯЩИК НА ВЫШКЕ** 📍 **{loc}**")
                    active_map_events[m.id] = True
        for eid in list(active_map_events.keys()):
            if eid not in m_ids: del active_map_events[eid]
    except: pass

async def run_fcm_listener():
    """Слушатель уведомлений (Самый надежный метод для Windows)"""
    if "fcm_credentials" not in config: return
    def fcm_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Прямая итерация без .connect()
            receiver = PushReceiver(config["fcm_credentials"])
            async def listen():
                print("🚀 [FCM] Слушатель уведомлений запущен.")
                async for msg in receiver:
                    chan = bot.get_channel(int(config["channel_id"]))
                    if not chan: continue
                    body = getattr(msg, 'message', getattr(msg, 'body', '')).lower()
                    title = getattr(msg, 'title', 'Rust Alert')
                    if any(w in body for w in ['destroyed', 'raided', 'killed', 'door', 'wall', 'frame']):
                        emb = discord.Embed(title=f"🔔 {title}", description=body, color=0xff0000)
                        asyncio.run_coroutine_threadsafe(chan.send(content="@everyone ⚠️ **РЕЙД!**", embed=emb), bot.loop)
            loop.run_until_complete(listen())
        except Exception as e: print(f"FCM Error: {e}")
    threading.Thread(target=fcm_thread, daemon=True).start()

# ==========================================
# 🤖 COMMANDS
# ==========================================

@bot.command()
async def demy(ctx):
    emb = discord.Embed(title="⚙️ .demy V98.3", color=0x3498db)
    emb.add_field(name="🛰️ Разведка", value="`!find [Ник/Тег/SteamID]`\n`!online` - Враги онлайн", inline=False)
    emb.add_field(name="👣 Слежка", value="`!add [ID] [Имя]`\n`!targets` - Список", inline=False)
    emb.add_field(name="🚁 Карта", value="`!status` - Инфо\n`!testraid` - Тест", inline=False)
    await ctx.send(embed=emb)

@bot.command()
async def find(ctx, *, query):
    await ctx.send(f"🔎 Поиск: **{query}**...")
    data = await fetch_bm(f"https://api.battlemetrics.com/servers/{config['bm_server_id']}?include=player")
    if not data: return
    players = {str(item["id"]): item["attributes"]["name"] for item in data.get("included", []) if item["type"] == "player"}
    if query.isdigit() and len(query) == 17:
        data_id = await fetch_bm(f"https://api.battlemetrics.com/players?filter[identifiers]={query}&page[size]=1")
        if data_id and data_id.get("data"):
            p = data_id["data"][0]; pid = str(p["id"])
            st = "🚨 В ИГРЕ" if pid in players else "❌ ОФФЛАЙН"
            await ctx.send(f"👤 **{p['attributes']['name']}** (ID: `{pid}`)\n🌐 {st}\n🔗 [BM](https://www.battlemetrics.com/players/{pid})")
            return
    found = [pid for pid, name in players.items() if query.lower() in name.lower()]
    if not found: await ctx.send("❌ Никого не нашел."); return
    txt = "\n".join([f"• **{players[pid]}** — ID: `{pid}`" for pid in found[:25]])
    await ctx.send(embed=discord.Embed(title=f"🔎 Найдено {len(found)}", description=txt, color=0x3498db))

@bot.command()
async def online(ctx):
    msg = "🔴 **Враги ОНЛАЙН:**\n"
    found = False
    for pid, tag in data_store["watchlist"].items():
        if pid in last_known_online:
            name = last_known_online[pid]
            display = tag if tag != "Target" else name
            msg += f"🔥 **{display}** — ID: `{pid}`\n"
            found = True
    await ctx.send(msg if found else "✅ На сервере спокойно.")

@bot.command()
async def add(ctx, bm_id, *, name="Target"):
    data_store["watchlist"][str(bm_id)] = name
    with open(FILES["watchlist"], "w", encoding="utf-8") as f: json.dump(data_store["watchlist"], f)
    await ctx.send(f"✅ Добавлен: **{name}**")

@bot.command()
async def testraid(ctx):
    emb = discord.Embed(title="🔔 You're getting raided!", description="wall destroyed at I20", color=0xff0000)
    await ctx.send(content="@everyone ⚠️ **ТЕСТ!**", embed=emb)

@bot.command()
async def gaysex(ctx): await ctx.send("🏳️‍🌈 **Дмитрий Шамаев пидарас** 🏳️‍🌈")

@bot.command()
async def status(ctx):
    if not rust_socket.ws: await ctx.send("❌ Rust+ не подключен"); return
    try:
        info = await rust_socket.get_info()
        await ctx.send(f"🗺️ **{info.name}**\n👥 {info.players}/{info.max_players}")
    except: await ctx.send("❌ Ошибка получения данных")

@bot.event
async def on_ready():
    print(f"✅ {bot.user} ONLINE")
    
    async def connect_rust():
        await asyncio.sleep(5) # Задержка для обхода защиты Facepunch
        try:
            await rust_socket.connect()
            print("✅ Rust+ Connected")
            info = await rust_socket.get_info()
            global MAP_SIZE_VAL
            MAP_SIZE_VAL = info.size
        except Exception as e: print(f"⚠️ Rust+ Error: {e}")

    asyncio.create_task(connect_rust())
    asyncio.create_task(run_fcm_listener())
    watchlist_task.start()
    map_task.start()

if __name__ == "__main__":
    bot.run(config["discord_token"])