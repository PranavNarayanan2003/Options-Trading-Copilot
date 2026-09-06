"""Live-data launcher for local use or a cloud container."""
from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
os.environ["DATA_MODE"]="live"
import uvicorn
if __name__=="__main__":
    uvicorn.run("app.main:app",host=os.getenv("HOST","0.0.0.0"),port=int(os.getenv("PORT","8000")),reload=False)
