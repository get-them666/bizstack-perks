from fastapi import FastAPI, Form, Request, Depends, Response, BackgroundTasks, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import datetime
import os
import sqlite3
import requests
import uvicorn
from contextlib import asynccontextmanager

# ... [Full Python code logic, including DB setup, FastAPI routes (/login, /dashboard, /api/logs/view),
#      Finnhub data integration, and Background Tasks, as presented in the original response] ...
# ... [Use the code provided in the original prompt here] ...

