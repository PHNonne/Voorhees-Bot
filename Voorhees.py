import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv
from gtts import gTTS
import os
import requests
import asyncio
import yt_dlp
import aiohttp
import google.genai as genai
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
import sys
import shutil

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    _MEIPASS = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _MEIPASS = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.join(_MEIPASS, "modules"))
import tts_piper

# Force PyInstaller to include PyNaCl for discord.py voice support
try:
    import nacl.secret
    import nacl.utils
except ImportError:
    pass

def find_executable(name: str, folder: str, legacy_path: str) -> str:
    """Look next to the bot first (portable), then the legacy path, then PATH."""
    exe = f"{name}.exe"
    candidates = [
        os.path.join(BASE_DIR, exe),
        os.path.join(BASE_DIR, folder, "bin", exe),
        os.path.join(BASE_DIR, folder, exe),
        os.path.join(os.path.dirname(BASE_DIR), folder, "bin", exe),
        os.path.join(os.path.dirname(BASE_DIR), folder, exe),
        legacy_path,
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return shutil.which(name) or name

FFMPEG_EXECUTABLE = find_executable("ffmpeg", "ffmpeg", r"C:\BotDiscord\ffmpeg\bin\ffmpeg.exe")
print(f"[FFMPEG] Using executable: {FFMPEG_EXECUTABLE}")

# yt-dlp executable
YTDLP_EXECUTABLE = find_executable("yt-dlp", "yt-dlp", r"C:\BotDiscord\yt-dlp\yt-dlp.exe")
print(f"[YTDLP] Using executable: {YTDLP_EXECUTABLE}")

# Music cache directory
MUSIC_CACHE_DIR = os.path.join(BASE_DIR, "music_cache")
os.makedirs(MUSIC_CACHE_DIR, exist_ok=True)
print(f"[MUSIC CACHE] Directory: {MUSIC_CACHE_DIR}")

load_dotenv(os.path.join(BASE_DIR, ".env"))
BLACKLIST_FILE = os.path.join(BASE_DIR, "blacklist_words.txt")
STEAM_LINKS_FILE = os.path.join(BASE_DIR, "steam_links.json")
INACTIVE_CHECK_FILE = os.path.join(BASE_DIR, "inactive_check.json")
USER_ACTIVITY_FILE = os.path.join(BASE_DIR, "user_activity.json")
REMOVED_REACTIONS_FILE = os.path.join(BASE_DIR, "removed_reactions.json")
BOT_CHANNEL_STATE_FILE = os.path.join(BASE_DIR, "bot_channel_state.json")  # Voice-restricted state
BOT_CHANNEL_ORIGINAL_STATE_FILE = os.path.join(BASE_DIR, "bot_channel_original_state.json")  # Original state
VOICE_SETTINGS_FILE = os.path.join(BASE_DIR, "voice_settings.json")  # Voice settings for talkgoogle/talkvoice

auto_disconnect_tasks: dict[int, asyncio.Task] = {}
dm_sessions: dict[int, dict] = {}
processed_ai_message_ids: set[int] = set()
processed_dm_message_ids: set[int] = set()
MAX_PROCESSED_MESSAGE_IDS = 1000

WELCOME_CHANNEL_ID = 1407054815611326464  # kênh chào mừng
GOODBYE_CHANNEL_ID = 1454875824128790558  # kênh tạm biệt
kicked_banned_users = set()

# ================== JAIL SYSTEM CONSTANTS ==================
JAIL_ROLE_ID = 1452981971905937460  # Role tù nhân
JAIL_CHANNEL_ID = 1530809091385987163  # Kênh chat nhà tù
JAIL_ADMIN_ROLE_ID = 1407045564964536500  # Role admin được phép dùng lệnh jail/unjail
JAIL_STATE_FILE = os.path.join(BASE_DIR, "jail_state.json")  # File lưu trạng thái tù nhân
JAIL_SPAM_DELAY = 15.0  # Delay (giây) giữa các lần spam keyword hợp lệ

# ================== SPAM FILTER CONSTANTS ==================
DECLARE_MESSAGE_ID = 1465050575589605407  # Tin nhắn khai báo role
DECLARE_CHANNEL_ID = 1465050026215477428  # Kênh khai báo role
SPAM_FILTER_CHANNEL_ID = 1536384115769614446  # Kênh lọc spam (cấm chat)
WARNING_CHANNEL_ID = 1536384115769614446  # Kênh cảnh báo spam
AFFECTED_TEXT_CHANNELS = [1530019951031877783, 1454830895453442089, 1459632136201175398, 1399701361469165669]  # Các kênh text bị dính spam
AFFECTED_VOICE_CHANNELS = [1530147183670595634, 1452964269334200330, 1399701317080846390]  # Các kênh voice bị dính spam
WARNING_MESSAGE_FILE = os.path.join(BASE_DIR, "warning_message_state.json")  # File lưu trạng thái tin nhắn cảnh báo

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID=1399701316573597797
AI_ADMIN_ID = 925391116491165736  # ID duy nhất được phép dùng các lệnh thủ công/admin

class MyClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)


    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

client = MyClient()

async def setup_spam_warning_message(guild):
    """Thiết lập tin nhắn cảnh báo spam - chỉ gửi 1 lần duy nhất"""
    try:
        warning_channel = guild.get_channel(WARNING_CHANNEL_ID)
        if not warning_channel:
            print("[SPAM WARNING] Không tìm thấy kênh cảnh báo")
            return

        # Load trạng thái tin nhắn cảnh báo
        try:
            with open(WARNING_MESSAGE_FILE, "r", encoding="utf-8") as f:
                warning_state = json.load(f)
        except Exception:
            warning_state = {}

        # Kiểm tra xem tin nhắn cảnh báo đã tồn tại chưa
        if warning_state.get("message_id"):
            try:
                await warning_channel.fetch_message(warning_state["message_id"])
                print("[SPAM WARNING] Tin nhắn cảnh báo đã tồn tại, không cần gửi lại")
                return
            except discord.NotFound:
                print("[SPAM WARNING] Tin nhắn cảnh báo cũ đã bị xóa, sẽ gửi lại")
            except Exception as e:
                print(f"[SPAM WARNING] Lỗi kiểm tra tin nhắn cũ: {e}")

        # Gửi tin nhắn cảnh báo mới
        warning_msg = await warning_channel.send(
            "⚠️ **CẢNH BÁO SPAM** ⚠️\n\n"
            "Đừng gửi tin nhắn vào kênh này nếu không muốn bị mất hết role/áo!\n"
            "Kênh này được dùng để lọc spam, bất kỳ ai chat vào đây sẽ bị xóa role ngay lập tức."
        )

        # Lưu ID tin nhắn cảnh báo
        warning_state["message_id"] = warning_msg.id
        with open(WARNING_MESSAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(warning_state, f, indent=4)

        print("[SPAM WARNING] Đã gửi tin nhắn cảnh báo spam")
    except Exception as e:
        print(f"[SPAM WARNING] Lỗi thiết lập tin nhắn cảnh báo: {e}")

@client.event
async def on_ready():
    print(f"🤖 Bot đăng nhập thành công: {client.user}")

    # ================== 1️⃣ SETUP DECLARE MESSAGE ==================
    # Khởi động vòng lặp theo dõi Steam nickname
    if not track_steam_nicknames.is_running():
        track_steam_nicknames.start()

    # Khởi động vòng lặp kiểm tra trùng role
    if not check_duplicate_roles.is_running():
        check_duplicate_roles.start()

    # Khởi động vòng lặp cleanup music cache
    if not cleanup_music_cache.is_running():
        cleanup_music_cache.start()

    try:
        await setup_declare_message()
        await sync_declare_reactions()
    except Exception as e:
        print(f"[DECLARE ERROR] {e}")

    # ================== 2️⃣ AI CHANNEL LOGIC ==================
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("❌ Không tìm thấy guild")
        return

    # ================== 3️⃣ RESTORE BOT CHANNEL ORIGINAL STATE ==================
    try:
        # Chỉ khôi phục nếu file tồn tại (tức là bot tắt đột ngột khi đang trong voice)
        if os.path.exists(BOT_CHANNEL_ORIGINAL_STATE_FILE):
            await restore_original_bot_channel_state(guild)
            print("[BOT CHANNEL] Khôi phục kênh chat thành công sau khi restart")
    except Exception as e:
        print(f"[BOT CHANNEL] Error during original state restoration: {e}")

    state = load_ai_state()

    # Nếu chưa từng init → dùng kênh template
    if not state.get("current_channel_id"):
        state["current_channel_id"] = AI_TEMPLATE_CHANNEL_ID
        save_ai_state(state)

    # Nếu đến hạn reset → rotate kênh AI
    if need_weekly_reset(state.get("last_reset")):
        await rotate_ai_channel(guild)

    # Khởi chạy check user inactive
    await check_inactive_users(guild)

    # Khởi chạy welcome bù
    await check_missed_welcomes(guild)

    # ================== 4️⃣ SPAM FILTER WARNING MESSAGE ==================
    await setup_spam_warning_message(guild)

@client.event 
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    try:
        activity_data = load_user_activity()
        activity_data[str(message.author.id)] = datetime.utcnow().isoformat()
        save_user_activity(activity_data)
    except Exception as e:
        print(f"[ACTIVITY LOG] Error saving activity: {e}")

    if await handle_blacklisted_message(message):
        return

    if isinstance(message.channel, discord.DMChannel):
        await handle_dm_reply(message)
        return

    # Handle !voice and !google commands
    if message.channel.id == 1454830895453442089:
        content = message.content.strip()
        
        if content.startswith("!voice "):
            await handle_voice_command(message, content[7:])
            return
        elif content.startswith("!google "):
            await handle_google_command(message, content[8:])
            return

    # ================== SPAM FILTER ==================
    if message.channel.id == SPAM_FILTER_CHANNEL_ID:
        try:
            # Xóa reaction của người spam khỏi tin nhắn khai báo
            declare_channel = message.guild.get_channel(DECLARE_CHANNEL_ID)
            if declare_channel:
                try:
                    declare_msg = await declare_channel.fetch_message(DECLARE_MESSAGE_ID)
                    for reaction in declare_msg.reactions:
                        async for user in reaction.users():
                            if user.id == message.author.id:
                                try:
                                    await declare_msg.remove_reaction(reaction.emoji, message.author)
                                    print(f"[SPAM FILTER] Đã xóa reaction của {message.author.name} khỏi tin nhắn khai báo")
                                except Exception as e:
                                    print(f"[SPAM FILTER] Lỗi xóa reaction: {e}")
                except Exception as e:
                    print(f"[SPAM FILTER] Lỗi lấy tin nhắn khai báo: {e}")
            
            # Xóa tin nhắn spam của người đó từ các kênh bị ảnh hưởng
            all_affected_channels = AFFECTED_TEXT_CHANNELS + AFFECTED_VOICE_CHANNELS
            for channel_id in all_affected_channels:
                channel = message.guild.get_channel(channel_id)
                if channel:
                    try:
                        async for msg in channel.history(limit=100):
                            if msg.author.id == message.author.id:
                                try:
                                    await msg.delete()
                                    print(f"[SPAM FILTER] Đã xóa tin nhắn của {message.author.name} từ kênh {channel.name}")
                                except Exception:
                                    continue
                    except Exception as e:
                        print(f"[SPAM FILTER] Lỗi xóa tin nhắn từ kênh {channel_id}: {e}")
            
            # Xóa tin nhắn spam vừa gửi trong kênh lọc
            await message.delete()
            print(f"[SPAM FILTER] Đã xóa tin nhắn spam của {message.author.name} từ kênh lọc")
        except Exception as e:
            print(f"[SPAM FILTER] Lỗi xử lý spam: {e}")
        return

    # ================== JAIL SYSTEM KEYWORD DETECTION ==================
    if message.channel.id == JAIL_CHANNEL_ID:
        jail_state = load_jail_state()
        user_id_str = str(message.author.id)
        
        if user_id_str in jail_state:
            user_data = jail_state[user_id_str]
            keyword = user_data["keyword"]
            required_count = user_data["required_count"]
            current_count = user_data["current_count"]
            last_spam_time = user_data["last_spam_time"]
            
            current_time = time.time()
            time_since_last_spam = current_time - last_spam_time
            
            # Kiểm tra từ khóa
            if message.content.strip() == keyword:
                # Kiểm tra delay
                if time_since_last_spam >= JAIL_SPAM_DELAY:
                    # Spam hợp lệ
                    user_data["current_count"] = current_count + 1
                    user_data["last_spam_time"] = current_time
                    remaining = required_count - user_data["current_count"]
                    
                    if remaining <= 0:
                        # Đủ số lần spam - thả tù nhân
                        try:
                            # Khôi phục role cũ
                            old_roles = user_data["old_roles"]
                            for role_id in old_roles:
                                role = message.guild.get_role(role_id)
                                if role:
                                    await message.author.add_roles(role)
                            
                            # Xóa role tù nhân
                            jail_role = message.guild.get_role(JAIL_ROLE_ID)
                            if jail_role:
                                await message.author.remove_roles(jail_role)
                            
                            # Xóa toàn bộ tin nhắn trong kênh nhà tù
                            async for msg in message.channel.history(limit=None):
                                try:
                                    await msg.delete()
                                except Exception:
                                    pass
                            
                            # Xóa khỏi jail state
                            del jail_state[user_id_str]
                            save_jail_state(jail_state)
                            
                            print(f"[JAIL] {message.author.id} đã hoàn thành hình phạt")
                        except Exception as e:
                            print(f"[JAIL] Lỗi khi thả tù nhân tự động: {e}")
                    else:
                        # Còn thiếu - thông báo số lượng còn lại
                        bot_msg = await message.channel.send(f"✅ Đã ghi nhận. Cần spam thêm {remaining} lần.")
                        user_data["bot_messages"].append(bot_msg.id)
                        save_jail_state(jail_state)
                        print(f"[JAIL] {message.author.id} spam hợp lệ - còn lại {remaining}")
                else:
                    # Spam quá nhanh
                    remaining = required_count - current_count
                    await message.channel.send(f"⏱️ Spam quá nhanh! Cần đợi {JAIL_SPAM_DELAY - time_since_last_spam:.1f} giây nữa. Còn thiếu {remaining} lần.")
                    print(f"[JAIL] {message.author.id} spam quá nhanh")
            else:
                # Từ khóa sai
                remaining = required_count - current_count
                await message.channel.send(f"❌ Từ khóa sai! Cần spam `{keyword}`. Còn thiếu {remaining} lần.")
                print(f"[JAIL] {message.author.id} spam từ khóa sai")

    await handle_ai_message(message)

def load_user_activity():
    if not os.path.exists(USER_ACTIVITY_FILE):
        return {}
    try:
        with open(USER_ACTIVITY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_user_activity(data):
    with open(USER_ACTIVITY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def load_inactive_check():
    if not os.path.exists(INACTIVE_CHECK_FILE):
        return []
    try:
        with open(INACTIVE_CHECK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_inactive_check(data):
    with open(INACTIVE_CHECK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def load_removed_reactions():
    if not os.path.exists(REMOVED_REACTIONS_FILE):
        return []
    try:
        with open(REMOVED_REACTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_removed_reactions(data):
    with open(REMOVED_REACTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def load_voice_settings():
    if not os.path.exists(VOICE_SETTINGS_FILE):
        return {"google": {"language": None}, "voice": {"language": None, "voice": None}}
    try:
        with open(VOICE_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"google": {"language": None}, "voice": {"language": None, "voice": None}}

def save_voice_settings(data):
    with open(VOICE_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

async def handle_voice_command(message: discord.Message, text: str):
    """Handle !voice command for Piper TTS"""
    voice_settings = load_voice_settings()
    
    if not voice_settings["voice"]["language"] or not voice_settings["voice"]["voice"]:
        await message.channel.send("❌ Chưa cài đặt giọng. Hãy dùng `/talkvoice` trước.", delete_after=10)
        return
    
    language = voice_settings["voice"]["language"]
    voice = voice_settings["voice"]["voice"]
    
    if not message.author.voice or not message.author.voice.channel:
        await message.channel.send("❌ Bạn phải vào voice channel trước.", delete_after=10)
        return
    
    guild_id = message.guild.id
    if guild_id in music_playing or guild_id in tts_playing:
        await message.channel.send("❌ Bot đang phát nhạc hoặc đọc TTS.", delete_after=20)
        try:
            await message.delete(delay=20)
        except Exception:
            pass
        return
    
    voice_channel = message.author.voice.channel
    vc = discord.utils.get(client.voice_clients, guild=message.guild)
    
    try:
        if not vc or not vc.is_connected():
            vc = await voice_channel.connect()
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)
    except Exception as e:
        print(f"[!voice] Lỗi kết nối voice: {e}")
        await message.channel.send("❌ Lỗi kết nối voice.", delete_after=10)
        return
    
    # Tạo file TTS bằng Piper
    def create_piper_tts():
        try:
            filename = tts_piper.generate_voice(language, voice, text)
            return filename
        except Exception as e:
            print(f"[!voice] Lỗi khi tạo TTS: {e}")
            raise
    
    try:
        filename = await asyncio.to_thread(create_piper_tts)
    except Exception as e:
        await message.channel.send(f"❌ Lỗi khi tạo âm thanh: {str(e)}", delete_after=10)
        return
    
    tts_playing.add(guild_id)
    
    # Gửi embed công khai
    embed = discord.Embed(
        title="🔊 Piper TTS",
        color=discord.Color.green(),
        description=f"**[{language.upper()}] [{voice}]** {text}"
    )
    embed.set_author(
        name=f"{message.author.display_name} đã nói:",
        icon_url=message.author.display_avatar.url
    )
    
    msg = await message.channel.send(embed=embed)
    pending_talkvoice_messages.add(msg)
    pending_talkvoice_messages.add(message)
    
    async def schedule_delete(m, user_msg):
        await asyncio.sleep(20)
        if m in pending_talkvoice_messages:
            try:
                await m.delete()
            except Exception:
                pass
            pending_talkvoice_messages.discard(m)
        if user_msg in pending_talkvoice_messages:
            try:
                await user_msg.delete()
            except Exception:
                pass
            pending_talkvoice_messages.discard(user_msg)
    
    asyncio.create_task(schedule_delete(msg, message))
    
    print(f"[!voice] File to play: {filename}")
    print(f"[!voice] File exists: {os.path.exists(filename)}")
    print(f"[!voice] FFmpeg executable: {FFMPEG_EXECUTABLE}")
    print(f"[!voice] VC object: {vc}")
    print(f"[!voice] VC is connected: {vc.is_connected() if vc else 'N/A'}")
    
    try:
        print("[!voice] Creating FFmpegPCMAudio source...")
        source = discord.FFmpegPCMAudio(executable=FFMPEG_EXECUTABLE, source=filename)
        print(f"[!voice] Source created: {source}")
        
        print("[!voice] Calling vc.play()...")
        vc.play(source)
        print(f"[!voice] vc.play() called, is_playing: {vc.is_playing()}")
        
        while vc.is_playing():
            await asyncio.sleep(1)
        print("[!voice] Finished playing")
        await asyncio.sleep(1)
    except Exception as e:
        print(f"[!voice] Error playing audio: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if os.path.exists(filename):
            try:
                tts_piper.cleanup_audio_file(filename)
            except Exception as e:
                print(f"[!voice] Cannot remove {filename}: {e}")
        tts_playing.discard(guild_id)

async def handle_google_command(message: discord.Message, text: str):
    """Handle !google command for Google TTS"""
    voice_settings = load_voice_settings()
    
    if not voice_settings["google"]["language"]:
        await message.channel.send("❌ Chưa cài đặt ngôn ngữ. Hãy dùng `/talkgoogle` trước.", delete_after=10)
        return
    
    lang = voice_settings["google"]["language"]
    
    if not message.author.voice or not message.author.voice.channel:
        await message.channel.send("❌ Bạn phải vào voice channel trước.", delete_after=10)
        return
    
    guild_id = message.guild.id
    if guild_id in music_playing or guild_id in tts_playing:
        await message.channel.send("❌ Bot đang phát nhạc hoặc đọc TTS.", delete_after=20)
        try:
            await message.delete(delay=20)
        except Exception:
            pass
        return
    
    voice_channel = message.author.voice.channel
    vc = discord.utils.get(client.voice_clients, guild=message.guild)
    
    try:
        if not vc or not vc.is_connected():
            vc = await voice_channel.connect()
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)
    except Exception as e:
        print(f"[!google] Lỗi kết nối voice: {e}")
        await message.channel.send("❌ Lỗi kết nối voice.", delete_after=10)
        return
    
    # Tạo file TTS
    def create_tts():
        try:
            tts = gTTS(text=text, lang=lang)
            fname = f"voice_{message.author.id}_{int(time.time() * 1000)}.mp3"
            tts.save(fname)
            return fname
        except Exception as e:
            print(f"[!google] Lỗi khi tạo TTS: {e}")
            raise
    
    try:
        filename = await asyncio.to_thread(create_tts)
    except Exception:
        await message.channel.send(f"❌ Ngôn ngữ `{lang}` không được hỗ trợ.", delete_after=10)
        return
    
    tts_playing.add(guild_id)
    
    # Gửi embed công khai
    embed = discord.Embed(
        title="🔊 Google TTS",
        color=discord.Color.blue(),
        description=f"**[{lang.upper()}]** {text}"
    )
    embed.set_author(
        name=f"{message.author.display_name} đã nói:",
        icon_url=message.author.display_avatar.url
    )
    
    msg = await message.channel.send(embed=embed)
    pending_talkgoogle_messages.add(msg)
    pending_talkgoogle_messages.add(message)
    
    async def schedule_delete(m, user_msg):
        await asyncio.sleep(20)
        if m in pending_talkgoogle_messages:
            try:
                await m.delete()
            except Exception:
                pass
            pending_talkgoogle_messages.discard(m)
        if user_msg in pending_talkgoogle_messages:
            try:
                await user_msg.delete()
            except Exception:
                pass
            pending_talkgoogle_messages.discard(user_msg)
    
    asyncio.create_task(schedule_delete(msg, message))
    
    print(f"[!google] File to play: {filename}")
    print(f"[!google] File exists: {os.path.exists(filename)}")
    print(f"[!google] FFmpeg executable: {FFMPEG_EXECUTABLE}")
    print(f"[!google] VC object: {vc}")
    print(f"[!google] VC is connected: {vc.is_connected() if vc else 'N/A'}")
    
    try:
        print("[!google] Creating FFmpegPCMAudio source...")
        source = discord.FFmpegPCMAudio(executable=FFMPEG_EXECUTABLE, source=filename)
        print(f"[!google] Source created: {source}")
        
        print("[!google] Calling vc.play()...")
        vc.play(source)
        print(f"[!google] vc.play() called, is_playing: {vc.is_playing()}")
        
        while vc.is_playing():
            await asyncio.sleep(1)
        print("[!google] Finished playing")
        await asyncio.sleep(1)
    except Exception as e:
        print(f"[!google] Error playing audio: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as e:
                print(f"[!google] Cannot remove {filename}: {e}")
        tts_playing.discard(guild_id)

def load_bot_channel_state():
    """Load bot channel overwrites state from JSON file"""
    if not os.path.exists(BOT_CHANNEL_STATE_FILE):
        return {}
    try:
        with open(BOT_CHANNEL_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_bot_channel_state(overwrites: dict):
    """Save bot channel overwrites state to JSON file"""
    # Convert PermissionOverwrite objects to serializable format
    serializable = {}
    for target, overwrite in overwrites.items():
        if isinstance(target, discord.Role):
            key = f"role_{target.id}"
        elif isinstance(target, discord.Member):
            key = f"member_{target.id}"
        else:
            continue
        
        serializable[key] = {
            "type": "role" if isinstance(target, discord.Role) else "member",
            "id": target.id,
            "allow": overwrite.pair()[0].value,
            "deny": overwrite.pair()[1].value
        }
    
    with open(BOT_CHANNEL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=4)

async def restore_bot_channel_state(guild: discord.Guild):
    """Restore bot channel overwrites from saved voice state"""
    state = load_bot_channel_state()
    if not state:
        print("[BOT CHANNEL] No saved voice state found")
        return
    
    bot_channel = guild.get_channel(1454830895453442089)
    if not bot_channel:
        print("[BOT CHANNEL] Bot channel not found")
        return
    
    try:
        new_overwrites = {}
        for key, data in state.items():
            target_id = data["id"]
            allow = discord.Permissions(data["allow"])
            deny = discord.Permissions(data["deny"])
            
            if data["type"] == "role":
                target = guild.get_role(target_id)
            else:
                target = guild.get_member(target_id)
            
            if target:
                new_overwrites[target] = discord.PermissionOverwrite(
                    allow=allow,
                    deny=deny
                )
        
        await bot_channel.edit(overwrites=new_overwrites)
        print(f"[BOT CHANNEL] Restored voice state with {len(new_overwrites)} overwrites")
    except Exception as e:
        print(f"[BOT CHANNEL] Error restoring voice state: {e}")

def save_original_bot_channel_state(overwrites: dict):
    """Save original bot channel overwrites state to JSON file"""
    # Convert PermissionOverwrite objects to serializable format
    serializable = {}
    for target, overwrite in overwrites.items():
        if isinstance(target, discord.Role):
            key = f"role_{target.id}"
        elif isinstance(target, discord.Member):
            key = f"member_{target.id}"
        else:
            continue
        
        serializable[key] = {
            "type": "role" if isinstance(target, discord.Role) else "member",
            "id": target.id,
            "allow": overwrite.pair()[0].value,
            "deny": overwrite.pair()[1].value
        }
    
    with open(BOT_CHANNEL_ORIGINAL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=4)

def load_original_bot_channel_state():
    """Load original bot channel overwrites state from JSON file"""
    if not os.path.exists(BOT_CHANNEL_ORIGINAL_STATE_FILE):
        return {}
    try:
        with open(BOT_CHANNEL_ORIGINAL_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# ================== JAIL SYSTEM STATE FUNCTIONS ==================
def save_jail_state(state: dict):
    """Save jail state to JSON file"""
    with open(JAIL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)

def load_jail_state():
    """Load jail state from JSON file"""
    if not os.path.exists(JAIL_STATE_FILE):
        return {}
    try:
        with open(JAIL_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

async def restore_original_bot_channel_state(guild: discord.Guild):
    """Restore bot channel overwrites with a hardcoded Admin sequence"""
    bot_channel = guild.get_channel(1454830895453442089)
    if not bot_channel:
        print("[BOT CHANNEL] Bot channel not found")
        return
    
    try:
        new_overwrites = dict(bot_channel.overwrites)
        
        # 1. Xóa hết tất cả Members trong overrides (ngoại trừ chủ server)
        for target in list(new_overwrites.keys()):
            if isinstance(target, discord.Member):
                if target.id != guild.owner_id:
                    del new_overwrites[target]
                    
        # 2. Thêm Role có ID 1407269404890370120 và cho phép View, Send, Read History
        target_role = guild.get_role(1407269404890370120)
        if target_role:
            new_overwrites[target_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
            
        await bot_channel.edit(overwrites=new_overwrites)
        print(f"[BOT CHANNEL] Restored original state with hardcoded Admin process")
        
        # Xóa file backup (dùng như cờ đánh dấu) và clear bộ nhớ tạm sau khi khôi phục thành công
        bot_channel_original_overwrites.clear()
        try:
            if os.path.exists(BOT_CHANNEL_ORIGINAL_STATE_FILE):
                os.remove(BOT_CHANNEL_ORIGINAL_STATE_FILE)
                print("[BOT CHANNEL] Deleted original state file after successful restore")
        except Exception as e:
            pass
            
    except Exception as e:
        print(f"[BOT CHANNEL] Error restoring original state: {e}")

async def remove_declare_reactions(member: discord.Member):
    """Xóa reactions của user khỏi tin nhắn declare và lưu ID vào file"""
    guild = member.guild
    declare_channel = guild.get_channel(1465050026215477428)
    if not declare_channel:
        return
    
    try:
        target_msg = await declare_channel.fetch_message(1465050575589605407)
        valid_emojis = {EMOJI_VIET_ID, EMOJI_NON_VIET_ID}
        
        for reaction in target_msg.reactions:
            if hasattr(reaction.emoji, "id") and reaction.emoji.id in valid_emojis:
                async for user in reaction.users():
                    if user.id == member.id:
                        await target_msg.remove_reaction(reaction.emoji, user)
                        print(f"[DECLARE] Đã xóa reaction của {member.name} ({member.id})")
        
        # Lưu ID vào file removed_reactions.json
        removed_list = load_removed_reactions()
        if member.id not in removed_list:
            removed_list.append(member.id)
            save_removed_reactions(removed_list)
            print(f"[DECLARE] Đã lưu {member.id} vào removed_reactions.json")
            
    except discord.NotFound:
        print(f"[DECLARE] Không tìm thấy tin nhắn declare")
    except Exception as e:
        print(f"[DECLARE] Lỗi khi xóa reactions: {e}")

def ensure_blacklist_file():
    if not os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            f.write(
                "# Ghi moi tu/cau cam trong dau ngoac kep rieng.\n"
                "# Vi du: them tu cam bang cach bo dau # o dong duoi va sua noi dung.\n"
                "# \"tu cam\"\n"
            )


def load_blacklist_terms() -> list[str]:
    ensure_blacklist_file()

    with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
        content = "\n".join(
            line for line in f.readlines()
            if not line.lstrip().startswith("#")
        )

    terms = []
    for match in re.findall(r'"([^"]+)"', content):
        term = match.strip()
        if term:
            terms.append(term)
    return terms


def build_blacklist_pattern(term: str) -> str:
    compact_term = re.sub(r"[\W_]+", "", term, flags=re.UNICODE)
    if not compact_term:
        return rf"(?<!\w){re.escape(term)}(?!\w)"

    flexible_term = r"[\W_]*".join(re.escape(char) for char in compact_term)
    return rf"(?<!\w){flexible_term}(?!\w)"


def find_blacklisted_term(content: str) -> str | None:
    for term in load_blacklist_terms():
        pattern = build_blacklist_pattern(term)
        if re.search(pattern, content, flags=re.IGNORECASE):
            return term
    return None


async def handle_blacklisted_message(message: discord.Message) -> bool:
    matched_term = find_blacklisted_term(message.content)
    if not matched_term:
        return False

    try:
        await message.delete()
    except discord.Forbidden:
        print(f"[BLACKLIST] Khong du quyen xoa tin nhan cua {message.author}")
    except discord.HTTPException as e:
        print(f"[BLACKLIST] Loi khi xoa tin nhan: {e}")

    warning = (
        f"{message.author.mention}, that word is in black list -"
        "-  từ đó nằm trong danh sách đen."
    )

    try:
        await message.channel.send(warning, delete_after=8)
    except discord.HTTPException as e:
        print(f"[BLACKLIST] Loi khi gui canh bao: {e}")

    print(f"[BLACKLIST] Da chan tin nhan cua {message.author}: {matched_term}")
    return True


def remember_processed_message(message_id: int, processed_ids: set[int]) -> bool:
    if message_id in processed_ids:
        return False

    processed_ids.add(message_id)
    if len(processed_ids) > MAX_PROCESSED_MESSAGE_IDS:
        processed_ids.pop()
    return True
#-----------------------------GIVE ROLE---------------------------------
async def apply_declare_role(
    member: discord.Member,
    emoji_id: int,
    mode: str  # "add" | "remove" | "sync"
):
    guild = member.guild

    role_viet = guild.get_role(ROLE_VIET_ID)
    role_non_viet = guild.get_role(ROLE_NON_VIET_ID)
    role_common = guild.get_role(ROLE_COMMON_ID)

    try:
        # 🇻🇳 Vietnamese
        if emoji_id == EMOJI_VIET_ID and role_viet and role_common:
            if mode in ("add", "sync"):
                await member.add_roles(role_viet, role_common)
            elif mode == "remove":
                await member.remove_roles(role_viet, role_common)

        # 🌍 Non-Vietnamese
        elif emoji_id == EMOJI_NON_VIET_ID and role_non_viet and role_common:
            if mode in ("add", "sync"):
                await member.add_roles(role_non_viet, role_common)
            elif mode == "remove":
                await member.remove_roles(role_non_viet, role_common)

    except discord.Forbidden:
        print(f"❌ Thiếu quyền xử lý role cho {member}")
def load_declare_message_id():
    if not os.path.exists(DECLARE_FILE):
        return None
    with open(DECLARE_FILE, "r") as f:
        return json.load(f).get("message_id")


def save_declare_message_id(message_id: int):
    with open(DECLARE_FILE, "w") as f:
        json.dump({"message_id": message_id}, f)
# ====== CONFIG ======
DECLARE_CHANNEL_ID = 1465050026215477428  

EMOJI_VIET_ID = 1465040596480819230
EMOJI_NON_VIET_ID = 1459954752019239091

ROLE_VIET_ID = 1452965477319049359
ROLE_NON_VIET_ID = 1452968245509226619
ROLE_COMMON_ID = 1407269404890370120   # 👈 ROLE CHUNG
DECLARE_FILE = "declare_message.json"
async def setup_declare_message():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("❌ Không lấy được guild")
        return

    channel = guild.get_channel(DECLARE_CHANNEL_ID)
    if not channel:
        print("❌ Không tìm thấy kênh khai báo")
        return

    # Nếu đã có MESSAGE_ID thì KHÔNG gửi lại
    message_id = load_declare_message_id()
    if message_id:
        try:
            await channel.fetch_message(message_id)
            print("ℹ️ Thông báo khai báo đã tồn tại")
            return
        except discord.NotFound:
            pass  # message bị xóa → tạo lại

    # Gửi thông báo
    message = await channel.send(
        "**Please choose your nationality:**\n\n"
        "🇻🇳 If you are Vietnamese, react with the first emoji.\n"
        "🌍 If you are NOT Vietnamese, react with the second emoji."
    )

    # Lấy emoji TỪ GUILD (quan trọng)
    emoji_viet = guild.get_emoji(EMOJI_VIET_ID)
    emoji_non_viet = guild.get_emoji(EMOJI_NON_VIET_ID)

    if not emoji_viet or not emoji_non_viet:
        print("❌ Không tìm thấy emoji trong guild")
        return

    # Bot thả reaction mẫu
    await message.add_reaction(emoji_viet)
    await message.add_reaction(emoji_non_viet)

    save_declare_message_id(message.id)
    print("✅ Đã tạo thông báo khai báo & reaction mẫu")
async def sync_declare_reactions():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return

    channel = guild.get_channel(DECLARE_CHANNEL_ID)
    if not channel:
        return

    valid_emojis = {EMOJI_VIET_ID, EMOJI_NON_VIET_ID}
    removed_users = load_removed_reactions()

    async for message in channel.history(limit=None):
        for reaction in message.reactions:

            # ❌ Emoji sai → xóa
            if not hasattr(reaction.emoji, "id") or reaction.emoji.id not in valid_emojis:
                async for user in reaction.users():
                    if not user.bot:
                        await message.remove_reaction(reaction.emoji, user)
                continue

            # ✅ Emoji đúng → sync role
            async for user in reaction.users():
                if user.bot:
                    continue

                # Bỏ qua user đã bị xóa reaction
                if user.id in removed_users:
                    continue

                try:
                    member = await guild.fetch_member(user.id)
                    await apply_declare_role(member, reaction.emoji.id, mode="sync")
                except discord.NotFound:
                    continue
                except Exception as e:
                    print(f"Lỗi khi fetch member {user.id}: {e}")

    print("✅ Startup cleanup hoàn tất (chỉ DECLARE_CHANNEL)")
@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):

    if payload.user_id == client.user.id:
        return

    # 🚫 CHẶN TOÀN BỘ KÊNH KHÁC
    if payload.message_id != load_declare_message_id():
        return

    if payload.channel_id != DECLARE_CHANNEL_ID:
        return

    guild = client.get_guild(payload.guild_id)
    if not guild:
        return

    channel = guild.get_channel(payload.channel_id)
    try:
        member = await guild.fetch_member(payload.user_id)
    except discord.NotFound:
        return

    if not channel or not member:
        return

    # ❌ Emoji không hợp lệ → xóa
    emoji_id = getattr(payload.emoji, "id", None)
    if emoji_id not in (
        EMOJI_VIET_ID,
        EMOJI_NON_VIET_ID
    ):
        try:
            msg = await channel.fetch_message(payload.message_id)
            await msg.remove_reaction(payload.emoji, member)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        return

    # ✅ Emoji hợp lệ → cấp role
    await apply_declare_role(member, emoji_id, mode="add")
@client.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.message_id != load_declare_message_id():
        return

    emoji_id = getattr(payload.emoji, "id", None)
    if emoji_id not in (EMOJI_VIET_ID, EMOJI_NON_VIET_ID):
        return

    guild = client.get_guild(payload.guild_id)
    if not guild:
        return

    member = guild.get_member(payload.user_id)
    if not member:
        return

    await apply_declare_role(member, emoji_id, mode="remove")
async def check_missed_welcomes(guild: discord.Guild):
    channel = guild.get_channel(WELCOME_CHANNEL_ID)
    if not channel:
        return

    now = datetime.utcnow()
    recent_members = []
    
    # Lấy những người vào server trong 24h qua
    for member in guild.members:
        if member.joined_at:
            joined_naive = member.joined_at.replace(tzinfo=None)
            if (now - joined_naive).total_seconds() <= 86400:
                recent_members.append(member)

    if not recent_members:
        return

    declare_channel_id = 1465050026215477428
    target_msg_id = 1465050575589605407
    reacted_user_ids = set()
    
    try:
        declare_channel = guild.get_channel(declare_channel_id)
        if declare_channel:
            target_msg = await declare_channel.fetch_message(target_msg_id)
            for reaction in target_msg.reactions:
                async for user in reaction.users():
                    reacted_user_ids.add(user.id)
    except Exception as e:
        print(f"[WELCOME CHECK] Lỗi lấy reaction: {e}")

    welcomed_ids = set()
    try:
        # Lấy lịch sử 200 tin nhắn gần nhất trong kênh welcome
        async for msg in channel.history(limit=200):
            if msg.author.id == client.user.id and msg.embeds:
                desc = msg.embeds[0].description or ""
                matches = re.findall(r"<@!?(\d+)>", desc)
                for m in matches:
                    welcomed_ids.add(int(m))
    except Exception as e:
        print(f"[WELCOME CHECK] Lỗi quét lịch sử: {e}")
        return

    for member in recent_members:
        if member.id not in welcomed_ids:
            has_no_roles = len(member.roles) == 1
            has_reacted = member.id in reacted_user_ids
            
            if has_no_roles or has_reacted:
                print(f"[WELCOME CHECK] Đang gửi welcome bù cho {member.name}")
                await on_member_join(member)

#----------CHỨC NĂNG CHÀO MỪNG-----------
@client.event
async def on_member_join(member: discord.Member):
    # ================== JAIL SYSTEM MEMBER JOIN DETECTION ==================
    # Kiểm tra xem người dùng có trong jail state không
    jail_state = load_jail_state()
    user_id_str = str(member.id)
    
    if user_id_str in jail_state:
        # Người dùng đang bị giam - đảm bảo họ có role tù nhân
        jail_role = member.guild.get_role(JAIL_ROLE_ID)
        if jail_role:
            try:
                await member.add_roles(jail_role)
                print(f"[JAIL] {member.id} vào lại server - đã gán lại role tù nhân")
            except Exception as e:
                print(f"[JAIL] Lỗi khi gán lại role tù nhân cho {member.id}: {e}")
    
    channel = client.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        return

    embed = discord.Embed(
        title="🎉 Chào mừng!",
        description=(
            f"XIN CHÀO & CHAO XÌN<:zo:1407589454570328074>{member.mention} 👋\n"
            f"-Chào mừng bạn đến với<:pepelaughing:1407589824835227698>**{member.guild.name}**<:MichaelMyers:1555609639062732831>\n"
            f"-Nơi giao lưu, tụ họp và chuyện trò của những con người có chung niềm đam mê với <:bantumlum:1454884543948394549>**Game**.\n"
            f"-À và cả những tựa game khác nữa<:thanlong1:1516838947282485258><:pepehutthuoc:1411605379552378910>....\n"
            f"-Đừng quên đọc qua <#{1407058431134666992}> để nắm thêm thông tin"
        ),
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(
        text=f"Thành viên thứ {member.guild.member_count}"
    )

    await channel.send(embed=embed)
#----------CHỨC NĂNG TẠM BIỆT-----------
@client.event
async def on_member_remove(member: discord.Member):
    if member.id in kicked_banned_users:
        kicked_banned_users.discard(member.id)
        return

    channel = client.get_channel(GOODBYE_CHANNEL_ID)
    if channel is None:
        return

    embed = discord.Embed(
        title="<:mow_emoji:1407590613049675886> Tạm biệt!",
        description=(
            f"-{member.mention} AKA {member.display_name} đã rời khỏi **{member.guild.name}**<:Rana:1407589615875002379>\n"
            "- Tạm biệt..🫶"
        ),
        color=discord.Color.red()
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    await channel.send(embed=embed)

    # Xóa reactions khỏi tin nhắn declare
    await remove_declare_reactions(member)

    # Tự động xoá theo dõi nickname Steam nếu người dùng rời server
    links = load_steam_links()
    user_id_str = str(member.id)
    if user_id_str in links:
        del links[user_id_str]
        save_steam_links(links)
        print(f"[STEAM NICK] Đã xóa gán Steam cho {member.name} ({member.id}) vì rời server.")

#-------------------SLASH COMMAND KICK & BAN---------------------
MOD_ROLE_ID = 1407045564964536500

@client.tree.command(
    name="ban",
    description="Ban một thành viên khỏi server",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Thành viên cần ban", reason="Lý do ban", language="Ngôn ngữ (0=Tiếng Việt, 1=English)")
async def ban_command(interaction: discord.Interaction, member: discord.Member, reason: str, language: int):
    if not any(role.id == MOD_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    
    channel = client.get_channel(GOODBYE_CHANNEL_ID)
    
    kicked_banned_users.add(member.id)
    try:
        # Gửi DM thông báo cho user bị ban
        try:
            if language == 1:
                dm_message = (
                    f"You have been banned from the 🇻🇳 server for the following reason: {reason}\n"
                    f"If you have any complaints, please contact the admins at:\n"
                    f"- Hoài Nam: sleepyraynor\n"
                    f"- Nonne: phuonghoangphoenix\n"
                    f"- Negan: kutj19953(temporarily unavailable)"
                )
            else:
                dm_message = (
                    f"Bạn đã bị ban khỏi server 🇻🇳 với lý do: {reason}\n"
                    f"Nếu có khiếu nại xin hãy liên hệ admin có địa chỉ như sau:\n"
                    f"- Hoài Nam: sleepyraynor\n"
                    f"- Nonne: phuonghoangphoenix\n"
                    f"- Negan: kutj19953(đã tạm dừng vô thời hạn)"
                )
            await member.send(dm_message)
        except discord.Forbidden:
            print(f"[BAN] Không thể gửi DM cho {member.name} ({member.id}) - có thể đã block bot")
        except Exception as e:
            print(f"[BAN] Lỗi khi gửi DM: {e}")
        
        await member.ban(reason=f"Banned by {interaction.user.name}: {reason}")
        
        # Xóa reactions khỏi tin nhắn declare
        await remove_declare_reactions(member)
        
        if channel:
            if language == 1:
                embed = discord.Embed(
                    title="<:mow_emoji:1407590613049675886> Goodbye!",
                    description=f"-{member.mention} AKA {member.display_name} has been banned from **{member.guild.name}** for the following reason: {reason}<:Rana:1407589615875002379>\n",
                    color=discord.Color.red()
                )
            else:
                embed = discord.Embed(
                    title="<:mow_emoji:1407590613049675886> Tạm biệt!",
                    description=f"-{member.mention} AKA {member.display_name} đã bị ban khỏi **{member.guild.name}** với lý do: {reason}<:Rana:1407589615875002379>\n",
                    color=discord.Color.red()
                )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)
            
        await interaction.followup.send(f"✅ Đã ban {member.mention}.", ephemeral=True)
    except discord.Forbidden:
        kicked_banned_users.discard(member.id)
        await interaction.followup.send("❌ Bot không đủ quyền để ban người này.", ephemeral=True)
    except Exception as e:
        kicked_banned_users.discard(member.id)
        await interaction.followup.send(f"❌ Có lỗi xảy ra: {e}", ephemeral=True)


@client.tree.command(
    name="kick",
    description="Kick một thành viên khỏi server",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Thành viên cần kick", reason="Lý do kick", language="Ngôn ngữ (0=Tiếng Việt, 1=English)")
async def kick_command(interaction: discord.Interaction, member: discord.Member, reason: str, language: int):
    if not any(role.id == MOD_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    
    channel = client.get_channel(GOODBYE_CHANNEL_ID)
    
    kicked_banned_users.add(member.id)
    try:
        # Gửi DM thông báo cho user bị kick
        try:
            if language == 1:
                dm_message = (
                    f"You have been kicked from the 🇻🇳 server for the following reason: {reason}\n"
                    f"If you have any complaints, please contact the admins at:\n"
                    f"- Hoài Nam: sleepyraynor\n"
                    f"- Nonne: phuonghoangphoenix\n"
                    f"- Negan: kutj19953(temporarily unavailable)"
                )
            else:
                dm_message = (
                    f"Bạn đã bị kick khỏi server 🇻🇳 với lý do: {reason}\n"
                    f"Nếu có khiếu nại xin hãy liên hệ admin có địa chỉ như sau:\n"
                    f"- Hoài Nam: sleepyraynor\n"
                    f"- Nonne: phuonghoangphoenix\n"
                    f"- Negan: kutj19953(đã tạm dừng vô thời hạn)"
                )
            await member.send(dm_message)
        except discord.Forbidden:
            print(f"[KICK] Không thể gửi DM cho {member.name} ({member.id}) - có thể đã block bot")
        except Exception as e:
            print(f"[KICK] Lỗi khi gửi DM: {e}")
        
        await member.kick(reason=f"Kicked by {interaction.user.name}: {reason}")
        
        # Xóa reactions khỏi tin nhắn declare
        await remove_declare_reactions(member)
        
        if channel:
            if language == 1:
                embed = discord.Embed(
                    title="<:mow_emoji:1407590613049675886> Goodbye!",
                    description=f"-{member.mention} AKA {member.display_name} has been kicked from **{member.guild.name}** for the following reason: {reason}<:Rana:1407589615875002379>\n",
                    color=discord.Color.red()
                )
            else:
                embed = discord.Embed(
                    title="<:mow_emoji:1407590613049675886> Tạm biệt!",
                    description=f"-{member.mention} AKA {member.display_name} đã bị kick khỏi **{member.guild.name}** với lý do: {reason}<:Rana:1407589615875002379>\n",
                    color=discord.Color.red()
                )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)
            
        await interaction.followup.send(f"✅ Đã kick {member.mention}.", ephemeral=True)
    except discord.Forbidden:
        kicked_banned_users.discard(member.id)
        await interaction.followup.send("❌ Bot không đủ quyền để kick người này.", ephemeral=True)
    except Exception as e:
        kicked_banned_users.discard(member.id)
        await interaction.followup.send(f"❌ Có lỗi xảy ra: {e}", ephemeral=True)

#-------------------MANUAL WELCOME & GOODBYE---------------------
@client.tree.command(
    name="welcome_manual",
    description="Gửi tin nhắn chào mừng thủ công cho một thành viên",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Thành viên muốn chào mừng")
async def welcome_manual(interaction: discord.Interaction, member: discord.Member):
    if interaction.user.id != AI_ADMIN_ID:
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    channel = client.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("❌ Không tìm thấy kênh chào mừng.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎉 Chào mừng!",
        description=(
            f"XIN CHÀO & CHAO XÌN<:zo:1407589454570328074>{member.mention} 👋\n"
            f"-Chào mừng bạn đến với<:pepelaughing:1407589824835227698>**{interaction.guild.name}**<:MichaelMyers:1555609639062732831>\n"
            f"-Nơi giao lưu, tụ họp và chuyện trò của những con người có chung niềm đam mê với <:bantumlum:1454884543948394549>**Game**.\n"
            f"-À và cả những tựa game khác nữa<:thanlong1:1516838947282485258><:pepehutthuoc:1411605379552378910>....\n"
            f"-Đừng quên đọc qua <#{1407058431134666992}> để nắm thêm thông tin"
        ),
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(
        text=f"Thành viên thứ {interaction.guild.member_count}"
    )

    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Đã gửi lời chào mừng tới {member.mention}.", ephemeral=True)

@client.tree.command(
    name="goodbye_manual",
    description="Gửi tin nhắn tạm biệt thủ công cho một thành viên",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Thành viên muốn tạm biệt")
async def goodbye_manual(interaction: discord.Interaction, member: discord.Member):
    if interaction.user.id != AI_ADMIN_ID:
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    channel = client.get_channel(GOODBYE_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("❌ Không tìm thấy kênh tạm biệt.", ephemeral=True)
        return

    embed = discord.Embed(
        title="<:mow_emoji:1407590613049675886> Tạm biệt!",
        description=(
            f"-{member.mention} AKA {member.display_name} đã rời khỏi **{interaction.guild.name}**<:Rana:1407589615875002379>\n"
            "-Hẹn ngày trở về!🫶"
        ),
        color=discord.Color.red()
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Đã gửi lời tạm biệt tới {member.mention}.", ephemeral=True)

#-------------------SLASH COMMAND---------------------
# Slash command căn bản
@client.tree.command(
    name="ping",
    description="Kiểm tra bot hoạt động"
)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")

#----XEM AVATAR-----
ALLOWED_ROLE_IDS = [
    1452965477319049359,
    1452968245509226619  # <-- thay bằng Role ID của bạn
]

@client.tree.command(
    name="avatar",
    description="Xem avatar của người được đề cập"
)
@app_commands.describe(user="Người cần xem avatar")
async def avatar(
    interaction: discord.Interaction,
    user: discord.User
):
    # Kiểm tra role
    if interaction.guild is None:
        await interaction.response.send_message(
            "Lệnh này chỉ dùng trong server.",
            ephemeral=True
        )
        return

    member: discord.Member = interaction.user

    if not any(role.id in ALLOWED_ROLE_IDS for role in member.roles):
        await interaction.response.send_message(
            "❌ Bạn không có quyền dùng lệnh này.",
            ephemeral=True
        )
        return

    # Lấy avatar
    avatar_url = user.display_avatar.url

    embed = discord.Embed(
        title=f"Avatar của {user}",
        color=discord.Color.blurple()
    )
    embed.set_image(url=avatar_url)

    await interaction.response.send_message(embed=embed)

# ----------------THEO DÕI TÀI KHOẢN STEAM-----------
CHECKSTEAM_ALLOWED_ROLE_IDS = [1452965477319049359, 1452968245509226619, 1407045564964536500]  # Thay bằng role được phép
# ---- STEAM API CONFIG -----
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
STEAM_ACCOUNTS = {
    "Nonne": "76561199069666188",
    "Kingen": "76561198441625090",
    "Anh Ba Mien Trung": "76561199182682344",
    "jesse": "76561199024184094",
    "Tình đây nè": "76561199878825832",
    "Nguyên Nick Em": "76561199218906718",
    "1": "76561198174311699",
    "sapseabread": "76561199377868301",
    "Tung Tung Tung Sahur": "76561199043325812",
    "Remainz": "76561198095189400",
    "Hoài Nam": "76561199226126455"
}
@client.tree.command(
    name="checksteam",
    description="Kiểm tra trạng thái Steam của một tài khoản theo tên định nghĩa",
    guild=discord.Object(id=GUILD_ID)  # có thể giữ
)
@app_commands.describe(username="Tên tài khoản trong danh sách STEAM_ACCOUNTS")
async def checksteam(interaction: discord.Interaction, username: str):

    # Chỉ cho phép dùng trong guild
    if interaction.guild is None:
        await interaction.response.send_message(
            "Lệnh này chỉ dùng trong server.",
            ephemeral=True
        )
        return

    # interaction.user trong guild = discord.Member
    member: discord.Member = interaction.user

    # Kiểm tra role
    if not any(role.id in CHECKSTEAM_ALLOWED_ROLE_IDS for role in member.roles):
        await interaction.response.send_message(
            "❌ Bạn không có quyền dùng lệnh này.",
            ephemeral=True
        )
        return

    # Kiểm tra username có tồn tại không
    if username not in STEAM_ACCOUNTS:
        available_names = ", ".join(STEAM_ACCOUNTS.keys())
        await interaction.response.send_message(
            f"❌ Không tìm thấy tên `{username}` trong danh sách theo dõi.\n"
            f"Các tên có thể dùng: {available_names}",
            ephemeral=True
        )
        return

    if not STEAM_API_KEY:
        await interaction.response.send_message(
            "âŒ Thiáº¿u STEAM_API_KEY trong file .env.",
            ephemeral=True
        )
        return

    steamid = STEAM_ACCOUNTS[username]
    url = (
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
        f"?key={STEAM_API_KEY}&steamids={steamid}"
    )

    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10)
        response.raise_for_status()
        data = response.json()

        players = data.get("response", {}).get("players", [])
        if not players:
            await interaction.response.send_message(
                "⚠️ Không lấy được dữ liệu từ Steam API.",
                ephemeral=True
            )
            return

        player = players[0]
        state = player.get("personastate", 0)

        state_dict = {
            0: "🔴 Offline",
            1: "💡 Online",
            2: "⛔ Busy",
            3: "💤 Away (AFK)",
            4: "😴 Snooze",
            5: "💰 Looking to trade",
            6: "🎮 Looking to play"
        }

        status_text = state_dict.get(state, "❓ Unknown")

        if "gameextrainfo" in player:
            message = (
                f"🎮 **{username}** đang chơi **{player['gameextrainfo']}**\n"
                f"Trạng thái: {status_text}"
            )
        else:
            message = f"🧾 **{username}**: {status_text}"

        await interaction.response.send_message(message)

    except requests.exceptions.RequestException as e:
        await interaction.response.send_message(
            f"❌ Lỗi kết nối Steam API: {e}",
            ephemeral=True
        )

# ---------------- THEO DÕI NICKNAME STEAM TỰ ĐỘNG ----------------
COUNTRY_FLAG_MAP = {
    "indonesia": "🇮🇩",
    "thailand": "🇹🇭",
    "philippines": "🇵🇭",
    "china": "🇨🇳",
    "malaysia": "🇲🇾",
    "taiwan": "🇹🇼",
    "korea": "🇰🇷",
    "japan": "🇯🇵",
    "vietnam": "",  # Vietnam không có flag
}

def load_steam_links():
    if not os.path.exists(STEAM_LINKS_FILE):
        return {}
    try:
        with open(STEAM_LINKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_steam_links(links):
    with open(STEAM_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, indent=4)

async def resolve_steam_id(profile_url: str) -> str | None:
    """Chuyển đổi link profile Steam thành SteamID64"""
    url = profile_url.strip().strip('/')
    
    # 1. Nếu là dạng /profiles/[id64]
    id_match = re.search(r'profiles/(\d{17})', url)
    if id_match:
        return id_match.group(1)
    
    # 2. Nếu là dạng /id/[vanity_url]
    vanity_match = re.search(r'id/([^/?#\s]+)', url)
    if vanity_match:
        vanity_name = vanity_match.group(1)
        api_url = f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/?key={STEAM_API_KEY}&vanityurl={vanity_name}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("response", {}).get("success") == 1:
                            return data["response"]["steamid"]
        except Exception as e:
            print(f"[STEAM] Lỗi giải mã link: {e}")
    return None

@tasks.loop(hours=1.0)
async def check_duplicate_roles():
    """Kiểm tra và xử lý người dùng có 2 role trùng nhau mỗi 1 tiếng"""
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
        
    role1 = guild.get_role(1452965477319049359)
    role2 = guild.get_role(1452968245509226619)
    if not role1 or not role2:
        return

    channel = guild.get_channel(1465050026215477428)
    warning_channel = guild.get_channel(1399701361469165669)
    if not channel or not warning_channel:
        return
        
    try:
        msg = await channel.fetch_message(1465050575589605407)
    except Exception as e:
        print(f"[DUPLICATE ROLE] Lỗi không lấy được tin nhắn khai báo: {e}")
        return

    for member in guild.members:
        if role1 in member.roles and role2 in member.roles:
            has_reaction = False
            for reaction in msg.reactions:
                async for user in reaction.users():
                    if user.id == member.id:
                        has_reaction = True
                        try:
                            await msg.remove_reaction(reaction.emoji, member)
                            print(f"[DUPLICATE ROLE] Đã xoá reaction của {member.name}")
                        except Exception as e:
                            print(f"[DUPLICATE ROLE] Lỗi khi xoá reaction của {member.name}: {e}")
            
            if not has_reaction:
                try:
                    await warning_channel.send(f"{member.mention} bạn đã lấy nhầm role")
                    print(f"[DUPLICATE ROLE] Đã gửi cảnh báo cho {member.name}")
                except Exception as e:
                    print(f"[DUPLICATE ROLE] Lỗi gửi cảnh báo cho {member.name}: {e}")

@tasks.loop(hours=24.0)
async def cleanup_music_cache():
    """Clean up old music cache files every 24 hours"""
    print("[CLEANUP] Running scheduled cache cleanup")
    cleanup_old_cache_files(max_age_hours=168)  # 7 days

@tasks.loop(hours=1.0)
async def track_steam_nicknames():
    """Task tự động cập nhật nickname Discord theo Steam mỗi 1 tiếng"""
    guild = client.get_guild(GUILD_ID)
    if not guild or not STEAM_API_KEY:
        return

    if not guild.me.guild_permissions.manage_nicknames:
        print("[STEAM TASK] Bot thiếu quyền Manage Nicknames.")
        return

    links = load_steam_links()
    if not links:
        return

    # Extract steam_ids from links (handle both old string format and new dict format)
    steam_ids = []
    for link_data in links.values():
        if isinstance(link_data, str):
            steam_ids.append(link_data)
        else:
            steam_ids.append(link_data.get("steam_id", ""))
    
    all_players = {}

    # Steam API cho phép lấy tối đa 100 IDs mỗi lần
    for i in range(0, len(steam_ids), 100):
        chunk = steam_ids[i:i+100]
        summary_url = (
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
            f"?key={STEAM_API_KEY}&steamids={','.join(chunk)}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(summary_url, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        players = data.get("response", {}).get("players", [])
                        for p in players:
                            all_players[p['steamid']] = p['personaname']
        except Exception as e:
            print(f"[STEAM TASK] Lỗi Steam API: {e}")

    # Duyệt danh sách để cập nhật nickname
    links_changed = False
    for discord_id_str in list(links.keys()):
        link_data = links[discord_id_str]
        # Handle both old format (string) and new format (dict)
        if isinstance(link_data, str):
            steam_id = link_data
            country = ""
        else:
            steam_id = link_data.get("steam_id", "")
            country = link_data.get("country", "")
        
        try:
            member = guild.get_member(int(discord_id_str))
            if not member:
                member = await guild.fetch_member(int(discord_id_str))
        except (discord.NotFound, discord.HTTPException):
            # Không tìm thấy người dùng (đã rời server) -> Xóa gán
            del links[discord_id_str]
            links_changed = True
            print(f"[STEAM TASK] Đã xóa theo dõi Steam cho {discord_id_str} vì không tìm thấy member.")
            continue
        except Exception:
            continue

        if steam_id in all_players:
            try:
                steam_name = all_players[steam_id]
                # Lấy tên gốc Discord (Global Name hoặc Username), không lấy biệt danh server
                discord_base_name = member.global_name or member.name
                country_flag = COUNTRY_FLAG_MAP.get(country, "")
                new_nick = f"{discord_base_name} ({steam_name})"
                
                # Thêm flag quốc gia nếu có (trừ Vietnam)
                if country_flag:
                    new_nick = f"{new_nick} {country_flag}"
                
                # Đảm bảo không quá 32 ký tự (giới hạn của Discord)
                if len(new_nick) > 32:
                    new_nick = new_nick[:32]

                # Cập nhật nếu nickname hiện tại khác với định dạng mới
                if member and member.display_name != new_nick:
                    # Kiểm tra quyền: Bot phải có role cao hơn user và user không phải chủ server
                    if guild.me.top_role > member.top_role and member.id != guild.owner_id:
                        await member.edit(nick=new_nick, reason="Đồng bộ Steam Nickname")
                        print(f"[STEAM NICK] Đã cập nhật {member.name} -> {new_nick}")
                else:
                    print(f"[STEAM NICK] Nickname của {member.name} đã đúng: {new_nick}")
            except Exception:
                continue

    if links_changed:
        save_steam_links(links)

@client.tree.command(
    name="linksteam",
    description="Gán nickname Steam để bot tự động cập nhật cho người dùng",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người dùng Discord", profile_url="Link profile Steam", country="Quốc gia (indonesia, thailand, philippines, china, malaysia, taiwan, korea, japan, vietnam)")
async def linksteam(interaction: discord.Interaction, member: discord.Member, profile_url: str, country: str = ""):
    if not any(role.id in CHECKSTEAM_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    steam_id = await resolve_steam_id(profile_url)
    
    if not steam_id:
        await interaction.followup.send("❌ Không thể lấy SteamID từ link. Hãy dùng link dạng /profiles/ hoặc /id/.", ephemeral=True)
        return

    # Normalize country name to lowercase
    country_lower = country.lower().strip()
    country_flag = COUNTRY_FLAG_MAP.get(country_lower, "")
    
    links = load_steam_links()
    links[str(member.id)] = {
        "steam_id": steam_id,
        "country": country_lower
    }
    save_steam_links(links)

    flag_text = f" với flag {country_flag}" if country_flag else ""
    await interaction.followup.send(f"✅ Đã gán {member.mention} với SteamID `{steam_id}`{flag_text}. Nickname sẽ tự động cập nhật mỗi giờ.", ephemeral=True)

@client.tree.command(
    name="unlinksteam",
    description="Xóa theo dõi và gán nickname Steam",
    guild=discord.Object(id=GUILD_ID)
)
async def unlinksteam(interaction: discord.Interaction, member: discord.Member):
    if not any(role.id in CHECKSTEAM_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    links = load_steam_links()
    if str(member.id) in links:
        del links[str(member.id)]
        save_steam_links(links)
        await interaction.response.send_message(f"✅ Đã xóa gán nickname Steam cho {member.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Người dùng này chưa được gán nickname Steam.", ephemeral=True)

@client.tree.command(
    name="listlink",
    description="Xem danh sách các tài khoản Discord đã được gán nickname Steam",
    guild=discord.Object(id=GUILD_ID)
)
async def listlink(interaction: discord.Interaction):
    if not any(role.id in CHECKSTEAM_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    links = load_steam_links()
    if not links:
        await interaction.response.send_message("📭 Hiện chưa có tài khoản nào được gán nickname Steam.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    
    lines = []
    for discord_id_str, steam_id in links.items():
        member = interaction.guild.get_member(int(discord_id_str))
        user_display = member.mention if member else f"ID: `{discord_id_str}` (Đã rời server)"
        lines.append(f"👤 {user_display} 🔗 SteamID: `{steam_id}`")

    embed = discord.Embed(
        title="📋 Danh sách gán nickname Steam",
        description="\n".join(lines),
        color=discord.Color.blue()
    )
    
    await interaction.followup.send(embed=embed)

# --------------------- SLASH COMMAND NÓI CHỊ GOOGLE ---------------------
# --------------------- ROLE RIÊNG CHO LỆNH VOICE ---------------------

VOICE_ALLOWED_ROLE_IDS = [1452965477319049359, 1452968245509226619]
tts_playing = set()     # guild_id đang talkgoogle
pending_talkgoogle_messages = set() # tin nhắn talkgoogle đang chờ xóa
bot_channel_original_overwrites = {} # Lưu trạng thái permissions ban đầu của kênh bot

@tasks.loop(seconds=5.0)
async def sync_voice_permissions():
    guild = client.get_guild(GUILD_ID)
    if not guild: return
    vc = discord.utils.get(client.voice_clients, guild=guild)
    if not vc or not vc.is_connected():
        sync_voice_permissions.stop()
        return
        
    bot_channel = guild.get_channel(1454830895453442089)
    if not bot_channel: return
    
    current_overwrites = bot_channel.overwrites
    current_member_ids = {k.id for k, v in current_overwrites.items() if isinstance(k, discord.Member) and v.view_channel == True}
    vc_member_ids = {m.id for m in vc.channel.members}
    
    if current_member_ids != vc_member_ids:
        new_overwrites = {}
        new_overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        for member in vc.channel.members:
            new_overwrites[member] = discord.PermissionOverwrite(view_channel=True)
            
        try:
            await bot_channel.edit(overwrites=new_overwrites)
            # Lưu trạng thái kênh vào file
            save_bot_channel_state(new_overwrites)
        except Exception:
            pass

@client.tree.command(
    name="talkgoogle",
    description="Cài đặt ngôn ngữ cho Google TTS (sau đó dùng !google <text> để đọc)",
    guild=discord.Object(id=GUILD_ID)
)
@discord.app_commands.describe(
    lang="Languages Code, which support voice in Google Translate(Examples: vi, en, ja, id, ko...)"
)
async def talkgoogle(interaction: discord.Interaction, lang: str):

    guild_id = interaction.guild.id

    if interaction.channel.id != 1454830895453442089:
        try:
            await interaction.response.send_message(
                "⚠️ Lệnh này chỉ được dùng trong kênh <#1454830895453442089>.",
                ephemeral=True
            )
        except Exception:
            pass
        return

    # Kiểm tra role
    if not any(role.id in VOICE_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        try:
            await interaction.response.send_message(
                "❌ Bạn không có quyền dùng lệnh này.",
                ephemeral=True
            )
        except Exception:
            pass
        return

    # Kiểm tra user có ở voice không
    if not interaction.user.voice or not interaction.user.voice.channel:
        try:
            await interaction.response.send_message(
                "❌ Bạn phải vào voice channel trước khi dùng lệnh này.",
                ephemeral=True
            )
        except Exception:
            pass
        return

    # Lưu cài đặt ngôn ngữ
    voice_settings = load_voice_settings()
    voice_settings["google"]["language"] = lang
    save_voice_settings(voice_settings)

    voice_channel = interaction.user.voice.channel

    # Lấy hoặc kết nối voice client
    vc = discord.utils.get(
        interaction.client.voice_clients,
        guild=interaction.guild
    )

    # Ẩn kênh chat với everyone và hiện với người trong voice channel
    bot_channel = interaction.guild.get_channel(1454830895453442089)
    if bot_channel:
        try:
            # CHỈ lưu trạng thái gốc LẦN ĐẦU TIÊN (khi file chưa tồn tại)
            # Sau đó KHÔNG ghi đè để giữ nguyên trạng thái gốc ban đầu
            if not os.path.exists(BOT_CHANNEL_ORIGINAL_STATE_FILE):
                save_original_bot_channel_state(bot_channel.overwrites)
                print("[talkgoogle] Saved original bot channel state (first time)")
            
            # Lưu lại trạng thái ban đầu của kênh nếu chưa lưu (cho runtime)
            if not bot_channel_original_overwrites:
                bot_channel_original_overwrites.update(bot_channel.overwrites)
            
            # Xoá hết các role cũ, chỉ giữ @everyone và cập nhật thành viên trong voice
            new_overwrites = {}
            new_overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
                
            # Cấp quyền xem cho các thành viên có mặt trong voice channel
            for member in voice_channel.members:
                new_overwrites[member] = discord.PermissionOverwrite(view_channel=True)
                
            await bot_channel.edit(overwrites=new_overwrites)
            
            # Lưu trạng thái voice vào file
            save_bot_channel_state(new_overwrites)
        except Exception as e:
            print(f"[talkgoogle] Lỗi đổi quyền: {e}")

    try:
        if not vc or not vc.is_connected():
            vc = await voice_channel.connect()
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)
            
        if not sync_voice_permissions.is_running():
            sync_voice_permissions.start()
    except Exception as e:
        print(f"[talkgoogle] Lỗi khi kết nối voice: {e}")
        try:
            await interaction.response.send_message("❌ Có lỗi khi kết nối vào voice channel.", ephemeral=True)
        except Exception:
            pass
        return

    try:
        await interaction.response.send_message(
            f"✅ Đã cài đặt Google TTS với ngôn ngữ: **{lang}**\n"
            f"Sử dụng `!google <text>` để bot đọc.",
            ephemeral=True
        )
    except Exception:
        pass

# --------------------- SLASH COMMAND PIPER TTS ---------------------
pending_talkvoice_messages = set() # tin nhắn talkvoice đang chờ xóa

# Autocomplete for language parameter
async def language_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    languages = tts_piper.get_available_languages()
    return [
        app_commands.Choice(name=lang, value=lang)
        for lang in languages
        if current.lower() in lang.lower()
    ][:25]

@client.tree.command(
    name="talkvoice",
    description="Cài đặt ngôn ngữ và giọng cho Piper TTS (sau đó dùng !voice <text> để đọc)",
    guild=discord.Object(id=GUILD_ID)
)
@discord.app_commands.describe(
    language="Language code (e.g., vi, en, ja, ko...)",
    voice="Voice model name"
)
@discord.app_commands.autocomplete(language=language_autocomplete)
async def talkvoice(interaction: discord.Interaction, language: str, voice: str):

    guild_id = interaction.guild.id

    if interaction.channel.id != 1454830895453442089:
        try:
            await interaction.response.send_message(
                "⚠️ Lệnh này chỉ được dùng trong kênh <#1454830895453442089>.",
                ephemeral=True
            )
        except Exception:
            pass
        return

    # Kiểm tra role
    if not any(role.id in VOICE_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        try:
            await interaction.response.send_message(
                "❌ Bạn không có quyền dùng lệnh này.",
                ephemeral=True
            )
        except Exception:
            pass
        return

    # Kiểm tra user có ở voice không
    if not interaction.user.voice or not interaction.user.voice.channel:
        try:
            await interaction.response.send_message(
                "❌ Bạn phải vào voice channel trước khi dùng lệnh này.",
                ephemeral=True
            )
        except Exception:
            pass
        return

    # Validate language and voice
    available_languages = tts_piper.get_available_languages()
    if language not in available_languages:
        try:
            await interaction.response.send_message(
                f"❌ Ngôn ngữ `{language}` không được hỗ trợ. Các ngôn ngữ có sẵn: {', '.join(available_languages)}",
                ephemeral=True
            )
        except Exception:
            pass
        return

    available_voices = tts_piper.get_available_voices(language)
    if voice not in available_voices:
        try:
            await interaction.response.send_message(
                f"❌ Giọng `{voice}` không tồn tại cho ngôn ngữ `{language}`. Các giọng có sẵn: {', '.join(available_voices)}",
                ephemeral=True
            )
        except Exception:
            pass
        return

    # Lưu cài đặt ngôn ngữ và giọng
    voice_settings = load_voice_settings()
    voice_settings["voice"]["language"] = language
    voice_settings["voice"]["voice"] = voice
    save_voice_settings(voice_settings)

    voice_channel = interaction.user.voice.channel

    # Lấy hoặc kết nối voice client
    vc = discord.utils.get(
        interaction.client.voice_clients,
        guild=interaction.guild
    )

    # Ẩn kênh chat với everyone và hiện với người trong voice channel
    bot_channel = interaction.guild.get_channel(1454830895453442089)
    if bot_channel:
        try:
            # CHỈ lưu trạng thái gốc LẦN ĐẦU TIÊN (khi file chưa tồn tại)
            # Sau đó KHÔNG ghi đè để giữ nguyên trạng thái gốc ban đầu
            if not os.path.exists(BOT_CHANNEL_ORIGINAL_STATE_FILE):
                save_original_bot_channel_state(bot_channel.overwrites)
                print("[talkvoice] Saved original bot channel state (first time)")
            
            # Lưu lại trạng thái ban đầu của kênh nếu chưa lưu (cho runtime)
            if not bot_channel_original_overwrites:
                bot_channel_original_overwrites.update(bot_channel.overwrites)
            
            # Xoá hết các role cũ, chỉ giữ @everyone và cập nhật thành viên trong voice
            new_overwrites = {}
            new_overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
                
            # Cấp quyền xem cho các thành viên có mặt trong voice channel
            for member in voice_channel.members:
                new_overwrites[member] = discord.PermissionOverwrite(view_channel=True)
                
            await bot_channel.edit(overwrites=new_overwrites)
            
            # Lưu trạng thái voice vào file
            save_bot_channel_state(new_overwrites)
        except Exception as e:
            print(f"[talkvoice] Lỗi đổi quyền: {e}")

    try:
        if not vc or not vc.is_connected():
            vc = await voice_channel.connect()
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)
            
        if not sync_voice_permissions.is_running():
            sync_voice_permissions.start()
    except Exception as e:
        print(f"[talkvoice] Lỗi khi kết nối voice: {e}")
        try:
            await interaction.response.send_message("❌ Có lỗi khi kết nối vào voice channel.", ephemeral=True)
        except Exception:
            pass
        return

    try:
        await interaction.response.send_message(
            f"✅ Đã cài đặt Piper TTS với ngôn ngữ: **{language}**, giọng: **{voice}**\n"
            f"Sử dụng `!voice <text>` để bot đọc.",
            ephemeral=True
        )
    except Exception:
        pass

@client.event
async def on_voice_state_update(member, before, after):
    # Chỉ xử lý khi bot đang ở voice
    if not member.guild:
        return

    vc = discord.utils.get(client.voice_clients, guild=member.guild)
    if not vc or not vc.is_connected():
        return

    channel = vc.channel
    guild = member.guild

    # Nếu còn người (ngoài bot) → hủy auto-disconnect
    if len(channel.members) > 1:
        task = auto_disconnect_tasks.pop(guild.id, None)
        if task:
            task.cancel()
        return

    # Nếu chỉ còn bot → tạo task auto-disconnect với đầy đủ cleanup
    if guild.id not in auto_disconnect_tasks:
        async def delayed_disconnect():
            await asyncio.sleep(5)  # Giảm xuống 5 giây để nhạy bén hơn
            
            # Kiểm tra lại trước khi disconnect
            if not vc.is_connected() or len(channel.members) > 1:
                return
            
            # Dừng sync permissions
            if sync_voice_permissions.is_running():
                sync_voice_permissions.cancel()
            
            # Xóa các tin nhắn talkgoogle chưa tới 20s
            for msg in list(pending_talkgoogle_messages):
                try:
                    await msg.delete()
                except Exception:
                    pass
            pending_talkgoogle_messages.clear()

            # Xóa các tin nhắn talkvoice chưa tới 20s
            for msg in list(pending_talkvoice_messages):
                try:
                    await msg.delete()
                except Exception:
                    pass
            pending_talkvoice_messages.clear()

            # Khôi phục quyền kênh chat về trạng thái gốc từ file
            bot_channel = guild.get_channel(1454830895453442089)
            if bot_channel:
                try:
                    await restore_original_bot_channel_state(guild)
                    print("[AUTO-DISCONNECT] Restored original bot channel state")
                except Exception as e:
                    print(f"[AUTO-DISCONNECT] Lỗi khôi phục quyền: {e}")

            # Disconnect
            await vc.disconnect()
            print(f"[AUTO-DISCONNECT] Bot đã rời voice channel")

        auto_disconnect_tasks[guild.id] = asyncio.create_task(
            delayed_disconnect()
        )


# --------------------- SLASH COMMAND RỜI VOICE ---------------------
@client.tree.command(
    name="leavevoice",
    description="Bot rời voice channel",
    guild=discord.Object(id=GUILD_ID)
)
async def leavevoice(interaction: discord.Interaction):
    # Kiểm tra role của người dùng
    if not any(role.id in VOICE_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    if sync_voice_permissions.is_running():
        sync_voice_permissions.cancel()

    # Xóa các tin nhắn talkgoogle chưa tới 20s
    for msg in list(pending_talkgoogle_messages):
        try:
            await msg.delete()
        except Exception:
            pass
    pending_talkgoogle_messages.clear()

    # Xóa các tin nhắn talkvoice chưa tới 20s
    for msg in list(pending_talkvoice_messages):
        try:
            await msg.delete()
        except Exception:
            pass
    pending_talkvoice_messages.clear()

    # Khôi phục quyền kênh chat về trạng thái gốc từ file
    bot_channel = interaction.guild.get_channel(1454830895453442089)
    if bot_channel:
        try:
            await restore_original_bot_channel_state(interaction.guild)
            print("[leavevoice] Restored original bot channel state")
        except Exception as e:
            print(f"[leavevoice] Lỗi khôi phục quyền: {e}")

    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)
    if vc and vc.is_connected():
        await vc.disconnect()
        await interaction.response.send_message("👋 Bot đã rời voice channel và khôi phục kênh chat.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Bot không ở trong voice channel nào.", ephemeral=True)

# --------------------- SLASH COMMAND JAIL SYSTEM ---------------------
@client.tree.command(
    name="jail",
    description="Giam người vi phạm vào nhà tù",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Người bị giam",
    keyword="Từ khóa cần spam để ra tù",
    count="Số lần cần spam từ khóa",
    reason="Lý do bị giam"
)
async def jail(
    interaction: discord.Interaction,
    member: discord.Member,
    keyword: str,
    count: int,
    reason: str
):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    # Kiểm tra admin role
    if not any(role.id == JAIL_ADMIN_ROLE_ID for role in interaction.user.roles):
        await interaction.followup.send("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return
    
    # Kiểm tra số lượng spam
    if count <= 0:
        await interaction.followup.send("❌ Số lần spam phải lớn hơn 0.", ephemeral=True)
        return
    
    # Lưu role cũ của người bị giam
    old_roles = [role.id for role in member.roles if not role.is_bot_managed() and role.id != member.guild.id]
    
    # Gán role tù nhân
    jail_role = interaction.guild.get_role(JAIL_ROLE_ID)
    if not jail_role:
        await interaction.followup.send("❌ Không tìm thấy role tù nhân.", ephemeral=True)
        return
    
    try:
        await member.add_roles(jail_role)
        # Xóa các role cũ (trừ role tù nhân và @everyone)
        for role_id in old_roles:
            role = interaction.guild.get_role(role_id)
            if role and role.id != JAIL_ROLE_ID:
                await member.remove_roles(role)
        
        # Lưu trạng thái vào file
        jail_state = load_jail_state()
        jail_state[str(member.id)] = {
            "keyword": keyword,
            "required_count": count,
            "current_count": 0,
            "last_spam_time": 0,
            "old_roles": old_roles,
            "bot_messages": [],  # ID các tin nhắn bot thông báo số lượng còn lại
            "reason": reason  # Lý do bị giam
        }
        save_jail_state(jail_state)
        
        # Gửi tin nhắn thông báo vào kênh nhà tù
        jail_channel = interaction.guild.get_channel(JAIL_CHANNEL_ID)
        if jail_channel:
            initial_msg = await jail_channel.send(
                f"🔒 **{member.mention} đã bị giam vào nhà tù!**\n\n"
                f"**Lý do:** {reason}\n"
                f"**Người giam:** {interaction.user.mention}\n\n"
                f"Để được thả, bạn cần spam từ khóa `{keyword}` đúng {count} lần.\n"
                f"Bắt đầu spam ngay bây giờ!"
            )
            jail_state[str(member.id)]["bot_messages"].append(initial_msg.id)
            save_jail_state(jail_state)
        
        await interaction.followup.send(f"🔒 Đã giam {member.mention} vào nhà tù. Cần spam `{keyword}` {count} lần để ra tù.", ephemeral=True)
        print(f"[JAIL] Giam {member.id} - Keyword: {keyword}, Count: {count}, Reason: {reason}")
    except Exception as e:
        await interaction.followup.send(f"❌ Lỗi khi giam: {e}", ephemeral=True)
        print(f"[JAIL] Lỗi: {e}")

@client.tree.command(
    name="unjail",
    description="Thả tù nhân ra khỏi nhà tù",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Người được thả"
)
async def unjail(
    interaction: discord.Interaction,
    member: discord.Member
):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    # Kiểm tra admin role
    if not any(role.id == JAIL_ADMIN_ROLE_ID for role in interaction.user.roles):
        await interaction.followup.send("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return
    
    # Kiểm tra xem người đó có trong jail state không
    jail_state = load_jail_state()
    if str(member.id) not in jail_state:
        await interaction.followup.send("❌ Người này không đang bị giam.", ephemeral=True)
        return
    
    try:
        # Khôi phục role cũ
        old_roles = jail_state[str(member.id)]["old_roles"]
        for role_id in old_roles:
            role = interaction.guild.get_role(role_id)
            if role:
                await member.add_roles(role)
        
        # Xóa role tù nhân
        jail_role = interaction.guild.get_role(JAIL_ROLE_ID)
        if jail_role:
            await member.remove_roles(jail_role)
        
        # Xóa toàn bộ tin nhắn trong kênh nhà tù
        jail_channel = interaction.guild.get_channel(JAIL_CHANNEL_ID)
        if jail_channel:
            async for msg in jail_channel.history(limit=None):
                try:
                    await msg.delete()
                except Exception:
                    pass
        
        # Xóa khỏi jail state
        del jail_state[str(member.id)]
        save_jail_state(jail_state)
        
        await interaction.followup.send(f"🔓 Đã thả {member.mention} khỏi nhà tù.", ephemeral=True)
        print(f"[JAIL] Thả {member.id}")
    except Exception as e:
        await interaction.followup.send(f"❌ Lỗi khi thả: {e}", ephemeral=True)
        print(f"[JAIL] Lỗi: {e}")

# --------------------- SLASH COMMAND GỬI TIN NHẮN RIÊNG --------------------
INBOX_ALLOWED_ROLE_IDS = [1452965477319049359]  # Role được phép

@client.tree.command(
    name="inbox",
    description="Gửi tin nhắn inbox cho một thành viên",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Người nhận tin nhắn",
    message="Nội dung bắt buộc"
)
async def inbox(
    interaction: discord.Interaction,
    member: discord.Member,
    message: str
):

    state = load_ai_state()

    if state["enabled"]:
        await interaction.response.send_message(
            "❌ Không thể dùng /inbox khi AI đang bật.",
            ephemeral=True
        )
        return
    # Kiểm tra quyền role
    if not any(role.id in INBOX_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message(
            "❌ Bạn không có quyền dùng lệnh này.",
            ephemeral=True
        )
        return

    # Chống gửi chuỗi rỗng
    if not message.strip():
        await interaction.response.send_message(
            "❌ Nội dung tin nhắn không được để trống.",
            ephemeral=True
        )
        return

    try:
        # Gửi DM
        await member.send(message)

        # Lưu session DM
        dm_sessions[member.id] = {
            "sender_id": interaction.user.id
        }

        # ----- EMBED XÁC NHẬN -----
        embed = discord.Embed(
            title="📤 Inbox đã gửi thành công",
            description=message,
            color=discord.Color.green()
        )

        embed.add_field(
            name="👤 Người nhận",
            value=member.mention,
            inline=False
        )

        embed.set_footer(
            text="📬 Hãy kiểm tra Inbox để nhận phản hồi từ người dùng"
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ Không thể gửi inbox cho {member.mention}. (Họ đã tắt inbox)",
            ephemeral=True
        )
ai_dm_users_lock = asyncio.Lock()
async def remove_ai_dm_user(user_id: int):
    async with ai_dm_users_lock:
        users = load_ai_dm_users()

        if user_id in users:
            users.remove(user_id)
            save_ai_dm_users(users)
async def add_ai_dm_user(user_id: int):
    async with ai_dm_users_lock:
        users = load_ai_dm_users()

        if user_id not in users:
            users.append(user_id)
            save_ai_dm_users(users)
#---phản hồi tin nhắn----
async def handle_dm_reply(message: discord.Message):
    if not isinstance(message.channel, discord.DMChannel):
        return

    state = load_ai_state()

    # ================= AI DM (*ai) =================
    if state["enabled"] and message.content.lower().startswith(AI_DM_PREFIX.lower()):
        if not remember_processed_message(message.id, processed_dm_message_ids):
            return

        await process_unified_ai_logic(message, AI_DM_PREFIX)
        return

    if state["enabled"] and not message.content.lower().startswith(AI_DM_PREFIX.lower()):
        await message.channel.send(
            "❌ Inbox hiện đang tạm ngưng vì AI đang bật."
        )
        return

    # ================= NORMAL DM HANDLING =================
    user_id = message.author.id
    if user_id not in dm_sessions:
        # Nếu không có session, gửi về admin mặc định nếu cần (giữ logic cũ)
        return

    session = dm_sessions[user_id]
    sender = client.get_user(session["sender_id"])
    if sender is None:
        return

    main_embed = discord.Embed(
        title="📩 Phản hồi inbox",
        color=discord.Color.orange()
    )

    main_embed.set_author(
        name=str(message.author),
        icon_url=message.author.display_avatar.url
    )

    has_text = bool(message.content and message.content.strip())
    has_attachments = len(message.attachments) > 0

    if has_text:
        main_embed.description = message.content
    elif has_attachments:
        main_embed.description = "📎 Người dùng đã gửi tệp đính kèm"
    else:
        main_embed.description = "*[Không có nội dung]*"

    embeds_to_send = [main_embed]

    for att in message.attachments:
        if att.content_type and att.content_type.startswith("image"):
            img_embed = discord.Embed(
                title="🖼 Hình ảnh phản hồi",
                color=discord.Color.orange()
            )
            img_embed.set_image(url=att.url)
            embeds_to_send.append(img_embed)

        elif att.filename.lower().endswith((".mp4", ".mov", ".mkv", ".wmv")):
            video_embed = discord.Embed(
                title="🎥 Video phản hồi",
                description=f"[🎬 {att.filename}]({att.url})",
                color=discord.Color.orange()
            )
            embeds_to_send.append(video_embed)

        else:
            file_embed = discord.Embed(
                title="📎 Tệp đính kèm",
                description=f"[{att.filename}]({att.url})",
                color=discord.Color.orange()
            )
            embeds_to_send.append(file_embed)

    try:
        for emb in embeds_to_send:
            await sender.send(
                content="🔔 Bạn nhận được **phản hồi inbox**:",
                embed=emb
            )
    except discord.Forbidden:
        pass

#--- phản hồi reaction vào DM của bot ---
@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):

    # Bỏ qua bot
    if user.bot:
        return

    message = reaction.message

    # Chỉ xử lý DM
    if not isinstance(message.channel, discord.DMChannel):
        return

    # Chỉ xử lý reaction vào tin nhắn DO BOT GỬI
    if message.author != client.user:
        return

    user_id = user.id
    if user_id not in dm_sessions:
        return

    session = dm_sessions[user_id]
    sender = client.get_user(session["sender_id"])
    if sender is None:
        return

    emoji = str(reaction.emoji)

    embed = discord.Embed(
        title="📩 Phản hồi inbox bằng reaction",
        color=discord.Color.orange()
    )

    embed.set_author(
        name=str(user),
        icon_url=user.display_avatar.url
    )

    embed.add_field(
        name="-loại emoji phản hồi",
        value=emoji,
        inline=False
    )

    if message.content:
        embed.add_field(
            name="📝 Tin nhắn được phản hồi",
            value=message.content[:1024],
            inline=False
        )

    # Attachments của tin nhắn bot
    image_used = False
    file_links = []

    for att in message.attachments:
        if att.content_type and att.content_type.startswith("image") and not image_used:
            embed.set_image(url=att.url)
            image_used = True
        else:
            file_links.append(f"[{att.filename}]({att.url})")

    if file_links:
        embed.add_field(
            name="📎 Tệp đính kèm",
            value="\n".join(file_links),
            inline=False
        )

    try:
        await sender.send(
            content="🔔 Người dùng đã **phản hồi bằng reaction**:",
            embed=embed
        )
    except discord.Forbidden:
        pass


# --------------------- SLASH COMMAND XÓA TIN NHẮN RIÊNG ---------------------
@client.tree.command(
    name="delete_inbox",
    description="Thu hồi (xóa) tin nhắn DM gần nhất bot đã gửi cho một người dùng"
)
@app_commands.describe(
    member="Người dùng cần thu hồi inbox"
)
async def delete_inbox(interaction: discord.Interaction, member: discord.Member):

    # Chỉ dùng trong server
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.",
            ephemeral=True
        )
        return

    # Kiểm tra role
    if not any(role.id in INBOX_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message(
            "❌ Bạn không có quyền dùng lệnh này.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"🔄 Đang thu hồi inbox gần nhất của bot với **{member}**...",
        ephemeral=True
    )

    try:
        dm = await member.create_dm()
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Không thể mở inbox với người dùng này.",
            ephemeral=True
        )
        return

    # Tìm DUY NHẤT 1 tin nhắn bot gửi gần nhất
    async for msg in dm.history(limit=20, oldest_first=False):
        if msg.author == interaction.client.user:
            try:
                await msg.delete()
                await interaction.followup.send(
                    f"✅ Đã thu hồi **tin nhắn DM gần nhất** gửi cho {member.mention}.",
                    ephemeral=True
                )
                return
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Bot không có quyền xóa tin nhắn này.",
                    ephemeral=True
                )
                return
            except discord.HTTPException:
                await interaction.followup.send(
                    "⚠️ Không thể xóa tin nhắn (có thể do rate-limit).",
                    ephemeral=True
                )
                return

    # Không tìm thấy tin bot
    await interaction.followup.send(
        f"⚠️ Không tìm thấy tin nhắn nào của bot để thu hồi cho {member.mention}.",
        ephemeral=True
    )
# --------------------- SLASH COMMAND XÓA TOÀN BỘ TIN NHẮN RIÊNG ---------------------
@client.tree.command(
    name="clear_inbox",
    description="Xóa toàn bộ inbox mà bot đã gửi cho một người dùng"
)
@app_commands.describe(
    target_user="Người dùng cần xóa inbox của bot"
)
async def clear_dm_user(interaction: discord.Interaction, target_user: discord.User):

    # ✅ Chỉ cho dùng trong server
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.",
            ephemeral=True
        )
        return

    # ✅ Kiểm tra quyền
    if not any(role.id in INBOX_ALLOWED_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message(
            "❌ Bạn không có quyền dùng lệnh này.",
            ephemeral=True
        )
        return

    # ✅ Không cho xóa khi AI đang bật
    state = load_ai_state()
    if state["enabled"]:
        await interaction.response.send_message(
            "❌ Không thể dùng /clear_inbox khi AI đang bật.",
            ephemeral=True
        )
        return

    # ✅ Defer sau khi đã pass toàn bộ check
    await interaction.response.defer(ephemeral=True)

    try:
        dm = await target_user.create_dm()

        deleted_count = 0

        async for msg in dm.history(limit=None):
            if msg.author == interaction.client.user:
                await msg.delete()
                deleted_count += 1
                await asyncio.sleep(0.8)

        # 🔥 reset AI user
        await remove_ai_dm_user(target_user.id)

        await interaction.followup.send(
            f"✅ Đã xóa **{deleted_count}** tin nhắn bot gửi cho {target_user.mention}.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Bot không có quyền truy cập DM người này.",
            ephemeral=True
        )

    except Exception as e:
        await interaction.followup.send(
            "❌ Không thể xóa DM.",
            ephemeral=True
        )

# --------------------- SLASH COMMAND GỬI TIN NHẮN VÀO KÊNH ---------------------
SAY_ALLOWED_ROLE_IDS = [1452965477319049359, 1452968245509226619]  # Role được phép dùng
@client.tree.command(
    name="saytochannel",
    description="Gửi tin nhắn đến kênh chỉ định thay cho bạn",
    guild=discord.Object(id=GUILD_ID)
)
@discord.app_commands.describe(
    channel="Chọn kênh để gửi tin nhắn",
    message="Nội dung tin nhắn"
)
async def saytochannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str
):
    # Chỉ dùng trong server
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.",
            ephemeral=True
        )
        return

    member: discord.Member = interaction.user

    # Kiểm tra role
    if not any(role.id in SAY_ALLOWED_ROLE_IDS for role in member.roles):
        await interaction.response.send_message(
            "❌ Bạn không có quyền dùng lệnh này.",
            ephemeral=True
        )
        return

    try:
        await channel.send(f"💬 {message}")
        await interaction.response.send_message(
            f"✅ Tin nhắn đã được gửi đến {channel.mention}.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ Bot không có quyền gửi tin nhắn đến {channel.mention}.",
            ephemeral=True
        )

    except Exception as e:
        await interaction.response.send_message(
            f"❌ Đã xảy ra lỗi: `{e}`",
            ephemeral=True
        )
        
#-----------------------------------BẬT NHẠC TỪ YOUTUBE--------------------------------
BOT_CHANNEL_ID = 1454830895453442089
# Danh sách ROLE ID được phép dùng các action command
MUSIC_ROLE_IDS = {
    1452965477319049359,
    1452968245509226619
}
async def music_allowed_role(interaction: discord.Interaction) -> bool:
    """
    Trả về True nếu user có ít nhất 1 role hợp lệ
    """
    user_role_ids = {role.id for role in interaction.user.roles}

    return not MUSIC_ROLE_IDS.isdisjoint(user_role_ids)
async def ensure_music_channel(interaction: discord.Interaction) -> bool:
    if interaction.channel.id != BOT_CHANNEL_ID:
        await interaction.response.send_message(
            "⚠️ Lệnh nhạc chỉ được dùng trong kênh bot <#1454830895453442089>.",
            ephemeral=True
        )
        return False
    return True
#--------------------------------------------PHÁT NHẠC YOUTUBE-------------------------
music_state = {}
music_playing = set()   # guild_id đang phát YouTube

def format_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def get_video_duration(info: dict) -> int:
    duration = info.get("duration")
    if duration is None:
        return 0
    try:
        return int(float(duration))
    except (TypeError, ValueError):
        return 0

def build_song_info(info: dict, source_url: str, file_path: str = None) -> dict:
    duration = get_video_duration(info)
    return {
        "title": info.get("title", "Không rõ"),
        "url": info.get("url", ""),  # Keep for compatibility but won't be used for playback
        "source_url": info.get("webpage_url") or info.get("original_url") or source_url,
        "video_id": info.get("id", ""),
        "duration": duration,
        "duration_seconds": duration,
        "duration_text": format_duration(duration) if duration else "Không rõ",
        "file_path": file_path,
        "http_headers": info.get("http_headers") or {}
    }

def extract_song_info(url: str) -> dict:
    """Extract song info using yt-dlp.exe with fallback methods"""
    import subprocess
    import json
    
    # Build base command
    def build_cmd(extra_args=[]):
        cmd = [
            YTDLP_EXECUTABLE,
            "--dump-json",
            "--no-playlist",
            "--no-check-certificate",
            "--ignore-errors",
            "--extract-flat", "false",
            "--no-warnings",
            url
        ]
        cmd.extend(extra_args)
        return cmd
    
    # Try primary extraction
    try:
        cmd = build_cmd([
            "--extractor-args", "youtube:player_client=android,android_music,web",
            "--extractor-args", "youtube:player_skip=js,configs,age_gate",
            "--extractor-args", "youtube:age_gate=False",
        ])
        print(f"[EXTRACT] Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            info = json.loads(result.stdout)
            video_id = info.get("id", "")
            file_path = os.path.join(MUSIC_CACHE_DIR, f"{video_id}.mp3")
            return build_song_info(info, url, file_path)
    except Exception as e:
        print(f"[EXTRACT] Primary extraction failed: {e}")
    
    # Fallback 1: Simpler options
    try:
        cmd = build_cmd([
            "--extractor-args", "youtube:player_client=android",
        ])
        print(f"[EXTRACT] Fallback 1: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            info = json.loads(result.stdout)
            video_id = info.get("id", "")
            file_path = os.path.join(MUSIC_CACHE_DIR, f"{video_id}.mp3")
            return build_song_info(info, url, file_path)
    except Exception as e:
        print(f"[EXTRACT] Fallback 1 failed: {e}")
    
    # Fallback 2: Minimal options
    try:
        cmd = build_cmd([])
        print(f"[EXTRACT] Fallback 2: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            info = json.loads(result.stdout)
            video_id = info.get("id", "")
            file_path = os.path.join(MUSIC_CACHE_DIR, f"{video_id}.mp3")
            return build_song_info(info, url, file_path)
    except Exception as e:
        print(f"[EXTRACT] Fallback 2 failed: {e}")
        raise

def download_song(song: dict) -> bool:
    """Download YouTube audio to local MP3 file using yt-dlp.exe with fallback methods"""
    import subprocess
    
    source_url = song.get("source_url") or song.get("webpage_url")
    if not source_url:
        print("[DOWNLOAD] No source URL found")
        return False

    video_id = song.get("video_id")
    if not video_id:
        print("[DOWNLOAD] No video ID found")
        return False

    file_path = os.path.join(MUSIC_CACHE_DIR, f"{video_id}.mp3")
    
    # Check if file already exists
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        print(f"[DOWNLOAD] File already exists: {file_path}")
        song["file_path"] = file_path
        return True

    print(f"[DOWNLOAD] Downloading {video_id} to {file_path}")
    
    # Build base command
    def build_cmd(extra_args=[]):
        cmd = [
            YTDLP_EXECUTABLE,
            "--format", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "192K",
            "--no-playlist",
            "--no-check-certificate",
            "--ignore-errors",
            "--extract-flat", "false",
            "--no-warnings",
            "--output", os.path.join(MUSIC_CACHE_DIR, "%(id)s.%(ext)s"),
            source_url
        ]
        cmd.extend(extra_args)
        return cmd
    
    # Try primary download
    try:
        cmd = build_cmd([
            "--extractor-args", "youtube:player_client=android,android_music,web",
            "--extractor-args", "youtube:player_skip=js,configs,age_gate",
            "--extractor-args", "youtube:age_gate=False",
            "--retries", "5",
            "--fragment-retries", "5",
        ])
        print(f"[DOWNLOAD] Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                print(f"[DOWNLOAD] Successfully downloaded: {file_path}")
                song["file_path"] = file_path
                return True
            else:
                print(f"[DOWNLOAD] File not created or empty: {file_path}")
        else:
            print(f"[DOWNLOAD] Primary download failed with code {result.returncode}")
            print(f"[DOWNLOAD] stderr: {result.stderr}")
    except Exception as e:
        print(f"[DOWNLOAD] Primary download failed: {e}")
    
    # Fallback 1: Simpler options
    try:
        print(f"[DOWNLOAD] Trying fallback 1...")
        cmd = build_cmd([
            "--extractor-args", "youtube:player_client=android",
        ])
        print(f"[DOWNLOAD] Fallback 1: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                print(f"[DOWNLOAD] Fallback 1 succeeded: {file_path}")
                song["file_path"] = file_path
                return True
    except Exception as e:
        print(f"[DOWNLOAD] Fallback 1 failed: {e}")
    
    # Fallback 2: Minimal options
    try:
        print(f"[DOWNLOAD] Trying fallback 2...")
        cmd = build_cmd([])
        print(f"[DOWNLOAD] Fallback 2: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                print(f"[DOWNLOAD] Fallback 2 succeeded: {file_path}")
                song["file_path"] = file_path
                return True
    except Exception as e:
        print(f"[DOWNLOAD] Fallback 2 failed: {e}")
    
    # All methods failed - clean up partial file
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            print(f"[DOWNLOAD] Cleaned up partial file: {file_path}")
        except Exception as cleanup_error:
            print(f"[DOWNLOAD] Error cleaning up partial file: {cleanup_error}")
    
    return False

def cleanup_old_cache_files(max_age_hours: int = 168):
    """Remove cache files older than max_age_hours (default 7 days)"""
    if not os.path.exists(MUSIC_CACHE_DIR):
        return
    
    import time
    current_time = time.time()
    max_age_seconds = max_age_hours * 3600
    deleted_count = 0
    
    try:
        for filename in os.listdir(MUSIC_CACHE_DIR):
            if not filename.endswith(".mp3"):
                continue
            
            file_path = os.path.join(MUSIC_CACHE_DIR, filename)
            file_age = current_time - os.path.getmtime(file_path)
            
            # Check if file is currently being played
            is_playing = False
            for guild_id, state in music_state.items():
                current = state.get("current")
                if current and current.get("file_path") == file_path:
                    if state.get("playing"):
                        is_playing = True
                        break
            
            if is_playing:
                print(f"[CLEANUP] Skipping currently playing file: {filename}")
                continue
            
            if file_age > max_age_seconds:
                try:
                    os.remove(file_path)
                    print(f"[CLEANUP] Deleted old file: {filename} (age: {file_age/3600:.1f}h)")
                    deleted_count += 1
                except Exception as e:
                    print(f"[CLEANUP] Error deleting {filename}: {e}")
        
        if deleted_count > 0:
            print(f"[CLEANUP] Deleted {deleted_count} old cache files")
    except Exception as e:
        print(f"[CLEANUP] Error during cleanup: {e}")

def parse_seek_time(value: str) -> int:
    parts = value.strip().split(":")
    if not parts or len(parts) > 3 or any(part.strip() == "" for part in parts):
        raise ValueError

    numbers = [int(part) for part in parts]
    if any(number < 0 for number in numbers):
        raise ValueError

    if len(numbers) == 1:
        return numbers[0]

    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            raise ValueError
        return minutes * 60 + seconds

    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        raise ValueError
    return hours * 3600 + minutes * 60 + seconds

@client.tree.command(
    name="play",
    description="Phát nhạc từ YouTube (chỉ âm thanh)",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(url="Link YouTube")
async def play(interaction: discord.Interaction, url: str):

    # 🔒 Kiểm tra role
    if not await music_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    # 🔍 Kiểm tra kênh hợp lệ
    if not await ensure_music_channel(interaction):
        return

    guild_id = interaction.guild.id

    # ❌ Nếu đang TTS → chặn
    if guild_id in tts_playing:
        await interaction.response.send_message(
            "❌ Bot đang đọc talkgoogle. Không thể phát nhạc lúc này.",
            ephemeral=True
        )
        return

    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            "❌ Bạn phải vào voice channel trước.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    state = get_music_state(guild_id)

    voice_channel = interaction.user.voice.channel
    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)

    print(f"[PLAY] Voice channel: {voice_channel.name if voice_channel else 'None'}")
    print(f"[PLAY] Current VC: {vc}")
    print(f"[PLAY] VC connected: {vc.is_connected() if vc else 'N/A'}")

    if not vc:
        print("[PLAY] Connecting to voice channel...")
        vc = await voice_channel.connect()
        print(f"[PLAY] Connected successfully: {vc}")
    elif vc.channel != voice_channel:
        print("[PLAY] Moving to voice channel...")
        await vc.move_to(voice_channel)
        print(f"[PLAY] Moved successfully")

    state["voice"] = vc

    try:
        song = await asyncio.to_thread(extract_song_info, url)

        # 🎵 ĐANG PHÁT → QUEUE
        if state["playing"]:
            if len(state["queue"]) >= 5:
                await interaction.followup.send(
                    "❌ Queue tối đa 5 bài.",
                    ephemeral=True
                )
                return

            state["queue"].append(song)
            await interaction.followup.send(
                f"➕ Đã thêm vào queue: **{song['title']}**\n"
                f"⏱️ Duration: **{song['duration_text']}**",
                ephemeral=True
            )
            return

        # ▶️ CHƯA PHÁT → PHÁT
        state["queue"].insert(0, song)
        await play_next(interaction.guild)

        await interaction.followup.send(
            f"🎵 Đang phát: **{song['title']}**\n"
            f"⏱️ Duration: **{song['duration_text']}**",
            ephemeral=True
        )

    except Exception:
        await interaction.followup.send(
            "❌ Không thể phát video này.",
            ephemeral=True
        )

@client.tree.command(name="pause", description="Tạm dừng nhạc", guild=discord.Object(id=GUILD_ID))
async def pause(interaction: discord.Interaction):
    # 🔒 Kiểm tra role
    if not await music_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return
    
    # 🔍 Kiểm tra kênh hợp lệ
    if not await ensure_music_channel(interaction):
        return

    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Đã tạm dừng.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Không có nhạc đang phát.", ephemeral=True)

@client.tree.command(name="resume", description="Tiếp tục phát nhạc", guild=discord.Object(id=GUILD_ID))
async def resume(interaction: discord.Interaction):
    # 🔒 Kiểm tra role
    if not await music_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    # 🔍 Kiểm tra kênh hợp lệ
    if not await ensure_music_channel(interaction):
        return

    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Tiếp tục phát.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Không có nhạc bị tạm dừng.", ephemeral=True)

@client.tree.command(name="stop", description="Dừng nhạc và rời voice", guild=discord.Object(id=GUILD_ID))
async def stop(interaction: discord.Interaction):
    # 🔒 Kiểm tra role
    if not await music_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    # 🔍 Kiểm tra kênh hợp lệ
    if not await ensure_music_channel(interaction):
        return

    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("⏹️ Đã dừng và rời voice", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Bot không ở voice.", ephemeral=True)

def get_music_state(guild_id: int):
    if guild_id not in music_state:
        music_state[guild_id] = {
            "queue": [],
            "playing": False,
            "voice": None,
            "current": None,   # 🎵 bài đang phát
            "current_duration": 0,
            "replay": False,
            "seeking": False,
            "play_id": 0,
            "seek_lock": asyncio.Lock()
        }
    else:
        music_state[guild_id].setdefault("current_duration", 0)
        music_state[guild_id].setdefault("play_id", 0)
        music_state[guild_id].setdefault("seeking", False)
        if "seek_lock" not in music_state[guild_id]:
            music_state[guild_id]["seek_lock"] = asyncio.Lock()
    return music_state[guild_id]
async def play_next(guild: discord.Guild):
    guild_id = guild.id
    state = get_music_state(guild_id)
    vc = state["voice"]

    if not vc or not vc.is_connected():
        state["playing"] = False
        music_playing.discard(guild_id)
        return

    if not state["queue"]:
        state["playing"] = False
        music_playing.discard(guild_id)
        return

    song = state["queue"].pop(0)
    state["current"] = song
    state["current_duration"] = song.get("duration_seconds") or song.get("duration") or 0
    state["playing"] = True
    state["play_id"] = state.get("play_id", 0) + 1
    play_id = state["play_id"]
    music_playing.add(guild_id)

    def after_music(err):
        print(f"[AFTER_MUSIC] Callback called, play_id: {play_id}, current play_id: {state.get('play_id')}")
        if state.get("play_id") != play_id:
            print("[AFTER_MUSIC] Play ID mismatch, ignoring")
            return

        if state.get("seeking"):
            print("[AFTER_MUSIC] Seeking, ignoring")
            music_playing.discard(guild_id)
            return

        if err:
            print(f"[FFMPEG ERROR] {err}")
            import traceback
            traceback.print_exc()

        print("[AFTER_MUSIC] Song finished, playing next")
        music_playing.discard(guild_id)
        state["playing"] = False

        # 🔁 Replay
        if state.get("replay") and state.get("current"):
            state["queue"].insert(0, state["current"])
            asyncio.run_coroutine_threadsafe(
                play_next(guild),
                client.loop
            )
            return

        # ▶️ Bài tiếp theo
        asyncio.run_coroutine_threadsafe(
            play_next(guild),
            client.loop
        )

    try:
        await play_song(vc, song, start_time=0, after=after_music)
    except Exception as e:
        print(f"[PLAY ERROR] {e}")
        if state.get("play_id") == play_id:
            state["playing"] = False
            music_playing.discard(guild_id)
            asyncio.create_task(play_next(guild))
async def play_song(vc, song, start_time=0, after=None, download=True):
    print("[FFMPEG DEBUG] Executable:", FFMPEG_EXECUTABLE)
    print("[FFMPEG DEBUG] Exists:", os.path.isfile(FFMPEG_EXECUTABLE))
    print(f"[PLAY SONG] Function called")
    print(f"[PLAY SONG] VC object: {vc}")
    print(f"[PLAY SONG] VC is playing: {vc.is_playing() if vc else 'N/A'}")
    
    # Download song if needed
    if download:
        print("[PLAY SONG] Downloading song...")
        success = await asyncio.to_thread(download_song, song)
        if not success:
            print("[PLAY SONG] Download failed")
            raise Exception("Failed to download song")
        print("[PLAY SONG] Download completed")

    file_path = song.get("file_path")
    if not file_path or not os.path.exists(file_path):
        print(f"[PLAY SONG] File not found: {file_path}")
        raise Exception(f"File not found: {file_path}")

    print(f"[PLAY SONG] File path: {file_path}")
    print(f"[PLAY SONG] Title: {song.get('title', 'Unknown')}")
    
    # Simple FFmpeg options for local file playback
    before_options = (
        "-nostdin "
        f"-ss {int(start_time)} "
    )

    options = "-vn -dn -sn -ignore_unknown -loglevel warning"

    try:
        print("[PLAY SONG] Creating FFmpegPCMAudio source...")
        source = discord.FFmpegPCMAudio(
            file_path,
            executable=FFMPEG_EXECUTABLE,
            before_options=before_options,
            options=options
        )
        print(f"[PLAY SONG] Source created: {source}")
        
        print("[PLAY SONG] Calling vc.play()...")
        vc.play(source, after=after)
        print(f"[PLAY SONG] vc.play() called successfully")
        print(f"[PLAY SONG] VC is playing after play: {vc.is_playing()}")
    except Exception as e:
        print(f"[PLAY SONG ERROR] {e}")
        import traceback
        traceback.print_exc()
        raise

@client.tree.command(
    name="testaudio",
    description="Test âm thanh đơn giản (beep)",
    guild=discord.Object(id=GUILD_ID)
)
async def testaudio(interaction: discord.Interaction):
    """Test command để kiểm tra xem bot có thể phát âm thanh được không"""
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ Bạn phải vào voice channel trước.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    voice_channel = interaction.user.voice.channel
    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)

    print(f"[TEST AUDIO] Voice channel: {voice_channel.name}")
    print(f"[TEST AUDIO] Current VC: {vc}")

    if not vc:
        print("[TEST AUDIO] Connecting to voice...")
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        print("[TEST AUDIO] Moving to voice...")
        await vc.move_to(voice_channel)

    print(f"[TEST AUDIO] Creating simple beep source...")
    
    # Tạo một beep đơn giản bằng FFmpeg
    source = discord.FFmpegPCMAudio(
        executable=FFMPEG_EXECUTABLE,
        source="anullsrc=r=44100:cl=mono",  # Tạo silence
        before_options="-f lavfi -t 3",  # 3 giây
        options="-f s16le -ar 44100 -ac 1"
    )

    print("[TEST AUDIO] Playing beep...")
    vc.play(source)
    
    print(f"[TEST AUDIO] Is playing: {vc.is_playing()}")
    
    await interaction.followup.send("🔊 Đang test âm thanh (3 giây beep)", ephemeral=True)
    
    # Đợi 3 giây rồi dừng
    await asyncio.sleep(3)
    if vc.is_playing():
        vc.stop()
        print("[TEST AUDIO] Stopped")
    
    await interaction.followup.send("✅ Test hoàn tất", ephemeral=True)
           
@client.tree.command(
    name="queue",
    description="Xem danh sách bài hát đang chờ",
    guild=discord.Object(id=GUILD_ID)
)
async def queue(interaction: discord.Interaction):
    # 🔒 Kiểm tra role
    if not await music_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    # 🔍 Kiểm tra kênh hợp lệ
    if not await ensure_music_channel(interaction):
        return

    guild_id = interaction.guild.id
    state = get_music_state(guild_id)

    if not state["queue"]:
        await interaction.response.send_message(
            "📭 Queue hiện đang trống.",
            ephemeral=True
        )
        return

    desc = "\n".join(
        f"{i+1}. {song['title']}"
        for i, song in enumerate(state["queue"])
    )

    embed = discord.Embed(
        title="🎶 Danh sách bài hát đang chờ",
        description=desc,
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )
@client.tree.command(
    name="clearqueue",
    description="Xóa toàn bộ bài hát trong queue",
    guild=discord.Object(id=GUILD_ID)
)
async def clearqueue(interaction: discord.Interaction):
    # 🔒 Kiểm tra role
    if not await music_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    # 🔍 Kiểm tra kênh hợp lệ
    if not await ensure_music_channel(interaction):
        return

    guild_id = interaction.guild.id
    state = get_music_state(guild_id)

    if not state["queue"]:
        await interaction.response.send_message(
            "📭 Queue hiện đang trống.",
            ephemeral=True
        )
        return

    cleared = len(state["queue"])
    state["queue"].clear()

    await interaction.response.send_message(
        f"🧹 Đã xóa **{cleared}** bài hát trong queue.\n"
        f"▶️ Bài đang phát sẽ tiếp tục cho đến khi kết thúc.",
        ephemeral=True
    )

async def get_waifu_gif(action: str) -> str | None:
    """Get GIF from nekos.best API with proper error handling and retry mechanism"""
    url = f"https://nekos.best/api/v2/{action}"
    
    # Proper User-Agent as required by NEKOSBEST documentation
    headers = {
        "User-Agent": "VoorheesBot (conchochu4@gmail.com)",
        "Accept": "application/json"
    }
    
    max_retries = 3
    retry_delay = 2  # seconds
    
    for attempt in range(max_retries):
        try:
            print(f"[NEKOS API] Attempt {attempt + 1}/{max_retries}: GET {url}")
            print(f"[NEKOS API] User-Agent: {headers['User-Agent']}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, 
                    headers=headers, 
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    print(f"[NEKOS API] Status: {resp.status}")
                    print(f"[NEKOS API] Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
                    
                    # Check status code
                    if resp.status == 200:
                        content_type = resp.headers.get('Content-Type', '')
                        if 'application/json' not in content_type:
                            print(f"[NEKOS API] ERROR: Unexpected Content-Type: {content_type}")
                            print(f"[NEKOS API] Response text: {await resp.text()}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delay)
                                continue
                            return None
                        
                        data = await resp.json()
                        gif_url = data["results"][0]["url"]
                        print(f"[NEKOS API] SUCCESS: Got GIF URL")
                        return gif_url
                    
                    elif resp.status == 403:
                        error_text = await resp.text()
                        print(f"[NEKOS API] ERROR 403 Forbidden: {error_text}")
                        return None
                    
                    elif resp.status == 404:
                        print(f"[NEKOS API] ERROR 404: Action '{action}' not found")
                        return None
                    
                    elif resp.status == 429:
                        print(f"[NEKOS API] ERROR 429: Rate limited, retrying in {retry_delay}s...")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            continue
                        return None
                    
                    elif resp.status >= 500:
                        print(f"[NEKOS API] ERROR {resp.status}: Server error")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            continue
                        return None
                    
                    else:
                        print(f"[NEKOS API] ERROR {resp.status}: Unexpected status")
                        return None
        
        except aiohttp.ClientConnectorError as e:
            print(f"[NEKOS API] Connection error: {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            return None
        
        except asyncio.TimeoutError:
            print(f"[NEKOS API] Timeout error")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            return None
        
        except Exception as e:
            print(f"[NEKOS API] Unexpected error: {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            return None
    
    print(f"[NEKOS API] FAILED: All {max_retries} attempts exhausted")
    return None

@client.tree.command(
    name="skip",
    description="Bỏ qua bài hát hiện tại",
    guild=discord.Object(id=GUILD_ID)
)
async def skip(interaction: discord.Interaction):

    # 🔒 Kiểm tra role
    if not await music_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return
    
    # 🔍 Kiểm tra kênh hợp lệ
    if not await ensure_music_channel(interaction):
        return

    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)

    if not vc or not vc.is_connected():
        await interaction.response.send_message(
            "❌ Bot không ở trong voice channel.",
            ephemeral=True
        )
        return

    if not vc.is_playing():
        await interaction.response.send_message(
            "❌ Không có bài hát nào đang phát.",
            ephemeral=True
        )
        return

    guild_id = interaction.guild.id
    state = get_music_state(guild_id)

    # ⏭️ Skip bài hiện tại
    vc.stop()

    if state["queue"]:
        await interaction.response.send_message(
            "⏭️ Đã bỏ qua bài hiện tại. Đang phát bài tiếp theo...",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "⏹️ Đã bỏ qua bài hiện tại. Queue trống.",
            ephemeral=True
        )

@client.tree.command(
    name="replay",
    description="Bật / tắt phát lại bài hát hiện tại",
    guild=discord.Object(id=GUILD_ID)
)
async def replay(interaction: discord.Interaction):

    # 🔒 Kiểm tra role
    if not await music_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    # 🔍 Kiểm tra kênh hợp lệ
    if not await ensure_music_channel(interaction):
        return

    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)
    if not vc or not vc.is_connected():
        await interaction.response.send_message(
            "❌ Bot không ở trong voice channel.",
            ephemeral=True
        )
        return

    if not vc.is_playing():
        await interaction.response.send_message(
            "❌ Không có bài hát nào đang phát.",
            ephemeral=True
        )
        return

    guild_id = interaction.guild.id
    state = get_music_state(guild_id)

    # 🔁 Toggle replay
    state["replay"] = not state.get("replay", False)

    if state["replay"]:
        await interaction.response.send_message(
            "🔁 **Replay ON** – Bài hát hiện tại sẽ được phát lại liên tục.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "▶️ **Replay OFF** – Bot sẽ phát bài tiếp theo trong queue.",
            ephemeral=True
        )

@client.tree.command(
    name="seek",
    description="Tua đến thời điểm cụ thể",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(time="Thời gian muốn tua đến (vd: 90 hoặc 01:30)")
async def seek(interaction: discord.Interaction, time: str):

    if not await music_allowed_role(interaction):
        return await interaction.response.send_message("Không đủ quyền.", ephemeral=True)

    if not await ensure_music_channel(interaction):
        return

    vc = discord.utils.get(client.voice_clients, guild=interaction.guild)
    if not vc or not vc.is_connected():
        return await interaction.response.send_message("Bot không ở voice.", ephemeral=True)

    state = get_music_state(interaction.guild.id)

    if not state["current"]:
        return await interaction.response.send_message("Không có bài đang phát.", ephemeral=True)

    try:
        seek_time = parse_seek_time(time)
    except ValueError:
        return await interaction.response.send_message("Sai format thời gian.", ephemeral=True)

    duration = state.get("current_duration") or 0
    if duration and seek_time >= duration:
        return await interaction.response.send_message("Vượt quá duration.", ephemeral=True)

    async with state["seek_lock"]:

        await interaction.response.defer(ephemeral=True)

        state["seeking"] = True
        state["play_id"] += 1
        play_id = state["play_id"]

        # 🔥 STOP sạch pipeline cũ
        try:
            vc.stop()
        except:
            pass

        # Chờ một khoảng ngắn để FFmpeg cũ giải phóng handle stream
        await asyncio.sleep(0.5)

        def after_seek(err):
            if state.get("play_id") != play_id:
                return

            if err:
                print(f"[SEEK AFTER ERROR] {err}")

            music_playing.discard(interaction.guild.id)
            state["playing"] = False
            state["seeking"] = False

            asyncio.run_coroutine_threadsafe(
                play_next(interaction.guild),
                client.loop
            )

        try:
            await play_song(
                vc,
                state["current"],
                start_time=seek_time,
                after=after_seek,
                download=False
            )

            state["playing"] = True

        except Exception as e:
            print(f"[SEEK ERROR] {e}")
            state["seeking"] = False
            state["playing"] = False

            return await interaction.followup.send(
                "❌ Không thể seek bài này.",
                ephemeral=True
            )

    await interaction.followup.send(
        f"⏩ Đã tua đến {format_duration(seek_time)}",
        ephemeral=True
    )

#-----------------------------------HÀNH ĐỘNG------------------------------------
KSH_ROLE_IDS = {
    1407269404890370120  # ví dụ: Member
}
async def ksh_allowed_role(interaction: discord.Interaction) -> bool:
    """
    Trả về True nếu user có ít nhất 1 role hợp lệ
    """
    user_role_ids = {role.id for role in interaction.user.roles}

    return not KSH_ROLE_IDS.isdisjoint(user_role_ids)
@client.tree.command(
    name="kiss",
    description="Hôn một người nào đó 💋",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người bạn muốn hôn")
async def kiss(interaction: discord.Interaction, member: discord.Member):

    # 🔒 Kiểm tra role
    if not await ksh_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
        )
        return

    if member.bot:
        await interaction.response.send_message(
            "Không thể dùng lệnh này với bot 🤖",
            ephemeral=True
        )
        return

    gif_url = await get_waifu_gif("kiss")
    if gif_url is None:
        await interaction.response.send_message(
            "❌ Không thể lấy GIF từ API. Vui lòng thử lại sau.",
            ephemeral=True
        )
        return

    if member.id == interaction.user.id:
        description = f"{interaction.user.mention} tự ôm lấy chính mình 😳"
    else:
        description = f"{interaction.user.mention} hôn {member.mention} 💖"

    embed = discord.Embed(
        description=description,
        color=discord.Color.pink()
    )
    embed.set_image(url=gif_url)

    await interaction.response.send_message(embed=embed)

@client.tree.command(
    name="slap",
    description="Tát ai đó (chỉ để đùa thôi)",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người bạn muốn tát")
async def slap(interaction: discord.Interaction, member: discord.Member):

    # 🔒 Kiểm tra role
    if not await ksh_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    if member.bot:
        await interaction.response.send_message(
            "Không thể dùng lệnh này với bot 🤖",
            ephemeral=True
        )
        return

    gif_url = await get_waifu_gif("slap")
    if gif_url is None:
        await interaction.response.send_message(
            "❌ Không thể lấy GIF từ API. Vui lòng thử lại sau.",
            ephemeral=True
        )
        return

    if member.id == interaction.user.id:
        description = f"{interaction.user.mention} tự tát mình... bình tĩnh nào 😅"
    else:
        description = f"{interaction.user.mention} tát {member.mention} 👋"

    embed = discord.Embed(
        description=description,
        color=discord.Color.red()
    )
    embed.set_image(url=gif_url)

    await interaction.response.send_message(embed=embed)

@client.tree.command(
    name="hug",
    description="Ôm một người nào đó 🤗",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người bạn muốn ôm")
async def hug(interaction: discord.Interaction, member: discord.Member):

    # 🔒 Kiểm tra role
    if not await ksh_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
            )
        return

    if member.bot:
        await interaction.response.send_message(
            "Không thể dùng lệnh này với bot 🤖",
            ephemeral=True
        )
        return

    gif_url = await get_waifu_gif("hug")
    if gif_url is None:
        await interaction.response.send_message(
            "❌ Không thể lấy GIF từ API. Vui lòng thử lại sau.",
            ephemeral=True
        )
        return

    if member.id == interaction.user.id:
        description = f"{interaction.user.mention} tự ôm lấy mình 🤍"
    else:
        description = f"{interaction.user.mention} ôm {member.mention} thật chặt 🤗"

    embed = discord.Embed(
        description=description,
        color=discord.Color.green()
    )
    embed.set_image(url=gif_url)

    await interaction.response.send_message(embed=embed)

@client.tree.command(
    name="baka",
    description="Gọi ai đó là đồ ngốc🤫",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người bạn muốn gọi")
async def baka(interaction: discord.Interaction, member: discord.Member):

    # 🔒 Kiểm tra role (dùng lại hàm cũ của bạn)
    if not await ksh_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    if member.bot:
        await interaction.response.send_message(
            "Không thể dùng lệnh này với bot 🤖",
            ephemeral=True
        )
        return

    gif_url = await get_waifu_gif("baka")
    if gif_url is None:
        await interaction.response.send_message(
            "❌ Không thể lấy GIF từ API. Vui lòng thử lại sau.",
            ephemeral=True
        )
        return

    if member.id == interaction.user.id:
        description = f"{interaction.user.mention} tự nói mình là đồ ngốc 🤐"
    else:
        description = (
            f"{interaction.user.mention} chỉ vào {member.mention} "
            "đồ ngốc 🤫"
        )

    embed = discord.Embed(
        description=description,
        color=discord.Color.dark_gray()
    )
    embed.set_image(url=gif_url)

    await interaction.response.send_message(embed=embed)

@client.tree.command(
    name="laugh",
    description="Cười vào mặt ai đó 😂",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người bạn muốn cười vào")
async def laugh(interaction: discord.Interaction, member: discord.Member):

    # 🔒 Kiểm tra role
    if not await ksh_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    if member.bot:
        await interaction.response.send_message(
            "Không thể dùng lệnh này với bot 🤖",
            ephemeral=True
        )
        return

    gif_url = await get_waifu_gif("laugh")
    if gif_url is None:
        await interaction.response.send_message(
            "❌ Không thể lấy GIF từ API. Vui lòng thử lại sau.",
            ephemeral=True
        )
        return

    if member.id == interaction.user.id:
        description = f"{interaction.user.mention} tự cười chính mình 🤭"
    else:
        description = f"{interaction.user.mention} cười vào mặt {member.mention} 😂"

    embed = discord.Embed(
        description=description,
        color=discord.Color.orange()
    )
    embed.set_image(url=gif_url)

    await interaction.response.send_message(embed=embed)

@client.tree.command(
    name="stare",
    description="Nhìn chằm chằm vào ai đó 👀",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người bạn muốn nhìn chằm chằm")
async def stare(interaction: discord.Interaction, member: discord.Member):

    # 🔒 Kiểm tra role
    if not await ksh_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    if member.bot:
        await interaction.response.send_message(
            "Không thể dùng lệnh này với bot 🤖",
            ephemeral=True
        )
        return

    gif_url = await get_waifu_gif("stare")
    if gif_url is None:
        await interaction.response.send_message(
            "❌ Không thể lấy GIF từ API. Vui lòng thử lại sau.",
            ephemeral=True
        )
        return

    if member.id == interaction.user.id:
        description = f"{interaction.user.mention} tự nhìn chằm chằm vào bản thân... 🤨"
    else:
        description = f"{interaction.user.mention} nhìn chằm chằm vào {member.mention} 👀"

    embed = discord.Embed(
        description=description,
        color=discord.Color.blurple()
    )
    embed.set_image(url=gif_url)

    await interaction.response.send_message(embed=embed)

@client.tree.command(
    name="poke",
    description="Chọc nhẹ ai đó 👉",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người bạn muốn chọc")
async def poke(interaction: discord.Interaction, member: discord.Member):

    # 🔒 Kiểm tra role
    if not await ksh_allowed_role(interaction):
        await interaction.response.send_message(
            "Bạn không có role phù hợp để dùng lệnh này.",
            ephemeral=True
        )
        return

    if member.bot:
        await interaction.response.send_message(
            "Không thể dùng lệnh này với bot 🤖",
            ephemeral=True
        )
        return

    gif_url = await get_waifu_gif("poke")
    if gif_url is None:
        await interaction.response.send_message(
            "❌ Không thể lấy GIF từ API. Vui lòng thử lại sau.",
            ephemeral=True
        )
        return

    if member.id == interaction.user.id:
        description = f"{interaction.user.mention} tự chọc mình... có chuyện gì vậy? 😅"
    else:
        description = f"{interaction.user.mention} chọc {member.mention} 👉"

    embed = discord.Embed(
        description=description,
        color=discord.Color.green()
    )
    embed.set_image(url=gif_url)

    await interaction.response.send_message(embed=embed)

#--------------------------------------------AI CHAT------------------------------------------
# --------------------- CẤU HÌNH GEMINI ---------------------
AI_DM_USERS_FILE = "ai_dm_users.json"
AI_DM_PREFIX = "*ai"
AI_KEYWORD = "!ai"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
GEMINI_MODEL = "gemini-2.5-flash"  # model ổn định, hỗ trợ generateContent
AI_CHANNEL_NAME = "ai-chat"
AI_STATE_FILE = "ai_state.json"
RESET_AFTER_DAYS = 30
AI_TEMPLATE_CHANNEL_ID = 1461753399757246617  # ID kênh mẫu
ai_state = {
    "enabled": False,
    "current_channel_id": AI_TEMPLATE_CHANNEL_ID,
    "last_reset": None
}
AI_QUOTA_FILE = "ai_quota.json"
DAILY_LIMIT = 20              # đúng với free tier Gemini của bạn
WARNING_LEVEL_1 = 5            # cảnh báo vàng
WARNING_LEVEL_2 = 2            # cảnh báo đỏ
AI_COOLDOWN = 5
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
user_cooldown: dict[int, float] = {}
# ---------------- AI MEMORY ----------------
ai_user_conversations: dict[int, list[str]] = {}
MAX_HISTORY = 6  # 3 lượt hỏi + 3 lượt trả lời
# --------------------- AI STATE ---------------------
AI_CHAT_CHANNEL_ID = 1461753399757246617  # ID kênh AI cố định (bạn tự thay)
AI_ALLOWED_ROLE_IDS = [
    1407269404890370120
]
@client.tree.command(
    name="ai",
    description="Bật / Tắt AI chat (toggle)",
    guild=discord.Object(id=GUILD_ID)
)
async def ai_toggle(interaction: discord.Interaction):
    state = load_ai_state()

    # 🔒 Kiểm tra role
    if not any(r.id in AI_ALLOWED_ROLE_IDS for r in interaction.user.roles):
        await interaction.response.send_message(
            "❌ Bạn không có quyền sử dụng AI.",
            ephemeral=True
        )
        return

    # 🔍 Kiểm tra kênh hợp lệ
    allowed_channels = {AI_TEMPLATE_CHANNEL_ID, state["current_channel_id"]}
    if interaction.channel.id not in allowed_channels:
        await interaction.response.send_message(
            "⚠️ Bot không hoạt động trên kênh chat này.",
            ephemeral=True
        )
        return

    # 🔁 TOGGLE
    state["enabled"] = not state["enabled"]
    save_ai_state(state)

    if state["enabled"]:
        await interaction.response.send_message(
            "✅ **AI chat đã được BẬT.**\n"
            f"👉 Dùng `{AI_KEYWORD}` để hỏi AI.\n"
            f"📩 Dùng `{AI_DM_PREFIX}` trong inbox để hỏi AI.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "🛑 **AI chat đã được TẮT.**",
            ephemeral=True
        )

def load_ai_dm_users():
    if not os.path.exists(AI_DM_USERS_FILE):
        return []
    with open(AI_DM_USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
def save_ai_dm_users(users):
    with open(AI_DM_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f)
@client.tree.command(
    name="inboxai",
    description="Mở hướng dẫn AI chat qua DM",
    guild=discord.Object(id=GUILD_ID)
)
async def inboxai(interaction: discord.Interaction):

    state = load_ai_state()

    if not state["enabled"]:
        await interaction.response.send_message(
            "⚠️ AI hiện đang tắt. Hãy bật bằng `/ai` trước.",
            ephemeral=True
        )
        return

    try:
        await interaction.user.send(
            "🤖 **AI DM đã sẵn sàng.**\n\n"
            f"Dùng cú pháp:\n"
            f"`{AI_DM_PREFIX} nội dung`\n\n"
            "Ví dụ:\n"
            "`*ai giải thích định luật Ohm`"
        )

        await interaction.response.send_message(
            "📩 Tôi đã gửi hướng dẫn vào DM của bạn.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Bạn đã tắt DM. Không thể gửi hướng dẫn.",
            ephemeral=True
        )
@client.tree.command(
    name="checkinboxai",
    description="Xem danh sách người đã dùng AI DM",
    guild=discord.Object(id=GUILD_ID)
)
async def checkinboxai(interaction: discord.Interaction):

    # Kiểm tra quyền
    if interaction.user.id != AI_ADMIN_ID:
        await interaction.response.send_message(
            "❌ Bạn không có quyền dùng lệnh này.",
            ephemeral=True
        )
        return

    users = load_ai_dm_users()

    if not users:
        await interaction.response.send_message(
            "📭 Chưa có ai sử dụng AI qua DM.",
            ephemeral=True
        )
        return

    guild = interaction.guild

    lines = []
    for user_id in users:
        member = guild.get_member(user_id)
        if member:
            lines.append(f"{member} — `{user_id}`")
        else:
            lines.append(f"Unknown User — `{user_id}`")

    result = "\n".join(lines)

    await interaction.response.send_message(
        f"📋 **Danh sách đã dùng AI DM:**\n\n{result}",
        ephemeral=True
    )
async def process_unified_ai_logic(message: discord.Message, prefix: str):
    """Hàm xử lý AI dùng chung cho cả !ai và *ai để đồng bộ logic"""
    state = load_ai_state()
    quota = load_ai_quota()
    reset_quota_if_needed(quota)

    if quota["count"] >= DAILY_LIMIT:
        await message.channel.send(
            "🚫 **AI đã hết giới hạn request hôm nay (Gemini Free Tier).**\n"
            "⏳ Vui lòng chờ đến 7:00 AM sáng mai."
        )
        return

    if gemini_client is None:
        await message.channel.send("❌ Thiếu GEMINI_API_KEY trong file .env.")
        return

    prompt = message.content[len(prefix):].strip()
    if not prompt:
        await message.channel.send("⚠️ Bạn chưa nhập nội dung để hỏi AI.")
        return

    user_id = message.author.id
    now = time.time()
    last_time = user_cooldown.get(user_id, 0)
    if now - last_time < AI_COOLDOWN:
        await message.reply(f"⏳ Vui lòng chờ {AI_COOLDOWN} giây trước khi gửi yêu cầu tiếp theo.")
        return
    user_cooldown[user_id] = now

    history = ai_user_conversations.get(user_id, [])
    full_prompt = "You are a helpful AI assistant.\n\n"
    for h in history:
        full_prompt += h + "\n"
    full_prompt += f"User: {prompt}\nAI:"

    async with message.channel.typing():
        max_retries = 3
        response_text = None
        for attempt in range(max_retries):
            try:
                response = await gemini_client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=full_prompt
                )
                if response and response.text:
                    response_text = response.text
                    break
            except Exception as e:
                err = str(e)
                if ("503" in err or "UNAVAILABLE" in err or "429" in err or "RESOURCE_EXHAUSTED" in err) and attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                
                error_msg = f"❌ AI gặp lỗi không xác định: {err}"
                if "RESOURCE_EXHAUSTED" in err or "429" in err:
                    error_msg = "🚫 **AI đã hết giới hạn request hôm nay.**"
                elif "503" in err or "UNAVAILABLE" in err:
                    error_msg = "🚧 **Gemini đang quá tải.** Thử lại sau vài phút."
                
                await message.channel.send(error_msg)
                return

        if not response_text:
            await message.channel.send("❌ AI không phản hồi.")
            return

        # Sync DM user list
        if isinstance(message.channel, discord.DMChannel):
            await add_ai_dm_user(user_id)

        # Update history
        ai_user_conversations.setdefault(user_id, []).extend([
            f"User: {prompt}",
            f"AI: {response_text}"
        ])
        ai_user_conversations[user_id] = ai_user_conversations[user_id][-MAX_HISTORY:]

        # Update quota
        quota["count"] += 1
        save_ai_quota(quota)

        # Build warning message
        remaining = DAILY_LIMIT - quota["count"]
        warning = ""
        if remaining == 0:
            warning = "\n\n❌ Bạn đã hết request hôm nay."
        elif remaining <= WARNING_LEVEL_2:
            warning = f"\n\n🚨 **CẢNH BÁO:** Chỉ còn {remaining} request hôm nay!"
        elif remaining <= WARNING_LEVEL_1:
            warning = f"\n\n⚠️ Lưu ý: Còn {remaining} request hôm nay."

        await send_long_message(message.channel, response_text + warning)

async def handle_ai_message(message: discord.Message):
    if message.content.startswith("/"):
        return

    state = load_ai_state()
    if not state["enabled"]:
        return

    if message.channel.id not in {AI_TEMPLATE_CHANNEL_ID, state["current_channel_id"]}:
        return

    if not message.content.lower().startswith(AI_KEYWORD.lower()):
        return

    if not remember_processed_message(message.id, processed_ai_message_ids):
        return

    await process_unified_ai_logic(message, AI_KEYWORD)

async def send_long_message(channel, content: str):
    """Gửi tin nhắn dài (split 2000 chars)"""
    for i in range(0, len(content), 2000):
        await channel.send(content[i:i + 2000])


def load_ai_state():
    try:
        with open(AI_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "enabled": False,
            "current_channel_id": None,
            "last_reset": None
        }
def save_ai_state(state: dict):
    with open(AI_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
def need_weekly_reset(last_reset: str | None) -> bool:
    if not last_reset:
        return True
    last = datetime.fromisoformat(last_reset)
    return datetime.utcnow() - last >= timedelta(days=RESET_AFTER_DAYS)
async def reset_ai_channel(guild: discord.Guild, state: dict):

    old_id = state.get("current_channel_id")
    if not old_id:
        print("❌ Không có kênh AI cũ để reset")
        return

    old_channel: discord.TextChannel | None = guild.get_channel(old_id)
    if not old_channel:
        print("❌ Không tìm thấy kênh AI cũ")
        return

    # 📌 Lưu toàn bộ thuộc tính kênh cũ
    old_name = old_channel.name
    old_category = old_channel.category
    old_position = old_channel.position
    old_topic = old_channel.topic
    old_slowmode = old_channel.slowmode_delay
    old_nsfw = old_channel.nsfw
    old_overwrites = {
        target: overwrite
        for target, overwrite in old_channel.overwrites.items()
    }

    # ❌ Xóa kênh cũ
    await old_channel.delete(reason="Weekly AI channel reset")

    # ✅ Tạo kênh mới giống y hệt
    new_channel = await guild.create_text_channel(
        name=old_name,
        category=old_category,
        overwrites=old_overwrites,
        topic=old_topic,
        slowmode_delay=old_slowmode,
        nsfw=old_nsfw,
        reason="Create new weekly AI chat channel"
    )

    # 📌 Đặt lại đúng vị trí cũ
    await new_channel.edit(position=old_position)

    # 🧠 Update state
    state["current_channel_id"] = new_channel.id
    state["last_reset"] = datetime.utcnow().isoformat()
    state["enabled"] = False
    save_ai_state(state)

    await new_channel.send(
        "🆕 **Kênh AI chat mới đã được reset.**\n"
        "👉 Dùng `/ai` để bật và tắt AI."
    )

def clone_overwrites(channel: discord.TextChannel):
    overwrites = {}
    for target, overwrite in channel.overwrites.items():
        overwrites[target] = overwrite
    return overwrites
async def rotate_ai_channel(guild: discord.Guild):
    state = load_ai_state()
    template = guild.get_channel(AI_TEMPLATE_CHANNEL_ID)
    if not template:
        return

    old_id = state["current_channel_id"]

    # ❌ xóa kênh cũ (không xóa kênh mẫu)
    if old_id and old_id != AI_TEMPLATE_CHANNEL_ID:
        old = guild.get_channel(old_id)
        if old:
            await old.delete(reason="Weekly AI rotation")

    # ✅ clone permissions
    overwrites = {
        target: overwrite
        for target, overwrite in template.overwrites.items()
    }

    new_channel = await guild.create_text_channel(
        name=AI_CHANNEL_NAME,
        category=template.category,
        overwrites=overwrites,
        topic=template.topic,
        slowmode_delay=template.slowmode_delay,
        nsfw=template.nsfw,
        reason="Create new weekly AI chat"
    )

    state["current_channel_id"] = new_channel.id
    state["last_reset"] = datetime.utcnow().isoformat()
    state["enabled"] = False
    save_ai_state(state)

    await new_channel.send(
        "🆕 **Kênh AI chat mới đã được tạo.**\n"
        "👉 Dùng `/ai` để bật và tắt AI."
    )

def load_ai_quota():
    try:
        with open(AI_QUOTA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "count": 0,
            "last_reset": None
        }

def reset_quota_if_needed(quota: dict):
    """Reset chính xác vào 7:00 AM VN mỗi ngày"""
    now = datetime.now(VN_TZ)
    # Mốc reset gần nhất (7 AM hôm nay)
    target_reset = now.replace(hour=7, minute=0, second=0, microsecond=0)
    
    # Nếu hiện tại chưa tới 7 AM, mốc reset thực tế là 7 AM hôm qua
    if now < target_reset:
        target_reset -= timedelta(days=1)

    last_reset_str = quota.get("last_reset")
    needs_reset = False
    
    if not last_reset_str:
        needs_reset = True
    else:
        last_reset = datetime.fromisoformat(last_reset_str)
        if last_reset < target_reset:
            needs_reset = True

    if needs_reset:
        quota["count"] = 0
        quota["last_reset"] = target_reset.isoformat()
        save_ai_quota(quota)

def save_ai_quota(quota: dict):
    with open(AI_QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(quota, f, indent=2)
@client.tree.command(
    name="ai_clear",
    description="Xóa toàn bộ AI memory của bạn"
)
async def ai_clear(interaction: discord.Interaction):
    state = load_ai_state()
    # 🔒 Kiểm tra role
    if not any(r.id in AI_ALLOWED_ROLE_IDS for r in interaction.user.roles):
        await interaction.response.send_message(
            "❌ Bạn không có quyền sử dụng AI.",
            ephemeral=True
        )
        return

    # 🔍 Kiểm tra kênh hợp lệ
    allowed_channels = {AI_TEMPLATE_CHANNEL_ID, state["current_channel_id"]}
    if interaction.channel.id not in allowed_channels:
        await interaction.response.send_message(
            "⚠️ Bot không hoạt động trên kênh chat này.",
            ephemeral=True
        )
        return

    user_id = interaction.user.id

    if user_id not in ai_user_conversations or not ai_user_conversations[user_id]:
        await interaction.response.send_message(
            "ℹ️ **Bạn chưa có AI memory nào để xóa.**",
            ephemeral=True
        )
        return

    ai_user_conversations[user_id].clear()

    await interaction.response.send_message(
        "🧹 **Đã xóa toàn bộ AI memory của bạn.**\n"
        "🤖 Các câu hỏi tiếp theo sẽ được xem như hội thoại mới.",
        ephemeral=True
    )

async def check_inactive_users(guild: discord.Guild):
    checked_users = load_inactive_check()
    if not checked_users:
        return
    
    activity_data = load_user_activity()
    now = datetime.utcnow()
    users_to_remove = []

    channel = guild.get_channel(1465050026215477428)
    if not channel:
        print("❌ Không tìm thấy kênh để xoá reaction (1465050026215477428).")
        return
        
    try:
        msg = await channel.fetch_message(1465050575589605407)
    except Exception as e:
        print(f"❌ Không tìm thấy tin nhắn khai báo: {e}")
        return

    for user_id_str in checked_users:
        user_id = int(user_id_str)
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                users_to_remove.append(user_id_str)
                continue
            except Exception:
                continue

        last_active_str = activity_data.get(user_id_str)
        if last_active_str:
            last_active = datetime.fromisoformat(last_active_str)
        else:
            last_active = member.joined_at.replace(tzinfo=None) if member.joined_at else now

        inactive_duration = now - last_active
        if inactive_duration >= timedelta(days=30):
            # Kiểm tra lại lịch sử chat trước khi quyết định xoá
            last_time_live = None
            for txt_channel in guild.text_channels:
                perms = txt_channel.permissions_for(guild.me)
                if not perms.read_message_history or not perms.read_messages:
                    continue
                try:
                    async for msg_hist in txt_channel.history(limit=500):
                        if msg_hist.author.id == user_id:
                            if not last_time_live or msg_hist.created_at > last_time_live:
                                last_time_live = msg_hist.created_at
                            break
                except Exception:
                    continue
            
            if last_time_live:
                last_time_live_naive = last_time_live.replace(tzinfo=None)
                if last_time_live_naive > last_active:
                    last_active = last_time_live_naive
                    activity_data[user_id_str] = last_active.isoformat()
                    save_user_activity(activity_data)
                    inactive_duration = now - last_active

            if inactive_duration >= timedelta(days=30):
                try:
                    for reaction in msg.reactions:
                        async for user in reaction.users():
                            if user.id == user_id:
                                await msg.remove_reaction(reaction.emoji, member)
                    users_to_remove.append(user_id_str)
                    print(f"[INACTIVE CHECK] Đã xoá reaction của {member} vì inactive > 30 ngày.")
                except Exception as e:
                    print(f"[INACTIVE CHECK] Lỗi khi xoá reaction của {member}: {e}")
            else:
                users_to_remove.append(user_id_str)
                print(f"[INACTIVE CHECK] Đã huỷ xoá reaction và gỡ {member} khỏi danh sách kiểm tra vì phát hiện có chat mới.")

    if users_to_remove:
        new_checked = [u for u in checked_users if u not in users_to_remove]
        save_inactive_check(new_checked)


@client.tree.command(
    name="checkuser",
    description="Đưa một người dùng vào danh sách kiểm tra (inactive 30 ngày -> xoá reaction)",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người dùng cần kiểm tra")
async def checkuser(interaction: discord.Interaction, member: discord.Member):
    if interaction.user.id != AI_ADMIN_ID:
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    checked_users = load_inactive_check()
    user_id_str = str(member.id)
    if user_id_str in checked_users:
        await interaction.response.send_message(f"⚠️ {member.mention} đã nằm trong danh sách kiểm tra rồi.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    activity_data = load_user_activity()
    if user_id_str not in activity_data:
        # Tìm tin nhắn gần nhất của user trong tất cả text channels
        last_time = None
        for channel in interaction.guild.text_channels:
            perms = channel.permissions_for(interaction.guild.me)
            if not perms.read_message_history or not perms.read_messages:
                continue
            try:
                # Quét 500 tin nhắn gần nhất mỗi kênh để tìm mốc lịch sử
                async for msg in channel.history(limit=500):
                    if msg.author.id == member.id:
                        if not last_time or msg.created_at > last_time:
                            last_time = msg.created_at
                        break
            except Exception:
                continue
        if last_time:
            # Lưu lại vào file dưới dạng UTC naive
            activity_data[user_id_str] = last_time.replace(tzinfo=None).isoformat()
            save_user_activity(activity_data)

    checked_users.append(user_id_str)
    save_inactive_check(checked_users)
    await interaction.followup.send(f"✅ Đã thêm {member.mention} vào danh sách kiểm tra.", ephemeral=True)

@client.tree.command(
    name="uncheckuser",
    description="Gỡ người dùng khỏi danh sách kiểm tra",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(member="Người dùng cần gỡ kiểm tra")
async def uncheckuser(interaction: discord.Interaction, member: discord.Member):
    if interaction.user.id != AI_ADMIN_ID:
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    checked_users = load_inactive_check()
    user_id_str = str(member.id)
    if user_id_str not in checked_users:
        await interaction.response.send_message(f"⚠️ {member.mention} không nằm trong danh sách kiểm tra.", ephemeral=True)
        return

    checked_users.remove(user_id_str)
    save_inactive_check(checked_users)
    await interaction.response.send_message(f"✅ Đã gỡ {member.mention} khỏi danh sách kiểm tra.", ephemeral=True)

@client.tree.command(
    name="checkstatus",
    description="Xem danh sách người dùng đang bị kiểm tra và thời gian còn lại",
    guild=discord.Object(id=GUILD_ID)
)
async def checkstatus(interaction: discord.Interaction):
    if interaction.user.id != AI_ADMIN_ID:
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
        return

    checked_users = load_inactive_check()
    if not checked_users:
        await interaction.response.send_message("📭 Danh sách kiểm tra hiện đang trống.", ephemeral=True)
        return

    activity_data = load_user_activity()
    now = datetime.utcnow()
    lines = []

    for user_id_str in checked_users:
        user_id = int(user_id_str)
        member = interaction.guild.get_member(user_id)
        if not member:
            lines.append(f"👤 ID: `{user_id_str}` (Đã rời server, sẽ bị xóa khỏi list lần khởi động tới)")
            continue

        last_active_str = activity_data.get(user_id_str)
        if last_active_str:
            last_active = datetime.fromisoformat(last_active_str)
            base_type = "tin nhắn cuối"
        else:
            last_active = member.joined_at.replace(tzinfo=None) if member.joined_at else now
            base_type = "ngày vào server"

        # Tính chính xác thời điểm hết hạn 30 ngày
        expiration_date = last_active + timedelta(days=30)
        
        # Chuyển đổi thành Discord timestamp
        expiration_ts = int(expiration_date.replace(tzinfo=ZoneInfo("UTC")).timestamp())
        
        if now >= expiration_date:
            status_text = "Sẽ bị xử lý ở lần khởi động bot tiếp theo 🔴"
        else:
            status_text = f"Đến hạn vào <t:{expiration_ts}:f> (<t:{expiration_ts}:R>) 🟢 (từ {base_type})"

        lines.append(f"👤 {member.mention}: {status_text}")

    embed = discord.Embed(
        title="📋 Danh sách đang bị kiểm tra inactive",
        description="\n".join(lines),
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

if not BOT_TOKEN:
    print(f"❌ Không tìm thấy DISCORD_BOT_TOKEN. Hãy đặt file .env cạnh bot: {BASE_DIR}")
    if getattr(sys, "frozen", False):
        input("Nhấn Enter để thoát...")
    sys.exit(1)

try:
    client.run(BOT_TOKEN)
except Exception:
    if getattr(sys, "frozen", False):
        import traceback
        traceback.print_exc()
        input("Nhấn Enter để thoát...")
    raise
