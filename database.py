import os
import uuid
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# 載入環境變數
load_dotenv()
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("please set DATABASE_URL environment variable!")

if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,  # 每次從連接池拿取連線前先發送一個簡單的 query 確認連線活著
    pool_recycle=1800    # 每 30 分鐘強制重置連線，避免被伺服器單方面斷開
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(100), unique=True, index=True)
    name = Column(String(100), nullable=True)
    
    # 將性別綁定於使用者帳號，防止發送請求時遭到惡意竄改
    gender = Column(String(10), nullable=True) 
    
    last_message_sent_at = Column(DateTime, nullable=True)
    requests = relationship("ExchangeRequest", back_populates="user")

class ExchangeRequest(Base):
    __tablename__ = "exchange_requests"
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"))

    gender = Column(String(10), index=True)
    
    current_building = Column(String(50))
    current_block = Column(String(10), nullable=True) # 棟號
    current_floor = Column(Integer)   
    current_room = Column(Integer)    
    current_bed = Column(String(10), nullable=True)   # 床號改為可選 (配合擷雲莊)
    
    target_buildings = Column(String(255)) 
    target_floor = Column(Integer, nullable=True)
    target_room = Column(Integer, nullable=True) 
    
    comment = Column(String(30), nullable=True) # 備註留言
    
    status = Column(String(20), default="PENDING", index=True)
    matched_with_id = Column(String(36), nullable=True)
    matched_email = Column(String(100), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="requests")

class SystemStat(Base):
    __tablename__ = "system_stats"
    id = Column(Integer, primary_key=True, default=1)
    total_postings = Column(Integer, default=0)
    total_matches = Column(Integer, default=0)
    total_contacts = Column(Integer, default=0)

def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        stat = db.query(SystemStat).filter(SystemStat.id == 1).first()
        if not stat:
            stat = SystemStat(id=1, total_postings=0, total_matches=0, total_contacts=0)
            db.add(stat)
            db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
    print("PostgreSQL (Neon) the database has been initialized.")