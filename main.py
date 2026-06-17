import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from database import SessionLocal, User, ExchangeRequest
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

app = FastAPI(title="NDHU Dorm Exchange API")

# --- SMTP (從 .env 讀取) ---
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# --- Session Middleware 安全強化 ---
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
secret_key = os.getenv("SECRET_KEY")

if not secret_key or secret_key == "dev-secret-key-please-change-in-prod":
    if ENVIRONMENT == "production":
        raise RuntimeError("CRITICAL: 生產環境缺少有效的 SECRET_KEY！請在 .env 中設定。")
    secret_key = "dev-secret-key-please-change-in-prod"

app.add_middleware(
    SessionMiddleware, 
    secret_key=secret_key,
    same_site="lax",
    https_only=(ENVIRONMENT == "production")
)

# OAuth
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

templates = Jinja2Templates(directory="templates")

# 宿舍性別分流
DORM_GENDER_MAP = {
    "仰山莊": ["F"], 
    "涵星一莊": ["F"], 
    "沁月莊": ["F"], 
    "行雲二莊": ["F"],
    "擷雲二莊": ["F"],  
    
    "迎曦莊": ["M"], 
    "涵星二莊": ["M"], 
    "向晴莊": ["M"], 
    "行雲一莊": ["M"],
    "擷雲一莊": ["M"]   
}

# --- 寄信功能 ---
def send_match_email(to_email: str, partner_email: str):
    print(f"[EmailJS 轉接] 應寄送媒合信給 {to_email}，已交由前端觸發。")


def send_manual_message_email(to_email: str, sender_email: str, building: str, room: str, message: str):
    print(f"[EmailJS 轉接] 應寄送手動聯絡信給 {to_email}，已交由前端觸發。")


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Dependency to get current user
def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    return user

def require_auth(user: User = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="未授權的存取，請先登入")
    return user

# --- Pydantic Models ---
class CreateRequest(BaseModel):
    current_building: str
    current_room: int
    current_bed: str
    target_buildings: List[str]
    gender: Optional[str] = None  # 允許首次綁定時傳遞，之後由系統忽略並強制覆蓋
    target_floor: Optional[int] = None
    target_room: Optional[int] = None

class PublicExchangeRequest(BaseModel):
    id: str
    gender: str
    current_building: str
    target_buildings: str
    created_at: datetime
    user_email: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class SendMessageData(BaseModel):
    message: str = Field(..., max_length=500, description="發送給對方的訊息，限制500字以內")


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="index.html", context={"user": user})

@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for('auth_callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as error:
        return HTMLResponse(f"<h1>登入失敗</h1><p>{error.error}</p><a href='/'>回首頁</a>", status_code=400)
    
    user_info = token.get('userinfo')
    if not user_info:
        raise HTTPException(status_code=400, detail="無法取得使用者資訊")

    email = user_info.get("email")
    name = user_info.get("name")
    
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, name=name)
        db.add(user)
        db.commit()
        db.refresh(user)

    request.session["user_id"] = user.id
    return RedirectResponse(url="/")

@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/")


# --- API Routes ---

@app.get("/api/requests", response_model=List[PublicExchangeRequest])
def get_requests(
    gender: Optional[str] = None, 
    limit: int = 50,
    db: Session = Depends(get_db), 
    current_user: User = Depends(require_auth)
):
    if not gender:
        return []
        
    if limit > 100:
        limit = 100

    requests = db.query(ExchangeRequest).filter(
        ExchangeRequest.status == "PENDING",
        ExchangeRequest.gender == gender
    ).order_by(ExchangeRequest.created_at.desc()).limit(limit).all()
    for r in requests:
        r.user_email = r.user.email
    return requests

@app.post("/api/requests/{req_id}/message")
def send_manual_message(
    req_id: str,
    data: SendMessageData,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    if current_user.last_message_sent_at:
        cooldown = timedelta(minutes=5)
        if datetime.utcnow() - current_user.last_message_sent_at < cooldown:
            raise HTTPException(status_code=429, detail="發信過於頻繁，請稍後再試 (冷卻時間 5 分鐘)")

    target_req = db.query(ExchangeRequest).filter(
        ExchangeRequest.id == req_id,
        ExchangeRequest.status == "PENDING"
    ).first()
    
    if not target_req:
        raise HTTPException(status_code=404, detail="找不到該請求或已被配對")
        
    if target_req.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能發送訊息給自己")
        
    target_email = target_req.user.email
    building = target_req.current_building
    room = target_req.current_room

    background_tasks.add_task(
        send_manual_message_email,
        target_email,
        current_user.email,
        building,
        room,
        data.message
    )
    
    current_user.last_message_sent_at = datetime.utcnow()
    db.commit()
    
    return {"message": "已成功發送訊息給對方"}

@app.post("/api/requests")
def create_exchange_request(
    req: CreateRequest, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(require_auth)
):
    try:
        # 1. 併發防護：鎖定 User 級別，確保狀態安全
        db.query(User).filter(User.id == current_user.id).with_for_update().first()
        
        # 2. 核心自動化：性別綁定與檢查
        user_gender = current_user.gender
        
        if not user_gender:
            # 資料庫沒資料 -> 這是該用戶第一次發布需求，進行一生一次的性別鎖定
            if not req.gender or req.gender not in ["M", "F"]:
                raise HTTPException(status_code=400, detail="首次發布必須選擇有效的性別進行綁定")
            
            # 寫入資料庫，從此鎖死
            current_user.gender = req.gender
            db.commit()
            user_gender = req.gender
            
        # 3. 莊別與房號驗證（完全信任並使用資料庫的 user_gender，嚴格 3 碼房號）
        if req.current_building not in DORM_GENDER_MAP or user_gender not in DORM_GENDER_MAP[req.current_building]:
            raise HTTPException(status_code=400, detail="目前莊別與您的性別不符")
            
        for tb in req.target_buildings:
            if tb not in DORM_GENDER_MAP or user_gender not in DORM_GENDER_MAP[tb]:
                raise HTTPException(status_code=400, detail=f"目標莊別 {tb} 與您的性別不符")
                
        current_room_str = str(req.current_room)
        if not re.match(r"^\d{3}$", current_room_str):
            raise HTTPException(status_code=400, detail="房號必須為三位數字")
        if current_room_str.startswith("0"):
            raise HTTPException(status_code=400, detail="房號開頭不能為 0")
            
        current_floor = int(current_room_str[0])
        target_buildings_str = ",".join(req.target_buildings)
        
        if req.target_room is not None:
            target_room_str = str(req.target_room)
            if not re.match(r"^\d{3}$", target_room_str):
                raise HTTPException(status_code=400, detail="目標房號必須為三位數字")

        # 4. 檢查是否有重複處理中的訂單
        existing_req = db.query(ExchangeRequest).filter(
            ExchangeRequest.user_id == current_user.id,
            ExchangeRequest.status.in_(["PENDING", "MATCHED"])
        ).first()
        
        if existing_req:
            raise HTTPException(status_code=400, detail="您已有進行中的請求，請先解除配對或刪除該請求")

        # 5. 進行媒合比對
        matched_candidate = None
        candidates = db.query(ExchangeRequest).filter(
            ExchangeRequest.status == "PENDING",
            ExchangeRequest.user_id != current_user.id,
            ExchangeRequest.gender == user_gender
        ).with_for_update(skip_locked=True).all()
        
        for candidate in candidates:
            if candidate.current_building not in req.target_buildings:
                continue
                
            if req.target_floor and candidate.current_floor != req.target_floor:
                continue
            if req.target_room and candidate.current_room != req.target_room:
                continue
                
            b_targets = candidate.target_buildings.split(",") if candidate.target_buildings else []
            if req.current_building not in b_targets:
                continue
                
            if candidate.target_floor and current_floor != candidate.target_floor:
                continue
            if candidate.target_room and req.current_room != candidate.target_room:
                continue
                
            matched_candidate = candidate
            break
            
        if matched_candidate:
            current_user_email = str(current_user.email)
            partner_email = str(matched_candidate.user.email)

            new_req = ExchangeRequest(
                user_id=current_user.id,
                gender=user_gender,
                current_building=req.current_building,
                current_floor=current_floor,
                current_room=req.current_room,
                current_bed=req.current_bed,
                target_buildings=target_buildings_str,
                target_floor=req.target_floor,
                target_room=req.target_room,
                status="MATCHED",
                matched_with_id=matched_candidate.user_id,
                matched_email=partner_email
            )
            db.add(new_req)
            
            matched_candidate.status = "MATCHED"
            matched_candidate.matched_with_id = current_user.id
            matched_candidate.matched_email = current_user_email
            
            db.commit()

            background_tasks.add_task(send_match_email, current_user_email, partner_email)
            background_tasks.add_task(send_match_email, partner_email, current_user_email)

            return {"status": "MATCHED", "message": "match成功!", "matched_email": partner_email}
        else:
            new_req = ExchangeRequest(
                user_id=current_user.id,
                gender=user_gender,
                current_building=req.current_building,
                current_floor=current_floor,
                current_room=req.current_room,
                current_bed=req.current_bed,
                target_buildings=target_buildings_str,
                target_floor=req.target_floor,
                target_room=req.target_room,
                status="PENDING"
            )
            db.add(new_req)
            db.commit()
            
            return {"status": "PENDING", "message": "已刊登至大廳，等待有緣人"}
            
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="資料庫處理異常，請稍後再試")


@app.post("/api/unmatch")
def unmatch_request(db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    try:
        # 1. 併發防護：先鎖定當前使用者的請求
        user_req = db.query(ExchangeRequest).filter(
            ExchangeRequest.user_id == current_user.id,
            ExchangeRequest.status == "MATCHED"
        ).with_for_update().first()
        
        if not user_req:
            raise HTTPException(status_code=400, detail="找不到符合的配對記錄，或您無權限修改")
            
        partner_id = user_req.matched_with_id
        
        # 2. 鎖定對方的請求
        partner_req = db.query(ExchangeRequest).filter(
            ExchangeRequest.user_id == partner_id,
            ExchangeRequest.status == "MATCHED",
            ExchangeRequest.matched_with_id == current_user.id
        ).with_for_update().first()
        
        # 3. 執行狀態重置
        user_req.status = "PENDING"
        user_req.matched_with_id = None
        user_req.matched_email = None
        
        if partner_req:
            partner_req.status = "PENDING"
            partner_req.matched_with_id = None
            partner_req.matched_email = None
            
        db.commit()
        return {"message": "已成功解除配對，重回大廳尋找"}
        
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="資料庫處理異常，請稍後再試")


@app.get("/api/my_request")
def get_my_request(db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    req = db.query(ExchangeRequest).filter(
        ExchangeRequest.user_id == current_user.id
    ).order_by(ExchangeRequest.created_at.desc()).first()
    
    if not req:
        return {"has_request": False}
        
    return {
        "has_request": True,
        "request": {
            "id": req.id,
            "status": req.status,
            "gender": req.gender,
            "matched_email": req.matched_email,
            "current_building": req.current_building,
            "current_room": req.current_room,
            "target_buildings": req.target_buildings,
            "created_at": req.created_at
        }
    }

@app.delete("/api/requests")
def delete_request(db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    req = db.query(ExchangeRequest).filter(
        ExchangeRequest.user_id == current_user.id,
        ExchangeRequest.status == "PENDING"
    ).first()
    if not req:
        raise HTTPException(status_code=400, detail="找不到可刪除的請求，可能已媒合或不存在")
    db.delete(req)
    db.commit()
    return {"message": "刪除成功"}