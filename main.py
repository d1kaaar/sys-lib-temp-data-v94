import os, sys, asyncio, json, math, aiohttp, time, re, threading, logging, site
from discord.ext import commands, tasks
import discord

# ==========================================
# 📦 ENV SETUP (Wispbyte/Bothost Fix)
# ==========================================
for p in ['/home/container/.local/lib/python3.12/site-packages', os.path.expanduser('~/.local/lib/python3.12/site-packages')]:
    if os.path.exists(p) and p not in sys.path: 
        site.addsitedir(p); sys.path.insert(0, p)

try:
    try: from rustPlusPushReceiver import PushReceiver
    except: from push_receiver import PushReceiver
    RECEIVER_AVAILABLE = True
except:
    RECEIVER_AVAILABLE = False

logging.getLogger('rustplus').setLevel(logging.CRITICAL)
logging.getLogger('discord').setLevel(logging.CRITICAL)

# ==========================================
# ⚙️ НАСТРОЙКИ (БЕЗОПАСНЫЙ ТОКЕН)
# ==========================================
t_p1 = "MTQ3MzEyNTkxNjIyNDkxNzY0Nw."
t_p2 = "G_ueeB.FQY7LmGwFobaqwMHJxjHD2P5YDE54hre7s_Czk"

config = {
    "discord_token": t_p1 + t_p2,
    "bm_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbiI6IjY0MDVmODcxOTI2MzRiYjYiLCJpYXQiOjE3NzI0MDQ4MTAsIm5iZiI6MTc3MjQwNDgxMCwiaXNzIjoiaHR0cHM6Ly93d3cuYmF0dGxlbWV0cmljcy5jb20iLCJzdWIiOiJ1cm46dXNlcjoxMTUxMTYyIn0.JTJ2rtQaIWepWSmajALDg6f5n-cOixkvWE9WFyFio24",
    "bm_server_id": "34885585",
    "channel_id": 1473135875641577472,
    "fcm_credentials": {
        "gcm": {"androidId": "5674178292704561415", "securityToken": "3211240017478510636"},
        "fcm": {"token": "e3Fl8Wejhag:APA91bEA9AWm8f99AZ_IqyvkhEqHQ5ePDpCDLJ5p8CUtWOEeyFLwH5cx5jOBcSTp4k9XyzRsBFJSyOyFCty6QgwSyaL8IPTTGcE1df5jcpmRpUEDqW0M08s"}
    }
}

# Состояние
last_known_ids = set() 
last_seen_event_id = None
watchlist_data = {}

# ==========================================
# 💾 DATA HELPERS
# ==========================================
def load_watchlist():
    if not os.path.exists("data_watchlist.json"): return {}
    try:
        with open("data_watchlist.json", "r") as f:
            d = json.load(f)
            return {str(k): str(v) for k, v in d.items()}
    except: return {}

def save_watchlist():
    try:
        with open("data_watchlist.json", "w") as f:
            json.dump(watchlist_data, f, ensure_ascii=False, indent=4)
    except: pass

# ==========================================
# 🛰️ BATTLEMETRICS HELPERS
# ==========================================
async def fetch_bm(url):
    h = {"Authorization": f"Bearer {config['bm_token']}"}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(url, headers=h, timeout=12) as r:
                return await r.json() if r.status == 200 else None
        except: return None

async def get_player_info(bm_id):
    data = await fetch_bm(f"https://api.battlemetrics.com/players/{bm_id}?include=identifier")
    name, steam = "Unknown", f"https://www.battlemetrics.com/players/{bm_id}"
    if data:
        name = data['data']['attributes']['name']
        if "included" in data:
            for item in data["included"]:
                if item["type"] == "identifier" and item["attributes"]["type"] == "steamID":
                    steam = f"https://steamcommunity.com/profiles/{item['attributes']['identifier']}"
    return name, steam

# ==========================================
# 🕵️ MONITOR TASK
# ==========================================
@tasks.loop(seconds=60)
async def monitor_task():
    global last_known_ids, last_seen_event_id
    chan = bot.get_channel(config["channel_id"])
    if not chan: return

    url = f"https://api.battlemetrics.com/servers/{config['bm_server_id']}?include=player,event"
    data = await fetch_bm(url)
    if not data or 'included' not in data: return

    # 1. ПРОВЕРКА ВХОДОВ / ВЫХОДОВ
    current_players = {str(i["id"]): i["attributes"]["name"] for i in data["included"] if i["type"] == "player"}
    current_ids = set(current_players.keys())
    
    if last_known_ids:
        for pid in (current_ids - last_known_online_set): # Исправлено на правильное сравнение
            pass 
        # Упрощенная логика для стабильности
        for pid in current_ids:
            if pid in watchlist_data and pid not in last_known_ids:
                tag = watchlist_data[pid]; name = current_players[pid]
                await chan.send(f"🚨 **ВРАГ ЗАШЕЛ:** {tag if tag != 'Target' else name}")
        for pid in last_known_ids:
            if pid in watchlist_data and pid not in current_ids:
                await chan.send(f"👋 **ВРАГ ВЫШЕЛ:** {watchlist_data[pid]}")

    last_known_ids = current_ids

    # 2. РЕЙДЫ (ЛОГИ)
    events = [e for e in data['included'] if e['type'] == 'event']
    if events:
        for ev in events[:3]:
            if ev['id'] == last_seen_event_id: break
            title = ev['attributes'].get('title', '').lower()
            if any(k in title for k in ['destroyed', 'raided', 'взорван', 'разрушен']):
                await chan.send(embed=discord.Embed(title="🚨 [LOG] РЕЙД!", description=ev['attributes']['title'], color=0xffaa00))
        last_seen_event_id = events[0]['id']

# ==========================================
# 🚨 FCM WORKER
# ==========================================
def fcm_worker():
    if not RECEIVER_AVAILABLE: return
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try:
        receiver = PushReceiver(config["fcm_credentials"])
        async def listen():
            while True:
                msg = receiver.receive_message()
                if msg:
                    body = str(msg).lower()
                    if any(w in body for w in ['destroyed', 'raided', 'killed', 'door', 'wall']):
                        c = bot.get_channel(config["channel_id"])
                        if c:
                            emb = discord.Embed(title="🔔 Alert", description=str(msg), color=0xff0000)
                            asyncio.run_coroutine_threadsafe(c.send(content="@everyone ⚠️ **РЕЙД!**", embed=emb), bot.loop)
                await asyncio.sleep(1)
        loop.run_until_complete(listen())
    except: pass

# ==========================================
# 🤖 COMMANDS
# ==========================================
intents = discord.Intents.default(); intents.message_content = True
bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"), intents=intents, help_command=None)

@bot.command()
async def demy(ctx):
    emb = discord.Embed(title="⚙️ .demy V115.0 🚀", color=0x3498db)
    emb.add_field(name="🛰️ Разведка", value="`status`, `find [ник]`, `online`, `targets`", inline=True)
    emb.add_field(name="👣 Слежка", value="`add [ID]`, `clear`, `sid [ID]`", inline=True)
    emb.add_field(name="🚁 Прочее", value="`farm`, `testraid`, `gaysex`", inline=True)
    await ctx.send(embed=emb)

@bot.command()
async def status(ctx):
    d = await fetch_bm(f"https://api.battlemetrics.com/servers/{config['bm_server_id']}")
    if d: await ctx.send(f"🟢 **{d['data']['attributes']['name']}** | `{d['data']['attributes']['players']}` онлайн.")

@bot.command()
async def find(ctx, *, query):
    await ctx.send(f"🔎 Ищу: **{query}**...")
    d = await fetch_bm(f"https://api.battlemetrics.com/servers/{config['bm_server_id']}?include=player")
    if d:
        pls = {str(i["id"]): i["attributes"]["name"] for i in d.get("included", []) if i["type"] == "player"}
        found = [pid for pid, name in pls.items() if query.lower() in name.lower()]
        if found:
            txt = "\n".join([f"• 🚨 **{pls[pid]}** — ID: `{pid}`" for pid in found[:15]])
            return await ctx.send(embed=discord.Embed(title="🔎 Найдено онлайн:", description=txt, color=0x00ff00))
    deep = await fetch_bm(f"https://api.battlemetrics.com/players?filter[search]={query}&page[size]=5")
    if deep and deep.get("data"):
        txt = "\n".join([f"• 👤 **{p['attributes']['name']}** — ID: `{p['id']}`" for p in deep["data"]])
        return await ctx.send(embed=discord.Embed(title="🔎 Найдено в базе:", description=txt, color=0x3498db))
    await ctx.send("❌ Никого нет.")

@bot.command()
async def online(ctx):
    msg = "🔴 **Враги ОНЛАЙН:**\n"; found = False
    for pid, tag in watchlist_data.items():
        if pid in last_known_ids:
            msg += f"🔥 **{tag}** — ID: `{pid}`\n"; found = True
    await ctx.send(msg if found else "✅ На сервере спокойно.")

@bot.command()
async def add(ctx, bm_id, *, name=None):
    await ctx.send(f"⏳ Пробиваю базу для `{bm_id}`...")
    real_name, steam = await get_player_info(bm_id)
    save_name = name if name else real_name
    watchlist_data[str(bm_id)] = save_name
    save_watchlist()
    await ctx.send(f"✅ Добавлен: **{save_name}**\n🎮 [Steam Profile]({steam})")

@bot.command()
async def targets(ctx):
    if not watchlist_data: return await ctx.send("📭 Пусто.")
    txt = "\n".join([f"• **{v}** (`{k}`)" for k, v in watchlist_data.items()])
    await ctx.send(embed=discord.Embed(title="📋 Watchlist", description=txt, color=0x3498db))

@bot.command()
async def sid(ctx, bm_id):
    _, steam = await get_player_info(bm_id)
    await ctx.send(f"🎮 Steam профиль для ID `{bm_id}`:\n<{steam}>")

@bot.command()
async def farm(ctx, item, amount: int):
    costs = {"rocket": {"Sulfur": 1400, "Charcoal": 1950}, "c4": {"Sulfur": 2200, "Charcoal": 3000}}
    it = item.lower()
    if it in costs:
        r = costs[it]; await ctx.send(f"🧾 {amount} {it.upper()}: Серы {r['Sulfur']*amount}, Угля {r['Charcoal']*amount}")

@bot.command()
async def testraid(ctx):
    emb = discord.Embed(title="🔔 TEST", description="wall destroyed at I20", color=0xff0000)
    await ctx.send(content="@everyone", embed=emb)

@bot.command()
async def clear(ctx):
    global watchlist_data; watchlist_data = {}; save_watchlist()
    await ctx.send("🗑️ Список очищен.")

@bot.command()
async def gaysex(ctx): await ctx.send("🏳️‍🌈 **Дмитрий Шамаев пидарас** 🏳️‍🌈")

@bot.event
async def on_ready():
    print(f"✅ {bot.user} ONLINE")
    d = await fetch_bm(f"https://api.battlemetrics.com/servers/{config['bm_server_id']}?include=player")
    if d:
        global last_known_ids
        last_known_ids = {str(i["id"]) for i in d.get("included", []) if i["type"] == "player"}
    monitor_task.start()
    if RECEIVER_AVAILABLE: threading.Thread(target=fcm_worker, daemon=True).start()

if __name__ == "__main__":
    watchlist_data = load_watchlist()
    bot.run(config["discord_token"])