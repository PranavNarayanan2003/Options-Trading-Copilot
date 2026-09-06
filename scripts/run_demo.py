"""Synthetic demo launcher. Forces demo mode even when .env is configured for live."""
from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
os.environ["DATA_MODE"]="demo"
import uvicorn
if __name__=="__main__": uvicorn.run("app.main:app",host="127.0.0.1",port=int(os.getenv("PORT","8000")),reload=False)
