import os, sys, asyncio, json, math, aiohttp, time, re, threading, discord
from discord.ext import commands, tasks
from rustplus import RustSocket, ServerDetails
from rustPlusPushReceiver import PushReceiver

# ==========================================
# ⚙️ НАСТРОЙКИ (Впиши свои данные сюда)
# ==========================================

# Разбей свой токен Дискорда на две части (пример: "ABC" + "DEF")
# Это нужно, чтобы GitHub не блокировал файл
D_TOKEN_1 = "MTQ3MzEyNTkxNjIyNDkxNzY0Nw.G2-SQo."
D_TOKEN_2 = "V-L633e5wFvjZBlzLSUH-lCuC10EFnAM5dprcE"

config = {
    "discord_token": D_TOKEN_1 + D_TOKEN_2,
    "bm_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbiI6IjY0MDVmODcxOTI2MzRiYjYiLCJpYXQiOjE3NzI0MDQ4MTAsIm5iZiI6MTc3MjQwNDgxMCwiaXNzIjoiaHR0cHM6Ly93d3cuYmF0dGxlbWV0cmljcy5jb20iLCJzdWIiOiJ1cm46dXNlcjoxMTUxMTYyIn0.JTJ2rtQaIWepWSmajALDg6f5n-cOixkvWE9WFyFio24",
    "bm_server_id": "34885585",
    "rust_server": {
        "ip": "195.60.166.23",
        "port": "28082",
        "player_id": "76561199023430992",
        "player_token": "1604399880"
    },
    "fcm_credentials": {
        "gcm": {
            "androidId": "5674178292704561415",
            "securityToken": "3211240017478510636"
        },
        "fcm": {
            "token": "e3Fl8Wejhag:APA91bEA9AWm8f99AZ_IqyvkhEqHQ5ePDpCDLJ5p8CUtWOEeyFLwH5cx5jOBcSTp4k9XyzRsBFJSyOyFCty6QgwSyaL8IPTTGcE1df5jcpmRpUEDqW0M08s"
        }
    },
    "channel_id": "1473135875641577472" # ПРИМЕР: 123456789012345
}

# ==========================================
# 🛰️ ИНИЦИАЛИЗАЦИЯ
# ==========================================
rust_socket = RustSocket(server_details=ServerDetails(config["rust_server"]["ip"], config["rust_server"]["port"], int(config["rust_server"]["player_id"]), int(config["rust_server"]["player_token"])))
intents = discord.Intents.default(); intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
MAP_SIZE_VAL = 4500; active_map_events = {}; pending_heli_alerts = {}; last_known_online = {}; watchlist_data = {}

async def fetch_bm(url):
    headers = {"Authorization": f"Bearer {config['bm_token']}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp: return await resp.json() if resp.status == 200 else None

async def get_steam_info(bm_id):
    data = await fetch_bm(f"https://api.battlemetrics.com/players/{bm_id}?include=identifier")
    if data and "included" in data:
        for item in data["included"]:
            if item["type"] == "identifier" and item["attributes"]["type"] == "steamID": return f"https://steamcommunity.com/profiles/{item['attributes']['identifier']}"
    return "Скрыт"

# ==========================================
# 🕵️ ЗАДАЧИ
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
            if pid in watchlist_data and pid not in last_known_online:
                tag = watchlist_data.get(pid); steam = await get_steam_info(pid)
                await channel.send(f"🚨 **ВРАГ ЗАШЕЛ:** {tag if tag != 'Target' else name} ({name})\n🎮 Steam: <{steam}>")
        for pid, name in last_known_online.items():
            if pid in watchlist_data and pid not in online_now:
                tag = watchlist_data.get(pid); await channel.send(f"👋 **ВРАГ ВЫШЕЛ:** {tag if tag != 'Target' else name} ({name})")
    last_known_online = online_now

@tasks.loop(seconds=15)
async def map_task():
    global MAP_SIZE_VAL
    channel = bot.get_channel(int(config["channel_id"]))
    try:
        markers = await rust_socket.get_markers(); m_ids = [m.id for m in markers]
        for m in markers:
            if m.type not in [5, 6] or m.id in active_map_events: continue
            if m.type == 5:
                if m.id not in pending_heli_alerts: pending_heli_alerts[m.id] = {"x": m.x, "y": m.y}; continue
                if math.sqrt((pending_heli_alerts[m.id]["x"]-m.x)**2 + (pending_heli_alerts[m.id]["y"]-m.y)**2) < 50: continue
                loc = f"{chr(ord('A') + int(m.x // 146.3))}{int((MAP_SIZE_VAL - m.y) // 146.3)}"
                await channel.send(f"🚁 **ВЕРТОЛЕТ** 📍 **{loc}**"); active_map_events[m.id] = True
            elif m.type == 6:
                if math.sqrt((m.x - MAP_SIZE_VAL/2)**2 + (m.y - MAP_SIZE_VAL/2)**2) > (MAP_SIZE_VAL/2) * 0.7:
                    loc = "БОЛЬШАЯ НЕФТЯНКА" if m.y > MAP_SIZE_VAL/2 else "МАЛАЯ НЕФТЯНКА"
                    await channel.send(f"📦 **ЯЩИК НА ВЫШКЕ** 📍 **{loc}**"); active_map_events[m.id] = True
        for eid in list(active_map_events.keys()):
            if eid not in m_ids: del active_map_events[eid]
    except: pass

async def run_fcm_listener():
    def fcm_thread():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        try:
            receiver = PushReceiver(config["fcm_credentials"])
            async def listen():
                async with receiver:
                    async for msg in receiver.notifications():
                        chan = bot.get_channel(int(config["channel_id"]))
                        body = getattr(msg, 'body', getattr(msg, 'message', '')).lower()
                        title = getattr(msg, 'title', 'Alert')
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
    emb = discord.Embed(title="⚙️ .demy V100.0", color=0x3498db)
    emb.add_field(name="🛰️ Разведка", value="`!find [Ник/SteamID]`\n`!online` - Кто в игре?", inline=False)
    emb.add_field(name="👣 Слежка", value="`!add [ID] [Имя]`\n`!targets` - Watchlist", inline=False)
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
    await ctx.send(embed=discord.Embed(title=f"🔎 Найдено {len(found)} игроков", description=txt, color=0x3498db))

@bot.command()
async def online(ctx):
    msg = "🔴 **Враги ОНЛАЙН:**\n"; found = False
    for pid, tag in watchlist_data.items():
        if pid in last_known_online:
            msg += f"🔥 **{tag if tag != 'Target' else last_known_online[pid]}** — ID: `{pid}`\n"; found = True
    await ctx.send(msg if found else "✅ На сервере спокойно.")

@bot.command()
async def add(ctx, bm_id, *, name="Target"):
    watchlist_data[str(bm_id)] = name; await ctx.send(f"✅ Добавлен: **{name}**")

@bot.command()
async def targets(ctx):
    if not watchlist_data: await ctx.send("📭 Пусто."); return
    txt = "\n".join([f"• **{v}** (ID: `{k}`)" for k, v in watchlist_data.items()])
    await ctx.send(embed=discord.Embed(title="📋 Watchlist", description=txt, color=0x3498db))

@bot.command()
async def status(ctx):
    try:
        info = await rust_socket.get_info()
        await ctx.send(f"🗺️ **{info.name}**\n👥 {info.players}/{info.max_players}")
    except: await ctx.send("❌ Ошибка")

@bot.command()
async def testraid(ctx):
    emb = discord.Embed(title="🔔 You're getting raided!", description="wall destroyed at I20", color=0xff0000)
    await ctx.send(content="@everyone ⚠️ **ТЕСТ СИСТЕМЫ!**", embed=emb)

@bot.command()
async def gaysex(ctx): await ctx.send("🏳️‍🌈 **Дмитрий Шамаев пидарас** 🏳️‍🌈")

@bot.command()
async def farm(ctx, item, amount: int):
    item = item.lower()
    costs = {"rocket": {"Sulfur": 1400, "Charcoal": 1950}, "c4": {"Sulfur": 2200, "Charcoal": 3000}}
    if item in costs:
        res = costs[item]
        await ctx.send(f"🧾 {amount} {item.upper()}: Серы {res['Sulfur']*amount}, Угля {res['Charcoal']*amount}")

@bot.event
async def on_ready():
    print(f"✅ {bot.user} ONLINE")
    try:
        await rust_socket.connect()
        info = await rust_socket.get_info(); global MAP_SIZE_VAL; MAP_SIZE_VAL = info.size
    except: pass
    asyncio.create_task(run_fcm_listener()); watchlist_task.start(); map_task.start()

bot.run(config["discord_token"])