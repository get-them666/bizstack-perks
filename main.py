from fastapi import FastAPI, Form, Request, Depends, Response, BackgroundTasks, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from datetime import datetime
import os
import sqlite3
import uvicorn
from contextlib import asynccontextmanager

# ====================================================
# GLOBAL SYSTEM VARIABLES & DATABASE INIT
# ====================================================
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join("data", "bizstack.db"))

def verify_and_build_production_schema_startup():
    data_dir = os.path.dirname(DATABASE_PATH)
    if data_dir and not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT UNIQUE NOT NULL
    );""")
    conn.commit()
    conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    verify_and_build_production_schema_startup()
    yield

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# ====================================================
# 🌐 ROUTES
# ====================================================
@app.get("/", response_class=HTMLResponse)
async def serve_public_homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    container_port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=container_port)
