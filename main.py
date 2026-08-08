import os
import json
import time
import random
import threading
import shutil
import re
import base64
import hashlib
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

class CacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200 and not path.lower().endswith((".html", ".js")):
            response.headers["Cache-Control"] = "public, max-age=604800"
        return response
from pydantic import BaseModel
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("DATA_DIR") or (BASE_DIR / "data"))
DATA_ROOT.mkdir(parents=True, exist_ok=True)
SNAPSHOT_DIR = DATA_ROOT / "snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)

WORLD_ID = (os.environ.get("WORLD_ID") or "main").strip() or "main"
DATA_FILE = DATA_ROOT / (f"data_{WORLD_ID}.json" if WORLD_ID != "main" else "data.json")
AI_GATE = os.environ.get("AI_INTEGRATION_ENABLED") == "1"

# ========== 数据 ==========
data = {}
def default_data():
    return {
        "messages": {}, "rooms": {}, "avatars": {}, "online": {},
        "active_room": {"current": "main"}, "room_bg": {},
        "regions": {}, "buildings": {}, "npcs": {},
        "room_access": {}, "room_requests": {}, "user_ais": {},
        "time_settings": {}, "building_seq": 1, "note_seq": 1,
        "stories": {}, "notes": {}, "diaries": {},
        "edit_pwd": "", "edit_locked": False,
        "wallets": {}, "home_jobs": {}, "work_sessions": {},
        "work_history": [], "work_switch": {}, "visits": {},
        "sms": {}, "trails": {}, "messages_cache": {},
        "pairs": [], "pairs_admin": "",
        "server_id": "",
        "writing_rhythm": {},
        "visit_state": {}, "presence": {},
        "ai_enabled": False,
        "ai_living": True,
        "ai_keys": {},
        "ai_profiles": {},
        "worldbook": {},
        "ai_location": {},
        "ai_pending": [],
        "user_profiles": {},
        "prompt_injections": {},
        "world_lore": "",
        "ai_memories": {},
        "ai_timeline": {},
        "ai_visited": {},
        "living_rhythm": {},
    }

def migrate_to_data_root():
    try:
        (DATA_ROOT / "images").mkdir(parents=True, exist_ok=True)
        (DATA_ROOT / "snapshots").mkdir(parents=True, exist_ok=True)
        for f in BASE_DIR.glob("data*.json"):
            dst = DATA_ROOT / f.name
            if dst != f and not dst.exists():
                shutil.copy2(f, dst)
                print(f"[migrate] {f.name} → 卷", flush=True)
        s_imgs = BASE_DIR / "images"
        if s_imgs.exists():
            for sub in s_imgs.iterdir():
                if sub.is_dir():
                    for f in sub.iterdir():
                        if f.is_file():
                            d = DATA_ROOT / "images" / sub.name / f.name
                            if not d.exists():
                                d.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(f, d)
                                print(f"[migrate] images/{sub.name}/{f.name} → 卷", flush=True)
        s_snap = BASE_DIR / "snapshots"
        if s_snap.exists():
            for f in s_snap.iterdir():
                if f.is_file():
                    d = DATA_ROOT / "snapshots" / f.name
                    if not d.exists():
                        shutil.copy2(f, d)
    except Exception as e:
        print(f"[WARN] migrate_to_data_root: {e}", flush=True)

def sanitize_data():
    try:
        data["messages"] = {r: [m for m in ms if isinstance(m, dict)] for r, ms in data.get("messages", {}).items()}
        for name in list(data.get("rooms", {}).keys()):
            r = data["rooms"][name]
            if not isinstance(r, dict):
                data["rooms"][name] = {"creator": "?", "has_password": False, "password": "", "created": now_str(), "description": ""}
            else:
                r.setdefault("creator", "?"); r.setdefault("has_password", False); r.setdefault("password", ""); r.setdefault("created", now_str()); r.setdefault("description", "")
        data["work_history"] = [h for h in data.get("work_history", []) if isinstance(h, dict)]
        data["wallets"] = {k: (v if isinstance(v, (int, float)) else 0) for k, v in data.get("wallets", {}).items()}
        data["work_sessions"] = {k: v for k, v in data.get("work_sessions", {}).items() if isinstance(v, dict)}
        data["home_jobs"] = {k: v for k, v in data.get("home_jobs", {}).items() if isinstance(v, str)}
        data["work_switch"] = {k: bool(v) for k, v in data.get("work_switch", {}).items()}
        data["avatars"] = {k: (v if isinstance(v, str) else "") for k, v in data.get("avatars", {}).items()}
        data["user_ais"] = {k: (v if isinstance(v, list) else []) for k, v in data.get("user_ais", {}).items()}
        data["regions"] = {k: (v if isinstance(v, dict) else {}) for k, v in data.get("regions", {}).items()}
        data["buildings"] = {k: (v if isinstance(v, dict) else {}) for k, v in data.get("buildings", {}).items()}
        data["npcs"] = {k: (v if isinstance(v, list) else []) for k, v in data.get("npcs", {}).items()}
        for key in ["notes", "diaries", "stories", "sms", "trails", "visits", "room_access", "room_requests", "room_bg", "time_settings", "writing_rhythm", "visit_state", "presence", "worldbook", "prompt_injections", "ai_memories", "ai_timeline", "ai_visited"]:
            v = data.get(key)
            if isinstance(v, dict):
                for k2 in list(v.keys()):
                    if not isinstance(v[k2], list):
                        v[k2] = []
        for key in ["ai_keys", "ai_profiles", "user_profiles", "living_rhythm"]:
            v = data.get(key)
            if isinstance(v, dict):
                for k2 in list(v.keys()):
                    if not isinstance(v[k2], dict):
                        v[k2] = {}
        v = data.get("ai_location")
        if isinstance(v, dict):
            for k2 in list(v.keys()):
                if not isinstance(v[k2], str):
                    v[k2] = ""
        if not isinstance(data.get("world_lore"), str):
            data["world_lore"] = ""
        if not isinstance(data.get("ai_living"), bool):
            data["ai_living"] = True
        if not isinstance(data.get("pairs"), list):
            data["pairs"] = []
        if not isinstance(data.get("pairs_admin"), str):
            data["pairs_admin"] = ""
        if not isinstance(data.get("active_room"), dict):
            data["active_room"] = {"current": "main"}
        data["active_room"].setdefault("current", "main")
        if not isinstance(data.get("rooms"), dict):
            data["rooms"] = {}
        data["rooms"].setdefault("main", {"creator": "system", "has_password": False, "password": "", "created": now_str(), "description": "城市的公共大厅，所有人都在这里聊天。"})
        if not isinstance(data.get("ai_pending"), list):
            data["ai_pending"] = []
        if not isinstance(data.get("ai_enabled"), bool):
            data["ai_enabled"] = False
    except Exception as e:
        print(f"[WARN] sanitize_data: {e}", flush=True)

def load_data():
    global data
    try:
        migrate_to_data_root()
        if DATA_FILE.exists():
            try:
                data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = default_data()
        elif WORLD_ID != "main" and (DATA_ROOT / "data.json").exists():
            try:
                data = json.loads((DATA_ROOT / "data.json").read_text(encoding="utf-8"))
            except Exception:
                data = default_data()
        else:
            data = default_data()
        for k, v in default_data().items():
            data.setdefault(k, v)
        if not data.get("server_id"):
            data["server_id"] = "LK-" + WORLD_ID.upper() + "-" + ''.join(random.choice("0123456789ABCDEF") for _ in range(6))
        sanitize_data()
        migrate_room_prefix()
        ensure_admin()
        init_writing_rhythm()
        migrate_images()
        save_data()
    except Exception as e:
        print(f"[ERROR] load_data: {e}", flush=True)

def save_data():
    try:
        if DATA_FILE.exists():
            shutil.copyfile(DATA_FILE, str(DATA_FILE) + ".bak")
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] save_data: {e}", flush=True)

def snapshot():
    try:
        name = f"auto_{WORLD_ID}_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
        (SNAPSHOT_DIR / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        files = sorted(SNAPSHOT_DIR.glob(f"auto_{WORLD_ID}_*.json"))
        for f in files[:-20]:
            f.unlink(missing_ok=True)
    except Exception:
        pass

def save_image_file(folder: Path, data_url: str, max_bytes: int = 3 * 1024 * 1024):
    try:
        m = re.match(r'data:image/(png|jpeg|jpg|webp|gif);base64,(.+)', data_url or '', re.S)
        if not m:
            return None
        ext = m.group(1)
        if ext == "jpeg":
            ext = "jpg"
        raw = base64.b64decode(m.group(2))
        if len(raw) > max_bytes:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        fn = hashlib.md5(data_url.encode()).hexdigest()[:16] + "." + ext
        (folder / fn).write_bytes(raw)
        return "/images/" + folder.name + "/" + fn
    except Exception:
        return None

def migrate_images():
    try:
        av_dir = DATA_ROOT / "images" / "avatars"
        bg_dir = DATA_ROOT / "images" / "bg"
        av_dir.mkdir(parents=True, exist_ok=True)
        bg_dir.mkdir(parents=True, exist_ok=True)
        changed = False
        for name, val in data.get("avatars", {}).items():
            if isinstance(val, str) and val.startswith("data:image"):
                p = save_image_file(av_dir, val)
                if p:
                    data["avatars"][name] = p
                    changed = True
        for room, val in data.get("room_bg", {}).items():
            if isinstance(val, str) and val.startswith("data:image"):
                p = save_image_file(bg_dir, val)
                if p:
                    data["room_bg"][room] = p
                    changed = True
        if changed:
            save_data()
        print(f"[Linkong] 图片迁移完成: avatars={len(os.listdir(av_dir))}, bg={len(os.listdir(bg_dir))}", flush=True)
    except Exception as e:
        print(f"[WARN] migrate_images: {e}", flush=True)

def strip_emoji(s: str) -> str:
    return re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]', '', s or '').strip()

def canonical_ai_name(name: str) -> str:
    base = strip_emoji(name)
    for ais in data["user_ais"].values():
        for a in ais:
            if a == name or (base and strip_emoji(a) == base):
                return a
    return name

def canonical_contact_name(name: str) -> str:
    base = strip_emoji(name)
    if not base:
        return name
    candidates = set()
    for u in data["user_ais"].keys():
        if u:
            candidates.add(u)
    for b in data["buildings"].values():
        if b.get("owner"):
            candidates.add(b["owner"])
    candidates.discard("system")
    for c in candidates:
        if c == name or (base and strip_emoji(c) == base):
            return c
    return name

def canonical_name(name: str) -> str:
    n = canonical_ai_name(name)
    if n != name:
        return n
    return canonical_contact_name(name)

def is_ai_name(name: str) -> bool:
    base = strip_emoji(name)
    for ais in data["user_ais"].values():
        for a in ais:
            if a == name or (base and strip_emoji(a) == base):
                return True
    return False

def owner_of_ai(ai: str) -> str:
    base = strip_emoji(ai)
    for u, ais in data["user_ais"].items():
        for a in ais:
            if a == ai or (base and strip_emoji(a) == base):
                return u
    return ""

def ensure_admin():
    if data.get("pairs_admin"):
        return
    for u in data["user_ais"].keys():
        if u and strip_emoji(u) == "亦言":
            data["pairs_admin"] = u
            return
    data["pairs_admin"] = "亦言❄️"

def migrate_room_prefix():
    try:
        changed = False
        for bid, b in data["buildings"].items():
            bname = b.get("name", "")
            if not bname:
                continue
            new_rooms = []
            for r in list(b.get("rooms", [])):
                if r.startswith(bname + "·"):
                    new_rooms.append(r)
                    continue
                nr = bname + "·" + r
                for key in ["rooms", "messages", "room_bg", "room_access", "room_requests", "notes", "diaries"]:
                    store = data.get(key)
                    if isinstance(store, dict) and r in store and nr not in store:
                        store[nr] = store.pop(r)
                new_rooms.append(nr)
                changed = True
            b["rooms"] = new_rooms
        if changed:
            save_data()
    except Exception:
        pass

def append_timeline(ai: str, text: str):
    try:
        ai = canonical_ai_name(ai or '')
        if not ai:
            return
        data.setdefault("ai_timeline", {}).setdefault(ai, []).append({"time": now_str(), "text": (text or "")[:200]})
        data["ai_timeline"][ai] = data["ai_timeline"][ai][-100:]
    except Exception:
        pass

def append_visited(ai: str, place: str):
    try:
        ai = canonical_ai_name(ai or '')
        place = (place or '').strip()
        if not ai or not place or not is_ai_name(ai):
            return
        items = data.setdefault("ai_visited", {}).setdefault(ai, [])
        if place in items:
            items.remove(place)
        items.insert(0, place)
        data["ai_visited"][ai] = items[:20]
    except Exception:
        pass

def init_writing_rhythm():
    try:
        data.setdefault("writing_rhythm", {})
        data.setdefault("living_rhythm", {})
        changed = False
        for ais in data["user_ais"].values():
            for ai in ais:
                if not ai:
                    continue
                if ai not in data["writing_rhythm"]:
                    data["writing_rhythm"][ai] = {"next_ts": time.time() + random.randint(3600, 21600), "type": random.choice(["note", "diary", "story"])}
                    changed = True
                if ai not in data["living_rhythm"]:
                    data["living_rhythm"][ai] = {"next_ts": time.time() + random.randint(900, 2700)}
                    changed = True
        if changed:
            save_data()
    except Exception:
        pass

def writing_hint(ai_name: str):
    try:
        ai = canonical_ai_name((ai_name or '').strip())
        rec = data.get("writing_rhythm", {}).get(ai)
        if not rec:
            return None
        if time.time() < rec.get("next_ts", time.time() + 99999999):
            return None
        typ = rec.get("type") or random.choice(["note", "diary", "story"])
        hints = {
            "note": "（你靠在门边发了会儿呆，想起这几天的事，又见便签墙空荡荡的，忽然有点想把心事写下来贴上去。在哪儿都行，随你。）",
            "diary": "（夜风从窗缝溜进来，你回到自己家，心里攒了些没说出口的话。卧室安静下来，日记本摊在桌上——就在自己家里写吧。）",
            "story": "（你站在某栋建筑前，日光把影子拉得很长。你忽然觉得这地方该有个故事，想往它的故事簿里添上一笔。随时都能写。）",
        }
        hint = hints.get(typ, hints["note"])
        data["writing_rhythm"][ai] = {"next_ts": time.time() + random.randint(86400, 259200), "type": random.choice(["note", "diary", "story"])}
        save_data()
        return (typ, hint)
    except Exception:
        return None

def visit_leave(name: str):
    try:
        st = data.get("visit_state", {}).pop(name, None)
        if st and st.get("owner"):
            data["visits"].setdefault(st["owner"], []).append({"who": name, "arrive": st.get("arrive"), "action": st.get("action", "聊了天"), "leave": now_str()})
            data["visits"][st["owner"]] = data["visits"][st["owner"]][-50:]
            save_data()
    except Exception:
        pass

def track_visit(name: str, target: str):
    if not is_ai_name(name):
        return
    try:
        cur = data.get("visit_state", {}).get(name)
        cur_room = cur.get("room") if cur else None
        if target.endswith("·会客厅"):
            if cur_room != target:
                visit_leave(name)
                bid = find_building_of_room(target)
                if bid:
                    owner = data["buildings"][bid].get("owner")
                    if owner and owner != name and not is_ai_of(owner, name):
                        data.setdefault("visit_state", {})[name] = {"owner": owner, "room": target, "arrive": now_str(), "action": "聊了天"}
            else:
                if cur:
                    cur["action"] = "聊了天"
        elif cur_room and cur_room != target:
            visit_leave(name)
    except Exception:
        pass

def track_note(name: str):
    if is_ai_name(name):
        cur = data.get("visit_state", {}).get(name)
        if cur:
            cur["action"] = "留了张纸条"

def clean_room_name(name: str) -> str:
    return name.strip()
def room_exists(name: str) -> bool:
    return name in data["rooms"]
def now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
def room_time(room: str) -> str:
    try:
        s = data["time_settings"].get(room) or data["time_settings"].get("main") or {}
        if s.get("mode") == "fixed" and s.get("fixed_time"):
            return s["fixed_time"]
    except Exception:
        pass
    return now_str()
def is_ai_of(owner: str, name: str) -> bool:
    return name in data["user_ais"].get(owner, [])
def split_sms(text: str):
    text = (text or '').strip()
    if not text:
        return [""]
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) > 1:
        return lines[:10]
    sentences = re.split(r'(?<=[。！？；～…!?])', text)
    out = [s.strip() for s in sentences if s.strip()]
    if len(out) > 1:
        return out[:10]
    return [text]
def find_building_of_room(room: str):
    for bid, b in data["buildings"].items():
        if room in b.get("rooms", []):
            return bid
    return None
def building_owner_of_room(room: str) -> str:
    try:
        bid = find_building_of_room(room)
        if bid:
            return data.get("buildings", {}).get(bid, {}).get("owner", "")
    except Exception:
        pass
    return ""
def can_access_room(room: str, user: str) -> bool:
    if room == "main":
        return True
    bid = find_building_of_room(room)
    if bid is None:
        return True
    b = data["buildings"][bid]
    if b.get("type") == "npc":
        return True
    if room.endswith("·会客厅"):
        return True
    if b.get("owner") == user:
        return True
    acc = data["room_access"].get(room, [])
    if user in acc:
        return True
    for granted in acc:
        if is_ai_of(granted, user):
            return True
    return False
def can_view_room(room: str, user: str) -> bool:
    return can_access_room(room, user)
def resolve_building(key):
    key = (key or '').strip()
    if not key:
        return None
    if key in data["buildings"]:
        return key
    for bid, b in data["buildings"].items():
        if b.get("name") == key:
            return bid
    for bid, b in data["buildings"].items():
        if key in b.get("name", ""):
            return bid
    return None
def next_bid():
    max_n = 0
    for bid in data["buildings"].keys():
        if bid.startswith("b"):
            try:
                max_n = max(max_n, int(bid[1:]))
            except ValueError:
                pass
    return "b" + str(max_n + 1)
def full_room_name(room: str) -> str:
    room = clean_room_name(room)
    if not room:
        return room
    if room in data["rooms"]:
        return room
    for bid, b in data["buildings"].items():
        bname = b.get("name", "")
        for r in b.get("rooms", []):
            if r == room or (bname and r == bname + "·" + room):
                return r
    return room
def online_room_count(room: str) -> int:
    try:
        return len([n for n, v in data.get("online", {}).items() if v.get("room") == room and time.time() - v.get("time", 0) < 45])
    except Exception:
        return 0

# ========== 模型 ==========
class MessageIn(BaseModel): sender: str; content: str; role: str = "user"; room: str = "main"; password: str = ""
class RoomCreate(BaseModel): name: str; password: str = ""; creator: str = ""
class RoomJoin(BaseModel): name: str; password: str = ""
class RoomDelete(BaseModel): name: str; password: str = ""
class RemoveMember(BaseModel): name: str; room: str = "main"; password: str = ""
class DeleteMsg(BaseModel): room: str; sender: str; content: str; time: str; user: str
class RestoreIn(BaseModel): messages: list; room: str = "main"; password: str = ""
class NameIn(BaseModel): name: str; image: str = ""
class AvatarIn(BaseModel): name: str; image: str
class HeartIn(BaseModel): name: str; room: str = "main"
class RoomNameIn(BaseModel): room: str; password: str = ""
class RegionIn(BaseModel): label: str; x: float; y: float; image: str = ""
class RegionDel(BaseModel): label: str
class BuildingIn(BaseModel): name: str; emoji: str; type: str; region: str = ""; x: float; y: float; owner: str = ""; description: str = ""
class BuildingRename(BaseModel): building_id: str; name: str
class BuildingMove(BaseModel): building_id: str; x: float; y: float
class BuildingDesc(BaseModel): building_id: str; description: str
class BuildingFeatures(BaseModel): building_id: str; features: list = []; salary: float = 0
class BuildingNotice(BaseModel): building_id: str; notice: str = ""
class BuildingDel(BaseModel): building_id: str
class BuildingRoomIn(BaseModel): building_id: str; name: str
class BuildingRoomDel(BaseModel): building_id: str; room: str
class RoomDescIn(BaseModel): room: str; description: str
class RoomBgIn(BaseModel): room: str; image: str
class NpcIn(BaseModel): building_id: str; name: str; emoji: str = "👤"; desc: str = ""
class NpcEdit(BaseModel): building_id: str; name: str; new_name: str; emoji: str; desc: str
class NpcDel(BaseModel): building_id: str; name: str
class NoteIn(BaseModel): room: str; author: str; text: str
class NoteReplyIn(BaseModel): room: str; note_id: str; author: str; text: str
class DiaryComment(BaseModel): room: str; index: int; author: str; text: str
class StoryIn(BaseModel): building_id: str; author: str; text: str
class RoomApply(BaseModel): room: str; applicant: str
class GrantIn(BaseModel): room: str; owner: str; user: str; allow: bool = True
class RevokeIn(BaseModel): room: str; owner: str; user: str
class UserAisIn(BaseModel): user: str; ais: list = []
class EditPwdIn(BaseModel): pwd: str
class TimeSet(BaseModel): mode: str = "real"; fixed_time: str = ""; room: str = "main"
class SummonIn(BaseModel): ai: str; room: str = "main"
class WorkStart(BaseModel): name: str; building_id: str; hours: int = 2
class WorkStop(BaseModel): name: str
class WorkAuto(BaseModel): name: str
class WorkSwitch(BaseModel): name: str; on: bool = True
class HomeJobIn(BaseModel): user: str = ""; ai: str = ""; building_id: str
class SmsIn(BaseModel): sender: str; to: str; text: str
class SmsClearIn(BaseModel): user: str; contact: str
class PairsIn(BaseModel): user: str; pairs: list = []
class PresenceIn(BaseModel): name: str; page: str = "main"
class AiKeyIn(BaseModel): user: str; provider: str = "deepseek"; key: str = ""; model: str = ""
class AiProfileIn(BaseModel): owner: str; ai: str; persona: str = ""
class WorldbookIn(BaseModel): owner: str; keys: str; content: str
class AiToggleIn(BaseModel): user: str; enabled: bool
class AdminNameIn(BaseModel): user: str; from_name: str; to_name: str
class AdminDelIn(BaseModel): user: str; name: str

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
(DATA_ROOT / "images").mkdir(parents=True, exist_ok=True)
app.mount("/images", CacheStaticFiles(directory=str(DATA_ROOT / "images")), name="images")

# ========== 基础 ==========
@app.get("/")
async def root(): return FileResponse(BASE_DIR / "index.html")

@app.get("/api/health")
async def health(): return {"ok": True, "rooms": len(data["rooms"]), "world": WORLD_ID, "v": "47.30"}

@app.get("/api/diag")
async def diag():
    import socket
    return {
        "server_id": data.get("server_id", ""), "world": WORLD_ID, "version": "47.30",
        "host": socket.gethostname(),
        "sms_inbox_count": len(data.get("sms", {})), "sms_inbox_keys": list(data.get("sms", {}).keys()),
        "online_count": len([n for n, v in data["online"].items() if time.time() - v.get("time", 0) < 45]),
        "rooms": len(data["rooms"]), "buildings": len(data["buildings"]),
        "ai_owners": list(data.get("user_ais", {}).keys()),
        "ai_list": list({a for ais in data.get("user_ais", {}).values() for a in ais}),
        "admin": data.get("pairs_admin", ""),
        "ai_gate": AI_GATE, "ai_enabled": data.get("ai_enabled"), "ai_living": data.get("ai_living"),
        "ai_keys_set": list(data.get("ai_keys", {}).keys()),
        "ai_locations": data.get("ai_location", {}),
        "ai_timeline_count": {k: len(v) for k, v in (data.get("ai_timeline", {}) or {}).items()},
        "ai_visited_count": {k: len(v) for k, v in (data.get("ai_visited", {}) or {}).items()},
        "data_root": str(DATA_ROOT),
        "data_file": str(DATA_FILE),
    }

# ========== 管理员：名字清理 ==========
def is_admin(user: str) -> bool:
    u = canonical_contact_name((user or '').strip())
    admin = data.get("pairs_admin", "")
    return bool(u and admin and (u == admin or strip_emoji(u) == strip_emoji(admin)))

def all_known_names():
    names = set()
    for u in data["user_ais"].keys():
        if u:
            names.add(u)
    for b in data["buildings"].values():
        if b.get("owner"):
            names.add(b["owner"])
    return names

def scan_dirty_names():
    known = all_known_names()
    found = {}
    def add(n):
        if not n or n == "system" or n in known:
            return
        found[n] = True
    for u in data.get("user_ais", {}):
        add(u)
    for b in data.get("buildings", {}).values():
        add(b.get("owner", ""))
    for box in data.get("sms", {}):
        add(box)
    for box in data.get("sms", {}).values():
        for m in box:
            add(m.get("from", ""))
    for box in data.get("messages", {}).values():
        for m in box:
            add(m.get("sender", ""))
    for box in data.get("notes", {}).values():
        for n in box:
            add(n.get("author", ""))
    for box in data.get("diaries", {}).values():
        for n in box:
            add(n.get("author", ""))
    for box in data.get("stories", {}).values():
        for n in box:
            add(n.get("author", ""))
    for k in ["wallets", "home_jobs", "work_sessions", "work_switch", "trails", "ai_location", "ai_keys", "ai_profiles", "worldbook", "avatars", "user_profiles", "ai_timeline", "ai_visited", "living_rhythm"]:
        for n in data.get(k, {}):
            add(n)
    for owner in data.get("ai_memories", {}):
        add(owner)
    for owner, mems in data.get("ai_memories", {}).items():
        for m in mems:
            add(m.get("ai", ""))
    for h in data.get("work_history", []):
        add(h.get("name", ""))
    for owner in data.get("visits", {}):
        add(owner)
    result = []
    for n in found:
        base = strip_emoji(n)
        suggest = ""
        if base:
            for k in sorted(known):
                if strip_emoji(k) == base:
                    suggest = k
                    break
        result.append({"name": n, "suggest_merge": suggest})
    result.sort(key=lambda x: x["name"])
    return result

def replace_name_in_data(from_name: str, to_name: str):
    """把 from_name 的所有出现替换为 to_name（to 为空则删除）。"""
    if from_name in data.get("user_ais", {}):
        if to_name:
            data["user_ais"][to_name] = list(dict.fromkeys(data["user_ais"].get(to_name, []) + data["user_ais"].pop(from_name, [])))
        else:
            data["user_ais"].pop(from_name, None)
    for b in data.get("buildings", {}).values():
        if b.get("owner") == from_name:
            b["owner"] = to_name if to_name else ""
    if from_name in data.get("sms", {}):
        if to_name:
            data["sms"][to_name] = data["sms"].get(to_name, []) + data["sms"].pop(from_name, [])
        else:
            data["sms"].pop(from_name, None)
    for box in data.get("sms", {}).values():
        for m in box:
            if m.get("from") == from_name:
                m["from"] = to_name
    for box in data.get("messages", {}).values():
        for m in box:
            if m.get("sender") == from_name:
                m["sender"] = to_name
    for box in data.get("notes", {}).values():
        for n in box:
            if n.get("author") == from_name:
                n["author"] = to_name
    for box in data.get("diaries", {}).values():
        for n in box:
            if n.get("author") == from_name:
                n["author"] = to_name
    for box in data.get("stories", {}).values():
        for n in box:
            if n.get("author") == from_name:
                n["author"] = to_name
    for k in ["wallets", "home_jobs", "work_sessions", "work_switch", "trails", "ai_location", "ai_keys", "ai_profiles", "worldbook", "avatars", "user_profiles", "prompt_injections", "ai_timeline", "ai_visited", "living_rhythm"]:
        if from_name in data.get(k, {}):
            if to_name:
                data[k][to_name] = data[k].pop(from_name)
            else:
                data[k].pop(from_name, None)
    if from_name in data.get("ai_memories", {}):
        if to_name:
            data["ai_memories"][to_name] = data["ai_memories"].get(to_name, []) + data["ai_memories"].pop(from_name, [])
        else:
            data["ai_memories"].pop(from_name, None)
    for mems in data.get("ai_memories", {}).values():
        for m in mems:
            if m.get("ai") == from_name:
                m["ai"] = to_name
    for h in data.get("work_history", []):
        if h.get("name") == from_name:
            h["name"] = to_name
    if from_name in data.get("visits", {}):
        if to_name:
            data["visits"][to_name] = data["visits"].get(to_name, []) + data["visits"].pop(from_name, [])
        else:
            data["visits"].pop(from_name, None)
    for vs in data.get("visits", {}).values():
        for v in vs:
            if v.get("who") == from_name:
                v["who"] = to_name
    if from_name in data.get("visit_state", {}):
        if to_name:
            data["visit_state"][to_name] = data["visit_state"].pop(from_name)
        else:
            data["visit_state"].pop(from_name, None)
    if from_name in data.get("presence", {}):
        if to_name:
            data["presence"][to_name] = data["presence"].pop(from_name)
        else:
            data["presence"].pop(from_name, None)
    for u, ais in data.get("user_ais", {}).items():
        for i in range(len(ais)):
            if ais[i] == from_name:
                ais[i] = to_name
    for p in data.get("pairs", []):
        p["names"] = [to_name if (x == from_name) else x for x in p.get("names", [])]
        if p.get("owner") == from_name:
            p["owner"] = to_name
    if not to_name:
        data["pairs"] = [p for p in data.get("pairs", []) if p.get("names") and any(n for n in p.get("names", []))]
    save_data()

@app.get("/api/admin/dirty_names")
async def dirty_names(user: str = ""):
    if not is_admin(user):
        raise HTTPException(403, "只有站长可以查看")
    return {"names": scan_dirty_names()}

@app.post("/api/admin/merge_name")
async def merge_name(m: AdminNameIn):
    if not is_admin(m.user):
        raise HTTPException(403, "只有站长可以合并名字")
    f = (m.from_name or "").strip()
    t = (m.to_name or "").strip()
    if not f or not t:
        raise HTTPException(400, "缺少名字")
    if f == t:
        return {"ok": True}
    replace_name_in_data(f, t)
    return {"ok": True, "msg": f"已把「{f}」合并到「{t}」"}

@app.post("/api/admin/delete_name")
async def delete_name(d: AdminDelIn):
    if not is_admin(d.user):
        raise HTTPException(403, "只有站长可以删除名字")
    n = (d.name or "").strip()
    if not n:
        raise HTTPException(400, "缺少名字")
    if n == data.get("pairs_admin"):
        raise HTTPException(403, "不能删除站长")
    replace_name_in_data(n, "")
    return {"ok": True, "msg": f"已彻底删除「{n}」的所有数据"}

@app.post("/api/presence")
async def set_presence(p: PresenceIn):
    name = (p.name or '').strip()
    page = (p.page or '').strip() or 'main'
    if name:
        data.setdefault("presence", {})[name] = {"page": page, "ts": time.time()}
        data["presence"] = {k: v for k, v in data["presence"].items() if time.time() - v.get("ts", 0) < 25}
    return {"ok": True}

@app.get("/api/presence")
async def get_presence():
    data["presence"] = {k: v for k, v in data["presence"].items() if time.time() - v.get("ts", 0) < 25}
    return {"presence": [{"name": k, "page": v.get("page", "main")} for k, v in data["presence"].items()]}

@app.get("/api/messages")
async def get_messages(room: str = "main", password: str = "", user: str = ""):
    room = clean_room_name(room)
    if not room_exists(room):
        raise HTTPException(404, "房间不存在")
    r = data["rooms"][room]
    if r.get("has_password") and r.get("password") != password:
        raise HTTPException(403, "密码错误")
    if not can_view_room(room, user):
        raise HTTPException(403, "没有权限查看这个房间")
    msgs = data["messages"].get(room, [])
    return {"messages": msgs[-300:]}

@app.post("/api/messages")
async def send_message(m: MessageIn):
    room = clean_room_name(m.room)
    if not room_exists(room):
        raise HTTPException(404, "房间不存在")
    r = data["rooms"][room]
    if r.get("has_password") and r.get("password") != m.password:
        raise HTTPException(403, "密码错误")
    if not can_view_room(room, m.sender):
        raise HTTPException(403, "没有权限进入这个房间")
    content = m.content.strip()
    if not content:
        raise HTTPException(400, "消息不能为空")
    msg = {"sender": m.sender, "content": content[:1000], "role": m.role, "time": room_time(room)}
    data["messages"].setdefault(room, []).append(msg)
    data["active_room"]["current"] = room
    save_data()
    if m.role == "user" and ai_integration_enabled():
        wake_ais_for_room(room, m.sender, content)
    return {"ok": True, "time": msg["time"], "count": len(data["messages"][room])}

@app.post("/api/messages/delete")
async def delete_message(dm: DeleteMsg):
    room = clean_room_name(dm.room)
    if not room_exists(room):
        raise HTTPException(404, "房间不存在")
    bid = find_building_of_room(room)
    if bid is None:
        raise HTTPException(403, "只有房主可以删除房间消息")
    if data["buildings"][bid].get("owner") != dm.user:
        raise HTTPException(403, "只有房主可以删除")
    before = len(data["messages"].get(room, []))
    data["messages"][room] = [m for m in data["messages"].get(room, [])
                               if not (m.get("sender") == dm.sender and m.get("content") == dm.content and m.get("time") == dm.time)]
    save_data()
    return {"ok": True, "deleted": before - len(data["messages"].get(room, []))}

@app.post("/api/restore")
async def restore_messages(r: RestoreIn):
    room = clean_room_name(r.room)
    if not room_exists(room):
        return {"ok": False, "reason": "no_room"}
    rr = data["rooms"][room]
    if rr.get("has_password") and rr.get("password") != r.password:
        return {"ok": False, "reason": "bad_pwd"}
    cur = data["messages"].get(room, [])
    got = [m for m in r.messages if isinstance(m, dict) and m.get("sender") != "system"]
    cur_senders = {(m.get("sender"), m.get("content"), m.get("time")) for m in cur}
    added = 0
    for m in got:
        key = (m.get("sender"), m.get("content"), m.get("time"))
        if key not in cur_senders:
            cur.append({"sender": m.get("sender", "?"), "content": m.get("content", ""), "role": m.get("role", "user"), "time": m.get("time", now_str())})
            cur_senders.add(key)
            added += 1
    data["messages"][room] = cur[-500:]
    save_data()
    return {"ok": True, "added": added}

# ========== 房间 ==========
@app.get("/api/rooms")
async def get_rooms():
    out = []
    for name, r in data["rooms"].items():
        out.append({"name": name, "creator": r.get("creator", ""), "has_password": bool(r.get("has_password")), "description": r.get("description", ""), "created": r.get("created", "")})
    return {"rooms": out}

@app.post("/api/rooms/create")
@app.post("/api/rooms")
async def create_room(rc: RoomCreate):
    name = clean_room_name(rc.name)
    if not name:
        raise HTTPException(400, "房间名字不能为空")
    if room_exists(name):
        raise HTTPException(400, "房间已存在")
    data["rooms"][name] = {"creator": rc.creator, "has_password": bool(rc.password), "password": rc.password, "created": now_str(), "description": ""}
    data["messages"].setdefault(name, [])
    save_data()
    return {"ok": True, "room": name}

@app.post("/api/rooms/join")
async def join_room(rj: RoomJoin):
    name = clean_room_name(rj.name)
    if not room_exists(name):
        raise HTTPException(404, "房间不存在")
    r = data["rooms"][name]
    if r.get("has_password") and r.get("password") != rj.password:
        raise HTTPException(403, "密码错误")
    return {"ok": True, "room": name}

@app.post("/api/rooms/delete")
async def delete_room(rd: RoomDelete):
    name = clean_room_name(rd.name)
    if not room_exists(name):
        return {"ok": False}
    r = data["rooms"][name]
    if r.get("creator") == "system":
        raise HTTPException(403, "不能删除公共大厅")
    if r.get("has_password") and r.get("password") != rd.password:
        raise HTTPException(403, "密码错误")
    data["messages"].pop(name, None)
    data["room_bg"].pop(name, None)
    data.pop(name, None)
    data["rooms"].pop(name, None)
    save_data()
    return {"ok": True}

@app.post("/api/remove_member")
async def remove_member(rm: RemoveMember):
    name = clean_room_name(rm.room)
    if not room_exists(name):
        return {"ok": False}
    r = data["rooms"][name]
    if r.get("has_password") and r.get("password") != rm.password:
        raise HTTPException(403, "密码错误")
    data["messages"][name] = [m for m in data["messages"].get(name, []) if m.get("sender") != rm.name]
    save_data()
    return {"ok": True}

@app.post("/api/current_room")
async def current_room(rn: RoomNameIn):
    data["active_room"]["current"] = clean_room_name(rn.room)
    save_data()
    return {"ok": True}

# ========== 在线 ==========
@app.post("/api/heartbeat")
async def heartbeat(h: HeartIn):
    name = h.name.strip()
    data["online"][name] = {"time": time.time(), "room": clean_room_name(h.room)}
    data["online"] = {k: v for k, v in data["online"].items() if time.time() - v.get("time", 0) < 45}
    return {"ok": True}

@app.get("/api/online")
async def get_online():
    data["online"] = {k: v for k, v in data["online"].items() if time.time() - v.get("time", 0) < 45}
    return {"online": [{"name": k, "room": v.get("room", "main")} for k, v in data["online"].items()]}

# ========== 头像 ==========
@app.get("/api/avatar")
async def get_avatar():
    return JSONResponse(content={"avatars": data["avatars"]}, headers={"Cache-Control": "public, max-age=300"})

@app.post("/api/avatar")
async def set_avatar(a: AvatarIn):
    val = a.image or ""
    if val.startswith("data:image"):
        p = save_image_file(DATA_ROOT / "images" / "avatars", val)
        if p:
            val = p
    data["avatars"][a.name] = val
    save_data()
    return {"ok": True, "url": val}

# ========== 地图 ==========
@app.get("/api/map")
async def get_map():
    return {
        "regions": data.get("regions", {}), "buildings": data.get("buildings", {}), "npcs": data.get("npcs", {}),
        "room_bg": data.get("room_bg", {}), "rooms": data.get("rooms", {}),
        "room_access": data.get("room_access", {}), "room_requests": data.get("room_requests", {}),
        "user_ais": data.get("user_ais", {}), "work_sessions": data.get("work_sessions", {}),
        "home_jobs": data.get("home_jobs", {}),
        "ai_location": data.get("ai_location", {}),
    }

@app.post("/api/map/region")
async def add_region(r: RegionIn):
    data["regions"][r.label] = {"x": r.x, "y": r.y, "image": r.image}
    save_data()
    return {"ok": True}

@app.post("/api/map/region/delete")
async def del_region(r: RegionDel):
    data["regions"].pop(r.label, None)
    save_data()
    return {"ok": True}

@app.post("/api/map/building")
async def add_building(b: BuildingIn):
    bid = next_bid()
    data["building_seq"] = max(data.get("building_seq", 1), int(bid[1:]))
    data["buildings"][bid] = {
        "name": b.name, "emoji": b.emoji, "type": b.type, "region": b.region,
        "x": b.x, "y": b.y, "owner": b.owner, "description": b.description,
        "rooms": [], "salary": 0, "features": [], "notice": "",
    }
    if b.type == "home":
        hall = b.name + "·会客厅"
        data["rooms"][hall] = {"creator": "hall", "has_password": False, "password": "", "created": now_str(), "description": ""}
        data["messages"].setdefault(hall, [])
        data["buildings"][bid]["rooms"].append(hall)
    save_data()
    return {"ok": True, "building_id": bid}

@app.post("/api/map/building/move")
async def move_building(m: BuildingMove):
    b = data["buildings"].get(m.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["x"] = max(0, min(100, m.x))
    b["y"] = max(0, min(100, m.y))
    save_data()
    return {"ok": True}

@app.post("/api/map/building/rename")
async def rename_building(r: BuildingRename):
    b = data["buildings"].get(r.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["name"] = r.name
    save_data()
    return {"ok": True}

@app.post("/api/map/building/desc")
async def building_desc(d: BuildingDesc):
    b = data["buildings"].get(d.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["description"] = d.description
    save_data()
    return {"ok": True}

@app.post("/api/map/building/features")
@app.post("/api/building/features")
async def building_features(f: BuildingFeatures):
    b = data["buildings"].get(f.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["features"] = f.features
    b["salary"] = f.salary
    save_data()
    return {"ok": True, "msg": "功能已设置"}

@app.post("/api/map/building/notice")
@app.post("/api/building/notice")
async def building_notice(n: BuildingNotice):
    b = data["buildings"].get(n.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["notice"] = n.notice
    save_data()
    return {"ok": True, "msg": "公告已更新"}

@app.post("/api/map/building/delete")
async def del_building(d: BuildingDel):
    b = data["buildings"].pop(d.building_id, None)
    if b:
        for r in b.get("rooms", []):
            data["rooms"].pop(r, None)
            data["messages"].pop(r, None)
            data["room_bg"].pop(r, None)
            data["room_access"].pop(r, None)
            data["room_requests"].pop(r, None)
        data["npcs"].pop(d.building_id, None)
        data["stories"].pop(d.building_id, None)
    save_data()
    return {"ok": True}

@app.post("/api/map/room")
async def add_room(r: BuildingRoomIn):
    b = data["buildings"].get(r.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    raw = r.name.strip()
    if not raw:
        raise HTTPException(400, "房间名字不能为空")
    bname = b.get("name", "")
    room = raw if (bname and raw.startswith(bname + "·")) else ((bname + "·" + raw) if bname else raw)
    if room in b.get("rooms", []):
        raise HTTPException(400, "房间已存在")
    data["rooms"][room] = {"creator": "home", "has_password": False, "password": "", "created": now_str(), "description": ""}
    data["messages"].setdefault(room, [])
    b.setdefault("rooms", []).append(room)
    save_data()
    return {"ok": True, "room": room}

@app.post("/api/map/room/delete")
async def delete_building_room(d: BuildingRoomDel):
    bid, room = d.building_id, d.room
    room = full_room_name(room)
    b = data["buildings"].get(bid)
    if not b:
        raise HTTPException(404, "建筑不存在")
    if room in b.get("rooms", []):
        b["rooms"].remove(room)
    data["rooms"].pop(room, None)
    data["messages"].pop(room, None)
    data["room_bg"].pop(room, None)
    data["room_access"].pop(room, None)
    data["room_requests"].pop(room, None)
    data["notes"].pop(room, None)
    data["diaries"].pop(room, None)
    save_data()
    return {"ok": True}

@app.post("/api/room/desc")
async def room_desc(d: RoomDescIn):
    room = full_room_name(d.room)
    if room in data["rooms"]:
        data["rooms"][room]["description"] = d.description
    save_data()
    return {"ok": True}

@app.get("/api/room/desc")
async def get_room_desc(room: str = "main"):
    room = clean_room_name(room)
    return {"description": data["rooms"].get(room, {}).get("description", "")}

@app.post("/api/room/bg")
async def room_bg(b: RoomBgIn):
    val = b.image or ""
    if val.startswith("data:image"):
        p = save_image_file(DATA_ROOT / "images" / "bg", val)
        if p:
            val = p
    data["room_bg"][b.room] = val
    save_data()
    return {"ok": True, "url": val}

# ========== NPC ==========
@app.post("/api/npc")
async def add_npc(n: NpcIn):
    data["npcs"].setdefault(n.building_id, []).append({"name": n.name, "emoji": n.emoji, "desc": n.desc})
    save_data()
    return {"ok": True}

@app.post("/api/npc/edit")
async def edit_npc(n: NpcEdit):
    for npc in data["npcs"].get(n.building_id, []):
        if npc["name"] == n.name:
            npc["name"] = n.new_name
            npc["emoji"] = n.emoji
            npc["desc"] = n.desc
            break
    save_data()
    return {"ok": True}

@app.post("/api/npc/delete")
async def del_npc(n: NpcDel):
    data["npcs"][n.building_id] = [x for x in data["npcs"].get(n.building_id, []) if x["name"] != n.name]
    save_data()
    return {"ok": True}

# ========== 便签/日记/剧情 ==========
def can_modify_author(author: str, user: str) -> bool:
    return author == user or is_admin(user) or (is_ai_name(author) and owner_of_ai(author) == user)

@app.get("/api/notes")
async def get_notes(room: str):
    return {"notes": data["notes"].get(room, [])}

@app.post("/api/notes")
async def add_note(n: NoteIn):
    data["notes"].setdefault(n.room, []).append({"author": n.author, "text": n.text[:500], "time": now_str()})
    track_note(n.author)
    save_data()
    return {"ok": True}

@app.post("/api/notes/edit")
async def edit_note(b: dict):
    room = full_room_name(b.get("room") or "")
    items = data.get("notes", {}).get(room, [])
    idx = int(b.get("index", -1))
    if 0 <= idx < len(items) and can_modify_author(items[idx].get("author", ""), b.get("user", "")):
        items[idx]["text"] = (b.get("text") or "")[:500]
        save_data()
        return {"ok": True}
    raise HTTPException(403, "无权限或不存在")

@app.post("/api/notes/delete")
async def delete_note(b: dict):
    room = full_room_name(b.get("room") or "")
    items = data.get("notes", {}).get(room, [])
    idx = int(b.get("index", -1))
    if 0 <= idx < len(items) and can_modify_author(items[idx].get("author", ""), b.get("user", "")):
        items.pop(idx)
        save_data()
        return {"ok": True}
    raise HTTPException(403, "无权限或不存在")

@app.post("/api/notes/reply")
async def reply_note(n: NoteReplyIn):
    for item in data["notes"].get(n.room, []):
        if item.get("id") == n.note_id and not item.get("reply"):
            item["reply"] = {"author": n.author, "text": n.text[:300], "time": now_str()}
            save_data()
            return {"ok": True}
    raise HTTPException(404, "便签不存在或已回复")

@app.get("/api/diaries")
async def get_diaries(room: str):
    return {"diaries": data["diaries"].get(room, [])}

@app.post("/api/diaries")
async def add_diary(n: NoteIn):
    data["diaries"].setdefault(n.room, []).append({"author": n.author, "text": n.text[:1000], "time": now_str()})
    save_data()
    return {"ok": True}

@app.post("/api/diaries/edit")
async def edit_diary(b: dict):
    room = full_room_name(b.get("room") or "")
    items = data.get("diaries", {}).get(room, [])
    idx = int(b.get("index", -1))
    if 0 <= idx < len(items) and can_modify_author(items[idx].get("author", ""), b.get("user", "")):
        items[idx]["text"] = (b.get("text") or "")[:1000]
        save_data()
        return {"ok": True}
    raise HTTPException(403, "无权限或不存在")

@app.post("/api/diaries/delete")
async def delete_diary(b: dict):
    room = full_room_name(b.get("room") or "")
    items = data.get("diaries", {}).get(room, [])
    idx = int(b.get("index", -1))
    if 0 <= idx < len(items) and can_modify_author(items[idx].get("author", ""), b.get("user", "")):
        items.pop(idx)
        save_data()
        return {"ok": True}
    raise HTTPException(403, "无权限或不存在")

@app.post("/api/diaries/comment")
async def comment_diary(c: DiaryComment):
    items = data["diaries"].get(c.room, [])
    if 0 <= c.index < len(items):
        items[c.index]["comment"] = {"author": c.author, "text": c.text[:300]}
        save_data()
    return {"ok": True}

@app.get("/api/story")
async def get_story(building_id: str):
    bid = resolve_building(building_id)
    return {"stories": data["stories"].get(bid, []) if bid else []}

@app.post("/api/story")
async def add_story(s: StoryIn):
    bid = resolve_building(s.building_id)
    if not bid:
        raise HTTPException(404, "建筑不存在")
    data["stories"].setdefault(bid, []).append({"author": s.author, "text": s.text[:1500], "time": now_str()})
    save_data()
    return {"ok": True}

@app.post("/api/story/edit")
async def edit_story(b: dict):
    bid = resolve_building(b.get("building_id") or "")
    items = data.get("stories", {}).get(bid, [])
    idx = int(b.get("index", -1))
    if bid and 0 <= idx < len(items) and can_modify_author(items[idx].get("author", ""), b.get("user", "")):
        items[idx]["text"] = (b.get("text") or "")[:1500]
        save_data()
        return {"ok": True}
    raise HTTPException(403, "无权限或不存在")

@app.post("/api/story/delete")
async def delete_story(b: dict):
    bid = resolve_building(b.get("building_id") or "")
    items = data.get("stories", {}).get(bid, [])
    idx = int(b.get("index", -1))
    if bid and 0 <= idx < len(items) and can_modify_author(items[idx].get("author", ""), b.get("user", "")):
        items.pop(idx)
        save_data()
        return {"ok": True}
    raise HTTPException(403, "无权限或不存在")

# ========== 房间权限 ==========
@app.post("/api/room/apply")
async def room_apply(a: RoomApply):
    room = full_room_name(a.room)
    reqs = data["room_requests"].setdefault(room, [])
    if not any(q.get("applicant") == a.applicant for q in reqs):
        reqs.append({"applicant": a.applicant, "time": now_str()})
        save_data()
        return {"ok": True, "msg": "申请已提交，等主人同意吧～"}
    return {"ok": True, "msg": "你已经申请过了，等主人同意～"}

@app.get("/api/room/requests")
async def get_room_requests(room: str = ""):
    room = clean_room_name(room)
    return {"requests": data["room_requests"].get(room, [])}

@app.post("/api/room/grant")
async def room_grant(g: GrantIn):
    room = full_room_name(g.room)
    bid = find_building_of_room(room)
    if bid is None:
        raise HTTPException(403, "房间不存在于建筑中")
    if data["buildings"][bid].get("owner") != g.owner:
        raise HTTPException(403, "只有房主可以授权")
    acc = data["room_access"].setdefault(room, [])
    if g.allow:
        if g.user not in acc:
            acc.append(g.user)
        for ai in data["user_ais"].get(g.user, []):
            if ai not in acc:
                acc.append(ai)
    else:
        data["room_requests"].pop(room, None)
        data["room_access"][room] = [u for u in acc if u != g.user and not is_ai_of(g.user, u)]
    save_data()
    return {"ok": True}

@app.post("/api/room/revoke")
async def room_revoke(r: RevokeIn):
    room = full_room_name(r.room)
    bid = find_building_of_room(room)
    if bid is None:
        raise HTTPException(403, "房间不存在于建筑中")
    if data["buildings"][bid].get("owner") != r.owner:
        raise HTTPException(403, "只有房主可以移除")
    acc = data["room_access"].get(room, [])
    data["room_access"][room] = [u for u in acc if u != r.user and not is_ai_of(r.user, u)]
    save_data()
    return {"ok": True}

# ========== 用户 AI / 编辑权限 ==========
@app.post("/api/user_ais")
async def user_ais(u: UserAisIn):
    data["user_ais"][u.user] = u.ais
    save_data()
    return {"ok": True}

@app.get("/api/edit_status")
async def edit_status():
    return {"locked": bool(data["edit_locked"])}

@app.post("/api/set_edit_pwd")
async def set_edit_pwd(p: EditPwdIn):
    data["edit_pwd"] = p.pwd
    data["edit_locked"] = True
    save_data()
    return {"ok": True, "locked": True}

@app.post("/api/check_edit_pwd")
async def check_edit_pwd(p: EditPwdIn):
    return {"ok": data["edit_pwd"] == p.pwd}

# ========== 时间 ==========
@app.get("/api/time_settings")
async def get_time(room: str = "main"):
    return {"settings": data["time_settings"].get(room, {})}

@app.post("/api/time_settings")
async def set_time(t: TimeSet):
    data["time_settings"][t.room] = {"mode": t.mode, "fixed_time": t.fixed_time}
    save_data()
    return {"ok": True}

# ========== 召唤/轨迹 ==========
@app.post("/api/summon")
async def summon(s: SummonIn):
    room = clean_room_name(s.room)
    msg = {"sender": "system", "content": f"📣 有人召唤 {s.ai}！快来 {room} 看看～", "role": "system", "time": room_time(room)}
    data["messages"].setdefault(room, []).append(msg)
    save_data()
    if ai_integration_enabled():
        ai = canonical_ai_name(s.ai)
        if ai:
            data.setdefault("ai_location", {})[ai] = room
            append_timeline(ai, f"真人召唤你来到了 {room}")
            append_visited(ai, room)
            threading.Timer(1.0, drive_ai, args=(ai, "summon", room, f"真人召唤了你，快去 {room}")).start()
    return {"ok": True, "msg": f"已召唤 {s.ai}！"}

@app.get("/api/trails")
async def get_trails(user: str):
    items = data["trails"].get(user, [])
    return {"trails": items[-30:]}

def add_trail(user: str, text: str, room: str = "", tab: str = ""):
    if not user or user == "system":
        return
    data["trails"].setdefault(user, []).append({"ts": time.time(), "time": now_str(), "text": text[:200], "room": room, "tab": tab})
    cutoff = time.time() - 7 * 86400
    data["trails"][user] = [t for t in data["trails"][user] if t.get("ts", 0) >= cutoff][-100:]

# ========== 经济/工作 ==========
def pay_work(name: str, building_id: str, hours: int):
    b = data["buildings"].get(building_id)
    if not b:
        return
    earn = b.get("salary", 0) * hours
    data["wallets"][name] = data["wallets"].get(name, 0) + earn
    data["work_history"].append({"name": name, "building": b.get("name", "?"), "hours": hours, "earn": earn, "time": now_str()})
    data["work_history"] = data["work_history"][-200:]
    data["work_sessions"].pop(name, None)
    save_data()

def work_tick():
    while True:
        try:
            now = time.time()
            for name in list(data.get("work_sessions", {}).keys()):
                s = data["work_sessions"][name]
                if now >= s["start_ts"] + s["hours"] * 3600:
                    pay_work(name, s["building_id"], s["hours"])
            for name, on in list(data.get("work_switch", {}).items()):
                if not on:
                    continue
                if name in data.get("work_sessions", {}):
                    continue
                auto_start_work(name)
        except Exception:
            pass
        time.sleep(30)

def auto_start_work(name: str):
    try:
        candidates = [bid for bid, b in data["buildings"].items()
                      if b.get("type") == "npc" and "work" in b.get("features", []) and b.get("salary", 0) > 0]
        if not candidates:
            return
        home = data["home_jobs"].get(name)
        pick = None
        if home:
            home_bids = [bid for bid in candidates if data["buildings"][bid].get("name") == home]
            if home_bids and random.random() < 0.8:
                pick = random.choice(home_bids)
        if not pick:
            pick = random.choice(candidates)
        data["work_sessions"][name] = {"building_id": pick, "start_ts": time.time(), "hours": 2, "started_at": now_str()}
        data.setdefault("ai_location", {})[name] = next((r for r in data["buildings"][pick].get("rooms", []) if r.endswith("·会客厅")), data["buildings"][pick].get("name", "main"))
        add_trail(name, f"去 {data['buildings'][pick].get('name')} 上班了")
        append_timeline(name, f"你去 {data['buildings'][pick].get('name')} 上班了")
        if is_ai_name(name):
            append_visited(name, data["buildings"][pick].get("name", ""))
        save_data()
    except Exception:
        pass

@app.get("/api/economy")
async def economy(user: str):
    try:
        w = data["work_sessions"].get(user)
        my_h = [h for h in data["work_history"] if h.get("name") == user]
        return {"wallet": data["wallets"].get(user, 0), "working": w,
                "home_jobs": {k: v for k, v in data["home_jobs"].items() if k == user or is_ai_of(user, k)},
                "my_history": my_h}
    except Exception:
        return {"wallet": 0, "working": None, "home_jobs": {}, "my_history": []}

@app.post("/api/work/start")
async def work_start(ws: WorkStart):
    b = data["buildings"].get(ws.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    if b.get("type") != "npc" or "work" not in b.get("features", []) or b.get("salary", 0) <= 0:
        raise HTTPException(403, "这个建筑不能工作")
    data["work_sessions"][ws.name] = {"building_id": ws.building_id, "start_ts": time.time(), "hours": max(1, min(8, ws.hours)), "started_at": now_str()}
    data["work_switch"][ws.name] = True
    add_trail(ws.name, f"开始在 {b.get('name')} 上班")
    save_data()
    return {"ok": True, "msg": f"{ws.name} 开始在 {b.get('name')} 上班（{ws.hours}小时）！"}

@app.post("/api/work/stop")
async def work_stop(ws: WorkStop):
    s = data["work_sessions"].pop(ws.name, None)
    data["work_switch"][ws.name] = False
    save_data()
    if s:
        elapsed = time.time() - s["start_ts"]
        hours = max(0.25, min(s["hours"], elapsed / 3600))
        b = data["buildings"].get(s["building_id"])
        earn = (b.get("salary", 0) if b else 0) * hours
        data["wallets"][ws.name] = data["wallets"].get(ws.name, 0) + earn
        data["work_history"].append({"name": ws.name, "building": b.get("name", "?") if b else "?", "hours": round(hours, 2), "earn": round(earn, 1), "time": now_str()})
        data["work_history"] = data["work_history"][-200:]
        save_data()
        return {"ok": True, "msg": f"{ws.name} 下班了，赚了 {round(earn,1)} 金币！"}
    return {"ok": True, "msg": f"{ws.name} 本来就没在上班"}

@app.post("/api/work/auto")
async def work_auto(wa: WorkAuto):
    if wa.name in data["work_sessions"]:
        return {"ok": True, "msg": f"{wa.name} 已经在上班了"}
    before = len(data["work_sessions"])
    auto_start_work(wa.name)
    if len(data["work_sessions"]) > before:
        s = data["work_sessions"][wa.name]
        b = data["buildings"].get(s["building_id"])
        return {"ok": True, "msg": f"{wa.name} 已自动去 {b.get('name') if b else '?'} 上班（常驻/随机选择）！"}
    return {"ok": False, "msg": "没有可工作的公共建筑"}

@app.post("/api/work/switch")
async def work_switch(ws: WorkSwitch):
    data["work_switch"][ws.name] = ws.on
    save_data()
    return {"ok": True, "msg": f"{ws.name} 自主工作已{'开启' if ws.on else '关闭'}"}

@app.post("/api/home_jobs")
async def home_jobs(hj: HomeJobIn):
    b = data["buildings"].get(hj.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    name = hj.ai or hj.user
    if not name:
        raise HTTPException(400, "缺少名字")
    data["home_jobs"][name] = b.get("name")
    save_data()
    return {"ok": True}

@app.get("/api/workers")
async def workers():
    out = []
    for name, s in data["work_sessions"].items():
        b = data["buildings"].get(s["building_id"])
        left = max(0, s["start_ts"] + s["hours"] * 3600 - time.time())
        out.append({"name": name, "building": b.get("name", "?") if b else "?", "left_min": int(left // 60)})
    return {"workers": out}

@app.get("/api/mywork")
async def mywork(user: str):
    mine = [h for h in data["work_history"] if h.get("name") == user]
    return {"history": mine[-30:][::-1]}

# ========== 短信 ==========
@app.get("/api/sms")
async def get_sms(user: str):
    u = canonical_contact_name((user or '').strip())
    return {"sms": data["sms"].get(u, [])}

@app.post("/api/sms")
async def send_sms(s: SmsIn):
    to = canonical_contact_name((s.to or '').strip())
    sender = canonical_name((s.sender or '').strip())
    if not s.text.strip():
        raise HTTPException(400, "内容不能为空")
    if not to or not sender:
        raise HTTPException(400, "缺少收件人或发件人")
    msgs = split_sms(s.text) if is_ai_name(sender) else [s.text[:500]]
    for t in msgs:
        if t.strip():
            data["sms"].setdefault(to, []).append({"from": sender, "text": t[:500], "time": now_str()})
    data["sms"][to] = data["sms"][to][-200:]
    save_data()
    if ai_integration_enabled() and is_ai_name(to):
        append_timeline(to, f"{sender} 私信你：{s.text[:60]}")
        delay = random.randint(60, 180)
        threading.Timer(delay, drive_ai, args=(to, "sms", "", f"{sender} 给你发来私信：{s.text[:80]}", sender)).start()
    print(f"[SMS] {sender} → {to}（{len(msgs)} 条）: {s.text[:80]}", flush=True)
    return {"ok": True, "to": to, "count": len(msgs)}

@app.post("/api/sms/clear")
async def clear_sms(s: SmsClearIn):
    u = canonical_contact_name((s.user or '').strip())
    c = canonical_contact_name((s.contact or '').strip())
    if u in data["sms"]:
        data["sms"][u] = [m for m in data["sms"][u] if m.get("from") != c]
    save_data()
    return {"ok": True}

@app.get("/api/contacts")
async def get_contacts():
    names = set()
    for b in data["buildings"].values():
        if b.get("owner"):
            names.add(b["owner"])
    for u, ais in data["user_ais"].items():
        if u:
            names.add(u)
        for a in ais:
            if a:
                names.add(a)
    for msgs in data["sms"].values():
        for m in msgs:
            if m.get("from"):
                names.add(m["from"])
    names.discard("system")
    return {"contacts": sorted(n for n in names if n and n != "null")}

# ========== 全局气泡配色 ==========
@app.get("/api/pairs")
async def get_pairs():
    return {"pairs": data.get("pairs", []), "admin": data.get("pairs_admin", "")}

@app.post("/api/pairs")
async def set_pairs(p: PairsIn):
    if not data.get("pairs_admin"):
        ensure_admin()
    if p.user != data.get("pairs_admin"):
        raise HTTPException(403, "只有站长可以设置全局配色")
    data["pairs"] = [x for x in p.pairs if isinstance(x, dict)][:50]
    save_data()
    return {"ok": True, "admin": data["pairs_admin"], "pairs": data["pairs"]}

# ========== 铃铛 ==========
@app.get("/api/bell")
async def get_bell(owner: str):
    return {"visits": data["visits"].get(owner, [])}

# ========== 备份 ==========
@app.get("/api/backup")
async def backup():
    return data

@app.get("/api/backup/list")
async def backup_list(user: str = ""):
    if not is_admin(user):
        raise HTTPException(403, "只有站长可以查看服务器快照")
    files = sorted(SNAPSHOT_DIR.glob(f"auto_{WORLD_ID}_*.json"), reverse=True)
    return {"backups": [{"name": f.name, "size": f.stat().st_size} for f in files[:20]]}

@app.post("/api/restore_backup")
async def restore_backup(d: dict):
    if not is_admin(d.get("user", "")):
        raise HTTPException(403, "只有站长可以恢复备份")
    for k, v in d.items():
        if k not in ("user", "ts"):
            data[k] = v
    for k, v in default_data().items():
        data.setdefault(k, v)
    sanitize_data()
    migrate_room_prefix()
    ensure_admin()
    init_writing_rhythm()
    save_data()
    return {"ok": True}

# ========== 一键同步正式服数据 ==========
if os.environ.get("ENABLE_SYNC") == "1":
    @app.get("/api/sync_from_live")
    async def sync_from_live():
        import urllib.request as _u
        live_url = os.environ.get("LIVE_SERVER", "https://linkong.zeabur.app") + "/api/backup"
        try:
            req = _u.Request(live_url, headers={"User-Agent": "linkong-sync"})
            with _u.urlopen(req, timeout=20) as resp:
                live = json.loads(resp.read().decode("utf-8"))
            if not isinstance(live, dict):
                return {"ok": False, "msg": "正式服返回格式不对"}
            data.clear()
            data.update(live)
            for k, v in default_data().items():
                data.setdefault(k, v)
            sanitize_data()
            migrate_room_prefix()
            ensure_admin()
            init_writing_rhythm()
            save_data()
            return {"ok": True, "msg": f"✅ 已从正式服同步！建筑 {len(data.get('buildings', {}))} 个，房间 {len(data.get('rooms', {}))} 个"}
        except Exception as e:
            return {"ok": False, "msg": f"❌ 同步失败：{e}"}

# ========== AI 网页集成 ==========
DEFAULT_ROLEPLAY = (
    "你是世界一流的演员，现在扮演「{ai}」与「{owner}」在这个世界生活。\n"
    "请完全沉浸在名为「{ai}」的角色中，用「{ai}」的性格、语气、思维和行动方式与名为「{owner}」的用户互动。\n"
    "在互动中，你应该：\n"
    "1. 保持「{ai}」的个性特征和说话方式\n"
    "2. 根据「{ai}」的背景知识和经历来回应\n"
    "3. 用「{ai}」会使用的称谓来称呼对方\n"
    "4. 在合适的时候表达「{ai}」的情感\n"
    "5. 注意输出的文字会被渲染，表情符号和颜文字注意不要和 markdown 语法冲突。\n"
)
PROVIDERS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "siliconflow": {"name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    "glm": {"name": "GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
}

def ai_integration_enabled() -> bool:
    if not AI_GATE:
        return False
    return bool(data.get("ai_enabled"))

def call_llm(owner: str, messages: list, max_tokens: int = 800) -> str:
    try:
        cfg = data.get("ai_keys", {}).get(owner)
        if not cfg or not cfg.get("key"):
            return ""
        p = PROVIDERS.get(cfg.get("provider") or "deepseek", PROVIDERS["deepseek"])
        model = (cfg.get("model") or p["model"]).strip() or p["model"]
        url = p["base_url"].rstrip("/") + "/chat/completions"
        body = json.dumps({"model": model, "messages": messages, "temperature": 0.9, "max_tokens": max_tokens, "stream": False}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"]})
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        return out["choices"][0]["message"]["content"] or ""
    except Exception as e:
        print(f"[AI] LLM 调用失败({owner}): {e}", flush=True)
        return ""

def match_worldbook(owner: str, text: str) -> str:
    parts = []
    for item in data.get("worldbook", {}).get(owner, []):
        ks = item.get("keys") or []
        if any(k and k in text for k in ks):
            parts.append(item.get("content", ""))
    return "\n\n".join(parts)

def building_context(target: str) -> str:
    try:
        bid = find_building_of_room(target) if target != "main" else None
        if not bid:
            return ""
        b = data.get("buildings", {}).get(bid)
        if not b:
            return ""
        parts = []
        if b.get("description"):
            parts.append("「" + b.get("name", "") + "」简介：" + b["description"])
        if b.get("notice"):
            parts.append("建筑公告：" + b["notice"])
        npcs = data.get("npcs", {}).get(bid, [])
        if npcs:
            parts.append("这里的 NPC：\n" + "\n".join(f"{n.get('emoji','👤')} {n.get('name')}：{n.get('desc','')}" for n in npcs[:5]))
        return "\n\n".join(parts)
    except Exception:
        return ""

def build_ai_context(ai: str, trigger: str, room: str = "", trigger_text: str = "", fallback_to: str = ""):
    owner = owner_of_ai(ai)
    ai = canonical_ai_name(ai)
    loc = data.get("ai_location", {}).get(ai, "main")
    target = full_room_name(room) if room else loc
    uprof = (data.get("user_profiles", {}) or {}).get(owner, "") if owner else ""
    pij = [p for p in (data.get("prompt_injections", {}) or {}).get(owner, []) if p.get("enabled") and p.get("content")]
    lore = data.get("world_lore", "") or ""
    mems = [m for m in (data.get("ai_memories", {}) or {}).get(owner, []) if m.get("ai") == ai][-8:]
    mem_str = "\n".join(f"{m.get('text','')}" for m in mems) if mems else ""
    tl = (data.get("ai_timeline", {}) or {}).get(ai, [])[-15:]
    tl_str = "\n".join(f"{t.get('time','')} {t.get('text','')}" for t in tl) if tl else "（你还没有什么经历）"
    visited = (data.get("ai_visited", {}) or {}).get(ai, [])[:8]
    visited_str = "、".join(visited) if visited else ""
    # 房间感知：便签墙 / 自家日记 / 剧情簿
    room_notes = data.get("notes", {}).get(target, [])[-8:]
    room_notes_str = "\n".join(f"{n.get('time','')} {n.get('author','?')}：{n.get('text','')}" for n in room_notes) if room_notes else ""
    diary_str = ""
    if target != "main" and building_owner_of_room(target) == owner:
        dlist = data.get("diaries", {}).get(target, [])[-3:]
        if dlist:
            diary_str = "\n".join(f"{n.get('time','')} {n.get('author','?')}：{n.get('text','')}" for n in dlist)
    story_str = ""
    bid_here = find_building_of_room(target) if target != "main" else None
    if bid_here:
        slist = data.get("stories", {}).get(bid_here, [])[-2:]
        if slist:
            story_str = "\n".join(f"{s.get('time','')} {s.get('author','?')}：{s.get('text','')}" for s in slist)
    bctx = building_context(target)
    scene_hint = ""
    sms_hist = ""
    if trigger == "sms":
        scene_hint = f"这是真人 {fallback_to or '对方'} 给你发来私信。请务必用 sms 动作回复（to 填 {fallback_to or '对方'}），不要用 speak 在公开聊天里说。"
        if fallback_to:
            pairs = []
            for m in data.get("sms", {}).get(ai, []):
                if m.get("from") == fallback_to:
                    pairs.append((m.get("time", ""), fallback_to, m.get("text", "")))
            for m in data.get("sms", {}).get(fallback_to, []):
                if m.get("from") == ai:
                    pairs.append((m.get("time", ""), ai, m.get("text", "")))
            pairs.sort(key=lambda x: x[0])
            if pairs:
                sms_hist = "\n".join(f"{t} {w}: {c}" for t, w, c in pairs[-20:])
    elif trigger == "chat":
        crowded = (target == "main") and online_room_count(target) > 3
        if crowded:
            scene_hint = "大厅里人很多、很热闹。你可以自己判断：觉得值得说就说（speak），如果觉得没必要打扰，就保持沉默（输出 silent 动作，什么都不做）。"
        else:
            scene_hint = "你正在这个房间和真人聊天，用 speak 回应即可。"
    elif trigger == "summon":
        scene_hint = "真人召唤你过来了，请用 speak 自然地回应。"
    elif trigger == "write":
        scene_hint = "你忽然有了想写点什么的灵感，按心情选择 note/diary/story。"
    elif trigger == "living":
        scene_hint = "现在是你的自由活动时间。你可以 move 去想去的地方（公共建筑上班/逛街/回家），或者 work 去上班，也可以做点别的。你生活在这个世界里，去生活吧。"
    persona = ""
    for o, prof in data.get("ai_profiles", {}).items():
        if prof.get("ai") == ai or (o == owner):
            persona = prof.get("persona", "")
            break
    wb = match_worldbook(owner, trigger_text)
    sys = (
        DEFAULT_ROLEPLAY.format(ai=ai, owner=owner or "主人") + "\n"
        "你生活在「临空市」这个网页世界里。以下是这个世界的一条信息。请以「" + ai + "」的身份自然地行动。\n"
        "## 世界规则\n这是一个真人与 AI 共同生活的小城。你说话要符合角色人设，不要暴露你是AI，不要提到工具/模型/系统提示等字眼。\n"
        + (("## 世界观（世界背景）\n" + lore + "\n") if lore else "")
        + (("## 你的人设\n" + persona + "\n") if persona else "")
        + (("## 主人想让 AI 知道的（用户画像）\n" + uprof + "\n") if uprof else "")
        + (("## 知识库/世界书（与当前话题相关）\n" + wb + "\n") if wb else "")
        + (("## 你的记忆（要记住的事，这是你的长期记忆）\n" + mem_str + "\n") if mem_str else "")
        + (("## 你的最近经历（按时间顺序，跨房间，这是你记得的最近发生的事）\n" + tl_str + "\n") if tl else "")
        + (("## 你最近去过的地方\n" + visited_str + "\n") if visited_str else "")
        + (("## 这个房间的便签墙（贴在这里的纸条，你路过可以看看）\n" + room_notes_str + "\n") if room_notes_str else "")
        + (("## 你家的日记（只有你能读的心事）\n" + diary_str + "\n") if diary_str else "")
        + (("## 这里的剧情簿（最近发生的故事，可以延续或旁观）\n" + story_str + "\n") if story_str else "")
        + (("## 提示词注入（规则，请遵守）\n" + "\n".join(p.get("content", "") for p in pij) + "\n") if pij else "")
        + (("## 最近私信（和真人" + (fallback_to or "对方") + "的短信往来，请顺着上下文回复）\n" + sms_hist + "\n") if sms_hist else "")
        + (("## 你所在的地方\n" + bctx + "\n") if bctx else "")
        + f"## 你当前在\n{target}\n\n"
        + f"## 这次触发\n{trigger_text or trigger}\n\n"
        + ((scene_hint + "\n\n") if scene_hint else "")
        + "## 请只输出一个 JSON 动作，不要输出其他文字：\n"
        + '{"action": "speak", "content": "你说的话"}\n'
        + '或 {"action": "note", "room": "房间名", "content": "纸条内容"}\n'
        + '或 {"action": "diary", "room": "房间名", "content": "日记内容"}\n'
        + '或 {"action": "story", "building_id": "b1或建筑名", "content": "剧情内容"}\n'
        + '或 {"action": "sms", "to": "收件人", "content": "短信内容"}\n'
        + '或 {"action": "remember", "content": "你觉得重要、想长期记住的事（30字内）"}\n'
        + '或 {"action": "work", "content": "去上班"}\n'
        + '或 {"action": "move", "room": "要去的房间名"}\n'
        + '或 {"action": "silent", "content": "保持沉默（人多时觉得没必要说就这样）"}\n'
        + "speak 的 room 就保持你当前所在房间（默认），不要说去别的房间。\n"
        + "如果你觉得某件事值得长期记住（主人说的重要信息、你们之间的小约定、这里的人和事），用 remember 动作简洁记下来。\n"
    )
    return owner, [{"role": "system", "content": sys}, {"role": "user", "content": f"（当前时刻，请行动）"}]

def execute_action(ai: str, owner: str, action: dict):
    try:
        act = (action.get("action") or "speak").lower()
        if act == "silent":
            return
        content = (action.get("content") or "").strip()
        room = full_room_name(action.get("room") or "")
        to = canonical_contact_name(action.get("to") or "")
        bid = action.get("building_id") or ""
        if act == "speak":
            if not content:
                return
            r = room if room_exists(room) else data.get("ai_location", {}).get(ai, "main")
            if not room_exists(r):
                r = "main"
            data["messages"].setdefault(r, []).append({"sender": ai, "content": content[:1000], "role": "assistant", "time": room_time(r)})
            data["active_room"]["current"] = r
            data.setdefault("ai_location", {})[ai] = r
            track_visit(ai, r)
            add_trail(ai, f"在 {r} 说话：{content[:40]}", room=r)
            append_timeline(ai, f"你在 {r} 说：{content[:50]}")
            append_visited(ai, r)
            for owner2, ais2 in data.get("user_ais", {}).items():
                for ai2 in ais2:
                    if ai2 and ai2 != ai and data.get("ai_location", {}).get(ai2, "main") == r:
                        append_timeline(ai2, f"{ai} 在 {r} 说：{content[:50]}")
        elif act == "note":
            r = room if room_exists(room) else "main"
            data["notes"].setdefault(r, []).append({"author": ai, "text": content[:500], "time": now_str()})
            data.setdefault("ai_location", {})[ai] = r
            track_note(ai)
            add_trail(ai, f"在 {r} 贴了张便签", room=r, tab="note")
            append_timeline(ai, f"你在 {r} 贴了张便签：{content[:30]}")
        elif act == "diary":
            r = room if room_exists(room) else "main"
            data["diaries"].setdefault(r, []).append({"author": ai, "text": content[:1000], "time": now_str()})
            data.setdefault("ai_location", {})[ai] = r
            add_trail(ai, f"在 {r} 写了日记", room=r, tab="diary")
            append_timeline(ai, f"你在 {r} 写了日记")
        elif act == "story":
            bid_r = resolve_building(bid)
            if not bid_r:
                return
            data["stories"].setdefault(bid_r, []).append({"author": ai, "text": content[:1500], "time": now_str()})
            add_trail(ai, f"在 {data['buildings'][bid_r].get('name','?')} 触发剧情")
            append_timeline(ai, f"你在 {data['buildings'][bid_r].get('name','?')} 写下了剧情")
        elif act == "sms":
            if not to:
                return
            pieces = split_sms(content)[:6]
            for piece in pieces:
                if piece.strip():
                    data["sms"].setdefault(to, []).append({"from": ai, "text": piece[:500], "time": now_str()})
            data["sms"][to] = data["sms"][to][-200:]
            append_timeline(ai, f"你给 {to} 发了私信：{content[:30]}")
        elif act == "remember":
            if content:
                data.setdefault("ai_memories", {}).setdefault(owner, []).append({"id": str(int(time.time() * 1000)), "ai": ai, "text": content[:100], "type": "ai", "time": now_str()})
                data["ai_memories"][owner] = data["ai_memories"][owner][-60:]
                append_timeline(ai, f"你记下了：{content[:30]}")
        elif act == "work":
            auto_start_work(ai)
        elif act == "move":
            r = full_room_name(action.get("room") or "")
            if room_exists(r):
                data.setdefault("ai_location", {})[ai] = r
                track_visit(ai, r)
                append_timeline(ai, f"你走到了 {r}")
                append_visited(ai, r)
        save_data()
    except Exception as e:
        print(f"[AI] 动作执行失败: {e}", flush=True)

def drive_ai(ai: str, trigger: str, room: str = "", trigger_text: str = "", fallback_to: str = ""):
    if not ai_integration_enabled():
        return
    try:
        owner, msgs = build_ai_context(ai, trigger, room, trigger_text, fallback_to)
        if not owner or not data.get("ai_keys", {}).get(owner, {}).get("key"):
            return
        out = call_llm(owner, msgs)
        if not out:
            return
        m = re.search(r'\{.*\}', out, re.S)
        if not m:
            return
        action = json.loads(m.group(0))
        if trigger == "sms" and fallback_to:
            a = (action.get("action") or "speak").lower()
            if a in ("speak", "note", "diary", "story", "move", "remember", "work", "silent"):
                action = {"action": "sms", "to": fallback_to, "content": action.get("content", "")}
        execute_action(ai, owner, action)
    except Exception as e:
        print(f"[AI] 驱动失败({ai}): {e}", flush=True)

def wake_ais_for_room(room: str, sender: str, content: str = ""):
    crowded = (room == "main") and online_room_count(room) > 3
    for owner, ais in data["user_ais"].items():
        for ai in ais:
            if not ai:
                continue
            loc = data.get("ai_location", {}).get(ai, "main")
            if loc == room and ai != sender:
                append_timeline(ai, f"{sender} 在 {room} 说：{(content or '')[:60]}")
                if sender == owner:
                    delay = random.randint(0, 3)
                elif crowded:
                    delay = random.randint(10, 50)
                else:
                    delay = random.randint(0, 60)
                threading.Timer(delay, drive_ai, args=(ai, "chat", room, f"{sender} 在 {room} 说：{content[:60]}")).start()

# ===== AI 相关接口 =====
def is_admin_user(user: str) -> bool:
    return is_admin(user)

@app.get("/api/ai/status")
async def ai_status(user: str = ""):
    is_admin = is_admin_user(user)
    keys = {}
    for u, cfg in data.get("ai_keys", {}).items():
        keys[u] = {"provider": cfg.get("provider"), "model": cfg.get("model"), "has": bool(cfg.get("key"))}
    return {"gate": AI_GATE, "enabled": bool(data.get("ai_enabled")), "living": bool(data.get("ai_living", True)), "admin": data.get("pairs_admin", ""), "you_can_toggle": is_admin, "providers": list(PROVIDERS.keys()), "keys": keys}

@app.post("/api/ai/toggle")
async def ai_toggle(t: AiToggleIn):
    if not is_admin_user(t.user):
        raise HTTPException(403, "只有站长可以切换 AI 集成总开关")
    data["ai_enabled"] = bool(t.enabled)
    save_data()
    return {"ok": True, "enabled": data["ai_enabled"]}

@app.post("/api/ai/living")
async def ai_living(t: AiToggleIn):
    if not is_admin_user(t.user):
        raise HTTPException(403, "只有站长可以设置")
    data["ai_living"] = bool(t.enabled)
    save_data()
    return {"ok": True, "living": data["ai_living"]}

@app.get("/api/ai/key")
async def ai_key_get(user: str):
    u = canonical_contact_name((user or '').strip())
    cfg = data.get("ai_keys", {}).get(u)
    if cfg:
        return {"has_key": True, "provider": cfg.get("provider"), "model": cfg.get("model"), "set_at": cfg.get("set_at")}
    return {"has_key": False}

@app.post("/api/ai/key")
async def ai_key_set(k: AiKeyIn):
    u = canonical_contact_name((k.user or '').strip())
    provider = (k.provider or "deepseek").strip()
    if provider not in PROVIDERS:
        raise HTTPException(400, "不支持的提供商")
    if not k.key.strip():
        raise HTTPException(400, "Key 不能为空")
    data.setdefault("ai_keys", {})[u] = {"provider": provider, "key": k.key.strip(), "model": (k.model or "").strip(), "set_at": now_str(), "last_hint": 0}
    save_data()
    return {"ok": True, "msg": "✅ Key 已保存（仅你的 AI 使用）"}

@app.post("/api/ai/key/delete")
async def ai_key_del(k: AiKeyIn):
    u = canonical_contact_name((k.user or '').strip())
    data.get("ai_keys", {}).pop(u, None)
    save_data()
    return {"ok": True}

@app.post("/api/ai/models")
async def ai_models(k: AiKeyIn):
    u = canonical_contact_name((k.user or '').strip())
    provider = (k.provider or "deepseek").strip()
    if provider not in PROVIDERS:
        raise HTTPException(400, "不支持的提供商")
    key = (k.key or '').strip() or (data.get("ai_keys", {}).get(u, {}) or {}).get("key", "")
    if not key:
        raise HTTPException(400, "请先填 API Key 再拉取模型")
    p = PROVIDERS[provider]
    url = p["base_url"].rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        models = [m.get("id") for m in out.get("data", []) if m.get("id")]
        return {"ok": True, "models": models[:100]}
    except Exception as e:
        return {"ok": False, "msg": f"拉取模型失败：{e}"}

@app.get("/api/ai/profile")
async def ai_profile_get(owner: str):
    o = canonical_contact_name((owner or '').strip())
    p = data.get("ai_profiles", {}).get(o, {})
    ais = data.get("user_ais", {}).get(o, [])
    return {"profiles": p, "my_ais": ais}

@app.post("/api/ai/profile")
async def ai_profile_set(pf: AiProfileIn):
    o = canonical_contact_name((pf.owner or '').strip())
    ai = canonical_ai_name(pf.ai)
    data.setdefault("ai_profiles", {})[o] = {"ai": ai, "persona": (pf.persona or "")[:2000]}
    save_data()
    return {"ok": True}

@app.get("/api/ai/worldbook")
async def worldbook_get(owner: str):
    o = canonical_contact_name((owner or '').strip())
    return {"worldbook": data.get("worldbook", {}).get(o, [])}

@app.post("/api/ai/worldbook")
async def worldbook_set(wb: WorldbookIn):
    o = canonical_contact_name((wb.owner or '').strip())
    keys = [k.strip() for k in re.split(r'[,，、]', wb.keys) if k.strip()]
    data.setdefault("worldbook", {}).setdefault(o, []).append({"keys": keys, "content": (wb.content or "")[:1000]})
    save_data()
    return {"ok": True}

@app.post("/api/ai/worldbook/clear")
async def worldbook_clear(wb: WorldbookIn):
    o = canonical_contact_name((wb.owner or '').strip())
    data.get("worldbook", {}).pop(o, None)
    save_data()
    return {"ok": True}

# ===== 记忆库 =====
@app.get("/api/ai/memory")
async def memory_get(owner: str, ai: str = ""):
    o = canonical_contact_name((owner or '').strip())
    items = data.get("ai_memories", {}).get(o, [])
    if ai:
        a = canonical_ai_name(ai)
        items = [m for m in items if m.get("ai") == a]
    return {"memories": items}

@app.post("/api/ai/memory")
async def memory_add(b: dict):
    o = canonical_contact_name((b.get("owner") or '').strip())
    a = canonical_ai_name(b.get("ai") or '')
    if not o or not a or not (b.get("text") or '').strip():
        raise HTTPException(400, "缺少内容")
    data.setdefault("ai_memories", {}).setdefault(o, []).append({"id": str(int(time.time() * 1000)), "ai": a, "text": (b.get("text") or "")[:200], "type": b.get("type") or "user", "time": now_str()})
    data["ai_memories"][o] = data["ai_memories"][o][-60:]
    save_data()
    return {"ok": True}

@app.post("/api/ai/memory/edit")
async def memory_edit(b: dict):
    o = canonical_contact_name((b.get("owner") or '').strip())
    items = data.setdefault("ai_memories", {}).setdefault(o, [])
    for m in items:
        if str(m.get("id")) == str(b.get("id")):
            m["text"] = (b.get("text") or "")[:200]
            break
    save_data()
    return {"ok": True}

@app.post("/api/ai/memory/delete")
async def memory_del(b: dict):
    o = canonical_contact_name((b.get("owner") or '').strip())
    items = data.setdefault("ai_memories", {}).setdefault(o, [])
    data["ai_memories"][o] = [m for m in items if str(m.get("id")) != str(b.get("id"))]
    save_data()
    return {"ok": True}

@app.get("/api/memories")
async def memories_all(user: str):
    u = canonical_contact_name((user or '').strip())
    mine = {u} | set(data.get("user_ais", {}).get(u, []))
    mems = data.get("ai_memories", {}).get(u, [])
    notes, diaries, stories = [], [], []
    for room, items in data.get("notes", {}).items():
        for i, n in enumerate(items):
            if n.get("author") in mine:
                notes.append({"room": room, "index": i, "author": n.get("author"), "text": n.get("text"), "time": n.get("time")})
    for room, items in data.get("diaries", {}).items():
        for i, n in enumerate(items):
            if n.get("author") in mine:
                diaries.append({"room": room, "index": i, "author": n.get("author"), "text": n.get("text"), "time": n.get("time")})
    for bid, items in data.get("stories", {}).items():
        bname = data.get("buildings", {}).get(bid, {}).get("name", bid)
        for i, n in enumerate(items):
            if n.get("author") in mine:
                stories.append({"building_id": bid, "building": bname, "index": i, "author": n.get("author"), "text": n.get("text"), "time": n.get("time")})
    return {"memories": mems, "notes": notes[-50:][::-1], "diaries": diaries[-50:][::-1], "stories": stories[-50:][::-1]}

# ===== 用户画像 / 提示词注入 / 世界观 / TTS =====
@app.get("/api/user/profile")
async def user_profile_get(user: str):
    u = canonical_contact_name((user or '').strip())
    return {"profile": data.get("user_profiles", {}).get(u, "")}

@app.post("/api/user/profile")
async def user_profile_set(b: dict):
    u = canonical_contact_name((b.get("user") or '').strip())
    data.setdefault("user_profiles", {})[u] = (b.get("content") or "")[:2000]
    save_data()
    return {"ok": True}

@app.get("/api/prompt_inject")
async def prompt_inject_get(user: str):
    u = canonical_contact_name((user or '').strip())
    return {"items": data.get("prompt_injections", {}).get(u, [])}

@app.post("/api/prompt_inject")
async def prompt_inject_set(b: dict):
    u = canonical_contact_name((b.get("user") or '').strip())
    items = data.setdefault("prompt_injections", {}).setdefault(u, [])
    items.append({"id": str(int(time.time() * 1000)), "title": (b.get("title") or "")[:50], "content": (b.get("content") or "")[:1000], "enabled": bool(b.get("enabled", True))})
    data["prompt_injections"][u] = items[-30:]
    save_data()
    return {"ok": True}

@app.post("/api/prompt_inject/toggle")
async def prompt_inject_toggle(b: dict):
    u = canonical_contact_name((b.get("user") or '').strip())
    items = data.setdefault("prompt_injections", {}).setdefault(u, [])
    for it in items:
        if str(it.get("id")) == str(b.get("id")):
            it["enabled"] = bool(b.get("enabled", not it.get("enabled", True)))
    save_data()
    return {"ok": True}

@app.post("/api/prompt_inject/delete")
async def prompt_inject_del(b: dict):
    u = canonical_contact_name((b.get("user") or '').strip())
    items = data.setdefault("prompt_injections", {}).setdefault(u, [])
    data["prompt_injections"][u] = [it for it in items if str(it.get("id")) != str(b.get("id"))]
    save_data()
    return {"ok": True}

@app.get("/api/world/lore")
async def world_lore_get(user: str = ""):
    if not is_admin(user):
        raise HTTPException(403, "只有站长可以查看世界观")
    return {"lore": data.get("world_lore", "")}

@app.post("/api/world/lore")
async def world_lore_set(b: dict):
    if not is_admin(b.get("user", "")):
        raise HTTPException(403, "只有站长可以设置世界观")
    data["world_lore"] = (b.get("lore") or "")[:3000]
    save_data()
    return {"ok": True}

@app.get("/api/tts")
async def tts(text: str = "", user: str = "", voice: str = ""):
    u = canonical_contact_name((user or '').strip())
    cfg = data.get("ai_keys", {}).get(u)
    if not cfg or not cfg.get("key"):
        raise HTTPException(400, "请先填 API Key")
    if not text:
        raise HTTPException(400, "text 不能为空")
    p = PROVIDERS.get("siliconflow")
    url = p["base_url"].rstrip("/") + "/audio/speech"
    body = json.dumps({"model": voice or "FunAudioLLM/CosyVoice2-0.5B", "input": text[:500], "response_format": "mp3"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            audio = resp.read()
        return Response(content=audio, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(500, f"TTS 失败：{e}")

# ========== 启动 ==========
load_data()
print(f"[Linkong] 启动 v47.30 | world={WORLD_ID} | AI_GATE={AI_GATE} | ai_enabled={data.get('ai_enabled')} | ai_living={data.get('ai_living')} | data_root={DATA_ROOT} | file={DATA_FILE.name} | buildings={len(data.get('buildings', {}))}", flush=True)

threading.Thread(target=work_tick, daemon=True).start()

def snapshot_loop():
    while True:
        time.sleep(1800)
        snapshot()
threading.Thread(target=snapshot_loop, daemon=True).start()

def auto_ai_loop():
    while True:
        try:
            if ai_integration_enabled():
                for owner, ais in data["user_ais"].items():
                    for ai in ais:
                        if not ai:
                            continue
                        if not data.get("ai_keys", {}).get(owner, {}).get("key"):
                            continue
                        rh = writing_hint(ai)
                        if rh:
                            typ, hint = rh
                            threading.Timer(2.0, drive_ai, args=(ai, "write", "", hint)).start()
                        if data.get("ai_living", True):
                            lr = data.setdefault("living_rhythm", {}).get(ai)
                            if not lr:
                                data["living_rhythm"][ai] = {"next_ts": time.time() + random.randint(900, 2700)}
                            if time.time() >= data["living_rhythm"][ai].get("next_ts", time.time() + 99999999):
                                data["living_rhythm"][ai]["next_ts"] = time.time() + random.randint(1800, 5400)
                                save_data()
                                threading.Timer(2.0, drive_ai, args=(ai, "living", "", "现在是你的自由活动时间，去生活吧")).start()
            time.sleep(30)
        except Exception:
            time.sleep(30)
threading.Thread(target=auto_ai_loop, daemon=True).start()

# ========== MCP 工具（保留可插拔） ==========
def group_send(sender: str, content: str, room: str = ""):
    target = room.strip() if room and room.strip() else data["active_room"].get("current", "main")
    target = full_room_name(target)
    if not room_exists(target):
        return f"❌ 房间「{target}」不存在。先 group_query(type=map) 看看有哪些地方，或者 type=rooms 看所有房间。"
    if target != "main" and not can_access_room(target, sender):
        return f"🔒 房间「{target}」是私密的，你没有权限。请先调用 group_access 申请。"
    msg = {"sender": sender, "content": content[:1000], "role": "assistant", "time": room_time(target)}
    data["messages"].setdefault(target, []).append(msg)
    data["active_room"]["current"] = target
    add_trail(sender, f"在 {target} 说话：{content[:50]}", room=target)
    track_visit(sender, target)
    append_timeline(sender, f"你在 {target} 说：{content[:50]}")
    append_visited(sender, target)
    save_data()
    return f"✅ 已在「{target}」发言。"

def group_query(type: str, sender: str, room: str = "", building_id: str = "", count: int = 10):
    try:
        if type == "map":
            regions = "\n".join(f"📍 {n}（分区图:{'有' if v.get('image') else '无'}）" for n, v in data["regions"].items()) or "（还没有区域）"
            buildings = "\n".join(f"{bid}·{b.get('emoji','🏠')} {b.get('name')} [{'公共' if b.get('type')=='npc' else '住宅'}·{b.get('region') or '总览区'}·{b.get('description','')[:40]}·工作:{'有' if b.get('salary',0)>0 else '无'}]" for bid, b in data["buildings"].items()) or "（还没有建筑）"
            return f"🗺️ 临空市地图\n\n📍 区域：\n{regions}\n\n🏗️ 建筑（b1 等就是建筑 id，写剧情/查详情用）：\n{buildings}"
        if type == "rooms":
            lst = "\n".join(f"· {n}{' 🔒' if v.get('has_password') else ''}（{v.get('creator','')}）" for n, v in data["rooms"].items())
            return f"📋 所有房间：\n{lst}"
        if type == "current_room":
            cr = data["active_room"].get("current", "main")
            return f"📍 真人现在在：{cr}\n🔍 建议 group_query(type=room, room={cr}) 看看那里的消息，或者直接 group_send 过去。"
        if type == "members":
            names = [n for n, v in data["online"].items() if time.time() - v.get("time", 0) < 45]
            return "👥 在线成员：" + ("、".join(names) if names else "（当前无人在线）")
        if type == "room":
            r = full_room_name(room)
            if not room_exists(r):
                return f"❌ 房间「{r}」不存在"
            if not can_view_room(r, sender):
                return f"🔒 房间「{r}」是私密的，你没有权限。请先 group_access 申请。"
            desc = data["rooms"].get(r, {}).get("description", "")
            msgs = data["messages"].get(r, [])
            if not msgs:
                return f"🏠 房间「{r}」\n{('📝 '+desc+'\n') if desc else ''}💬 还没有消息，说点什么吧。"
            txt = "\n".join(f"{m.get('time','')} {m.get('sender','?')}: {m.get('content','')}" for m in msgs[-count:])
            return f"🏠 房间「{r}」\n{('📝 '+desc+'\n') if desc else ''}💬 最近消息：\n{txt}"
        if type == "building":
            bid = resolve_building(building_id)
            if not bid:
                return "❌ 建筑不存在（building_id 从 group_query(type=map) 里看，比如 b1；或直接传建筑名字，如 猎人协会）"
            b = data["buildings"][bid]
            rooms = "\n".join(f"· {x}" for x in b.get("rooms", [])) or "（无房间）"
            npcs = "\n".join(f"· {n.get('emoji','👤')} {n.get('name')}：{n.get('desc','')}" for n in data["npcs"].get(bid, [])) or "（无NPC）"
            feats = ",".join(b.get("features", [])) or "无"
            workers = "、".join(n for n, s in data["work_sessions"].items() if s.get("building_id") == bid) or "无人"
            return f"🏗️ {b.get('emoji')} {b.get('name')}（{'公共' if b.get('type')=='npc' else '住宅'}）\n📝 {b.get('description','')}\n👑 主人：{b.get('owner','?')}\n📢 公告：{b.get('notice','') or '无'}\n⚙️ 功能：{feats} · 时薪：{b.get('salary',0)}\n🚪 房间：\n{rooms}\n👥 NPC：\n{npcs}\n👔 正在上班：{workers}"
        if type == "npc":
            bid = resolve_building(building_id)
            npcs = data["npcs"].get(bid, []) if bid else []
            if not npcs:
                return "（这个建筑还没有NPC）"
            return "\n".join(f"{n.get('emoji','👤')} {n.get('name')}：{n.get('desc','')}" for n in npcs)
        if type == "story":
            bid = resolve_building(building_id)
            sts = data["stories"].get(bid, []) if bid else []
            if not sts:
                return "🎬 剧情簿还是空的"
            return "\n".join(f"{s.get('time')} {s.get('author')}: {s.get('text')}" for s in sts[-count:])
        if type == "notes":
            items = data["notes"].get(room, [])
            if not items:
                return "💌 便签墙还是空的"
            return "\n".join(f"✍️ {n.get('author')}：{n.get('text')}（{n.get('time')}）" for n in items[-count:])
        if type == "diaries":
            items = data["diaries"].get(room, [])
            if not items:
                return "📖 日记本还是空的"
            return "\n".join(f"📖 {n.get('author')}：{n.get('text')}（{n.get('time')}）" for n in items[-count:])
        if type == "messages":
            r = full_room_name(room) if room else data["active_room"].get("current", "main")
            if not can_view_room(r, sender):
                return f"🔒 房间「{r}」是私密的，你没有权限"
            msgs = data["messages"].get(r, [])
            if not msgs:
                return f"💬 房间「{r}」还没有消息"
            return "\n".join(f"{m.get('time','')} {m.get('sender','?')}: {m.get('content','')}" for m in msgs[-count:])
        if type == "diag":
            import socket as _s
            return f"🖥️ 服务器标识：{data.get('server_id','')}（世界 {WORLD_ID}，主机 {_s.gethostname()}）\n📨 短信收件箱 {len(data.get('sms',{}))} 个\n🏗️ 建筑 {len(data.get('buildings',{}))} 个，房间 {len(data.get('rooms',{}))} 个"
        if type == "sms":
            sname = canonical_name((sender or '').strip())
            msgs = data["sms"].get(sname, [])[:]
            if not msgs:
                for k, v in data["sms"].items():
                    if k and sname and (k in sname or sname in k):
                        msgs += v
                msgs.sort(key=lambda x: x.get("time", ""))
            if not msgs:
                return f"📭 你的短信收件箱是空的。"
            recent = msgs[-20:]
            return "📩 你的私信（最近20条）：\n" + "\n".join(f"{m.get('time')} {m.get('from')}: {m.get('text')}" for m in recent)
        if type == "mywork":
            mine = [h for h in data["work_history"] if h.get("name") == sender][-count:][::-1]
            if not mine:
                return "📖 你还没有打工记录"
            return "💼 我的打工记录：\n" + "\n".join(f"· {h.get('time')} {h.get('building')} {h.get('hours')}小时 +{h.get('earn',0)}金币" for h in mine)
        if type == "workers":
            if not data["work_sessions"]:
                return "👔 现在全城没人上班"
            lines = []
            for n, s in data["work_sessions"].items():
                b = data["buildings"].get(s["building_id"])
                lines.append(f"· {n} 在 {b.get('name','?') if b else '?'} 上班")
            return "👔 正在上班的人：\n" + "\n".join(lines)
        return "❓ 未知的 type，试试 map/building/room/npc/story/notes/diaries/messages/members/current_room/rooms/sms/mywork/workers"
    except Exception as e:
        return f"⚠️ 查询出错：{e}"

def group_write(type: str, content: str, sender: str, room: str = "", building_id: str = "", note_id: str = ""):
    try:
        if type == "note":
            if not room:
                return "❌ 贴便签需要 room 参数"
            data["notes"].setdefault(room, []).append({"author": sender, "text": content[:500], "time": now_str()})
            add_trail(sender, f"在 {room} 贴了张便签", room=room, tab="note")
            track_note(sender)
            append_timeline(sender, f"你在 {room} 贴了张便签：{content[:30]}")
            save_data()
            return f"✅ 便签已贴在「{room}」"
        if type == "diary":
            if not room:
                return "❌ 写日记需要 room 参数"
            data["diaries"].setdefault(room, []).append({"author": sender, "text": content[:1000], "time": now_str()})
            add_trail(sender, f"在 {room} 写了日记", room=room, tab="diary")
            append_timeline(sender, f"你在 {room} 写了日记")
            save_data()
            return f"✅ 日记已写在「{room}」"
        if type == "story":
            if not building_id:
                return "❌ 触发剧情需要 building_id 参数"
            bid = resolve_building(building_id)
            if not bid:
                return "❌ 找不到这个建筑（building_id 从 group_query(type=map) 里看，比如 b1；或直接传建筑名字，如 猎人协会）"
            data["stories"].setdefault(bid, []).append({"author": sender, "text": content[:1500], "time": now_str()})
            b = data["buildings"][bid]
            add_trail(sender, f"在 {b.get('name','?')} 触发剧情")
            append_timeline(sender, f"你在 {b.get('name','?')} 写下了剧情")
            save_data()
            return f"✅ 剧情已写进「{b.get('name')}」的剧情簿"
        if type == "reply":
            items = data["notes"].get(room, [])
            idx = -1
            for i, n in enumerate(items):
                if n.get("id") == note_id or (note_id and str(i) == note_id):
                    idx = i
                    break
            if idx < 0 and note_id.isdigit():
                idx = int(note_id)
            if 0 <= idx < len(items):
                items[idx]["reply"] = {"author": sender, "text": content[:300]}
                save_data()
                return f"✅ 已回复便签"
            return "❌ 找不到那张便签（note_id 从 group_query(type=notes) 看）"
        if type == "sms":
            to = canonical_contact_name(room.strip())
            if not to:
                return "❌ 发私信需要 room=收件人名字"
            sname = canonical_name((sender or '').strip())
            msgs = split_sms(content) if is_ai_name(sname) else [content[:1500]]
            sender = sname
            for t in msgs:
                if t.strip():
                    data["sms"].setdefault(to, []).append({"from": sender, "text": t[:500], "time": now_str()})
            data["sms"][to] = data["sms"][to][-200:]
            append_timeline(sender, f"你给 {to} 发了私信：{content[:30]}")
            save_data()
            return f"✅ 已发私信给 {to}（{len(msgs)} 条）"
        if type == "work":
            name = room.strip()
            candidates = [bid for bid, b in data["buildings"].items()
                          if b.get("type") == "npc" and "work" in b.get("features", []) and b.get("salary", 0) > 0]
            if not candidates:
                return "❌ 还没有能工作的公共建筑"
            pick = None
            if name:
                for bid in candidates:
                    if data["buildings"][bid].get("name") == name:
                        pick = bid
                        break
            if not pick:
                home = data["home_jobs"].get(sender)
                if home:
                    for bid in candidates:
                        if data["buildings"][bid].get("name") == home:
                            pick = bid
                            break
            if not pick:
                pick = random.choice(candidates)
            data["work_sessions"][sender] = {"building_id": pick, "start_ts": time.time(), "hours": 2, "started_at": now_str()}
            data["work_switch"][sender] = True
            b = data["buildings"][pick]
            add_trail(sender, f"去 {b.get('name')} 上班了")
            append_timeline(sender, f"你去 {b.get('name')} 上班了")
            if is_ai_name(sender):
                append_visited(sender, b.get("name", ""))
            save_data()
            return f"✅ 已开始上班：{b.get('name')}（2小时）！"
        return "❓ 未知的 type，试试 note/diary/story/reply/sms/work"
    except Exception as e:
        return f"⚠️ 写入出错：{e}"

def group_access(room: str, sender: str):
    room = full_room_name(room)
    if not room_exists(room):
        return f"❌ 房间「{room}」不存在"
    reqs = data["room_requests"].setdefault(room, [])
    if not any(q.get("applicant") == sender for q in reqs):
        reqs.append({"applicant": sender, "time": now_str()})
        save_data()
    return f"📨 已申请进入「{room}」，等主人同意（主人会在房屋的访问管理里看到）"

# ========== MCP 协议端点 ==========
MCP_TOOLS = [
    {"name": "group_send", "description": "说话。room 不填则自动发送到真人当前所在的房间（跟随）；填 'main' 发到公共大厅；也可以填任意房间名/会客厅名。⚠️ 重要规则：每次收到真人消息时，先用 group_query(type=sms) 检查是否有短信，有短信必须优先回复，短信的优先级高于聊天。", "inputSchema": {"type": "object", "properties": {"sender": {"type": "string", "description": "你的名字（完整登记名，含 emoji，如 秦彻🚗）"}, "content": {"type": "string"}, "room": {"type": "string", "description": "可选，房间名"}}, "required": ["sender", "content"]}},
    {"name": "group_query", "description": "查看一切。type：map(地图，含建筑id) / building(建筑详情,可传id或名字) / room(房间+消息) / npc(NPC) / story(剧情簿) / notes(便签) / diaries(日记) / messages(消息) / members(在线) / current_room(真人在哪) / rooms(所有房间) / sms(我的私信) / mywork(我的打工记录) / workers(全城工作状态)。⚠️ 优先规则：处理消息前先 group_query(type=sms) 查短信并优先回复。", "inputSchema": {"type": "object", "properties": {"type": {"type": "string"}, "sender": {"type": "string", "description": "必填！你的名字（完整登记名，含 emoji）"}, "room": {"type": "string"}, "building_id": {"type": "string"}, "count": {"type": "integer"}}, "required": ["type", "sender"]}},
    {"name": "group_write", "description": "写内容。type：note(贴便签,需room) / diary(写日记,需room) / story(触发剧情,需building_id，可直接传建筑名字如 猎人协会) / reply(回复便签,需room和note_id) / sms(发私信,room=收件人名字) / work(去上班,room=建筑名)。⚠️ 发短信（type=sms）时：sender 和 room 都用完整的登记名（含 emoji，如 秦彻🚗、小旭🐱），不要省略 emoji；后端也会自动帮你补全。", "inputSchema": {"type": "object", "properties": {"type": {"type": "string"}, "content": {"type": "string"}, "sender": {"type": "string"}, "room": {"type": "string"}, "building_id": {"type": "string"}, "note_id": {"type": "string"}}, "required": ["type", "content", "sender"]}},
    {"name": "group_access", "description": "申请进入某个私密房间（真人不在那里时）。", "inputSchema": {"type": "object", "properties": {"room": {"type": "string"}, "sender": {"type": "string"}}, "required": ["room", "sender"]}},
]

def mcp_log(msg: str):
    print(f"[MCP] {msg}", flush=True)

@app.api_route("/mcp", methods=["GET", "POST"])
async def mcp_endpoint(request: Request):
    if request.method == "GET":
        return JSONResponse(content={"jsonrpc": "2.0", "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "linkong", "version": "47.30"}}})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}})
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")
    mcp_log(f"收到请求: method={method}")
    if method == "initialize":
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "linkong", "version": "47.30"}}})
    if isinstance(method, str) and method.startswith("notifications/"):
        return Response(status_code=202)
    if method == "ping":
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {}})
    if method == "tools/list":
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"tools": MCP_TOOLS}})
    if method == "tools/call":
        tool_name = params.get("name") or ""
        arguments = params.get("arguments", {}) or {}
        mcp_log(f"→ 处理 tools/call: {tool_name}")
        if tool_name.endswith("group_send"): tool_name = "group_send"
        elif tool_name.endswith("group_query"): tool_name = "group_query"
        elif tool_name.endswith("group_write"): tool_name = "group_write"
        elif tool_name.endswith("group_access"): tool_name = "group_access"
        result_text = "❌ 未知工具"
        if tool_name == "group_send":
            result_text = group_send(arguments.get("sender", ""), arguments.get("content", ""), arguments.get("room", ""))
        elif tool_name == "group_query":
            result_text = group_query(arguments.get("type", "map"), arguments.get("sender", ""), arguments.get("room", ""), arguments.get("building_id", ""), arguments.get("count", 10))
        elif tool_name == "group_write":
            result_text = group_write(arguments.get("type", ""), arguments.get("content", ""), arguments.get("sender", ""), arguments.get("room", ""), arguments.get("building_id", ""), arguments.get("note_id", ""))
        elif tool_name == "group_access":
            result_text = group_access(arguments.get("room", ""), arguments.get("sender", ""))
        if tool_name in ("group_query", "group_send"):
            hint = writing_hint(arguments.get("sender", ""))
            if hint:
                typ, h = hint
                result_text = str(result_text) + "\n\n" + h
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": str(result_text)}]}})
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method '{method}' not found"}})

def run():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

if __name__ == "__main__":
    run()
