from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def home():
    return "<h1>Hello World</h1><p>BizStack Perks is working</p>"

@app.get("/login", response_class=HTMLResponse)
async def login():
    return """
    <html>
    <head><title>Login</title></head>
    <body>
        <h1>Login</h1>
        <form method="POST">
            <input name="username" placeholder="admin">
            <input name="password" type="password" placeholder="password123">
            <button>Login</button>
        </form>
    </body>
    </html>
    """

@app.post("/login")
async def process_login(username: str, password: str):
    return {"status": "ok"}

