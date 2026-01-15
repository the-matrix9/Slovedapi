import asyncio
import uvloop
import aiohttp
import aiofiles
import os
import time
import logging
import socket
import uuid
import random
import string
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import cloudscraper
from bs4 import BeautifulSoup

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TMP_DIR = Path("/tmp/audio_processor")
TMP_DIR.mkdir(exist_ok=True)

TEMPLATES_DIR = Path("templates")
TEMPLATES_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

cleanup_tasks = {}

PRESETS = {
    'slowed-reverb': {'id': 'slowed-reverb', 'name': 'Slowed + Reverb'},
    '8d-audio': {'id': '8d-audio', 'name': '8D Audio'},
    '432hz': {'id': '432hz', 'name': '432 Hz Converter'},
    'very-light-bass': {'id': 'very-light-bass', 'name': 'Very Light Bass Boost'},
    'light-bass': {'id': 'light-bass', 'name': 'Light Bass Boost'},
    'moderate-bass': {'id': 'moderate-bass', 'name': 'Moderate Bass Boost'},
    'heavy-bass': {'id': 'heavy-bass', 'name': 'Heavy Bass Boost'},
    'extreme-bass': {'id': 'extreme-bass', 'name': 'Extreme Bass Boost'},
    'vocal-reverb': {'id': 'vocal-reverb', 'name': 'Vocal Reverb'},
    'bathroom-reverb': {'id': 'bathroom-reverb', 'name': 'Bathroom Reverb'},
    'small-room-reverb': {'id': 'small-room-reverb', 'name': 'Small Room Reverb'},
    'medium-room-reverb': {'id': 'medium-room-reverb', 'name': 'Medium Room Reverb'},
    'large-room-reverb': {'id': 'large-room-reverb', 'name': 'Large Room Reverb'},
    'church-hall-reverb': {'id': 'church-hall-reverb', 'name': 'Church Hall Reverb'},
    'cathedral-reverb': {'id': 'cathedral-reverb', 'name': 'Cathedral Reverb'}
}


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_base_url(request: Request):
    host = request.headers.get("host", f"{get_local_ip()}:8000")
    scheme = request.url.scheme
    return f"{scheme}://{host}"


async def cleanup_file(file_path: str, delay: int = 60):
    await asyncio.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up file: {file_path}")
    except Exception as e:
        logger.error(f"Error cleaning up {file_path}: {e}")


class AsyncAudioConverter:
    def __init__(self):
        self.base_url = "https://audioalter.com"
        self.api_url = "https://api.audioalter.com"
        self.session = None
        self.socket_sid = None

    async def init_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Origin': 'https://audioalter.com',
                    'Referer': 'https://audioalter.com/',
                    'Accept-Language': 'en-US,en;q=0.9'
                }
            )

    async def close_session(self):
        if self.session:
            await self.session.close()

    def _generate_timestamp(self):
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

    async def visit_homepage(self):
        try:
            async with self.session.get(f"{self.base_url}/") as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Homepage error: {e}")
            return False

    async def scrape_preset_effects(self, preset_id):
        try:
            url = f"{self.base_url}/_next/data/5wTSQCmpJSyVhIyRp3raZ/preset/{preset_id}.json"
            async with self.session.get(url, params={'preset': preset_id}, headers={'x-nextjs-data': '1'}) as response:
                if response.status == 200:
                    data = await response.json()
                    effects = data.get('pageProps', {}).get('effects', [])
                    if effects:
                        return effects
        except Exception as e:
            logger.error(f"Preset scrape error: {e}")
        return None

    async def get_socket_session(self):
        params = {
            'EIO': '4',
            'transport': 'polling',
            't': self._generate_timestamp()
        }
        async with self.session.get(f"{self.api_url}/socketio/", params=params) as response:
            if response.status == 200:
                data = await response.text()
                if data.startswith('0'):
                    sid = data.split('"sid":"')[1].split('"')[0]
                    self.socket_sid = sid
                    return sid
        raise Exception("Failed to get socket session")

    async def upload_audio(self, file_path):
        async with aiofiles.open(file_path, 'rb') as f:
            content = await f.read()
            
        data = aiohttp.FormData()
        data.add_field('audio', content, filename=os.path.basename(file_path), content_type='audio/mpeg')
        data.add_field('preview', 'false')

        async with self.session.post(f"{self.api_url}/pages/upload", data=data) as response:
            if response.status == 201:
                result = await response.json()
                file_id = result['files'][0]['id']
                logger.info(f"Uploaded: {file_id}")
                return file_id
            else:
                raise Exception(f"Upload failed: {response.status}")

    async def apply_preset(self, file_id, preset_id):
        effects = await self.scrape_preset_effects(preset_id)
        if not effects:
            raise Exception(f"No effects for preset: {preset_id}")

        payload = {
            'id': preset_id,
            'ids': [file_id],
            'effects': effects
        }

        async with self.session.post(f"{self.api_url}/pages/preset", json=payload) as response:
            if response.status in [200, 201]:
                result = await response.json()
                return result.get('id')
            else:
                text = await response.text()
                raise Exception(f"Preset failed: {response.status} - {text}")

    async def join_socket_room(self, job_id):
        if not self.socket_sid:
            return

        params = {
            'EIO': '4',
            'transport': 'polling',
            't': self._generate_timestamp(),
            'sid': self.socket_sid
        }

        async with self.session.post(f"{self.api_url}/socketio/", params=params, data='40') as _:
            pass

        await asyncio.sleep(0.5)

        params['t'] = self._generate_timestamp()
        join_payload = f'42["join","{job_id}"]'
        async with self.session.post(f"{self.api_url}/socketio/", params=params, data=join_payload) as _:
            pass

    async def check_job_status(self, job_id):
        max_attempts = 90
        for attempt in range(max_attempts):
            try:
                async with self.session.get(f"{self.api_url}/pages/{job_id}") as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        if 'files' not in result or len(result['files']) == 0:
                            await asyncio.sleep(2)
                            continue

                        file_status = result['files'][0]['status']

                        if file_status == 2:
                            file_info = result['files'][0]
                            return {
                                'file_id': file_info['id'],
                                'original_name': file_info.get('originalName', 'unknown'),
                                'download_url': f"{self.api_url}/download/{file_info['id']}",
                                'preset': result.get('preset', 'unknown')
                            }
                        elif file_status == 3:
                            raise Exception(f"Processing failed")
            except Exception as e:
                logger.error(f"Status check error: {e}")

            await asyncio.sleep(3)

        raise Exception("Processing timeout")

    async def download_processed_file(self, file_id, output_path):
        download_url = f"{self.api_url}/download/{file_id}"
        async with self.session.get(download_url) as response:
            if response.status == 200:
                async with aiofiles.open(output_path, 'wb') as f:
                    await f.write(await response.read())
                return output_path
            else:
                raise Exception(f"Download failed: {response.status}")

    async def convert(self, input_file, preset_id):
        await self.init_session()
        try:
            await self.visit_homepage()
            sid = await self.get_socket_session()
            file_id = await self.upload_audio(input_file)
            job_id = await self.apply_preset(file_id, preset_id)
            await self.join_socket_room(job_id)
            result_info = await self.check_job_status(job_id)
            
            output_filename = f"processed_{uuid.uuid4().hex[:8]}_{os.path.basename(input_file)}"
            output_path = TMP_DIR / output_filename
            
            await self.download_processed_file(result_info['file_id'], str(output_path))
            
            result_info['local_file'] = str(output_path)
            result_info['filename'] = output_filename
            
            return result_info
        finally:
            await self.close_session()


async def download_from_url(url: str, output_path: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                async with aiofiles.open(output_path, 'wb') as f:
                    await f.write(await response.read())
                return output_path
            else:
                raise Exception(f"Download failed: {response.status}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting API...")
    local_ip = get_local_ip()
    logger.info(f"API running on: {local_ip}:8000")
    yield
    logger.info("Shutting down API...")
    for task in cleanup_tasks.values():
        task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    index_file = TEMPLATES_DIR / "index.html"
    if index_file.exists():
        return templates.TemplateResponse("index.html", {"request": request})
    else:
        return HTMLResponse(content="<h1>API is running. Please create templates/index.html</h1>", status_code=200)


@app.post("/process")
async def process_audio(
    request: Request,
    file: UploadFile = File(...),
    style: str = Query("slowed-reverb", description="Audio effect style")
):
    if style not in PRESETS:
        raise HTTPException(status_code=400, detail=f"Invalid style. Choose from: {list(PRESETS.keys())}")

    input_filename = f"input_{uuid.uuid4().hex[:8]}_{file.filename}"
    input_path = TMP_DIR / input_filename

    try:
        async with aiofiles.open(input_path, 'wb') as f:
            content = await file.read()
            await f.write(content)

        converter = AsyncAudioConverter()
        result = await converter.convert(str(input_path), style)

        if os.path.exists(input_path):
            os.remove(input_path)

        output_path = result['local_file']
        task = asyncio.create_task(cleanup_file(output_path, delay=60))
        cleanup_tasks[output_path] = task

        base_url = get_base_url(request)

        return JSONResponse({
            "success": True,
            "message": "Processing complete",
            "style": style,
            "style_name": PRESETS[style]['name'],
            "filename": result['filename'],
            "download_url": f"{base_url}/download/{result['filename']}",
            "expires_in_seconds": 60,
            "api_dev": "@ISmartCoder",
            "api_updates": "@abirxdhackz"
        })

    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/edit")
async def edit_from_url(
    request: Request,
    url: str = Query(..., description="Audio file URL"),
    style: str = Query("slowed-reverb", description="Audio effect style")
):
    if style not in PRESETS:
        raise HTTPException(status_code=400, detail=f"Invalid style. Choose from: {list(PRESETS.keys())}")

    input_filename = f"input_{uuid.uuid4().hex[:8]}.mp3"
    input_path = TMP_DIR / input_filename

    try:
        await download_from_url(url, str(input_path))

        converter = AsyncAudioConverter()
        result = await converter.convert(str(input_path), style)

        if os.path.exists(input_path):
            os.remove(input_path)

        output_path = result['local_file']
        task = asyncio.create_task(cleanup_file(output_path, delay=60))
        cleanup_tasks[output_path] = task

        base_url = get_base_url(request)

        return JSONResponse({
            "success": True,
            "message": "Processing complete",
            "style": style,
            "style_name": PRESETS[style]['name'],
            "filename": result['filename'],
            "download_url": f"{base_url}/download/{result['filename']}",
            "expires_in_seconds": 60,
            "api_dev": "@Rishu1286",
            "api_updates": "@Rishu1286"
        })

    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = TMP_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found or expired")
    return FileResponse(file_path, media_type='audio/mpeg', filename=filename)


@app.get("/styles")
async def get_styles():
    return JSONResponse({
        "styles": PRESETS,
        "api_dev": "@Rishu1286",
        "api_updates": "@Rishu1286"
    })


if __name__ == "__main__":
    import uvicorn
    
    local_ip = get_local_ip()
    
    print(f"\n{'='*60}")
    print(f"API Server Starting")
    print(f"{'='*60}")
    print(f"Network IP: {local_ip}")
    print(f"Port: 8000")
    print(f"Binding to: 0.0.0.0:8000")
    print(f"{'='*60}")
    print(f"Access URLs:")
    print(f"  - http://{local_ip}:8000")
    print(f"  - http://localhost:8000")
    print(f"  - http://0.0.0.0:8000")
    print(f"{'='*60}\n")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        loop="uvloop",
        log_level="info"
    )
