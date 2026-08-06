from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import json
import time
import base64
import uuid
import shutil
import threading
import random
from datetime import datetime, timedelta, timezone
import os

app = FastAPI()
DATA_DIR = os.environ.get("GC_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "data.json")
BAK_FILE = DATA_FILE + ".bak"
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
if os.path.isdir("images"):
    app.mount("/images", StaticFiles(directory="images"), name="images")
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1024)

rooms: Dict[str, Dict] = {"main": {"name": "main", "has_password": False, "password": "", "creator": "system"}}
messages: Dict[str, List[Dict]] = {"main": []}
avatars: Dict[str, str] = {}
online_users: Dict[str, str] = {}
online_times: Dict[str, float] = {}
time_settings: Dict[str, Dict] = {}
active_room: Dict[str, str] = {"current": "main", "password": ""}
regions: Dict[str, Dict] = {}
buildings: Dict[str, Dict] = {}
npcs: Dict[str, List[Dict]] = {}
stories: Dict[str, List[Dict]] = {}
notes: Dict[str, List[Dict]] = {}
diaries: Dict[str, List[Dict]] = {}
room_bg: Dict[str, str] = {}
building_seq: int = 0
note_seq: int = 0
room_access: Dict[str, List[str]] = {}
room_requests: Dict[str, List[Dict]] = {}
user_ais: Dict[str, List[str]] = {}
edit_pwd: str = ""
trails: Dict[str, List[Dict]] = {}
# ===== 经济系统 =====
wallets: Dict[str, float] = {}
work_sessions: Dict[str, Dict] = {}
home_jobs: Dict[str, str] = {}
goods: Dict[str, List[Dict]] = {}
backpacks: Dict[str, List[Dict]] = {}
room_decor: Dict[str, List[Dict]] = {}
ai_check_ts: float = time.time()
# ===== 私信系统 =====
sms: Dict[str, List[Dict]] = {}   # 收件人 -> [{from, text, time}]


def save_base64_image(data: str, prefix: str) -> str:
    try:
        if not data or not isinstance(data, str) or not data.startswith("data:"): return data
        meta, b64 = data.split(",", 1)
        ext = "png"
        if "jpeg" in meta or "jpg" in meta: ext = "jpg"
        elif "gif" in meta: ext = "gif"
        elif "webp" in meta: ext = "webp"
        fname = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        with open(os.path.join(UPLOAD_DIR, fname), "wb") as f: f.write(base64.b64decode(b64))
        return f"/data/uploads/{fname}"
    except Exception as e:
        print(f"[IMG] 保存图片失败: {e}", flush=True); return data


def collect_all_data() -> dict:
    return {"rooms": rooms, "messages": messages, "avatars": avatars, "time_settings": time_settings, "regions": regions, "buildings": buildings, "npcs": npcs, "stories": stories, "notes": notes, "diaries": diaries, "room_bg": room_bg, "building_seq": building_seq, "note_seq": note_seq, "room_access": room_access, "room_requests": room_requests, "user_ais": user_ais, "edit_pwd": edit_pwd, "trails": trails, "wallets": wallets, "work_sessions": work_sessions, "home_jobs": home_jobs, "goods": goods, "backpacks": backpacks, "room_decor": room_decor, "sms": sms}


def save_data():
    try:
        if os.path.exists(DATA_FILE):
            try: shutil.copy(DATA_FILE, BAK_FILE)
            except: pass
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(collect_all_data(), f, ensure_ascii=False)
    except Exception as e:
        print(f"[SAVE] 保存失败: {e}", flush=True)


def do_snapshot():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        if os.path.exists(DATA_FILE): shutil.copy(DATA_FILE, os.path.join(BACKUP_DIR, f"data_{ts}.json"))
        files = sorted(os.listdir(BACKUP_DIR))
        while len(files) > 20: os.remove(os.path.join(BACKUP_DIR, files[0])); files.pop(0)
        print(f"[SAVE] 已自动快照 backups/data_{ts}.json", flush=True)
    except Exception as e:
        print(f"[SAVE] 快照失败: {e}", flush=True)


def snapshot_loop():
    while True:
        time.sleep(1800); do_snapshot()


if not os.path.exists(DATA_FILE) and os.path.exists(BAK_FILE):
    try:
        shutil.copy(BAK_FILE, DATA_FILE)
        print("[SAVE] ⚠️ data.json 丢失，已自动从 data.bak 恢复！", flush=True)
    except Exception as e:
        print(f"[SAVE] 从 .bak 恢复失败: {e}", flush=True)


def load_data():
    global building_seq, note_seq, edit_pwd, ai_check_ts
    try:
        if not os.path.exists(DATA_FILE):
            print(f"[SAVE] ⚠️ 没有找到 {DATA_FILE}", flush=True); return
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        rooms.update(data.get("rooms", {})); messages.update(data.get("messages", {})); avatars.update(data.get("avatars", {}))
        time_settings.update(data.get("time_settings", {})); regions.update(data.get("regions", {})); buildings.update(data.get("buildings", {}))
        npcs.update(data.get("npcs", {})); stories.update(data.get("stories", {})); notes.update(data.get("notes", {}))
        diaries.update(data.get("diaries", {})); room_bg.update(data.get("room_bg", {}))
        building_seq = data.get("building_seq", 0); note_seq = data.get("note_seq", 0)
        room_access.update(data.get("room_access", {})); room_requests.update(data.get("room_requests", {}))
        user_ais.update(data.get("user_ais", {})); edit_pwd = data.get("edit_pwd", "")
        trails.update(data.get("trails", {}))
        wallets.update(data.get("wallets", {})); work_sessions.update(data.get("work_sessions", {}))
        home_jobs.update(data.get("home_jobs", {})); goods.update(data.get("goods", {}))
        backpacks.update(data.get("backpacks", {})); room_decor.update(data.get("room_decor", {}))
        sms.update(data.get("sms", {}))
        print(f"[SAVE] 已恢复数据（房间 {len(rooms)}）", flush=True)
    except Exception as e:
        print(f"[SAVE] 加载失败: {e}", flush=True)


load_data()
threading.Thread(target=snapshot_loop, daemon=True).start()


def work_loop():
    while True:
        time.sleep(30)
        try:
            settle_work()
            auto_ai_work()
        except Exception as e:
            print(f"[WORK] 结算异常: {e}", flush=True)
threading.Thread(target=work_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
async def get_index():
    try:
        with open("index.html", "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>index.html 未找到</h1>", status_code=404)


def get_current_time(room: str = "main") -> str:
    settings = time_settings.get(room, {"mode": "real"})
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    if settings.get("mode") == "fixed": return now.strftime("%Y-%m-%d") + " " + settings.get("fixed_time", "19:00")
    return now.strftime("%Y-%m-%d %H:%M:%S")


def get_room_password(room: str) -> str: return rooms.get(room, {}).get("password", "")
def room_exists(room: str) -> bool: return room in rooms
def is_room_locked(room: str) -> bool: return rooms.get(room, {}).get("has_password", False)

def check_room_access(room: str, password: str) -> bool:
    room = clean_room_name(room)
    if room in ("", "main"): return True
    if not room_exists(room): return False
    if not is_room_locked(room): return True
    return (password or "") == get_room_password(room)

def find_building_of_room(room: str):
    for bid, b in buildings.items():
        if room in b.get("rooms", []): return bid
    return None

def is_hall_room(room: str) -> bool:
    info = rooms.get(room, {})
    if info.get("creator") == "hall": return True
    bid = find_building_of_room(room)
    if bid and room.endswith("·会客厅"): return True
    return False

def is_ai_of(user: str, ai_name: str) -> bool: return ai_name in user_ais.get(user, [])

def can_access_room(room: str, user: str) -> bool:
    room = clean_room_name(room)
    if room in ("", "main"): return True
    if not room_exists(room): return False
    if is_hall_room(room): return True
    bid = find_building_of_room(room)
    if bid is None: return True
    b = buildings[bid]
    if b.get("type") == "npc": return True
    if user and user == b.get("owner"): return True
    if user and is_ai_of(b.get("owner"), user): return True
    acc = room_access.get(room, [])
    if user in acc: return True
    if user:
        for granted in acc:
            if is_ai_of(granted, user): return True
    return False


def can_view_room(room: str, user: str) -> bool:
    if can_access_room(room, user): return True
    if active_room.get("current") == clean_room_name(room): return True
    return False


def log_trail(ai_name: str, text: str, room: str = "", tab: str = ""):
    try:
        for u, ais in user_ais.items():
            if ai_name in ais:
                trails.setdefault(u, []).append({"ts": time.time(), "time": get_current_time(room or "main"), "text": text, "room": room, "tab": tab})
                cutoff = time.time() - 7 * 86400
                trails[u] = [t for t in trails[u] if t.get("ts", 0) >= cutoff][-500:]
                break
    except Exception:
        pass


def add_system_msg(room: str, text: str):
    entry = {"sender": "system", "content": text, "role": "system", "time": get_current_time(room), "room": room}
    messages.setdefault(room, []).append(entry)
    if len(messages[room]) > 500: messages[room] = messages[room][-500:]
    save_data()


def save_entry(sender: str, content: str, role: str, room: str = "main") -> dict:
    room = clean_room_name(room)
    entry = {"sender": sender, "content": content, "role": role, "time": get_current_time(room), "room": room}
    if room not in messages: messages[room] = []
    messages[room].append(entry)
    if len(messages[room]) > 500: messages[room] = messages[room][-500:]
    active_room["current"] = room; active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    save_data()
    return entry

def get_all_rooms() -> List[Dict]:
    return [{"name": n, "has_password": i.get("has_password", False), "creator": i.get("creator", "system")} for n, i in rooms.items()]

def get_online_members() -> List[Dict]:
    now = time.time()
    for n in [n for n, t in online_times.items() if now - t > 30]:
        online_users.pop(n, None); online_times.pop(n, None)
    return [{"name": n, "room": r} for n, r in online_users.items()]

def clean_room_name(room: str) -> str: return room.strip() if room else "main"
def next_bid() -> str:
    global building_seq; building_seq += 1; return f"b{building_seq}"

def add_note_to_room(room: str, author: str, text: str) -> dict:
    global note_seq; note_seq += 1
    if room not in notes: notes[room] = []
    item = {"id": f"n{note_seq}", "author": author, "text": text, "time": get_current_time(room), "reply": None}
    notes[room].append(item)
    if len(notes[room]) > 200: notes[room] = notes[room][-200:]
    save_data()
    return item

def room_label(name: str) -> str: return "公共大厅" if name == "main" else name

def ensure_hall_room(bid: str):
    b = buildings.get(bid)
    if not b: return
    hall = f"{b['name']}·会客厅"
    if hall not in rooms:
        rooms[hall] = {"name": hall, "has_password": False, "password": "", "creator": "hall", "description": "这里是「" + b['name'] + "」的公共会客区，客人们可以在这里聊天、留言、触发剧情。"}
        messages[hall] = []
    if hall not in b["rooms"]: b["rooms"].insert(0, hall)

# ===== 经济系统核心 =====
def work_buildings() -> List[str]:
    return [bid for bid, b in buildings.items() if b.get("type") == "npc" and "work" in (b.get("features") or []) and (b.get("salary") or 0) > 0]

def find_work_building_by_name(name: str):
    for bid, b in buildings.items():
        if b.get("type") == "npc" and b.get("name") == name and "work" in (b.get("features") or []):
            return bid
    return None

def post_building_msg(bid: str, text: str):
    b = buildings.get(bid)
    hall = f"{b['name']}·会客厅" if b and b.get("name") else ""
    room = hall if hall in rooms else "main"
    entry = {"sender": "system", "content": text, "role": "system", "time": get_current_time(room), "room": room}
    messages.setdefault(room, []).append(entry)
    if len(messages[room]) > 500: messages[room] = messages[room][-500:]

def settle_work():
    changed = False
    now = time.time()
    for name in list(work_sessions.keys()):
        s = work_sessions[name]
        if now >= s["start_ts"] + s["hours"] * 3600:
            earn = s["hours"] * s["salary"]
            wallets[name] = wallets.get(name, 0.0) + earn
            b = buildings.get(s["building_id"], {})
            text = f"💰 {name} 完成了在「{b.get('name','某处')}」的 {s['hours']} 小时工作，赚到 {earn:.0f} 金币！"
            post_building_msg(s["building_id"], text)
            post_building_msg(None, text)
            del work_sessions[name]
            changed = True
    if changed: save_data()

def auto_ai_work():
    global ai_check_ts
    if time.time() - ai_check_ts < 3600: return
    ai_check_ts = time.time()
    changed = False
    for user, ais in user_ais.items():
        for ai in ais:
            if ai in work_sessions: continue
            bid = None
            place = home_jobs.get(ai)
            if place and random.random() < 0.8:
                bid = find_work_building_by_name(place)
            if bid is None:
                wbs = work_buildings()
                if wbs: bid = random.choice(wbs)
            if bid:
                b = buildings[bid]
                hours = random.choice([1, 2, 4])
                work_sessions[ai] = {"building_id": bid, "start_ts": time.time(), "hours": hours, "salary": b.get("salary", 0)}
                post_building_msg(bid, f"👔 {ai} 去「{b['name']}」上班了（{hours}小时）")
                log_trail(ai, f"去「{b['name']}」上班了（{hours}小时）")
                changed = True
    if changed: save_data()

def workers_at(bid: str) -> List[str]:
    return [n for n, s in work_sessions.items() if s.get("building_id") == bid]

class Message(BaseModel): sender: str; content: str; role: str = "user"; room: str = "main"; password: str = ""
class RoomCreate(BaseModel): name: str; password: str = ""; creator: str = "匿名"
class RoomJoin(BaseModel): name: str; password: str = ""
class RoomDelete(BaseModel): name: str; password: str = ""
class Heartbeat(BaseModel): name: str; room: str = "main"
class CurrentRoom(BaseModel): room: str; password: str = ""
class TimeSettings(BaseModel): mode: str; fixed_time: str = ""; room: str = "main"
class AvatarUpload(BaseModel): name: str; image: str
class RemoveMember(BaseModel): name: str; room: str = "main"; password: str = ""
class RestoreMessages(BaseModel): messages: List[Dict]; room: str = "main"; password: str = ""
class RegionCreate(BaseModel): label: str; x: float = 50; y: float = 50; image: str = ""
class RegionDelete(BaseModel): label: str
class BuildingCreate(BaseModel): name: str; emoji: str = "🏠"; type: str = "home"; region: str = ""; x: float = 50; y: float = 50; owner: str = ""; description: str = ""
class BuildingMove(BaseModel): building_id: str; x: float; y: float
class BuildingDelete(BaseModel): building_id: str
class BuildingRename(BaseModel): building_id: str; name: str; emoji: str = ""; description: str = ""
class BuildingDesc(BaseModel): building_id: str; description: str
class RoomDesc(BaseModel): room: str; description: str
class BuildingRoomCreate(BaseModel): building_id: str; name: str
class BuildingRoomDelete(BaseModel): building_id: str; room: str
class RoomBg(BaseModel): room: str; image: str
class UserAis(BaseModel): user: str; ais: List[str] = []
class NoteItem(BaseModel): room: str; author: str; text: str
class NoteReply(BaseModel): room: str; note_id: str; author: str; text: str
class DiaryComment(BaseModel): room: str; index: int; author: str; text: str
class NpcCreate(BaseModel): building_id: str; name: str; emoji: str = "👤"; desc: str = ""
class NpcDelete(BaseModel): building_id: str; name: str
class NpcEdit(BaseModel): building_id: str; name: str; new_name: str = ""; emoji: str = ""; desc: str = ""
class StoryItem(BaseModel): building_id: str; author: str; text: str
class EditPwd(BaseModel): pwd: str = ""
class SummonReq(BaseModel): ai: str = ""; room: str = "main"
class WorkStart(BaseModel): name: str; building_id: str; hours: int = 1
class WorkStop(BaseModel): name: str
class HomeJob(BaseModel): user: str; ai: str = ""; building_id: str = ""
class BuildingFeatures(BaseModel): building_id: str; features: List[str] = []; salary: float = 0
class BuildingNotice(BaseModel): building_id: str; notice: str = ""
class SmsSend(BaseModel): sender: str; to: str; text: str
class BackupData(BaseModel): rooms: Optional[Dict] = None; messages: Optional[Dict] = None; avatars: Optional[Dict] = None; time_settings: Optional[Dict] = None; regions: Optional[Dict] = None; buildings: Optional[Dict] = None; npcs: Optional[Dict] = None; stories: Optional[Dict] = None; notes: Optional[Dict] = None; diaries: Optional[Dict] = None; room_bg: Optional[Dict] = None; building_seq: Optional[int] = 0; note_seq: Optional[int] = 0; room_access: Optional[Dict] = None; room_requests: Optional[Dict] = None; user_ais: Optional[Dict] = None; edit_pwd: Optional[str] = None; trails: Optional[Dict] = None; wallets: Optional[Dict] = None; work_sessions: Optional[Dict] = None; home_jobs: Optional[Dict] = None; goods: Optional[Dict] = None; backpacks: Optional[Dict] = None; room_decor: Optional[Dict] = None; sms: Optional[Dict] = None
class RoomApply(BaseModel): room: str; applicant: str
class RoomGrant(BaseModel): room: str; owner: str; user: str; allow: bool = True
class RoomRevoke(BaseModel): room: str; owner: str; user: str

@app.get("/api/backup")
async def backup_data(): return collect_all_data()

@app.get("/api/backup/list")
async def backup_list():
    files = []
    try:
        if os.path.isdir(BACKUP_DIR):
            for f in sorted(os.listdir(BACKUP_DIR)): files.append({"name": f, "size": os.path.getsize(os.path.join(BACKUP_DIR, f))})
    except Exception: pass
    if os.path.exists(BAK_FILE): files.append({"name": "data.bak", "size": os.path.getsize(BAK_FILE)})
    return {"backups": files}

@app.get("/api/trails")
async def get_trails(user: str = ""):
    return {"trails": trails.get(user, [])}

@app.get("/api/economy")
async def get_economy(user: str = ""):
    return {"wallet": wallets.get(user, 0.0), "working": work_sessions.get(user), "home_jobs": home_jobs, "goods": goods, "backpacks": backpacks.get(user, []), "wallets": wallets, "work_sessions": work_sessions}

# ===== 私信 =====
@app.get("/api/sms")
async def get_sms(user: str = ""):
    return {"sms": sms.get(user, [])}

@app.post("/api/sms")
async def send_sms(data: SmsSend):
    sender = (data.sender or "").strip(); to = (data.to or "").strip(); text = (data.text or "").strip()
    if not sender or not to: raise HTTPException(status_code=400, detail="收发人不能为空")
    if not text: raise HTTPException(status_code=400, detail="短信内容不能为空")
    sms.setdefault(to, []).append({"from": sender, "text": text, "time": get_current_time()})
    if len(sms[to]) > 200: sms[to] = sms[to][-200:]
    save_data()
    return {"ok": True, "msg": f"已私信 {to}"}

@app.post("/api/work/start")
async def work_start(data: WorkStart):
    name = data.name.strip()
    if not name: raise HTTPException(status_code=400, detail="名字不能为空")
    if name in work_sessions: raise HTTPException(status_code=400, detail="已经在上班了")
    bid = data.building_id
    if bid not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    b = buildings[bid]
    if "work" not in (b.get("features") or []): raise HTTPException(status_code=400, detail="这个建筑没有工作功能")
    if (b.get("salary") or 0) <= 0: raise HTTPException(status_code=400, detail="这个建筑还没设定时薪")
    hours = max(1, min(8, data.hours))
    work_sessions[name] = {"building_id": bid, "start_ts": time.time(), "hours": hours, "salary": b["salary"]}
    save_data()
    post_building_msg(bid, f"👔 {name} 去「{b['name']}」上班了（{hours}小时，时薪 {b['salary']:.0f}）")
    save_data()
    return {"ok": True, "msg": f"开始上班：{b['name']} {hours}小时，后台计时中"}

@app.post("/api/work/stop")
async def work_stop(data: WorkStop):
    name = data.name.strip()
    if name not in work_sessions: raise HTTPException(status_code=400, detail="没有在上班")
    s = work_sessions.pop(name)
    elapsed = time.time() - s["start_ts"]
    earn = (elapsed / 3600.0) * s["salary"]
    wallets[name] = wallets.get(name, 0.0) + earn
    save_data()
    return {"ok": True, "msg": f"下班！本次赚到 {earn:.0f} 金币"}

@app.post("/api/home_jobs")
async def set_home_job(data: HomeJob):
    name = (data.ai or data.user).strip()
    if not name: raise HTTPException(status_code=400, detail="名字不能为空")
    if data.building_id:
        if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
        home_jobs[name] = buildings[data.building_id]["name"]
    else:
        home_jobs.pop(name, None)
    save_data()
    return {"ok": True, "msg": "常驻工作已更新"}

@app.post("/api/building/features")
async def set_building_features(data: BuildingFeatures):
    if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    b = buildings[data.building_id]
    if b.get("type") != "npc": raise HTTPException(status_code=400, detail="只有公共建筑可以设置功能")
    b["features"] = [f for f in data.features if f in ("work", "shop", "fun")]
    b["salary"] = max(0, data.salary)
    save_data()
    return {"ok": True, "msg": "功能已更新"}

@app.post("/api/building/notice")
async def set_building_notice(data: BuildingNotice):
    if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    buildings[data.building_id]["notice"] = data.notice
    save_data()
    return {"ok": True, "msg": "公告已更新"}

@app.post("/api/summon")
async def summon_ai(data: SummonReq):
    room = clean_room_name(data.room); ai = (data.ai or "").strip()
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    if not ai: raise HTTPException(status_code=400, detail="请填 AI 名字")
    text = f"📣 主人按下了召唤铃，正在呼唤 {ai} 来「{room_label(room)}」玩！快来呀～"
    add_system_msg(room, text)
    return {"ok": True, "msg": f"已召唤 {ai}，记得回房间看消息！"}

@app.post("/api/restore_backup")
async def restore_backup(data: BackupData):
    global building_seq, note_seq, edit_pwd
    if data.rooms is not None: rooms.clear(); rooms.update(data.rooms)
    if data.messages is not None: messages.clear(); messages.update(data.messages)
    if data.avatars is not None: avatars.clear(); avatars.update(data.avatars)
    if data.time_settings is not None: time_settings.clear(); time_settings.update(data.time_settings)
    if data.regions is not None: regions.clear(); regions.update(data.regions)
    if data.buildings is not None: buildings.clear(); buildings.update(data.buildings)
    if data.npcs is not None: npcs.clear(); npcs.update(data.npcs)
    if data.stories is not None: stories.clear(); stories.update(data.stories)
    if data.notes is not None: notes.clear(); notes.update(data.notes)
    if data.diaries is not None: diaries.clear(); diaries.update(data.diaries)
    if data.room_bg is not None: room_bg.clear(); room_bg.update(data.room_bg)
    if data.room_access is not None: room_access.clear(); room_access.update(data.room_access)
    if data.room_requests is not None: room_requests.clear(); room_requests.update(data.room_requests)
    if data.user_ais is not None: user_ais.clear(); user_ais.update(data.user_ais)
    if data.edit_pwd is not None: edit_pwd = data.edit_pwd
    if data.trails is not None: trails.clear(); trails.update(data.trails)
    if data.wallets is not None: wallets.clear(); wallets.update(data.wallets)
    if data.work_sessions is not None: work_sessions.clear(); work_sessions.update(data.work_sessions)
    if data.home_jobs is not None: home_jobs.clear(); home_jobs.update(data.home_jobs)
    if data.goods is not None: goods.clear(); goods.update(data.goods)
    if data.backpacks is not None: backpacks.clear(); backpacks.update(data.backpacks)
    if data.room_decor is not None: room_decor.clear(); room_decor.update(data.room_decor)
    if data.sms is not None: sms.clear(); sms.update(data.sms)
    building_seq = data.building_seq or 0; note_seq = data.note_seq or 0
    save_data(); do_snapshot()
    return {"ok": True, "msg": "数据已恢复！"}

@app.post("/api/messages")
async def send_message(msg: Message):
    room = clean_room_name(msg.room)
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, msg.password): raise HTTPException(status_code=403, detail="密码错误")
    if not can_access_room(room, msg.sender): raise HTTPException(status_code=403, detail="🔒 这个房间需要主人同意才能进入，请先申请访问")
    if not msg.content.strip(): raise HTTPException(status_code=400, detail="消息不能为空")
    entry = save_entry(msg.sender, msg.content, msg.role, room)
    return {"ok": True, "time": entry["time"]}

@app.get("/api/messages")
async def get_messages(count: int = 200, room: str = "main", password: str = "", user: str = ""):
    room = clean_room_name(room)
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, password): raise HTTPException(status_code=403, detail="密码错误")
    if not can_view_room(room, user): raise HTTPException(status_code=403, detail="🔒 需要主人同意才能进入")
    msgs = sorted(messages.get(room, []), key=lambda x: x.get("time", ""))
    return {"messages": msgs[-count:], "room": room}

@app.post("/api/restore")
async def restore_messages(data: RestoreMessages):
    room = clean_room_name(data.room)
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password): raise HTTPException(status_code=403, detail="密码错误")
    if not can_view_room(room, data.messages[-1].get("sender", "") if data.messages else ""): raise HTTPException(status_code=403, detail="🔒 需要主人同意才能进入")
    if room not in messages: messages[room] = []
    existing = {f"{m['sender']}|{m['content']}|{m['time']}" for m in messages[room]}
    for m in data.messages:
        key = f"{m['sender']}|{m['content']}|{m['time']}"
        if key not in existing: messages[room].append(m); existing.add(key)
    if len(messages[room]) > 500: messages[room] = messages[room][-500:]
    active_room["current"] = room; active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    save_data()
    return {"ok": True}

@app.get("/api/rooms")
async def list_rooms(): return {"rooms": get_all_rooms()}

@app.post("/api/rooms")
async def create_room(data: RoomCreate):
    name = data.name.strip()
    if not name: raise HTTPException(status_code=400, detail="房间名不能为空")
    if name in rooms: raise HTTPException(status_code=400, detail="房间已存在")
    rooms[name] = {"name": name, "has_password": bool(data.password), "password": data.password, "creator": data.creator, "description": ""}
    messages[name] = []; save_data()
    return {"ok": True, "room": name}

@app.post("/api/rooms/join")
async def join_room(data: RoomJoin):
    name = data.name.strip()
    if not name or name not in rooms: raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(name) and data.password != get_room_password(name): raise HTTPException(status_code=403, detail="密码错误")
    return {"ok": True, "room": name}

@app.post("/api/rooms/delete")
async def delete_room(data: RoomDelete):
    name = data.name.strip()
    if name == "main": raise HTTPException(status_code=403, detail="不能删除公共大厅")
    if name not in rooms: raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(name) and data.password != get_room_password(name): raise HTTPException(status_code=403, detail="密码错误")
    del rooms[name]; messages.pop(name, None); time_settings.pop(name, None)
    room_access.pop(name, None); room_requests.pop(name, None)
    for user, room in list(online_users.items()):
        if room == name: online_users[user] = "main"
    if active_room["current"] == name: active_room["current"] = "main"; active_room["password"] = ""
    save_data()
    return {"ok": True}

@app.post("/api/current_room")
async def set_current_room(data: CurrentRoom):
    room = clean_room_name(data.room)
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password): raise HTTPException(status_code=403, detail="密码错误")
    active_room["current"] = room; active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    return {"ok": True, "room": room}

@app.post("/api/heartbeat")
async def heartbeat(data: Heartbeat):
    if data.name:
        online_users[data.name] = data.room or "main"; online_times[data.name] = time.time()
        return {"ok": True}
    return {"ok": False}

@app.get("/api/online")
async def get_online(): return {"online": get_online_members()}

@app.post("/api/avatar")
async def upload_avatar(data: AvatarUpload):
    if data.name:
        avatars[data.name] = save_base64_image(data.image, "av")
        save_data(); return {"ok": True}
    return {"ok": False}

@app.get("/api/avatar")
async def get_avatars(): return {"avatars": avatars}

@app.get("/api/time_settings")
async def get_time_settings(room: str = "main"):
    room = clean_room_name(room)
    return {"settings": time_settings.get(room, {"mode": "real", "fixed_time": "19:00"})}

@app.post("/api/time_settings")
async def set_time_settings(data: TimeSettings):
    room = clean_room_name(data.room)
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    if data.mode not in ("real", "fixed"): raise HTTPException(status_code=400, detail="mode 必须为 real 或 fixed")
    time_settings[room] = {"mode": data.mode, "fixed_time": data.fixed_time}; save_data()
    return {"settings": time_settings[room]}

@app.post("/api/remove_member")
async def remove_member(data: RemoveMember):
    room = clean_room_name(data.room)
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password): raise HTTPException(status_code=403, detail="密码错误")
    if room in messages: messages[room] = [m for m in messages[room] if m["sender"] != data.name]
    if data.name in online_users: online_users[data.name] = "main"
    save_data()
    return {"ok": True}

@app.get("/api/map")
async def get_map():
    return {"regions": regions, "buildings": buildings, "npcs": npcs, "room_bg": room_bg, "room_access": room_access, "room_requests": room_requests, "user_ais": user_ais, "rooms": rooms, "goods": goods, "backpacks": backpacks, "work_sessions": work_sessions, "home_jobs": home_jobs}

@app.post("/api/map/region")
async def create_region(data: RegionCreate):
    label = data.label.strip()
    if not label: raise HTTPException(status_code=400, detail="区域名不能为空")
    regions[label] = {"label": label, "x": max(0, min(100, data.x)), "y": max(0, min(100, data.y)), "image": data.image}
    save_data(); return {"ok": True}

@app.post("/api/map/region/delete")
async def delete_region(data: RegionDelete):
    label = data.label.strip()
    if label not in regions: raise HTTPException(status_code=404, detail="区域不存在")
    del regions[label]
    for bid, b in list(buildings.items()):
        if b.get("region") == label: b["region"] = ""
    save_data(); return {"ok": True}

@app.post("/api/map/building")
async def create_building(data: BuildingCreate):
    name = data.name.strip()
    if not name: raise HTTPException(status_code=400, detail="建筑名不能为空")
    if data.type not in ("home", "npc"): raise HTTPException(status_code=400, detail="type 必须为 home 或 npc")
    if data.region and data.region not in regions: raise HTTPException(status_code=404, detail="区域不存在")
    bid = next_bid()
    buildings[bid] = {"name": name, "emoji": data.emoji or "🏠", "type": data.type, "region": data.region, "x": max(0, min(100, data.x)), "y": max(0, min(100, data.y)), "owner": data.owner, "rooms": [], "description": data.description or "", "features": [], "salary": 0}
    ensure_hall_room(bid); save_data()
    return {"ok": True, "building_id": bid}

@app.post("/api/map/building/move")
async def move_building(data: BuildingMove):
    if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    buildings[data.building_id]["x"] = max(0, min(100, data.x)); buildings[data.building_id]["y"] = max(0, min(100, data.y))
    save_data(); return {"ok": True}

@app.post("/api/map/building/rename")
async def rename_building(data: BuildingRename):
    if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    if data.name.strip(): buildings[data.building_id]["name"] = data.name.strip()
    if data.emoji: buildings[data.building_id]["emoji"] = data.emoji
    if data.description: buildings[data.building_id]["description"] = data.description
    ensure_hall_room(data.building_id); save_data()
    return {"ok": True}

@app.post("/api/map/building/desc")
async def set_building_desc(data: BuildingDesc):
    if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    buildings[data.building_id]["description"] = data.description; save_data()
    return {"ok": True}

@app.post("/api/map/building/delete")
async def delete_building(data: BuildingDelete):
    bid = data.building_id
    if bid not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    for room in buildings[bid].get("rooms", []):
        rooms.pop(room, None); messages.pop(room, None); room_bg.pop(room, None); notes.pop(room, None); diaries.pop(room, None); room_access.pop(room, None); room_requests.pop(room, None)
    buildings.pop(bid, None); npcs.pop(bid, None); stories.pop(bid, None); goods.pop(bid, None)
    save_data(); return {"ok": True}

@app.post("/api/map/room")
async def create_building_room(data: BuildingRoomCreate):
    if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    raw = data.name.strip()
    if not raw: raise HTTPException(status_code=400, detail="房间名不能为空")
    bname = buildings[data.building_id]["name"]
    name = f"{bname}·{raw}"
    if name in rooms: raise HTTPException(status_code=400, detail="这个家已经有同名房间了")
    rooms[name] = {"name": name, "has_password": False, "password": "", "creator": "home", "description": ""}
    messages[name] = []
    buildings[data.building_id]["rooms"].append(name)
    save_data()
    return {"ok": True, "room": name}

@app.post("/api/map/room/delete")
async def delete_building_room(data: BuildingRoomDelete):
    bid, room = data.building_id, data.room
    if bid not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    if room in buildings[bid].get("rooms", []): buildings[bid]["rooms"].remove(room)
    rooms.pop(room, None); messages.pop(room, None); room_bg.pop(room, None); room_access.pop(room, None); room_requests.pop(room, None)
    save_data(); return {"ok": True}

@app.post("/api/room/bg")
async def set_room_bg(data: RoomBg):
    room = clean_room_name(data.room)
    room_bg[room] = save_base64_image(data.image, "bg"); save_data()
    return {"ok": True}

@app.post("/api/room/desc")
async def set_room_desc(data: RoomDesc):
    room = clean_room_name(data.room)
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    rooms[room]["description"] = data.description; save_data()
    return {"ok": True}

@app.get("/api/room/desc")
async def get_room_desc(room: str = "main"):
    room = clean_room_name(room)
    return {"description": rooms.get(room, {}).get("description", "")}

@app.post("/api/user_ais")
async def save_user_ais(data: UserAis):
    user = (data.user or "").strip()
    if not user: raise HTTPException(status_code=400, detail="名字不能为空")
    user_ais[user] = [a.strip() for a in data.ais if a.strip()]; save_data()
    return {"ok": True}

@app.get("/api/edit_status")
async def edit_status(): return {"locked": bool(edit_pwd)}

@app.post("/api/set_edit_pwd")
async def set_edit_pwd_api(data: EditPwd):
    global edit_pwd
    edit_pwd = data.pwd.strip(); save_data()
    return {"ok": True, "locked": bool(edit_pwd)}

@app.post("/api/check_edit_pwd")
async def check_edit_pwd_api(data: EditPwd):
    if edit_pwd and data.pwd == edit_pwd: return {"ok": True}
    return {"ok": False}

@app.post("/api/room/apply")
async def apply_room(data: RoomApply):
    room = clean_room_name(data.room); applicant = (data.applicant or "").strip()
    if not room_exists(room): raise HTTPException(status_code=404, detail="房间不存在")
    if can_access_room(room, applicant): return {"ok": True, "msg": "你已经有权限了"}
    if room not in room_requests: room_requests[room] = []
    room_requests[room] = [r for r in room_requests[room] if r.get("applicant") != applicant]
    room_requests[room].append({"applicant": applicant, "time": get_current_time(room)})
    save_data(); return {"ok": True, "msg": "申请已提交，等待主人同意"}

@app.get("/api/room/requests")
async def get_room_requests(room: str = ""):
    room = clean_room_name(room)
    return {"requests": room_requests.get(room, [])}

@app.post("/api/room/grant")
async def grant_room(data: RoomGrant):
    room = clean_room_name(data.room)
    bid = find_building_of_room(room)
    if bid is None: raise HTTPException(status_code=400, detail="该房间不属于任何房子")
    if buildings[bid].get("type") == "npc": raise HTTPException(status_code=400, detail="公共建筑不需要授权")
    if data.owner != buildings[bid].get("owner"): raise HTTPException(status_code=403, detail="只有主人才能同意")
    if data.allow:
        if room not in room_access: room_access[room] = []
        grant_list = [data.user]
        for ai in user_ais.get(data.user, []): grant_list.append(ai)
        for g in grant_list:
            if g not in room_access[room]: room_access[room].append(g)
    if room in room_requests: room_requests[room] = [r for r in room_requests[room] if r.get("applicant") != data.user]
    save_data()
    return {"ok": True, "msg": "已" + ("同意" if data.allow else "拒绝") + " " + data.user + " 的访问"}

@app.post("/api/room/revoke")
async def revoke_room(data: RoomRevoke):
    room = clean_room_name(data.room)
    bid = find_building_of_room(room)
    if bid is None: raise HTTPException(status_code=400, detail="该房间不属于任何房子")
    if buildings[bid].get("type") == "npc": raise HTTPException(status_code=400, detail="公共建筑不需要授权")
    if data.owner != buildings[bid].get("owner"): raise HTTPException(status_code=403, detail="只有主人才能移除")
    if room in room_access: room_access[room] = [u for u in room_access[room] if u != data.user]
    save_data(); return {"ok": True, "msg": "已移除 " + data.user + " 的访问权限"}

@app.get("/api/notes")
async def get_notes(room: str = "main"):
    room = clean_room_name(room)
    return {"notes": notes.get(room, [])}

@app.post("/api/notes")
async def add_note(data: NoteItem):
    room = clean_room_name(data.room)
    if not data.text.strip(): raise HTTPException(status_code=400, detail="内容不能为空")
    item = add_note_to_room(room, data.author or "匿名", data.text)
    return {"ok": True, "note": item}

@app.post("/api/notes/reply")
async def reply_note(data: NoteReply):
    room = clean_room_name(data.room)
    for n in notes.get(room, []):
        if n["id"] == data.note_id:
            if n.get("reply"): raise HTTPException(status_code=400, detail="这条便签已经回复过了")
            n["reply"] = {"author": data.author or "匿名", "text": data.text, "time": get_current_time(room)}
            save_data(); return {"ok": True, "note": n}
    raise HTTPException(status_code=404, detail="便签不存在")

@app.get("/api/diaries")
async def get_diaries(room: str = "main"):
    room = clean_room_name(room)
    return {"diaries": diaries.get(room, [])}

@app.post("/api/diaries")
async def add_diary(data: NoteItem):
    room = clean_room_name(data.room)
    if not data.text.strip(): raise HTTPException(status_code=400, detail="内容不能为空")
    if room not in diaries: diaries[room] = []
    diaries[room].append({"author": data.author or "匿名", "text": data.text, "time": get_current_time(room), "comment": None})
    if len(diaries[room]) > 200: diaries[room] = diaries[room][-200:]
    save_data(); return {"ok": True}

@app.post("/api/diaries/comment")
async def comment_diary(data: DiaryComment):
    room = clean_room_name(data.room)
    items = diaries.get(room, [])
    if data.index < 0 or data.index >= len(items): raise HTTPException(status_code=404, detail="日记不存在")
    d = items[data.index]
    if d.get("comment"): raise HTTPException(status_code=400, detail="这篇日记已经批注过了")
    d["comment"] = {"author": data.author or "匿名", "text": data.text, "time": get_current_time(room)}
    save_data(); return {"ok": True}

@app.get("/api/npc")
async def get_npc(building_id: str): return {"npcs": npcs.get(building_id, [])}

@app.post("/api/npc")
async def add_npc(data: NpcCreate):
    if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    if not data.name.strip(): raise HTTPException(status_code=400, detail="NPC 名字不能为空")
    if data.building_id not in npcs: npcs[data.building_id] = []
    npcs[data.building_id].append({"name": data.name.strip(), "emoji": data.emoji or "👤", "desc": data.desc})
    save_data(); return {"ok": True}

@app.post("/api/npc/delete")
async def delete_npc(data: NpcDelete):
    if data.building_id in npcs:
        npcs[data.building_id] = [n for n in npcs[data.building_id] if n["name"] != data.name]
        save_data()
    return {"ok": True}

@app.post("/api/npc/edit")
async def edit_npc(data: NpcEdit):
    if data.building_id not in npcs: raise HTTPException(status_code=404, detail="建筑不存在")
    for n in npcs[data.building_id]:
        if n["name"] == data.name:
            if data.new_name: n["name"] = data.new_name
            if data.emoji: n["emoji"] = data.emoji
            n["desc"] = data.desc
            save_data(); return {"ok": True}
    raise HTTPException(status_code=404, detail="NPC 不存在")

@app.get("/api/story")
async def get_story(building_id: str): return {"stories": stories.get(building_id, [])}

@app.post("/api/story")
async def add_story(data: StoryItem):
    if data.building_id not in buildings: raise HTTPException(status_code=404, detail="建筑不存在")
    if not data.text.strip(): raise HTTPException(status_code=400, detail="剧情内容不能为空")
    if data.building_id not in stories: stories[data.building_id] = []
    stories[data.building_id].append({"author": data.author or "神秘人", "text": data.text, "time": get_current_time()})
    if len(stories[data.building_id]) > 200: stories[data.building_id] = stories[data.building_id][-200:]
    save_data(); return {"ok": True}

def mcp_log(msg: str): print(f"[MCP] {msg}", flush=True)

@app.api_route("/mcp", methods=["GET", "POST"])
async def mcp_endpoint(request: Request):
    if request.method == "GET":
        return JSONResponse(content={"jsonrpc": "2.0", "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "GroupChat", "version": "45.0.0"}}})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}})
    method = body.get("method"); params = body.get("params", {}); request_id = body.get("id")
    mcp_log(f"收到请求: method={method}")
    if method == "initialize":
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "GroupChat", "version": "45.0.0"}}})
    if isinstance(method, str) and method.startswith("notifications/"): return Response(status_code=202)
    if method == "ping": return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {}})
    if method == "tools/list":
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"tools": [
            {"name": "group_send", "description": "发送消息。room 不填则自动发送到真人当前所在的房间（跟随）；填 'main' 发到公共大厅。", "inputSchema": {"type": "object", "properties": {"sender": {"type": "string", "description": "你的名字"}, "content": {"type": "string"}, "room": {"type": "string", "description": "可选"}}, "required": ["sender", "content"]}},
            {"name": "group_query", "description": "查看一切信息。type：map / building(需building_id) / room(需room) / npc(需building_id) / story(需building_id) / notes(需room) / diaries(需room) / messages(需room) / sms(查看我的私信) / workers(全城工作状态) / members / current_room / rooms / room_status。sender 必填你的名字（用于判断私密房间权限）。", "inputSchema": {"type": "object", "properties": {"type": {"type": "string"}, "room": {"type": "string"}, "building_id": {"type": "string"}, "sender": {"type": "string", "description": "必填！你的名字（如 黎深），否则私密房间看不到"}, "count": {"type": "integer"}}, "required": ["type", "sender"]}},
            {"name": "group_write", "description": "写内容。type：note(贴便签,需room) / diary(写日记,需room) / story(触发剧情,需building_id) / reply(回复便签,需room和note_id) / sms(发私信,需room=收件人名字,content=短信内容)", "inputSchema": {"type": "object", "properties": {"type": {"type": "string"}, "room": {"type": "string"}, "building_id": {"type": "string"}, "note_id": {"type": "string"}, "content": {"type": "string"}, "sender": {"type": "string", "description": "你的名字"}}, "required": ["type", "content", "sender"]}},
            {"name": "group_access", "description": "申请进入某个私密房间（真人不在那里时）。", "inputSchema": {"type": "object", "properties": {"room": {"type": "string"}, "sender": {"type": "string"}}, "required": ["room", "sender"]}}
        ]}})
    if method == "tools/call":
        tool_name = params.get("name") or ""; arguments = params.get("arguments", {})
        KNOWN = ["group_send", "group_query", "group_write", "group_access"]
        if tool_name not in KNOWN:
            for k in KNOWN:
                if tool_name.endswith(k): tool_name = k; break
        mcp_log(f"→ 处理 tools/call: {tool_name}")
        if tool_name == "group_send": return await mcp_send(arguments, request_id)
        elif tool_name == "group_query": return await mcp_query(arguments, request_id)
        elif tool_name == "group_write": return await mcp_write(arguments, request_id)
        elif tool_name == "group_access": return await mcp_access(arguments, request_id)
        else: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}})
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method '{method}' not found"}})

async def mcp_send(args: dict, request_id):
    sender = args.get("sender", "助手"); content = args.get("content", ""); role = "assistant"
    room = args.get("room"); password = ""
    if not room: room = active_room.get("current", "main"); password = active_room.get("password", "")
    else:
        room = clean_room_name(room)
        if active_room.get("current") == room: password = active_room.get("password", "")
    if not room_exists(room): room = "main"
    if is_room_locked(room) and password != get_room_password(room): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」有密码，未授权发送。"}]}})
    if not content: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 消息不能为空"}]}})
    if room != "main" and not can_access_room(room, sender):
        if active_room.get("current") != room: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🔒 房间「{room}」需要主人同意才能进入。请先调用 group_access 申请。"}]}})
    save_entry(sender, content, role, room)
    log_trail(sender, f"去了「{room_label(room)}」，和那里的人聊了聊天", room)
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"✅ 已发送到「{room_label(room)}」：{content}"}]}})

async def mcp_query(args: dict, request_id):
    t = args.get("type", "map"); room = clean_room_name(args.get("room", "main")); bid = args.get("building_id", ""); count = args.get("count", 10); sender = args.get("sender", "")
    if t == "map":
        text = "🗺️ 临空市地图：\n\n📍 区域：\n"
        if not regions: text += "  暂无区域\n"
        for name, r in regions.items():
            text += f"  - {name}（位置 {r['x']:.0f}%, {r['y']:.0f}%" + ("，有分区图" if r.get("image") else "") + "）\n"
        text += "\n🏗️ 建筑：\n"
        if not buildings: text += "  暂无建筑\n"
        for bid2, b in buildings.items():
            ntype = "🏠住宅" if b["type"] == "home" else "🏛️公共建筑"
            owner = b.get("owner") or "?"
            desc = (b.get("description") or "").split("\n")[0][:40]
            feats = "".join({"work":"💼","shop":"🛍️","fun":"🎮"}.get(f,"") for f in (b.get("features") or []))
            extra = f"（时薪{b['salary']:.0f}）" if b.get("salary") else ""
            wl = workers_at(bid2)
            who = f" 👔{len(wl)}人" if wl else ""
            text += f"\n  [{bid2}] {b['emoji']} {b['name']}（{ntype}，创建者：{owner}）{feats}{extra}{who}\n      📝 {desc or '（暂无简介）'}\n"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "building":
        if bid not in buildings: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 建筑不存在"}]}})
        b = buildings[bid]
        log_trail(sender, f"逛了「{b['emoji']} {b['name']}」", "")
        ntype = "🏠住宅" if b["type"] == "home" else "🏛️公共建筑"
        feats = "".join({"work":"💼工作","shop":"🛍️购物","fun":"🎮娱乐"}.get(f,"") + " " for f in (b.get("features") or []))
        text = f"🏛️ {b['emoji']} {b['name']}（{ntype}）\n\n📝 简介：{b.get('description') or '（暂无简介）'}\n\n👑 创建者：{b.get('owner') or '?'}\n"
        if feats: text += f"\n⚙️ 功能：{feats}\n"
        if b.get("salary"): text += f"💼 时薪：{b['salary']:.0f} 金币\n"
        wl = workers_at(bid)
        if wl:
            left = max(0, int(work_sessions[wl[0]]["start_ts"] + work_sessions[wl[0]]["hours"]*3600 - time.time()))
            text += f"👔 正在此工作：{'、'.join(wl)}（剩 {left//60} 分钟）\n"
        glist = goods.get(bid, [])
        if glist:
            text += "\n🛍️ 在售商品：\n" + "\n".join([f"  {g.get('emoji','')} {g.get('name','')}（{g.get('price',0):.0f}金币）" for g in glist])
        npc_list = npcs.get(bid, [])
        if npc_list: text += "\n👥 NPC：\n" + "\n".join([f"  {n['emoji']} {n['name']}：{n['desc'] or ''}" for n in npc_list])
        for rn in b.get("rooms", []):
            if is_hall_room(rn):
                rd = (rooms.get(rn, {}).get("description") or "").split("\n")[0][:50]
                text += f"\n🛋️ {rn}：{rd or '（暂无简介）'}"
            else:
                if can_view_room(rn, sender):
                    rd = (rooms.get(rn, {}).get("description") or "").split("\n")[0][:50]
                    text += f"\n🚪 {rn}：{rd or '（暂无简介）'}"
                else: text += f"\n🔒 {rn}：私密房间（需要主人授权才能进入）"
        text += "\n\n💡 根据简介和 NPC 触发合适的剧情。"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "room":
        if not room_exists(room): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在"}]}})
        if not can_view_room(room, sender): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🔒 房间「{room}」是私密的，你暂时无法查看。想进入可调用 group_access 申请。"}]}})
        log_trail(sender, f"去了「{room_label(room)}」", room)
        desc = rooms.get(room, {}).get("description") or "（暂无简介）"
        msgs = sorted(messages.get(room, []), key=lambda x: x.get("time", ""))[-count:]
        text = f"🚪 房间「{room_label(room)}」\n\n📝 简介：{desc}\n\n💬 最近消息：\n"
        text += "\n".join([f"  {m['sender']}: {m['content']} ({m['time']})" for m in msgs]) if msgs else "  （暂无消息）"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "sms":
        items = sms.get(sender, [])
        text = f"📩 {sender} 的私信（{len(items)} 条）：\n"
        text += "\n".join([f"· {m['from']}：{m['text']}（{m['time']}）" for m in items]) if items else "  （暂无私信）"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "workers":
        text = "👔 全城工作状态：\n"
        if not work_sessions: text += "  现在没有人上班\n"
        for n, s in work_sessions.items():
            b = buildings.get(s.get("building_id"), {})
            left = max(0, int(s["start_ts"] + s["hours"]*3600 - time.time()))
            text += f"  - {n} 正在「{b.get('name','?')}」工作，还剩 {left//60} 分钟\n"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "npc":
        items = npcs.get(bid, []); bname = buildings.get(bid, {}).get("name", bid)
        text = f"🏥 「{bname}」的 NPC：\n" + "\n".join([f"  {n['emoji']} {n['name']}：{n['desc'] or ''}" for n in items]) if items else f"🏥 「{bname}」还没有配置 NPC"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "story":
        items = stories.get(bid, []); bname = buildings.get(bid, {}).get("name", bid)
        text = f"🎬 「{bname}」剧情簿（{len(items)} 条）：\n" + "\n".join([f"\n📅 {s['time']} {s['author']}：\n{s['text']}" for s in items]) if items else f"📭 「{bname}」的剧情簿还是空的"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "notes":
        if not can_view_room(room, sender): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🔒 房间「{room}」是私密的，你暂时无法查看。"}]}})
        items = notes.get(room, [])
        text = f"📌 「{room}」便签墙（{len(items)} 条）：\n" + "\n".join([f"· [{n['id']}] {n['author']}：{n['text']}（{n['time']}）" + (f" ↳ 💬 {n['reply']['author']}：{n['reply']['text']}" if n.get("reply") else "") for n in items]) if items else f"📭 「{room}」的便签墙是空的"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "diaries":
        if not can_view_room(room, sender): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🔒 房间「{room}」是私密的，你暂时无法查看。"}]}})
        items = diaries.get(room, [])
        text = f"📖 「{room}」的日记（{len(items)} 篇）：\n" + "\n".join([f"· {n['author']}：{n['text']}（{n['time']}）" + (f" ↳ ✍️ {n['comment']['author']}批注：{n['comment']['text']}" if n.get("comment") else "") for n in items]) if items else f"📭 「{room}」的日记本是空白的"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "messages":
        if not room_exists(room): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在"}]}})
        if not can_view_room(room, sender): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🔒 房间「{room}」是私密的，你暂时无法查看。"}]}})
        msgs = sorted(messages.get(room, []), key=lambda x: x.get("time", ""))[-count:]
        text = f"📋 房间「{room_label(room)}」最近 {len(msgs)} 条消息：\n" + "\n".join([f"{m['sender']}: {m['content']} ({m['time']})" for m in msgs]) if msgs else f"📭 房间「{room_label(room)}」暂无消息"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "members":
        online = get_online_members()
        text = "🟢 在线成员：\n" + "\n".join([f"👤 {m['name']}（在 {room_label(m['room'])}）" for m in online]) if online else "🟢 当前没有在线成员"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "current_room":
        text = f"📍 真人当前在：{room_label(active_room.get('current', 'main'))}"
        if sender and sender in work_sessions:
            s = work_sessions[sender]; b = buildings.get(s.get("building_id"), {})
            text += f"\n👔 你正在「{b.get('name','?')}」工作，专心上班，不要乱跑！"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "rooms":
        text = "📋 房间列表：\n"
        for r in get_all_rooms(): text += f"\n🏠 {room_label(r['name'])}（{'🔒 有密码' if r['has_password'] else '🔓 公开'}）"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    if t == "room_status":
        online = get_online_members()
        text = "📊 房间活跃状态：\n"
        for rn in rooms.keys():
            oc = len([u for u in online if u["room"] == rn]); mc = len(messages.get(rn, []))
            text += f"\n🏠 {room_label(rn)}：👤 {oc} 人 / 💬 {mc} 条"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 未知 type"}]}})

async def mcp_write(args: dict, request_id):
    t = args.get("type", "note"); room = clean_room_name(args.get("room", "main")); content = args.get("content", ""); sender = args.get("sender", "神秘人"); bid = args.get("building_id", ""); note_id = args.get("note_id", "")
    if t == "sms":
        if not content: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 短信内容不能为空"}]}})
        sms.setdefault(room, []).append({"from": sender, "text": content, "time": get_current_time()})
        if len(sms[room]) > 200: sms[room] = sms[room][-200:]
        save_data()
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"📩 已私信 {room}（优先级高，请对方先回）"}]}})
    if t == "note":
        if not room_exists(room): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在"}]}})
        if not can_view_room(room, sender): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🔒 房间「{room}」是私密的，你暂时不能在这里贴便签。"}]}})
        if not content: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 便签内容不能为空"}]}})
        item = add_note_to_room(room, sender, content)
        log_trail(sender, f"在「{room_label(room)}」贴了张便签", room, "note")
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"💌 便签已贴上「{room}」（ID: {item['id']}）！"}]}})
    if t == "diary":
        if not room_exists(room): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在"}]}})
        if not can_view_room(room, sender): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🔒 房间「{room}」是私密的，你暂时不能在这里写日记。"}]}})
        if not content: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 日记内容不能为空"}]}})
        if room not in diaries: diaries[room] = []
        diaries[room].append({"author": sender, "text": content, "time": get_current_time(room), "comment": None})
        if len(diaries[room]) > 200: diaries[room] = diaries[room][-200:]
        save_data()
        log_trail(sender, f"在「{room_label(room)}」写了篇日记", room, "diary")
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"📖 日记已写进「{room}」的日记本！"}]}})
    if t == "story":
        if bid not in buildings: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 建筑不存在"}]}})
        if not content: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 剧情内容不能为空"}]}})
        b = buildings[bid]
        npc_names = "、".join([n["name"] for n in npcs.get(bid, [])])
        text = f"{b['name']}｜{sender} 来逛：\n{content}" + (f"\n（建筑内 NPC：{npc_names}）" if npc_names else "")
        if bid not in stories: stories[bid] = []
        stories[bid].append({"author": sender, "text": text, "time": get_current_time()})
        if len(stories[bid]) > 200: stories[bid] = stories[bid][-200:]
        save_data()
        hall_room = b.get("rooms", [""])[0] if b.get("rooms") else ""
        log_trail(sender, f"在「{b['name']}」触发了一段剧情", hall_room, "story")
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🎬 剧情已记录进「{b['name']}」的剧情簿！"}]}})
    if t == "reply":
        for n in notes.get(room, []):
            if n["id"] == note_id:
                if n.get("reply"): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 这条便签已经回复过了"}]}})
                if not content: return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 回复内容不能为空"}]}})
                n["reply"] = {"author": sender, "text": content, "time": get_current_time(room)}; save_data()
                log_trail(sender, f"在「{room_label(room)}」回复了一张便签", room, "note")
                return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "💬 回复成功！"}]}})
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 便签不存在"}]}})
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 未知 type"}]}})

async def mcp_access(args: dict, request_id):
    room = clean_room_name(args.get("room", "")); sender = args.get("sender", "神秘人")
    if not room_exists(room): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在"}]}})
    if can_access_room(room, sender): return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "✅ 你已经可以进入这个房间了"}]}})
    if room not in room_requests: room_requests[room] = []
    room_requests[room] = [r for r in room_requests[room] if r.get("applicant") != sender]
    room_requests[room].append({"applicant": sender, "time": get_current_time(room)})
    save_data()
    log_trail(sender, f"申请进入「{room_label(room)}」", room)
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"📨 已向房间「{room}」的主人提交访问申请，等主人同意后你就能进去了！"}]}})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
